from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView

from apps.chama.models import MemberStatus, Membership, MembershipRole
from apps.chama.services import get_effective_role
from apps.meetings.forms import MeetingForm
from apps.meetings.models import AgendaItem, Attendance, AttendanceStatus, Meeting

# Roles that are allowed to create / edit / delete meetings.
_MEETING_MANAGER_ROLES = {
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.ADMIN,
    
    MembershipRole.SECRETARY,
}


def _can_manage_meetings(user, membership) -> bool:
    """Return True if the user's effective role allows meeting management."""
    if not membership:
        return False
    effective_role = get_effective_role(user, membership.chama_id, membership)
    return effective_role in _MEETING_MANAGER_ROLES


@dataclass
class MeetingAttendeeView:
    user: object
    role: str
    attendance_status: str


class AttendeeCollection:
    def __init__(self, rows: list[MeetingAttendeeView]):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def __getitem__(self, item):
        return self._rows[item]

    def __len__(self):
        return len(self._rows)

    def count(self):
        return len(self._rows)

    def all(self):
        return self._rows


def _resolve_membership(request, *, chama_id=None):
    scoped_chama_id = (
        chama_id
        or request.GET.get("chama_id")
        or request.POST.get("chama_id")
        or request.session.get("active_chama_id")
    )
    memberships = Membership.objects.select_related("chama").filter(
        user=request.user,
        is_active=True,
        is_approved=True,
        status=MemberStatus.ACTIVE,
        exited_at__isnull=True,
    )

    membership = None
    if scoped_chama_id:
        membership = memberships.filter(chama_id=scoped_chama_id).first()

    if membership is None:
        membership = memberships.order_by("joined_at").first()

    if membership:
        request.session["active_chama_id"] = str(membership.chama_id)

    return membership


def _meeting_status(meeting: Meeting) -> str:
    now = timezone.now()
    start = meeting.date
    end = meeting.date + timedelta(hours=2)
    if now < start:
        return "upcoming"
    if start <= now <= end:
        return "ongoing"
    return "completed"


def _meeting_lead(meeting: Meeting):
    if meeting.created_by:
        return meeting.created_by

    lead_membership = (
        Membership.objects.select_related("user")
        .filter(
            chama=meeting.chama,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
            role__in=[MembershipRole.CHAMA_ADMIN, MembershipRole.SECRETARY],
        )
        .order_by("joined_at")
        .first()
    )
    if lead_membership:
        return lead_membership.user

    fallback = (
        Membership.objects.select_related("user")
        .filter(
            chama=meeting.chama,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        )
        .order_by("joined_at")
        .first()
    )
    return fallback.user if fallback else None


def _build_attendees(meeting: Meeting) -> AttendeeCollection:
    attendance_map = {
        str(row.member_id): row.status
        for row in meeting.attendance.select_related("member").all()
    }

    memberships = Membership.objects.select_related("user").filter(
        chama=meeting.chama,
        is_active=True,
        is_approved=True,
        status=MemberStatus.ACTIVE,
        exited_at__isnull=True,
    )

    rows: list[MeetingAttendeeView] = []
    for membership in memberships:
        rows.append(
            MeetingAttendeeView(
                user=membership.user,
                role=(
                    get_effective_role(
                        membership.user,
                        meeting.chama_id,
                        membership,
                    )
                    or membership.role
                    or "member"
                ).lower(),
                attendance_status=attendance_map.get(
                    str(membership.user_id),
                    AttendanceStatus.ABSENT,
                ),
            )
        )

    return AttendeeCollection(rows)


def _decorate_meeting(meeting: Meeting) -> Meeting:
    meeting.start_time = meeting.date
    meeting.end_time = meeting.date + timedelta(hours=2)
    meeting.status = _meeting_status(meeting)
    meeting.venue = meeting.chama.subcounty or meeting.chama.county or "Chama Hall"
    meeting.meeting_lead = _meeting_lead(meeting)
    meeting.minutes = meeting.minutes_text
    meeting.minutes_approved = meeting.minutes_status == "approved"
    meeting.minutes_approved_date = meeting.minutes_approved_at
    meeting.get_duration = "2 hours"
    meeting.get_meeting_type_display = lambda: "Regular Meeting"
    meeting.attendees = _build_attendees(meeting)
    return meeting


@method_decorator(login_required, name="dispatch")
class MeetingListView(TemplateView):
    template_name = "meetings/meeting_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["active_membership"] = membership
        context["title"] = "Chama Meetings"
        context["can_manage_meetings"] = _can_manage_meetings(self.request.user, membership)
        context["user_role"] = (
            get_effective_role(self.request.user, membership.chama_id, membership)
            if membership
            else "Member"
        )

        if not membership:
            context["meetings"] = []
            context["total_meetings"] = 0
            context["upcoming_meetings"] = 0
            context["completed_meetings"] = 0
            context["page_obj"] = Paginator([], 12).get_page(1)
            return context

        queryset = (
            Meeting.objects.select_related("chama", "created_by")
            .prefetch_related("attendance", "attendance__member", "agenda_items")
            .filter(chama=membership.chama)
            .order_by("-date")
        )

        query = (self.request.GET.get("q") or "").strip()
        if query:
            queryset = queryset.filter(Q(title__icontains=query) | Q(agenda__icontains=query))

        paginator = Paginator(queryset, 12)
        page = paginator.get_page(self.request.GET.get("page"))

        meetings = [_decorate_meeting(item) for item in page.object_list]
        now = timezone.now()

        context["meetings"] = meetings
        context["page_obj"] = page
        context["total_meetings"] = paginator.count
        context["upcoming_meetings"] = queryset.filter(date__gt=now).count()
        context["completed_meetings"] = queryset.filter(date__lt=now).count()
        return context


@method_decorator(login_required, name="dispatch")
class MeetingDetailView(TemplateView):
    template_name = "meetings/meeting_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        meeting = get_object_or_404(
            Meeting.objects.select_related("chama", "created_by").prefetch_related(
                "attendance",
                "attendance__member",
                "agenda_items",
            ),
            id=kwargs["meeting_id"],
        )
        membership = _resolve_membership(self.request, chama_id=meeting.chama_id)
        if not membership:
            context["meeting"] = meeting
            context["can_manage_meetings"] = False
            context["user_role"] = "Member"
            return context

        meeting = _decorate_meeting(meeting)
        context["meeting"] = meeting
        context["active_membership"] = membership
        context["can_manage_meetings"] = _can_manage_meetings(self.request.user, membership)
        context["user_role"] = get_effective_role(self.request.user, membership.chama_id, membership)
        context["title"] = f"Meeting: {meeting.title}"
        return context

    def dispatch(self, request, *args, **kwargs):
        meeting = get_object_or_404(Meeting, id=kwargs["meeting_id"])
        membership = _resolve_membership(request, chama_id=meeting.chama_id)
        if not membership:
            return HttpResponseForbidden("You are not allowed to access this meeting.")
        return super().dispatch(request, *args, **kwargs)


@method_decorator(login_required, name="dispatch")
class MeetingCreateView(TemplateView):
    template_name = "meetings/meeting_create.html"

    def dispatch(self, request, *args, **kwargs):
        membership = _resolve_membership(request)
        if not membership:
            messages.error(request, "You are not associated with an approved active chama.")
            return redirect("chama:chama_create")
        if not _can_manage_meetings(request.user, membership):
            return HttpResponseForbidden(
                "Only chama admin or secretary can schedule meetings."
            )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["form"] = kwargs.get("form") or MeetingForm()
        context["title"] = "Create New Meeting"
        context["today"] = timezone.localdate()
        context["active_membership"] = membership
        context["can_manage_meetings"] = True  # Only managers reach this view

        if membership:
            context["chama_members"] = Membership.objects.select_related("user").filter(
                chama=membership.chama,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
                exited_at__isnull=True,
            )
        else:
            context["chama_members"] = []
        return context

    def post(self, request, *args, **kwargs):
        membership = _resolve_membership(request)
        if not membership:
            messages.error(request, "You are not associated with an approved active chama.")
            return redirect("chama:chama_create")

        if not _can_manage_meetings(request.user, membership):
            return HttpResponseForbidden("Only chama admin or secretary can create meetings.")

        title = (request.POST.get("title") or "").strip()
        date_raw = (request.POST.get("date") or "").strip()
        start_time_raw = (request.POST.get("start_time") or "18:00").strip()
        end_time_raw = (request.POST.get("end_time") or "20:00").strip()
        description = (request.POST.get("description") or "").strip()

        if not title or not date_raw:
            messages.error(request, "Title and date are required.")
            return self.render_to_response(self.get_context_data())

        try:
            date_part = datetime.strptime(date_raw, "%Y-%m-%d").date()
            start_part = datetime.strptime(start_time_raw, "%H:%M").time()
            end_part = datetime.strptime(end_time_raw, "%H:%M").time()
            start_dt = timezone.make_aware(datetime.combine(date_part, start_part))
            end_dt = timezone.make_aware(datetime.combine(date_part, end_part))
        except ValueError:
            messages.error(request, "Provide valid date and time values.")
            return self.render_to_response(self.get_context_data())

        agenda_titles = [item.strip() for item in request.POST.getlist("agenda_titles") if item.strip()]
        agenda_descriptions = request.POST.getlist("agenda_descriptions")

        agenda_lines = [description] if description else []
        for index, title_line in enumerate(agenda_titles, start=1):
            detail = ""
            if index - 1 < len(agenda_descriptions):
                detail = (agenda_descriptions[index - 1] or "").strip()
            if detail:
                agenda_lines.append(f"{index}. {title_line} - {detail}")
            else:
                agenda_lines.append(f"{index}. {title_line}")
        agenda_text = "\n".join(line for line in agenda_lines if line)

        meeting = Meeting.objects.create(
            chama=membership.chama,
            title=title,
            date=start_dt,
            agenda=agenda_text,
            minutes_text=(
                f"Meeting scheduled from {start_dt.strftime('%H:%M')} to {end_dt.strftime('%H:%M')}."
            ),
            created_by=request.user,
            updated_by=request.user,
        )

        attendee_ids = request.POST.getlist("attendees")
        attendee_roles = request.POST.getlist("attendee_roles")
        role_note_map = {
            str(member_id): attendee_roles[index] if index < len(attendee_roles) else ""
            for index, member_id in enumerate(attendee_ids)
        }

        for member_id in attendee_ids:
            member_membership = Membership.objects.filter(
                chama=membership.chama,
                user_id=member_id,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
                exited_at__isnull=True,
            ).first()
            if not member_membership:
                continue

            Attendance.objects.get_or_create(
                meeting=meeting,
                member_id=member_id,
                defaults={
                    "status": AttendanceStatus.ABSENT,
                    "notes": role_note_map.get(str(member_id), ""),
                    "created_by": request.user,
                    "updated_by": request.user,
                },
            )

        for index, title_line in enumerate(agenda_titles):
            AgendaItem.objects.create(
                meeting=meeting,
                proposed_by=request.user,
                title=title_line,
                description=(agenda_descriptions[index] if index < len(agenda_descriptions) else "").strip(),
                status="approved",
                approved_by=request.user,
                approved_at=timezone.now(),
                created_by=request.user,
                updated_by=request.user,
            )

        messages.success(request, "Meeting created successfully.")
        return redirect("meetings:meeting_detail", meeting_id=meeting.id)


# Function-based views for backward compatibility
@login_required
def meeting_list_view(request):
    membership = _resolve_membership(request)
    if membership:
        effective_role = get_effective_role(request.user, membership.chama_id, membership)
        # Redirect secretaries to meeting creation page, unless they explicitly want to view all meetings
        if effective_role == MembershipRole.SECRETARY and request.GET.get('view') != 'all':
            return redirect('meetings:meeting_create')
        # Redirect chama admins to the most recent meeting's detail page, unless they want to view all
        elif effective_role == MembershipRole.CHAMA_ADMIN and request.GET.get('view') != 'all':
            # Find the most recent meeting (upcoming first, then completed)
            now = timezone.now()
            recent_meeting = (
                Meeting.objects.filter(chama=membership.chama)
                .order_by('-date')
                .first()
            )
            if recent_meeting:
                return redirect('meetings:meeting_detail', meeting_id=recent_meeting.id)
    return MeetingListView.as_view()(request)


@login_required
def meeting_detail_view(request, meeting_id):
    return MeetingDetailView.as_view()(request, meeting_id=meeting_id)


@login_required
def meeting_create_view(request):
    return MeetingCreateView.as_view()(request)
