"""
Calendar API - Aggregates events from all modules for calendar view.
"""
import logging
from datetime import timedelta

from django.utils import timezone
from rest_framework import permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.chama.models import Membership, MemberStatus
from apps.finance.models import ContributionGoal, Loan, LoanStatus
from apps.meetings.models import Meeting, Resolution
from apps.notifications.models import Notification

logger = logging.getLogger(__name__)


class CalendarEventType:
    """Calendar event types."""
    MEETING = "MEETING"
    LOAN_DUE = "LOAN_DUE"
    GOAL_DUE = "GOAL_DUE"
    REMINDER = "REMINDER"
    ACTION_ITEM = "ACTION_ITEM"


# Role-based visibility mapping
ROLE_EVENT_VISIBILITY = {
    # Members see their own loans, goals, reminders, and all meetings
    "MEMBER": [CalendarEventType.MEETING, CalendarEventType.LOAN_DUE, CalendarEventType.GOAL_DUE, CalendarEventType.REMINDER, CalendarEventType.ACTION_ITEM],
    # Treasurers see all financial events plus meetings
    "TREASURER": [CalendarEventType.MEETING, CalendarEventType.LOAN_DUE, CalendarEventType.GOAL_DUE, CalendarEventType.REMINDER, CalendarEventType.ACTION_ITEM],
    # Secretaries see meetings, agenda deadlines, minutes deadlines, action items
    "SECRETARY": [CalendarEventType.MEETING, CalendarEventType.ACTION_ITEM, CalendarEventType.REMINDER],
    # Auditors see meetings and governance-related items
    "AUDITOR": [CalendarEventType.MEETING, CalendarEventType.REMINDER],
    # Admins see everything
    "ADMIN": [CalendarEventType.MEETING, CalendarEventType.LOAN_DUE, CalendarEventType.GOAL_DUE, CalendarEventType.REMINDER, CalendarEventType.ACTION_ITEM],
    # Chairs see all
    "CHAMA_ADMIN": [CalendarEventType.MEETING, CalendarEventType.LOAN_DUE, CalendarEventType.GOAL_DUE, CalendarEventType.REMINDER, CalendarEventType.ACTION_ITEM],
}

# Event colors for frontend
EVENT_COLORS = {
    CalendarEventType.MEETING: "#3B82F6",       # Blue
    CalendarEventType.LOAN_DUE: "#EF4444",     # Red
    CalendarEventType.GOAL_DUE: "#10B981",     # Green
    CalendarEventType.REMINDER: "#F59E0B",     # Amber
    CalendarEventType.ACTION_ITEM: "#8B5CF6",  # Purple
}


def get_user_chamas(user):
    """Get all active chama memberships for a user."""
    return Membership.objects.filter(
        user=user,
        status=MemberStatus.ACTIVE,
        exited_at__isnull=True,
    ).select_related("chama")


def get_user_role_in_chama(user, chama_id):
    """Get user's role in a specific chama."""
    try:
        membership = Membership.objects.get(
            user=user,
            chama_id=chama_id,
            status=MemberStatus.ACTIVE,
        )
        return membership.role
    except Membership.DoesNotExist:
        return None


def get_allowed_event_types(user) -> list[str]:
    """Get event types visible to user based on their role."""
    user_role = getattr(user, 'role', None)
    
    # Check global role first
    if user_role in ROLE_EVENT_VISIBILITY:
        return ROLE_EVENT_VISIBILITY[user_role]
    
    # Default to member visibility
    return ROLE_EVENT_VISIBILITY.get("MEMBER", [])


def get_meeting_events(user, chama_ids: list, from_date, to_date):
    """Get meeting events."""
    meetings = Meeting.objects.filter(
        chama_id__in=chama_ids,
        date__gte=from_date,
        date__lte=to_date,
    ).order_by("date")

    events = []
    for meeting in meetings:
        # Determine end time (default 2 hours)
        end_time = meeting.date + timedelta(hours=2) if meeting.date else None
        
        events.append({
            "id": f"meeting_{meeting.id}",
            "type": CalendarEventType.MEETING,
            "title": meeting.title or "Meeting",
            "start": meeting.date.isoformat() if meeting.date else None,
            "end": end_time.isoformat() if end_time else None,
            "allDay": False,
            "chama_id": str(meeting.chama_id),
            "deep_link": f"/member/meetings/{meeting.id}",
            "color": EVENT_COLORS[CalendarEventType.MEETING],
            "meta": {
                "venue": getattr(meeting, 'venue', '') or '',
                "status": meeting.minutes_status,
                "agenda_deadline": None,  # Could be calculated
                "minutes_deadline": None,
            }
        })
    
    return events


def get_loan_due_events(user, chama_ids: list, from_date, to_date):
    """Get loan repayment due events."""
    loans = Loan.objects.filter(
        chama_id__in=chama_ids,
        status__in=[LoanStatus.ACTIVE, LoanStatus.DISBURSED],
    ).select_related("chama")

    events = []
    for loan in loans:
        # Get upcoming installments
        from apps.finance.models import InstallmentSchedule, InstallmentStatus
        
        installments = InstallmentSchedule.objects.filter(
            loan=loan,
            due_date__gte=from_date,
            due_date__lte=to_date,
            status__in=[InstallmentStatus.DUE, InstallmentStatus.OVERDUE],
        ).order_by("due_date")

        for installment in installments:
            is_overdue = installment.status == InstallmentStatus.OVERDUE
            events.append({
                "id": f"loan_due_{loan.id}_{installment.id}",
                "type": CalendarEventType.LOAN_DUE,
                "title": f"Loan payment due (KES {float(installment.expected_amount):,.0f})" + (" - OVERDUE" if is_overdue else ""),
                "start": f"{installment.due_date.isoformat()}T00:00:00+03:00",
                "end": f"{installment.due_date.isoformat()}T23:59:59+03:00",
                "allDay": True,
                "chama_id": str(loan.chama_id),
                "deep_link": f"/member/loans/{loan.id}",
                "color": EVENT_COLORS[CalendarEventType.LOAN_DUE],
                "meta": {
                    "loan_id": str(loan.id),
                    "installment_id": str(installment.id),
                    "amount": str(installment.expected_amount),
                    "status": installment.status,
                    "is_overdue": is_overdue,
                }
            })
    
    return events


def get_goal_events(user, chama_ids: list, from_date, to_date):
    """Get contribution goal events."""
    goals = ContributionGoal.objects.filter(
        chama_id__in=chama_ids,
        due_date__gte=from_date,
        due_date__lte=to_date,
        is_active=True,
    ).select_related("chama", "member")

    events = []
    for goal in goals:
        events.append({
            "id": f"goal_{goal.id}",
            "type": CalendarEventType.GOAL_DUE,
            "title": f"Goal: {goal.title} (KES {float(goal.target_amount):,.0f})",
            "start": f"{goal.due_date.isoformat()}T00:00:00+03:00",
            "end": f"{goal.due_date.isoformat()}T23:59:59+03:00",
            "allDay": True,
            "chama_id": str(goal.chama_id),
            "deep_link": "/member/goals",
            "color": EVENT_COLORS[CalendarEventType.GOAL_DUE],
            "meta": {
                "goal_id": str(goal.id),
                "target_amount": str(goal.target_amount),
                "current_amount": str(goal.current_amount),
                "member": goal.member.full_name if hasattr(goal.member, 'full_name') else str(goal.member),
            }
        })
    
    return events


def get_action_item_events(user, chama_ids: list, from_date, to_date):
    """Get resolution action item events (resolutions with due dates)."""
    from apps.meetings.models import ResolutionStatus
    
    action_items = Resolution.objects.filter(
        chama_id__in=chama_ids,
        due_date__gte=from_date,
        due_date__lte=to_date,
        status=ResolutionStatus.OPEN,
    ).select_related("chama", "meeting", "assigned_to")

    events = []
    for item in action_items:
        events.append({
            "id": f"action_{item.id}",
            "type": CalendarEventType.ACTION_ITEM,
            "title": f"Action: {item.text[:50]}{'...' if len(item.text) > 50 else ''}",
            "start": f"{item.due_date.isoformat()}T00:00:00+03:00",
            "end": f"{item.due_date.isoformat()}T23:59:59+03:00",
            "allDay": True,
            "chama_id": str(item.chama_id),
            "deep_link": "/secretary/resolutions",
            "color": EVENT_COLORS[CalendarEventType.ACTION_ITEM],
            "meta": {
                "resolution_id": str(item.id),
                "meeting_id": str(item.meeting_id) if item.meeting_id else None,
                "assigned_to": item.assigned_to.full_name if item.assigned_to else None,
                "status": item.status,
            }
        })
    
    return events


def get_reminder_events(user, chama_ids: list, from_date, to_date):
    """Get scheduled notification reminders."""
    notifications = Notification.objects.filter(
        recipient=user,
        scheduled_at__gte=from_date,
        scheduled_at__lte=to_date,
    ).order_by("scheduled_at")

    events = []
    for notif in notifications:
        events.append({
            "id": f"reminder_{notif.id}",
            "type": CalendarEventType.REMINDER,
            "title": notif.subject or "Reminder",
            "start": f"{notif.scheduled_at.strftime('%Y-%m-%dT%H:%M:%S')}+03:00",
            "allDay": False,
            "chama_id": str(notif.chama_id) if notif.chama_id else None,
            "deep_link": notif.action_url or "/notifications",
            "color": EVENT_COLORS[CalendarEventType.REMINDER],
            "meta": {
                "notification_id": str(notif.id),
            }
        })
    
    return events


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def calendar_events(request):
    """
    Get all calendar events for the authenticated user.
    
    Query Parameters:
    - from: Start date (YYYY-MM-DD)
    - to: End date (YYYY-MM-DD)
    - type: Filter by event type (MEETING, LOAN_DUE, GOAL_DUE, REMINDER, ACTION_ITEM)
    - chama_id: Filter by chama
    """
    user = request.user
    
    # Parse date parameters
    from_date_str = request.query_params.get("from")
    to_date_str = request.query_params.get("to")
    event_type = request.query_params.get("type")
    chama_id_filter = request.query_params.get("chama_id")
    
    # Default to current month
    today = timezone.now().date()
    if from_date_str:
        from_date = timezone.datetime.strptime(from_date_str, "%Y-%m-%d").date()
    else:
        from_date = today.replace(day=1)  # First of month
    
    if to_date_str:
        to_date = timezone.datetime.strptime(to_date_str, "%Y-%m-%d").date()
    else:
        # Default to 3 months
        to_date = from_date + timedelta(days=90)
    
    # Get user's chamas
    memberships = get_user_chamas(user)
    chama_ids = [m.chama_id for m in memberships]
    
    if not chama_ids:
        return Response({"count": 0, "results": []})
    
    # Filter by specific chama if provided
    if chama_id_filter:
        if chama_id_filter not in [str(cid) for cid in chama_ids]:
            return Response({"error": "Not a member of this chama"}, status=403)
        chama_ids = [chama_id_filter]
    
    # Get allowed event types based on user's role
    allowed_types = get_allowed_event_types(user)
    
    all_events = []
    
    # Get events based on allowed types and filters
    if (not event_type or event_type == CalendarEventType.MEETING) and CalendarEventType.MEETING in allowed_types:
        all_events.extend(get_meeting_events(user, chama_ids, from_date, to_date))
    
    if (not event_type or event_type == CalendarEventType.LOAN_DUE) and CalendarEventType.LOAN_DUE in allowed_types:
        all_events.extend(get_loan_due_events(user, chama_ids, from_date, to_date))
    
    if (not event_type or event_type == CalendarEventType.GOAL_DUE) and CalendarEventType.GOAL_DUE in allowed_types:
        all_events.extend(get_goal_events(user, chama_ids, from_date, to_date))
    
    if (not event_type or event_type == CalendarEventType.ACTION_ITEM) and CalendarEventType.ACTION_ITEM in allowed_types:
        all_events.extend(get_action_item_events(user, chama_ids, from_date, to_date))
    
    if (not event_type or event_type == CalendarEventType.REMINDER) and CalendarEventType.REMINDER in allowed_types:
        all_events.extend(get_reminder_events(user, chama_ids, from_date, to_date))
    
    # Sort by start date
    all_events.sort(key=lambda x: x.get("start", ""))
    
    return Response({
        "count": len(all_events),
        "results": all_events,
        "allowed_types": allowed_types,
    })


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def calendar_ics(request):
    """
    Export calendar events as ICS file.
    """
    request.query_params.get("from")
    request.query_params.get("to")
    
    # Get events
    response = calendar_events(request)
    events = response.data.get("results", [])
    
    # Build ICS content
    ics_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Digital Chama//Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    
    for event in events:
        ics_lines.append("BEGIN:VEVENT")
        ics_lines.append(f"UID:{event['id']}@digitalchama.co.ke")
        ics_lines.append(f"DTSTART:{event['start'].replace('+03:00', '').replace(':', '')}")
        if event.get("end"):
            ics_lines.append(f"DTEND:{event['end'].replace('+03:00', '').replace(':', '')}")
        ics_lines.append(f"SUMMARY:{event['title']}")
        if event.get("deep_link"):
            ics_lines.append(f"URL:{event['deep_link']}")
        ics_lines.append("END:VEVENT")
    
    ics_lines.append("END:VCALENDAR")
    
    from django.http import HttpResponse
    ics_content = "\r\n".join(ics_lines)
    
    http_response = HttpResponse(ics_content, content_type="text/calendar")
    http_response["Content-Disposition"] = "attachment; filename=chama_calendar.ics"
    
    return http_response
