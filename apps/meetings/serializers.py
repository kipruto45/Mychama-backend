from decimal import Decimal
from pathlib import Path

from django.conf import settings
from rest_framework import serializers

from apps.chama.models import Membership
from apps.meetings.models import (
    AgendaItem,
    AgendaItemStatus,
    Attendance,
    AttendanceStatus,
    Meeting,
    MeetingVote,
    MinutesApproval,
    MinutesStatus,
    Resolution,
    ResolutionStatus,
    VoteChoice,
)
from core.utils import get_file_size_mb


class MeetingSerializer(serializers.ModelSerializer):
    chama_id = serializers.UUIDField(write_only=True, required=False)
    created_by_id = serializers.UUIDField(read_only=True)
    cancelled_by_id = serializers.UUIDField(read_only=True)
    agenda = serializers.SerializerMethodField()
    attendance = serializers.SerializerMethodField()

    def get_agenda(self, obj):
        return AgendaItemSerializer(obj.agenda_items.all(), many=True).data

    def get_attendance(self, obj):
        return AttendanceSerializer(obj.attendance.all(), many=True).data

    class Meta:
        model = Meeting
        fields = [
            "id",
            "chama",
            "chama_id",
            "title",
            "description",
            "location",
            "location_type",
            "meeting_link",
            "date",
            "agenda",
            "minutes_text",
            "minutes_file",
            "attendance_qr_token",
            "quorum_percentage",
            "minutes_status",
            "cancelled_at",
            "cancelled_by_id",
            "cancellation_reason",
            "minutes_approved_by",
            "minutes_approved_at",
            "attendance",
            "created_by_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "chama",
            "minutes_text",
            "minutes_file",
            "created_by_id",
            "cancelled_at",
            "cancelled_by_id",
            "cancellation_reason",
            "created_at",
            "updated_at",
        ]


class MeetingCreateSerializer(serializers.ModelSerializer):
    chama_id = serializers.UUIDField(required=False)
    agenda = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        allow_empty=True,
    )

    class Meta:
        model = Meeting
        fields = [
            "chama_id",
            "title",
            "description",
            "location",
            "location_type",
            "meeting_link",
            "date",
            "agenda",
            "quorum_percentage",
        ]

    def validate(self, attrs):
        chama = self.context.get("chama")
        if not attrs.get("chama_id") and not chama:
            raise serializers.ValidationError(
                {"chama_id": "chama_id is required (or provide X-CHAMA-ID header)."}
            )
        return attrs


class MeetingUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    location = serializers.CharField(max_length=255, required=False, allow_blank=True)
    location_type = serializers.ChoiceField(
        choices=Meeting._meta.get_field("location_type").choices,
        required=False,
    )
    meeting_link = serializers.URLField(required=False, allow_blank=True)
    date = serializers.DateTimeField(required=False)
    quorum_percentage = serializers.IntegerField(required=False, min_value=1, max_value=100)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("At least one field is required.")
        return attrs


class MeetingCancelSerializer(serializers.Serializer):
    cancellation_reason = serializers.CharField(required=False, allow_blank=True, max_length=1000)


class AttendanceSerializer(serializers.ModelSerializer):
    member_id = serializers.UUIDField(read_only=True)
    member_name = serializers.CharField(source="member.full_name", read_only=True)
    member_phone = serializers.CharField(source="member.phone", read_only=True)

    class Meta:
        model = Attendance
        fields = [
            "id",
            "meeting",
            "member_id",
            "member_name",
            "member_phone",
            "status",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "meeting",
            "member_id",
            "member_name",
            "member_phone",
            "created_at",
            "updated_at",
        ]


class AttendanceMarkItemSerializer(serializers.Serializer):
    member_id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=AttendanceStatus.choices)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class BulkAttendanceMarkSerializer(serializers.Serializer):
    records = AttendanceMarkItemSerializer(many=True)

    def validate_records(self, value):
        if not value:
            raise serializers.ValidationError(
                "At least one attendance record is required."
            )

        member_ids = [str(item["member_id"]) for item in value]
        if len(member_ids) != len(set(member_ids)):
            raise serializers.ValidationError(
                "Duplicate member_id values are not allowed."
            )
        return value

    def validate(self, attrs):
        meeting = self.context["meeting"]
        member_ids = [item["member_id"] for item in attrs["records"]]
        approved_members_count = Membership.objects.filter(
            chama=meeting.chama,
            user_id__in=member_ids,
            is_active=True,
            is_approved=True,
        ).count()
        if approved_members_count != len(member_ids):
            raise serializers.ValidationError(
                {"records": "All members must be approved and active in this chama."}
            )
        return attrs


class AttendanceScanSerializer(serializers.Serializer):
    qr_payload = serializers.CharField()
    status = serializers.ChoiceField(
        choices=AttendanceStatus.choices,
        required=False,
        default=AttendanceStatus.PRESENT,
    )
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class MeetingMinutesUploadSerializer(serializers.Serializer):
    minutes_text = serializers.CharField(required=False, allow_blank=True)
    minutes_file = serializers.FileField(required=False, allow_null=True)

    def validate_minutes_file(self, value):
        max_size_mb = Decimal(
            str(getattr(settings, "MEETING_MINUTES_MAX_FILE_SIZE_MB", "5"))
        )
        file_size_mb = get_file_size_mb(value)
        if file_size_mb > max_size_mb:
            raise serializers.ValidationError(
                f"Minutes file exceeds {max_size_mb} MB size limit."
            )

        allowed_extensions = {
            ext.lower()
            for ext in getattr(
                settings,
                "MEETING_MINUTES_ALLOWED_EXTENSIONS",
                [".pdf", ".doc", ".docx", ".txt"],
            )
        }
        extension = Path(value.name).suffix.lower()
        if extension not in allowed_extensions:
            raise serializers.ValidationError(
                f"Unsupported file type. Allowed: {', '.join(sorted(allowed_extensions))}."
            )
        return value

    def validate(self, attrs):
        if "minutes_text" not in attrs and "minutes_file" not in attrs:
            raise serializers.ValidationError(
                {"detail": "Provide minutes_text or minutes_file."}
            )
        return attrs


class ResolutionSerializer(serializers.ModelSerializer):
    meeting_id = serializers.UUIDField(read_only=True)
    assigned_to_id = serializers.UUIDField(read_only=True)
    assigned_to_name = serializers.CharField(
        source="assigned_to.full_name", read_only=True
    )

    class Meta:
        model = Resolution
        fields = [
            "id",
            "meeting_id",
            "text",
            "assigned_to_id",
            "assigned_to_name",
            "due_date",
            "status",
            "completed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ResolutionCreateSerializer(serializers.Serializer):
    text = serializers.CharField()
    assigned_to_id = serializers.UUIDField(required=False, allow_null=True)
    due_date = serializers.DateField(required=False, allow_null=True)


class ResolutionStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=ResolutionStatus.choices)


class MeetingsQuerySerializer(serializers.Serializer):
    chama_id = serializers.UUIDField(required=False)
    search = serializers.CharField(required=False, allow_blank=True)
    scope = serializers.ChoiceField(
        choices=["all", "upcoming", "past"],
        required=False,
        default="all",
    )
    page = serializers.IntegerField(required=False, min_value=1, default=1)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=100, default=20)


class ActionItemsDashboardQuerySerializer(serializers.Serializer):
    chama_id = serializers.UUIDField(required=False)
    status = serializers.ChoiceField(
        choices=ResolutionStatus.choices,
        required=False,
    )


class AgendaItemSerializer(serializers.ModelSerializer):
    proposed_by_name = serializers.CharField(source="proposed_by.full_name", read_only=True)
    approved_by_name = serializers.CharField(source="approved_by.full_name", read_only=True)

    class Meta:
        model = AgendaItem
        fields = [
            "id",
            "meeting",
            "proposed_by",
            "proposed_by_name",
            "title",
            "description",
            "order",
            "duration_minutes",
            "status",
            "approved_by",
            "approved_by_name",
            "approved_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class AgendaItemCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True)
    order = serializers.IntegerField(required=False)
    duration_minutes = serializers.IntegerField(required=False)


class AgendaItemStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[
            AgendaItemStatus.APPROVED,
            AgendaItemStatus.REJECTED,
            AgendaItemStatus.DONE,
        ]
    )


class MeetingVoteCreateSerializer(serializers.Serializer):
    agenda_item_id = serializers.UUIDField(required=False)
    choice = serializers.ChoiceField(choices=VoteChoice.choices)
    note = serializers.CharField(required=False, allow_blank=True)


class MeetingVoteSerializer(serializers.ModelSerializer):
    voter_name = serializers.CharField(source="voter.full_name", read_only=True)

    class Meta:
        model = MeetingVote
        fields = [
            "id",
            "meeting",
            "agenda_item",
            "voter",
            "voter_name",
            "choice",
            "note",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class MinutesApprovalActionSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(
        choices=[MinutesStatus.APPROVED, MinutesStatus.REJECTED]
    )
    note = serializers.CharField(required=False, allow_blank=True)


class MinutesApprovalSerializer(serializers.ModelSerializer):
    reviewer_name = serializers.CharField(source="reviewer.full_name", read_only=True)

    class Meta:
        model = MinutesApproval
        fields = [
            "id",
            "meeting",
            "reviewer",
            "reviewer_name",
            "decision",
            "note",
            "decided_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
