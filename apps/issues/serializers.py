from decimal import Decimal
from pathlib import Path

from django.conf import settings
from rest_framework import serializers

from apps.chama.models import MembershipRole
from apps.issues.models import (
    AppealStatus,
    Issue,
    IssueActivityLog,
    IssueAttachment,
    IssueCategory,
    IssueComment,
    IssueMediationNote,
    IssueAppeal,
    IssuePriority,
    IssueStatus,
    Suspension,
    Warning,
    WarningSeverity,
)

ANONYMOUS_VIEWER_ROLES = {MembershipRole.MEMBER}
INTERNAL_NOTE_ROLES = {
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.SECRETARY,
    MembershipRole.TREASURER,
}


class IssueUserSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    full_name = serializers.CharField(read_only=True)
    phone = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True)


class IssueCommentSerializer(serializers.ModelSerializer):
    author = IssueUserSerializer(read_only=True)
    author_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = IssueComment
        fields = [
            "id",
            "issue",
            "author_id",
            "author",
            "message",
            "is_internal",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class IssueAttachmentSerializer(serializers.ModelSerializer):
    uploaded_by = IssueUserSerializer(read_only=True)
    uploaded_by_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = IssueAttachment
        fields = [
            "id",
            "issue",
            "uploaded_by_id",
            "uploaded_by",
            "file",
            "content_type",
            "size",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class IssueActivityLogSerializer(serializers.ModelSerializer):
    actor = IssueUserSerializer(read_only=True)

    class Meta:
        model = IssueActivityLog
        fields = ["id", "issue", "actor", "action", "meta", "created_at"]
        read_only_fields = fields


class IssueListSerializer(serializers.ModelSerializer):
    created_by_user = IssueUserSerializer(source="created_by", read_only=True)
    assigned_to_user = IssueUserSerializer(source="assigned_to", read_only=True)
    reported_user_data = IssueUserSerializer(source="reported_user", read_only=True)
    created_by_id = serializers.UUIDField(read_only=True)
    assigned_to_id = serializers.UUIDField(read_only=True)
    reported_user_id = serializers.UUIDField(read_only=True)
    loan_id = serializers.UUIDField(read_only=True)
    comment_count = serializers.SerializerMethodField()
    attachment_count = serializers.SerializerMethodField()

    class Meta:
        model = Issue
        fields = [
            "id",
            "chama",
            "title",
            "description",
            "category",
            "priority",
            "status",
            "assigned_to_id",
            "assigned_to_user",
            "reported_user_id",
            "reported_user_data",
            "loan_id",
            "report_type",
            "is_anonymous",
            "due_at",
            "resolved_at",
            "closed_at",
            "created_by_id",
            "created_by_user",
            "created_at",
            "updated_at",
            "comment_count",
            "attachment_count",
        ]
        read_only_fields = fields

    def get_comment_count(self, obj):
        return obj.comments.count()

    def get_attachment_count(self, obj):
        return obj.attachments.count()

    def to_representation(self, instance):
        payload = super().to_representation(instance)
        request = self.context.get("request")
        membership = self.context.get("membership")

        if not request:
            return payload

        if request.user.is_superuser:
            return payload

        membership_role = getattr(membership, "role", "")
        is_creator = instance.created_by_id == request.user.id
        should_hide_creator = (
            instance.is_anonymous
            and not is_creator
            and membership_role in ANONYMOUS_VIEWER_ROLES
        )
        if should_hide_creator:
            payload["created_by_id"] = None
            payload["created_by_user"] = None
        return payload


class IssueDetailSerializer(IssueListSerializer):
    comments = serializers.SerializerMethodField()
    attachments = IssueAttachmentSerializer(many=True, read_only=True)
    activity_logs = IssueActivityLogSerializer(many=True, read_only=True)

    class Meta(IssueListSerializer.Meta):
        fields = IssueListSerializer.Meta.fields + [
            "comments",
            "attachments",
            "activity_logs",
        ]
        read_only_fields = fields

    def get_comments(self, obj):
        request = self.context.get("request")
        membership = self.context.get("membership")
        comments = obj.comments.select_related("author")

        if request and not request.user.is_superuser:
            membership_role = getattr(membership, "role", "")
            is_internal_allowed = membership_role in INTERNAL_NOTE_ROLES
            if not is_internal_allowed:
                comments = comments.filter(is_internal=False)

        return IssueCommentSerializer(comments, many=True).data


class IssueCreateSerializer(serializers.ModelSerializer):
    chama_id = serializers.UUIDField(required=False)
    reported_user_id = serializers.UUIDField(required=False, allow_null=True)
    loan_id = serializers.UUIDField(required=False, allow_null=True)

    class Meta:
        model = Issue
        fields = [
            "chama_id",
            "title",
            "description",
            "category",
            "priority",
            "reported_user_id",
            "loan_id",
            "report_type",
            "is_anonymous",
            "due_at",
        ]

    def validate(self, attrs):
        report_type = attrs.get("report_type", "")
        reported_user_id = attrs.get("reported_user_id")
        if report_type and not reported_user_id:
            raise serializers.ValidationError(
                {"report_type": "report_type requires reported_user_id."}
            )
        return attrs


class IssueUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, max_length=255)
    description = serializers.CharField(required=False)
    category = serializers.ChoiceField(choices=IssueCategory.choices, required=False)
    priority = serializers.ChoiceField(choices=IssuePriority.choices, required=False)
    reported_user_id = serializers.UUIDField(required=False, allow_null=True)
    loan_id = serializers.UUIDField(required=False, allow_null=True)
    report_type = serializers.CharField(required=False, allow_blank=True)
    is_anonymous = serializers.BooleanField(required=False)
    due_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate(self, attrs):
        report_type = attrs.get("report_type")
        reported_user_id = attrs.get("reported_user_id")
        if report_type and not reported_user_id:
            raise serializers.ValidationError(
                {"report_type": "report_type requires reported_user_id."}
            )
        return attrs


class IssueAssignSerializer(serializers.Serializer):
    assigned_to_id = serializers.UUIDField()
    note = serializers.CharField(required=False, allow_blank=True)


class IssueStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=IssueStatus.choices)
    note = serializers.CharField(required=False, allow_blank=True)


class IssueCommentCreateSerializer(serializers.Serializer):
    message = serializers.CharField()
    is_internal = serializers.BooleanField(required=False, default=False)


class IssueAttachmentCreateSerializer(serializers.Serializer):
    file = serializers.FileField()

    def validate_file(self, value):
        max_size_mb = Decimal(
            str(getattr(settings, "ISSUE_ATTACHMENT_MAX_FILE_SIZE_MB", "10"))
        )
        file_size_mb = Decimal(str((value.size or 0) / (1024 * 1024))).quantize(
            Decimal("0.01")
        )
        if file_size_mb > max_size_mb:
            raise serializers.ValidationError(
                f"Attachment exceeds {max_size_mb} MB size limit."
            )

        allowed_extensions = {
            ext.lower()
            for ext in getattr(
                settings,
                "ISSUE_ATTACHMENT_ALLOWED_EXTENSIONS",
                [".jpg", ".jpeg", ".png", ".webp", ".pdf"],
            )
        }
        extension = Path(value.name).suffix.lower()
        if extension not in allowed_extensions:
            raise serializers.ValidationError(
                f"Unsupported file type. Allowed: {', '.join(sorted(allowed_extensions))}."
            )
        return value


class IssueWarnSerializer(serializers.Serializer):
    reason = serializers.CharField()
    severity = serializers.ChoiceField(choices=WarningSeverity.choices)
    message_to_user = serializers.CharField(required=False, allow_blank=True)
    channels = serializers.ListField(
        child=serializers.ChoiceField(choices=["sms", "email", "push"]),
        required=False,
        default=["sms", "email"],
    )


class IssueSuspendSerializer(serializers.Serializer):
    reason = serializers.CharField()
    starts_at = serializers.DateTimeField(required=False)
    ends_at = serializers.DateTimeField(required=False, allow_null=True)
    message_to_user = serializers.CharField(required=False, allow_blank=True)
    channels = serializers.ListField(
        child=serializers.ChoiceField(choices=["sms", "email", "push"]),
        required=False,
        default=["sms", "email"],
    )

    def validate(self, attrs):
        starts_at = attrs.get("starts_at")
        ends_at = attrs.get("ends_at")
        if starts_at and ends_at and ends_at < starts_at:
            raise serializers.ValidationError(
                {"ends_at": "ends_at cannot be earlier than starts_at."}
            )
        return attrs


class IssueLiftSuspensionSerializer(serializers.Serializer):
    lift_reason = serializers.CharField(required=False, allow_blank=True, default="")
    channels = serializers.ListField(
        child=serializers.ChoiceField(choices=["sms", "email", "push"]),
        required=False,
        default=["sms", "email"],
    )


class WarningSerializer(serializers.ModelSerializer):
    issued_by = IssueUserSerializer(read_only=True)
    user = IssueUserSerializer(read_only=True)

    class Meta:
        model = Warning
        fields = [
            "id",
            "chama",
            "user",
            "issue",
            "reason",
            "severity",
            "message_to_user",
            "issued_by",
            "issued_at",
            "status",
            "revoked_at",
            "revoked_by",
            "revocation_reason",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class SuspensionSerializer(serializers.ModelSerializer):
    suspended_by = IssueUserSerializer(read_only=True)
    lifted_by = IssueUserSerializer(read_only=True)
    user = IssueUserSerializer(read_only=True)

    class Meta:
        model = Suspension
        fields = [
            "id",
            "chama",
            "user",
            "issue",
            "reason",
            "starts_at",
            "ends_at",
            "suspended_by",
            "is_active",
            "lifted_at",
            "lifted_by",
            "lift_reason",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class IssueFilterSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField(required=False)
    status = serializers.ChoiceField(choices=IssueStatus.choices, required=False)
    category = serializers.ChoiceField(choices=IssueCategory.choices, required=False)
    priority = serializers.ChoiceField(choices=IssuePriority.choices, required=False)
    assigned_to = serializers.UUIDField(required=False)
    reported_user = serializers.UUIDField(required=False)
    loan_id = serializers.UUIDField(required=False)
    created_by = serializers.UUIDField(required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    search = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        date_from = attrs.get("date_from")
        date_to = attrs.get("date_to")
        if date_from and date_to and date_to < date_from:
            raise serializers.ValidationError(
                {"date_to": "date_to must be on or after date_from."}
            )
        return attrs


class IssueStatsQuerySerializer(serializers.Serializer):
    chama_id = serializers.UUIDField(required=False)


class IssueAppealSerializer(serializers.ModelSerializer):
    appellant = IssueUserSerializer(read_only=True)
    reviewed_by = IssueUserSerializer(read_only=True)

    class Meta:
        model = IssueAppeal
        fields = [
            "id",
            "issue",
            "appellant",
            "message",
            "status",
            "reviewed_by",
            "reviewed_at",
            "review_note",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class IssueAppealCreateSerializer(serializers.Serializer):
    message = serializers.CharField()


class IssueAppealReviewSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[
            AppealStatus.IN_REVIEW,
            AppealStatus.ACCEPTED,
            AppealStatus.REJECTED,
        ]
    )
    review_note = serializers.CharField(required=False, allow_blank=True)


class IssueMediationNoteSerializer(serializers.ModelSerializer):
    author = IssueUserSerializer(read_only=True)

    class Meta:
        model = IssueMediationNote
        fields = [
            "id",
            "issue",
            "author",
            "note",
            "is_private",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class IssueMediationNoteCreateSerializer(serializers.Serializer):
    note = serializers.CharField()
    is_private = serializers.BooleanField(default=True)


class IssueEscalationSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True)
    channels = serializers.ListField(
        child=serializers.ChoiceField(choices=["sms", "email", "push"]),
        required=False,
        default=["sms", "email"],
    )
