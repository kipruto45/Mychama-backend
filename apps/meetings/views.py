import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import MemberCard
from apps.billing.gating import BillingAccessMixin
from apps.chama.models import Chama, Membership, MembershipRole
from apps.chama.permissions import get_membership
from apps.chama.services import get_effective_role
from apps.meetings.models import (
    AgendaItem,
    AgendaItemStatus,
    Attendance,
    Meeting,
    MeetingVote,
    MinutesApproval,
    MinutesStatus,
    VoteChoice,
)
from apps.meetings.serializers import (
    ActionItemsDashboardQuerySerializer,
    AgendaItemCreateSerializer,
    AgendaItemSerializer,
    AgendaItemStatusSerializer,
    AttendanceScanSerializer,
    AttendanceSerializer,
    BulkAttendanceMarkSerializer,
    MeetingCreateSerializer,
    MeetingMinutesUploadSerializer,
    MeetingSerializer,
    MeetingsQuerySerializer,
    MeetingVoteCreateSerializer,
    MeetingVoteSerializer,
    MinutesApprovalActionSerializer,
    MinutesApprovalSerializer,
    ResolutionSerializer,
)
from apps.meetings.services import (
    build_action_items_dashboard,
    build_meeting_summary,
    schedule_meeting_reminders,
)
from core.algorithms.governance import quorum_required
from core.algorithms.meetings import build_meeting_window, detect_overlapping_windows


def _parse_uuid(value, field_name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError) as exc:
        raise ValidationError({field_name: f"Invalid {field_name}."}) from exc


def _resolve_chama_scope(request, payload_chama_id=None) -> str:
    header_chama_id = request.headers.get("X-CHAMA-ID")
    scoped_from_payload = (
        _parse_uuid(payload_chama_id, "chama_id") if payload_chama_id else None
    )
    scoped_from_header = (
        _parse_uuid(header_chama_id, "X-CHAMA-ID") if header_chama_id else None
    )

    if (
        scoped_from_payload
        and scoped_from_header
        and scoped_from_payload != scoped_from_header
    ):
        raise ValidationError(
            {"detail": "X-CHAMA-ID must match chama_id from request payload/query."}
        )

    scoped_chama_id = scoped_from_payload or scoped_from_header
    if not scoped_chama_id:
        raise ValidationError(
            {"chama_id": "Provide chama_id in query/body or X-CHAMA-ID header."}
        )
    return scoped_chama_id


def _require_member(user, chama_id):
    membership = get_membership(user, chama_id)
    if not membership:
        raise PermissionDenied("You are not an approved active member of this chama.")
    return membership


def _require_secretary_or_admin(user, chama_id):
    membership = _require_member(user, chama_id)
    effective_role = get_effective_role(user, chama_id, membership)
    if effective_role not in {MembershipRole.CHAMA_ADMIN, MembershipRole.SECRETARY}:
        raise PermissionDenied("Only chama admin or secretary can perform this action.")
    return membership


class MeetingsBaseView(BillingAccessMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    billing_feature_key = "meeting_scheduler"


class MeetingListCreateView(MeetingsBaseView):

    def get(self, request):
        query_serializer = MeetingsQuerySerializer(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)

        scoped_chama_id = _resolve_chama_scope(
            request,
            query_serializer.validated_data.get("chama_id"),
        )
        _require_member(request.user, scoped_chama_id)

        queryset = Meeting.objects.filter(chama_id=scoped_chama_id).order_by("-date")
        search_term = query_serializer.validated_data.get("search")
        if search_term:
            queryset = queryset.filter(
                Q(title__icontains=search_term) | Q(agenda__icontains=search_term)
            )

        return Response(MeetingSerializer(queryset, many=True).data)

    def post(self, request):
        payload_serializer = MeetingCreateSerializer(
            data=request.data,
            context={"chama": request.headers.get("X-CHAMA-ID")},
        )
        payload_serializer.is_valid(raise_exception=True)

        scoped_chama_id = _resolve_chama_scope(
            request,
            payload_serializer.validated_data.get("chama_id"),
        )
        _require_secretary_or_admin(request.user, scoped_chama_id)
        chama = get_object_or_404(Chama, id=scoped_chama_id)

        duration_minutes = int(
            getattr(settings, "MEETING_DEFAULT_DURATION_MINUTES", 120)
        )
        proposed_window = build_meeting_window(
            start=payload_serializer.validated_data["date"],
            duration_minutes=duration_minutes,
            metadata={"title": payload_serializer.validated_data["title"]},
        )
        nearby_meetings = (
            Meeting.objects.filter(
                chama=chama,
                date__lt=proposed_window.end,
                date__gt=proposed_window.start - timedelta(minutes=duration_minutes),
            )
            .only("id", "title", "date")
            .order_by("date")
        )
        existing_windows = [
            build_meeting_window(
                start=item.date,
                duration_minutes=duration_minutes,
                metadata={"id": str(item.id), "title": item.title},
            )
            for item in nearby_meetings
        ]
        conflicts = detect_overlapping_windows(
            proposed=proposed_window,
            existing=existing_windows,
        )
        if conflicts:
            first_conflict = conflicts[0]
            raise ValidationError(
                {
                    "date": ("Meeting conflicts with an existing schedule."),
                    "conflict_meeting": {
                        "id": first_conflict.metadata.get("id"),
                        "title": first_conflict.metadata.get("title"),
                        "date": first_conflict.start.isoformat(),
                    },
                }
            )

        meeting = Meeting.objects.create(
            chama=chama,
            title=payload_serializer.validated_data["title"],
            date=payload_serializer.validated_data["date"],
            agenda=payload_serializer.validated_data.get("agenda", ""),
            quorum_percentage=payload_serializer.validated_data.get(
                "quorum_percentage", 50
            ),
            created_by=request.user,
            updated_by=request.user,
        )
        reminders_count = schedule_meeting_reminders(meeting, request.user)

        response_payload = MeetingSerializer(meeting).data
        response_payload["reminders_scheduled"] = reminders_count
        return Response(response_payload, status=status.HTTP_201_CREATED)


class MeetingMinutesArchiveView(MeetingsBaseView):

    def get(self, request):
        scoped_chama_id = _resolve_chama_scope(
            request,
            request.query_params.get("chama_id"),
        )
        _require_member(request.user, scoped_chama_id)
        queryset = (
            Meeting.objects.filter(
                chama_id=scoped_chama_id,
                minutes_status=MinutesStatus.APPROVED,
            )
            .exclude(minutes_text="", minutes_file__isnull=True)
            .order_by("-date")
        )
        return Response(
            MeetingSerializer(queryset, many=True).data, status=status.HTTP_200_OK
        )


class MeetingDetailView(MeetingsBaseView):

    def get(self, request, id):
        meeting = get_object_or_404(Meeting.objects.select_related("chama"), id=id)
        _require_member(request.user, str(meeting.chama_id))
        return Response(MeetingSerializer(meeting).data)


class MeetingAttendanceMarkView(MeetingsBaseView):

    def post(self, request, id):
        meeting = get_object_or_404(Meeting.objects.select_related("chama"), id=id)
        _require_secretary_or_admin(request.user, str(meeting.chama_id))

        serializer = BulkAttendanceMarkSerializer(
            data=request.data,
            context={"meeting": meeting},
        )
        serializer.is_valid(raise_exception=True)

        attendance_rows = []
        with transaction.atomic():
            for row in serializer.validated_data["records"]:
                attendance, created = Attendance.objects.update_or_create(
                    meeting=meeting,
                    member_id=row["member_id"],
                    defaults={
                        "status": row["status"],
                        "notes": row.get("notes", ""),
                        "updated_by": request.user,
                    },
                )
                if created:
                    attendance.created_by = request.user
                    attendance.save(update_fields=["created_by"])
                attendance_rows.append(attendance)

        return Response(
            {
                "meeting_id": str(meeting.id),
                "records": AttendanceSerializer(attendance_rows, many=True).data,
            },
            status=status.HTTP_200_OK,
        )


class MeetingAttendanceScanView(MeetingsBaseView):

    def post(self, request, id):
        meeting = get_object_or_404(Meeting.objects.select_related("chama"), id=id)
        _require_member(request.user, str(meeting.chama_id))

        serializer = AttendanceScanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        payload = str(serializer.validated_data["qr_payload"]).strip()
        parts = payload.split("|")
        if len(parts) < 6:
            raise ValidationError({"qr_payload": "Invalid attendance QR payload."})
        if parts[0] != "CARD" or parts[2] != "USER" or parts[4] != "CHAMA":
            raise ValidationError({"qr_payload": "Invalid attendance QR format."})

        qr_token = parts[1]
        user_id = parts[3]
        chama_id = parts[5]
        if str(meeting.chama_id) != str(chama_id):
            raise ValidationError({"qr_payload": "QR payload chama mismatch."})

        member_card = MemberCard.objects.filter(
            qr_token=qr_token,
            user_id=user_id,
            chama_id=chama_id,
            is_active=True,
        ).first()
        if not member_card:
            raise ValidationError({"qr_payload": "Member card token is invalid."})

        member_membership = Membership.objects.filter(
            chama_id=chama_id,
            user_id=user_id,
            is_active=True,
            is_approved=True,
            exited_at__isnull=True,
        ).first()
        if not member_membership:
            raise ValidationError(
                {"qr_payload": "User is not an active approved member for attendance."}
            )

        attendance, created = Attendance.objects.update_or_create(
            meeting=meeting,
            member_id=user_id,
            defaults={
                "status": serializer.validated_data.get("status"),
                "notes": serializer.validated_data.get("notes", ""),
                "updated_by": request.user,
            },
        )
        if created:
            attendance.created_by = request.user
            attendance.save(update_fields=["created_by"])
        return Response(
            AttendanceSerializer(attendance).data, status=status.HTTP_200_OK
        )


class MeetingMinutesUploadView(MeetingsBaseView):
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request, id):
        meeting = get_object_or_404(Meeting.objects.select_related("chama"), id=id)
        _require_secretary_or_admin(request.user, str(meeting.chama_id))

        serializer = MeetingMinutesUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if "minutes_text" in serializer.validated_data:
            meeting.minutes_text = serializer.validated_data["minutes_text"]
        if "minutes_file" in serializer.validated_data:
            meeting.minutes_file = serializer.validated_data["minutes_file"]

        meeting.minutes_status = MinutesStatus.PENDING_APPROVAL
        meeting.minutes_approved_by = None
        meeting.minutes_approved_at = None
        meeting.updated_by = request.user
        meeting.save()

        try:
            from apps.meetings.tasks import meetings_ai_summarize_on_minutes_upload

            meetings_ai_summarize_on_minutes_upload.delay(str(meeting.id))
        except Exception:  # noqa: BLE001
            # Minutes upload should not fail due to async summarization dispatch.
            pass

        return Response(MeetingSerializer(meeting).data, status=status.HTTP_200_OK)


class MeetingSummaryView(MeetingsBaseView):

    def get(self, request, id):
        meeting = get_object_or_404(Meeting.objects.select_related("chama"), id=id)
        _require_member(request.user, str(meeting.chama_id))
        return Response(build_meeting_summary(meeting))


class MeetingAgendaItemsView(MeetingsBaseView):

    def get(self, request, id):
        meeting = get_object_or_404(Meeting.objects.select_related("chama"), id=id)
        _require_member(request.user, str(meeting.chama_id))
        queryset = meeting.agenda_items.select_related(
            "proposed_by", "approved_by"
        ).order_by("created_at")
        return Response(AgendaItemSerializer(queryset, many=True).data)

    def post(self, request, id):
        meeting = get_object_or_404(Meeting.objects.select_related("chama"), id=id)
        _require_member(request.user, str(meeting.chama_id))
        serializer = AgendaItemCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = AgendaItem.objects.create(
            meeting=meeting,
            proposed_by=request.user,
            title=serializer.validated_data["title"],
            description=serializer.validated_data.get("description", ""),
            status=AgendaItemStatus.PROPOSED,
            created_by=request.user,
            updated_by=request.user,
        )
        return Response(AgendaItemSerializer(item).data, status=status.HTTP_201_CREATED)


class MeetingAgendaItemStatusView(MeetingsBaseView):

    def post(self, request, id, agenda_id):
        meeting = get_object_or_404(Meeting.objects.select_related("chama"), id=id)
        _require_secretary_or_admin(request.user, str(meeting.chama_id))
        agenda_item = get_object_or_404(AgendaItem, id=agenda_id, meeting=meeting)

        serializer = AgendaItemStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        agenda_item.status = serializer.validated_data["status"]
        if agenda_item.status == AgendaItemStatus.APPROVED:
            agenda_item.approved_by = request.user
            agenda_item.approved_at = timezone.now()
        agenda_item.updated_by = request.user
        agenda_item.save(
            update_fields=[
                "status",
                "approved_by",
                "approved_at",
                "updated_by",
                "updated_at",
            ]
        )
        return Response(AgendaItemSerializer(agenda_item).data)


class MeetingVoteView(MeetingsBaseView):

    def post(self, request, id):
        meeting = get_object_or_404(Meeting.objects.select_related("chama"), id=id)
        _require_member(request.user, str(meeting.chama_id))
        serializer = MeetingVoteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        agenda_item = None
        agenda_item_id = serializer.validated_data.get("agenda_item_id")
        if agenda_item_id:
            agenda_item = get_object_or_404(
                AgendaItem, id=agenda_item_id, meeting=meeting
            )
            if agenda_item.status not in {
                AgendaItemStatus.APPROVED,
                AgendaItemStatus.DONE,
            }:
                raise ValidationError(
                    {
                        "agenda_item_id": "Voting is only allowed on approved agenda items."
                    }
                )

        vote, _created = MeetingVote.objects.update_or_create(
            meeting=meeting,
            agenda_item=agenda_item,
            voter=request.user,
            defaults={
                "choice": serializer.validated_data["choice"],
                "note": serializer.validated_data.get("note", ""),
                "updated_by": request.user,
                "created_by": request.user,
            },
        )
        return Response(MeetingVoteSerializer(vote).data, status=status.HTTP_200_OK)


class MeetingVoteSummaryView(MeetingsBaseView):

    def get(self, request, id):
        meeting = get_object_or_404(Meeting.objects.select_related("chama"), id=id)
        _require_member(request.user, str(meeting.chama_id))
        agenda_item_id = request.query_params.get("agenda_item_id")

        votes = MeetingVote.objects.filter(meeting=meeting)
        if agenda_item_id:
            votes = votes.filter(agenda_item_id=agenda_item_id)
        total_votes = votes.count()
        yes_count = votes.filter(choice=VoteChoice.YES).count()
        no_count = votes.filter(choice=VoteChoice.NO).count()
        abstain_count = votes.filter(choice=VoteChoice.ABSTAIN).count()

        eligible_members = Membership.objects.filter(
            chama=meeting.chama,
            is_active=True,
            is_approved=True,
            exited_at__isnull=True,
        ).count()
        required_votes = quorum_required(
            total_members=eligible_members,
            quorum_percentage=meeting.quorum_percentage,
            minimum_votes=1,
        )
        quorum_reached = total_votes >= required_votes

        return Response(
            {
                "meeting_id": str(meeting.id),
                "agenda_item_id": str(agenda_item_id) if agenda_item_id else None,
                "votes": {
                    "yes": yes_count,
                    "no": no_count,
                    "abstain": abstain_count,
                    "total": total_votes,
                },
                "quorum": {
                    "eligible_members": eligible_members,
                    "required_votes": required_votes,
                    "reached": quorum_reached,
                },
            }
        )


class MeetingMinutesApprovalView(MeetingsBaseView):

    def post(self, request, id):
        meeting = get_object_or_404(Meeting.objects.select_related("chama"), id=id)
        _require_secretary_or_admin(request.user, str(meeting.chama_id))

        serializer = MinutesApprovalActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        decision = serializer.validated_data["decision"]

        approval = MinutesApproval.objects.create(
            meeting=meeting,
            reviewer=request.user,
            decision=decision,
            note=serializer.validated_data.get("note", ""),
            created_by=request.user,
            updated_by=request.user,
        )

        meeting.minutes_status = decision
        if decision == MinutesStatus.APPROVED:
            meeting.minutes_approved_by = request.user
            meeting.minutes_approved_at = timezone.now()
        else:
            meeting.minutes_approved_by = None
            meeting.minutes_approved_at = None
        meeting.updated_by = request.user
        meeting.save(
            update_fields=[
                "minutes_status",
                "minutes_approved_by",
                "minutes_approved_at",
                "updated_by",
                "updated_at",
            ]
        )

        return Response(
            MinutesApprovalSerializer(approval).data, status=status.HTTP_201_CREATED
        )


class ActionItemsDashboardView(MeetingsBaseView):

    def get(self, request):
        query_serializer = ActionItemsDashboardQuerySerializer(
            data=request.query_params
        )
        query_serializer.is_valid(raise_exception=True)

        scoped_chama_id = _resolve_chama_scope(
            request,
            query_serializer.validated_data.get("chama_id"),
        )
        _require_member(request.user, scoped_chama_id)

        dashboard_data = build_action_items_dashboard(
            scoped_chama_id,
            query_serializer.validated_data.get("status"),
        )
        items = ResolutionSerializer(dashboard_data["queryset"], many=True).data

        return Response(
            {
                "chama_id": scoped_chama_id,
                "open_count": dashboard_data["open_count"],
                "done_count": dashboard_data["done_count"],
                "overdue_count": dashboard_data["overdue_count"],
                "items": items,
            }
        )
