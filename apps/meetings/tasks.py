from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from apps.chama.models import MemberStatus, Membership, MembershipRole
from apps.ai.tasks import ai_meeting_summarize_task
from apps.meetings.models import AttendanceStatus, Meeting
from apps.notifications.models import NotificationType
from apps.notifications.services import NotificationService

logger = logging.getLogger(__name__)


def _send_meeting_reminders(
    *,
    lead_delta: timedelta,
    idempotency_prefix: str,
    human_label: str,
):
    now = timezone.now()
    target = now + lead_delta
    window = timedelta(minutes=45)

    meetings = Meeting.objects.select_related("chama").filter(
        date__gte=target - window,
        date__lte=target + window,
        chama__status="active",
    )

    created = 0
    skipped_rsvp = 0
    for meeting in meetings:
        memberships = meeting.chama.memberships.select_related("user").filter(
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
        )
        for membership in memberships:
            # If RSVP/attendance has already been marked as present/excused, skip reminders.
            if meeting.attendance.filter(
                member_id=membership.user_id,
                status__in=[AttendanceStatus.PRESENT, AttendanceStatus.EXCUSED],
            ).exists():
                skipped_rsvp += 1
                continue

            NotificationService.send_notification(
                user=membership.user,
                chama=meeting.chama,
                channels=["sms", "email"],
                message=(
                    f"Reminder ({human_label}): Meeting '{meeting.title}' starts at "
                    f"{timezone.localtime(meeting.date).strftime('%Y-%m-%d %H:%M')}"
                ),
                subject="Meeting reminder",
                notification_type=NotificationType.MEETING_NOTIFICATION,
                idempotency_key=(
                    f"{idempotency_prefix}:{meeting.id}:{membership.user_id}:{timezone.localdate().isoformat()}"
                ),
            )
            created += 1

    return {
        "meetings": meetings.count(),
        "notifications": created,
        "skipped_confirmed_rsvp": skipped_rsvp,
        "lead_minutes": int(lead_delta.total_seconds() / 60),
    }


@shared_task
def meetings_reminder_24h():
    from apps.automations.services import AutomationJobRunner

    return AutomationJobRunner.run_job(
        name="meetings_reminder_24h",
        schedule="0 * * * *",
        description="Hourly scan for meetings happening in 24 hours.",
        callback=lambda: _send_meeting_reminders(
            lead_delta=timedelta(hours=24),
            idempotency_prefix="meeting-24h",
            human_label="24h to go",
        ),
    )


@shared_task
def meetings_reminder_2h():
    from apps.automations.services import AutomationJobRunner

    return AutomationJobRunner.run_job(
        name="meetings_reminder_2h",
        schedule="15 * * * *",
        description="Hourly scan for meetings happening in 2 hours.",
        callback=lambda: _send_meeting_reminders(
            lead_delta=timedelta(hours=2),
            idempotency_prefix="meeting-2h",
            human_label="2h to go",
        ),
    )


@shared_task
def meetings_reminder_48h():
    from apps.automations.services import AutomationJobRunner

    return AutomationJobRunner.run_job(
        name="meetings_reminder_48h",
        schedule="10 * * * *",
        description="Hourly scan for meetings happening in 48 hours.",
        callback=lambda: _send_meeting_reminders(
            lead_delta=timedelta(hours=48),
            idempotency_prefix="meeting-48h",
            human_label="48h to go",
        ),
    )


@shared_task
def meetings_reminder_4h():
    from apps.automations.services import AutomationJobRunner

    return AutomationJobRunner.run_job(
        name="meetings_reminder_4h",
        schedule="20 * * * *",
        description="Hourly scan for meetings happening in 4 hours.",
        callback=lambda: _send_meeting_reminders(
            lead_delta=timedelta(hours=4),
            idempotency_prefix="meeting-4h",
            human_label="4h to go",
        ),
    )


@shared_task
def meetings_reminder_30m():
    from apps.automations.services import AutomationJobRunner

    return AutomationJobRunner.run_job(
        name="meetings_reminder_30m",
        schedule="*/15 * * * *",
        description="15-minute scan for meetings happening in 30 minutes.",
        callback=lambda: _send_meeting_reminders(
            lead_delta=timedelta(minutes=30),
            idempotency_prefix="meeting-30m",
            human_label="30 minutes to go",
        ),
    )


@shared_task
def meetings_minutes_compliance_monitor():
    from apps.automations.services import AutomationJobRunner

    def callback():
        now = timezone.now()
        secretary_delay_days = 2
        admin_delay_days = 4
        secretary_cutoff = now - timedelta(days=secretary_delay_days)
        admin_cutoff = now - timedelta(days=admin_delay_days)

        pending_minutes = Meeting.objects.select_related("chama").filter(
            date__lt=secretary_cutoff,
            chama__status="active",
            minutes_text="",
        ).filter(Q(minutes_file="") | Q(minutes_file__isnull=True))

        secretary_alerts = 0
        admin_alerts = 0
        for meeting in pending_minutes:
            secretaries = Membership.objects.select_related("user").filter(
                chama=meeting.chama,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
                role=MembershipRole.SECRETARY,
            )
            for membership in secretaries:
                NotificationService.send_notification(
                    user=membership.user,
                    chama=meeting.chama,
                    channels=["in_app", "email"],
                    message=(
                        f"Minutes pending for meeting '{meeting.title}' held on "
                        f"{timezone.localtime(meeting.date).strftime('%Y-%m-%d')}."
                    ),
                    subject="Minutes compliance reminder",
                    notification_type=NotificationType.MEETING_NOTIFICATION,
                    idempotency_key=(
                        f"meeting-minutes-secretary:{meeting.id}:{membership.user_id}:{timezone.localdate()}"
                    ),
                )
                secretary_alerts += 1

            if meeting.date >= admin_cutoff:
                continue

            admins = Membership.objects.select_related("user").filter(
                chama=meeting.chama,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
                role__in=[MembershipRole.CHAMA_ADMIN, MembershipRole.ADMIN],
            )
            for membership in admins:
                NotificationService.send_notification(
                    user=membership.user,
                    chama=meeting.chama,
                    channels=["in_app", "email"],
                    message=(
                        f"Escalation: meeting '{meeting.title}' still has no uploaded minutes."
                    ),
                    subject="Minutes compliance escalation",
                    notification_type=NotificationType.MEETING_NOTIFICATION,
                    idempotency_key=(
                        f"meeting-minutes-admin:{meeting.id}:{membership.user_id}:{timezone.localdate()}"
                    ),
                )
                admin_alerts += 1

        return {
            "pending_minutes_meetings": pending_minutes.count(),
            "secretary_alerts": secretary_alerts,
            "admin_alerts": admin_alerts,
            "run_at": timezone.now().isoformat(),
        }

    return AutomationJobRunner.run_job(
        name="meetings_minutes_compliance_monitor",
        schedule="45 7 * * *",
        description="Escalates meetings that do not have minutes uploaded within SLA.",
        callback=callback,
    )


@shared_task
def meetings_ai_summarize_on_minutes_upload(meeting_id: str):
    try:
        async_result = ai_meeting_summarize_task.delay(str(meeting_id))
        return {"queued": True, "task_id": async_result.id}
    except Exception:  # noqa: BLE001
        logger.exception("Failed to queue AI meeting summarize task for %s", meeting_id)
        return {"queued": False, "meeting_id": meeting_id}
