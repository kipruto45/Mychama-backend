from decimal import Decimal
from pathlib import Path

from django.conf import settings
from rest_framework import serializers

from apps.chama.models import MembershipRole
from apps.issues.models import (
    AppealStatus,
    Issue,
    IssueActivityLog,
    IssueAppeal,
    IssueAutoTriggerLog,
    IssueCategory,
    IssueComment,
    IssueCommentType,
    IssueCommentVisibility,
    IssueEscalationType,
    IssueEvidence,
    IssueEvidenceType,
    IssueMediationNote,
    IssuePriority,
    IssueRating,
    IssueReopenDecision,
    IssueReopenRequest,
    IssueResolution,
    IssueResolutionStatus,
    IssueResolutionType,
    IssueScope,
    IssueSourceType,
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


class IssueStatusHistorySerializer(serializers.ModelSerializer):
    changed_by = IssueUserSerializer(read_only=True)

    class Meta:
        model = IssueStatus = None
        fields = ["id", "issue", "from_status", "to_status", "changed_by", "reason", "created_at"]


def get_status_history_serializer():
    from apps.issues.models import IssueStatusHistory
    return IssueStatusHistorySerializer


class IssueAssignmentHistorySerializer(serializers.ModelSerializer):
    assigned_from = IssueUserSerializer(read_only=True)
    assigned_to = IssueUserSerializer(read_only=True)
    assigned_by = IssueUserSerializer(read_only=True)

    class Meta:
        from apps.issues.models import IssueAssignmentHistory
        model = IssueAssignmentHistory
        fields = ["id", "issue", "assigned_from", "assigned_to", "assigned_role", "assigned_by", "note", "created_at"]


class IssueEvidenceSerializer(serializers.ModelSerializer):
    uploaded_by = IssueUserSerializer(read_only=True)
    uploaded_by_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = IssueEvidence
        fields = [
            "id",
            "issue",
            "uploaded_by_id",
            "uploaded_by",
            "file",
            "evidence_type",
            "caption",
            "content_type",
            "size",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


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
            "body",
            "comment_type",
            "visibility",
            "is_clarification_response",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class IssueResolutionSerializer(serializers.ModelSerializer):
    proposed_by = IssueUserSerializer(read_only=True)
    approved_by = IssueUserSerializer(read_only=True)
    rejected_by = IssueUserSerializer(read_only=True)

    class Meta:
        model = IssueResolution
        fields = [
            "id",
            "issue",
            "proposed_by",
            "approved_by",
            "rejected_by",
            "resolution_type",
            "summary",
            "detailed_action_taken",
            "financial_adjustment_amount",
            "approved_at",
            "rejected_at",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class IssueRatingSerializer(serializers.ModelSerializer):
    rated_by = IssueUserSerializer(read_only=True)

    class Meta:
        model = IssueRating
        fields = ["id", "issue", "rated_by", "score", "feedback", "created_at", "updated_at"]
        read_only_fields = fields


class IssueReopenRequestSerializer(serializers.ModelSerializer):
    requested_by = IssueUserSerializer(read_only=True)
    decided_by = IssueUserSerializer(read_only=True)

    class Meta:
        model = IssueReopenRequest
        fields = [
            "id",
            "issue",
            "requested_by",
            "reason",
            "decision",
            "decided_by",
            "decided_at",
            "decision_note",
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
    evidence_count = serializers.SerializerMethodField()
    resolution_count = serializers.SerializerMethodField()
    rating_average = serializers.SerializerMethodField()

    class Meta:
        model = Issue
        fields = [
            "id",
            "issue_code",
            "chama",
            "title",
            "description",
            "category",
            "severity",
            "status",
            "source_type",
            "issue_scope",
            "assigned_to_id",
            "assigned_to_user",
            "assigned_role",
            "reported_user_id",
            "reported_user_data",
            "loan_id",
            "report_type",
            "is_anonymous",
            "due_at",
            "resolved_at",
            "closed_at",
            "reopened_count",
            "escalation_type",
            "escalation_reason",
            "chairperson_approved",
            "chairperson_approved_at",
            "created_by_id",
            "created_by_user",
            "created_at",
            "updated_at",
            "comment_count",
            "evidence_count",
            "resolution_count",
            "rating_average",
        ]
        read_only_fields = fields

    def get_comment_count(self, obj):
        return obj.comments.count()

    def get_evidence_count(self, obj):
        return obj.evidences.count()

    def get_resolution_count(self, obj):
        return obj.resolutions.count()

    def get_rating_average(self, obj):
        ratings = obj.ratings.all()
        if not ratings:
            return None
        return sum(r.score for r in ratings) / len(ratings)

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
    evidences = IssueEvidenceSerializer(many=True, read_only=True)
    activity_logs = IssueActivityLogSerializer(many=True, read_only=True)
    resolutions = IssueResolutionSerializer(many=True, read_only=True)
    ratings = IssueRatingSerializer(many=True, read_only=True)
    reopen_requests = IssueReopenRequestSerializer(many=True, read_only=True)
    status_history = serializers.SerializerMethodField()
    assignment_history = serializers.SerializerMethodField()

    class Meta(IssueListSerializer.Meta):
        fields = IssueListSerializer.Meta.fields + [
            "comments",
            "evidences",
            "activity_logs",
            "resolutions",
            "ratings",
            "reopen_requests",
            "status_history",
            "assignment_history",
        ]
        read_only_fields = IssueListSerializer.Meta.fields

    def get_comments(self, obj):
        request = self.context.get("request")
        membership = self.context.get("membership")
        comments = obj.comments.select_related("author")

        if request and not request.user.is_superuser:
            membership_role = getattr(membership, "role", "")
            is_internal_allowed = membership_role in INTERNAL_NOTE_ROLES
            if not is_internal_allowed:
                comments = comments.filter(visibility=IssueCommentVisibility.MEMBER_VISIBLE)

        return IssueCommentSerializer(comments, many=True).data

    def get_status_history(self, obj):
        from apps.issues.models import IssueStatusHistory
        StatusHistSerializer = type(
            'StatusHistorySerializer',
            (serializers.ModelSerializer,),
            {
                'Meta': type(
                    'Meta',
                    (),
                    {
                        'model': IssueStatusHistory,
                        'fields': ['id', 'issue', 'from_status', 'to_status', 'changed_by', 'reason', 'created_at'],
                        'read_only_fields': ['id', 'issue', 'from_status', 'to_status', 'changed_by', 'reason', 'created_at'],
                    }
                )
            }
        )
        history = obj.status_history.order_by("created_at")[:20]
        return StatusHistSerializer(history, many=True).data

    def get_assignment_history(self, obj):
        history = obj.assignment_history.order_by("-created_at")[:10]
        return IssueAssignmentHistorySerializer(history, many=True).data


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
            "severity",
            "source_type",
            "issue_scope",
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
    category = serializers.ChoiceField(
        choices=IssueCategory.choices, required=False
    )
    severity = serializers.ChoiceField(
        choices=IssuePriority.choices, required=False
    )
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
    assigned_role = serializers.ChoiceField(
        choices=[
            ("chairperson", "Chairperson"),
            ("treasurer", "Treasurer"),
            ("committee", "Committee"),
            ("admin", "Admin"),
        ],
        required=False,
        allow_blank=True,
    )
    note = serializers.CharField(required=False, allow_blank=True)


class IssueStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=IssueStatus.choices)
    note = serializers.CharField(required=False, allow_blank=True)


class IssueClarificationRequestSerializer(serializers.Serializer):
    message = serializers.CharField()


class IssueClarificationResponseSerializer(serializers.Serializer):
    message = serializers.CharField()


class IssueInvestigationUpdateSerializer(serializers.Serializer):
    note = serializers.CharField()


class IssueResolutionProposeSerializer(serializers.Serializer):
    resolution_type = serializers.ChoiceField(choices=IssueResolutionType.choices)
    summary = serializers.CharField()
    detailed_action_taken = serializers.CharField(required=False, allow_blank=True)
    financial_adjustment_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True
    )


class IssueResolutionApproveSerializer(serializers.Serializer):
    pass


class IssueResolutionRejectSerializer(serializers.Serializer):
    reason = serializers.CharField()


class IssueChairpersonDecisionSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True)


class IssueDismissSerializer(serializers.Serializer):
    reason = serializers.CharField()


class IssueEscalateSerializer(serializers.Serializer):
    escalation_type = serializers.ChoiceField(choices=IssueEscalationType.choices)
    reason = serializers.CharField(required=False, allow_blank=True)


class IssueReopenSerializer(serializers.Serializer):
    reason = serializers.CharField()


class IssueRatingSerializer(serializers.Serializer):
    score = serializers.IntegerField(min_value=1, max_value=5)
    feedback = serializers.CharField(required=False, allow_blank=True)


class IssueCommentCreateSerializer(serializers.Serializer):
    body = serializers.CharField()
    comment_type = serializers.ChoiceField(
        choices=IssueCommentType.choices, required=False, default=IssueCommentType.PUBLIC_UPDATE
    )
    visibility = serializers.ChoiceField(
        choices=IssueCommentVisibility.choices, required=False, default=IssueCommentVisibility.MEMBER_VISIBLE
    )


class IssueEvidenceCreateSerializer(serializers.Serializer):
    file = serializers.FileField()
    evidence_type = serializers.ChoiceField(
        choices=IssueEvidenceType.choices, required=False, default=IssueEvidenceType.OTHER
    )
    caption = serializers.CharField(required=False, allow_blank=True, max_length=500)

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
            "issued_by issued_at",
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
    severity = serializers.ChoiceField(choices=IssuePriority.choices, required=False)
    source_type = serializers.ChoiceField(choices=IssueSourceType.choices, required=False)
    issue_scope = serializers.ChoiceField(choices=IssueScope.choices, required=False)
    assigned_to = serializers.UUIDField(required=False)
    reported_user = serializers.UUIDField(required=False)
    loan_id = serializers.UUIDField(required=False)
    created_by = serializers.UUIDField(required=False)
    escalation_type = serializers.ChoiceField(choices=IssueEscalationType.choices, required=False)
    reopened = serializers.BooleanField(required=False)
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


class IssueAutoTriggerLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = IssueAutoTriggerLog
        fields = [
            "id",
            "trigger_type",
            "linked_object_type",
            "linked_object_id",
            "metadata",
            "generated_issue",
            "created_at",
        ]
        read_only_fields = fields


class IssueExportSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField(required=True)
    status = serializers.ChoiceField(choices=IssueStatus.choices, required=False)
    category = serializers.ChoiceField(choices=IssueCategory.choices, required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)