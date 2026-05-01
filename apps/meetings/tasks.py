from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from apps.ai.tasks import ai_meeting_summarize_task
from apps.chama.models import (
    ChamaMeetingSetting,
    MeetingFrequency,
    Membership,
    MembershipRole,
    MemberStatus,
)
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
        if lead_delta <= timedelta(hours=1):
            memberships = memberships.filter(
                user_id__in=meeting.attendance.filter(
                    status__in=[AttendanceStatus.PRESENT, AttendanceStatus.LATE]
                ).values_list("member_id", flat=True)
            )
        for membership in memberships:
            # If RSVP/attendance has already been marked as present/excused, skip reminders.
            if lead_delta > timedelta(hours=1) and meeting.attendance.filter(
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


def _meeting_delta(frequency: str) -> timedelta:
    mapping = {
        MeetingFrequency.WEEKLY: timedelta(days=7),
        MeetingFrequency.BIWEEKLY: timedelta(days=14),
        MeetingFrequency.MONTHLY: timedelta(days=30),
        MeetingFrequency.QUARTERLY: timedelta(days=90),
    }
    return mapping.get(frequency, timedelta(days=30))


@shared_task
def meetings_reminder_7d():
    from apps.automations.services import AutomationJobRunner

    return AutomationJobRunner.run_job(
        name="meetings_reminder_7d",
        schedule="0 * * * *",
        description="Hourly scan for meetings happening in 7 days.",
        callback=lambda: _send_meeting_reminders(
            lead_delta=timedelta(days=7),
            idempotency_prefix="meeting-7d",
            human_label="1 week to go",
        ),
    )


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
def meetings_reminder_1h():
    from apps.automations.services import AutomationJobRunner

    return AutomationJobRunner.run_job(
        name="meetings_reminder_1h",
        schedule="*/15 * * * *",
        description="15-minute scan for meetings happening in 1 hour.",
        callback=lambda: _send_meeting_reminders(
            lead_delta=timedelta(hours=1),
            idempotency_prefix="meeting-1h",
            human_label="1 hour to go",
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
def meetings_rsvp_nudge_48h():
    from apps.automations.services import AutomationJobRunner

    def callback():
        now = timezone.now()
        target = now + timedelta(hours=48)
        window = timedelta(minutes=45)
        nudged = 0

        meetings = Meeting.objects.select_related("chama").filter(
            date__gte=target - window,
            date__lte=target + window,
            chama__status="active",
            cancelled_at__isnull=True,
        )
        for meeting in meetings:
            pending_memberships = meeting.chama.memberships.select_related("user").filter(
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
                exited_at__isnull=True,
            ).exclude(
                user_id__in=meeting.attendance.values_list("member_id", flat=True)
            )
            for membership in pending_memberships:
                NotificationService.send_notification(
                    user=membership.user,
                    chama=meeting.chama,
                    channels=["in_app", "push"],
                    message=(
                        f"Please respond for the upcoming meeting '{meeting.title}' on "
                        f"{timezone.localtime(meeting.date).strftime('%Y-%m-%d %H:%M')}."
                    ),
                    subject="Meeting RSVP reminder",
                    notification_type=NotificationType.MEETING_NOTIFICATION,
                    idempotency_key=f"meeting-rsvp:{meeting.id}:{membership.user_id}",
                    context_data={
                        "meeting_id": str(meeting.id),
                        "chama_id": str(meeting.chama_id),
                    },
                )
                nudged += 1
        return {"meetings": meetings.count(), "nudged": nudged}

    return AutomationJobRunner.run_job(
        name="meetings_rsvp_nudge_48h",
        schedule="*/30 * * * *",
        description="Nudges members who have not responded 48 hours before meetings.",
        callback=callback,
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


@shared_task
def meetings_auto_schedule_next():
    from apps.automations.services import AutomationJobRunner

    def callback():
        now = timezone.now()
        created = 0
        past_meetings = Meeting.objects.select_related("chama").filter(
            date__lt=now - timedelta(hours=1),
            cancelled_at__isnull=True,
            chama__status="active",
        )
        for meeting in past_meetings:
            meeting_setting = ChamaMeetingSetting.objects.filter(chama=meeting.chama).first()
            next_date = meeting.date + _meeting_delta(
                meeting_setting.meeting_frequency if meeting_setting else MeetingFrequency.MONTHLY
            )
            if Meeting.objects.filter(
                chama=meeting.chama,
                cancelled_at__isnull=True,
                date__gte=next_date - timedelta(days=1),
                date__lte=next_date + timedelta(days=1),
            ).exists():
                continue
            Meeting.objects.create(
                chama=meeting.chama,
                title=meeting.title,
                description=meeting.description,
                location=meeting.location,
                location_type=meeting.location_type,
                meeting_link=meeting.meeting_link,
                date=next_date,
                agenda=meeting.agenda,
                quorum_percentage=meeting.quorum_percentage,
                created_by=meeting.created_by,
                updated_by=meeting.updated_by or meeting.created_by,
            )
            created += 1
        return {"created": created}

    return AutomationJobRunner.run_job(
        name="meetings_auto_schedule_next",
        schedule="0 5 * * *",
        description="Seeds the next meeting from chama meeting frequency after meetings conclude.",
        callback=callback,
    )
