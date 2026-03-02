from datetime import timedelta

from django.conf import settings
from django.db.models import Count
from django.utils import timezone

from apps.meetings.models import AttendanceStatus, Resolution, ResolutionStatus
from apps.notifications.models import (
    Notification,
    NotificationPriority,
    NotificationType,
)
from apps.notifications.services import NotificationService


def schedule_meeting_reminders(meeting, actor) -> int:
    """
    Create per-member meeting reminder notifications.

    The notifications are scheduled before the meeting; if that timestamp is already
    in the past, they are queued for immediate processing.
    """
    lead_hours = int(getattr(settings, "MEETING_REMINDER_LEAD_HOURS", 24))
    scheduled_at = meeting.date - timedelta(hours=lead_hours)
    now = timezone.now()
    if scheduled_at < now:
        scheduled_at = now

    created_count = 0
    memberships = meeting.chama.memberships.select_related("user").filter(
        is_active=True,
        is_approved=True,
    )
    for membership in memberships:
        user = membership.user
        notification, created = Notification.objects.get_or_create(
            idempotency_key=f"meeting-reminder:{meeting.id}:{user.id}",
            defaults={
                "chama": meeting.chama,
                "recipient": user,
                "type": NotificationType.MEETING_NOTIFICATION,
                "priority": NotificationPriority.NORMAL,
                "subject": f"Meeting Reminder: {meeting.title}",
                "message": (
                    f"Reminder: '{meeting.title}' is scheduled for "
                    f"{timezone.localtime(meeting.date).strftime('%Y-%m-%d %H:%M')}."
                ),
                "send_email": bool(user.email),
                "send_sms": bool(user.phone),
                "email": user.email or "",
                "phone": user.phone or "",
                "scheduled_at": scheduled_at,
                "created_by": actor,
                "updated_by": actor,
            },
        )
        if not created:
            continue
        created_count += 1
        if scheduled_at <= now:
            NotificationService.queue_notification(notification)

    return created_count


def build_meeting_summary(meeting) -> dict:
    counts = {
        AttendanceStatus.PRESENT: 0,
        AttendanceStatus.ABSENT: 0,
        AttendanceStatus.LATE: 0,
        AttendanceStatus.EXCUSED: 0,
    }
    raw = meeting.attendance.values("status").annotate(total=Count("id"))
    for item in raw:
        status = item["status"]
        if status in counts:
            counts[status] = item["total"]

    total_members = meeting.chama.memberships.filter(
        is_active=True,
        is_approved=True,
    ).count()
    marked_count = sum(counts.values())
    attendance_rate = (
        round(
            (counts[AttendanceStatus.PRESENT] / total_members) * 100,
            2,
        )
        if total_members
        else 0.0
    )

    return {
        "meeting_id": str(meeting.id),
        "title": meeting.title,
        "date": meeting.date,
        "total_members": total_members,
        "attendance_marked": marked_count,
        "present_count": counts[AttendanceStatus.PRESENT],
        "absent_count": counts[AttendanceStatus.ABSENT],
        "late_count": counts[AttendanceStatus.LATE],
        "excused_count": counts[AttendanceStatus.EXCUSED],
        "attendance_rate": attendance_rate,
    }


def build_action_items_dashboard(chama_id, status_filter: str | None = None) -> dict:
    base_queryset = Resolution.objects.select_related("meeting", "assigned_to").filter(
        meeting__chama_id=chama_id
    )
    open_count = base_queryset.filter(status=ResolutionStatus.OPEN).count()
    done_count = base_queryset.filter(status=ResolutionStatus.DONE).count()
    overdue_count = base_queryset.filter(
        status=ResolutionStatus.OPEN,
        due_date__lt=timezone.localdate(),
    ).count()

    queryset = base_queryset
    if status_filter:
        queryset = queryset.filter(status=status_filter)

    return {
        "open_count": open_count,
        "done_count": done_count,
        "overdue_count": overdue_count,
        "queryset": queryset.order_by("due_date", "-created_at"),
    }
