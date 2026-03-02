from __future__ import annotations

import logging
from datetime import date, timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import Count, F
from django.utils import timezone

from apps.ai.services import AIWorkflowService
from apps.chama.models import Chama, ChamaStatus
from apps.chama.models import Membership, MembershipRole
from apps.finance.models import (
    Contribution,
    ContributionFrequency,
    ContributionType,
    InstallmentSchedule,
    InstallmentStatus,
    LoanStatus,
)
from apps.meetings.models import Meeting
from apps.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationEvent,
    NotificationPriority,
    NotificationStatus,
    NotificationTarget,
    NotificationType,
)
from apps.notifications.services import NotificationService
from apps.payments.models import PaymentReconciliationRun

logger = logging.getLogger(__name__)


def _period_bounds(frequency: str, on_date: date) -> tuple[date, date]:
    if frequency == ContributionFrequency.WEEKLY:
        start = on_date - timedelta(days=on_date.weekday())
        end = start + timedelta(days=6)
        return start, end

    if frequency == ContributionFrequency.MONTHLY:
        start = on_date.replace(day=1)
        if on_date.month == 12:
            end = date(on_date.year + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(on_date.year, on_date.month + 1, 1) - timedelta(days=1)
        return start, end

    if frequency == ContributionFrequency.QUARTERLY:
        quarter_start_month = ((on_date.month - 1) // 3) * 3 + 1
        start = date(on_date.year, quarter_start_month, 1)
        if quarter_start_month == 10:
            end = date(on_date.year + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(on_date.year, quarter_start_month + 3, 1) - timedelta(days=1)
        return start, end

    if frequency == ContributionFrequency.ANNUALLY:
        return date(on_date.year, 1, 1), date(on_date.year, 12, 31)

    # Default fallback.
    return on_date, on_date


@shared_task(bind=True, max_retries=5)
def process_notification(self, notification_id):
    try:
        NotificationService.process_notification(notification_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed processing notification %s", notification_id)
        countdown = min(60 * (2**self.request.retries), 3600)
        raise self.retry(countdown=countdown, exc=exc) from exc


@shared_task(bind=True, max_retries=5)
def send_sms_task(self, notification_id: str):
    notification = Notification.objects.filter(id=notification_id).first()
    if not notification:
        logger.warning("send_sms_task notification not found: %s", notification_id)
        return {"status": "not_found", "notification_id": notification_id}

    try:
        NotificationService.send_sms_for_notification(notification)
        return {"status": "sent", "notification_id": notification_id}
    except Exception as exc:  # noqa: BLE001
        logger.exception("send_sms_task failed for %s", notification_id)
        countdown = min(60 * (2**self.request.retries), 3600)
        raise self.retry(countdown=countdown, exc=exc) from exc


@shared_task(bind=True, max_retries=5)
def send_email_task(self, notification_id: str):
    notification = Notification.objects.filter(id=notification_id).first()
    if not notification:
        logger.warning("send_email_task notification not found: %s", notification_id)
        return {"status": "not_found", "notification_id": notification_id}

    try:
        NotificationService.send_email_for_notification(notification)
        return {"status": "sent", "notification_id": notification_id}
    except Exception as exc:  # noqa: BLE001
        logger.exception("send_email_task failed for %s", notification_id)
        countdown = min(60 * (2**self.request.retries), 3600)
        raise self.retry(countdown=countdown, exc=exc) from exc


@shared_task
def process_scheduled_notifications():
    pending_ids = Notification.objects.filter(
        status=NotificationStatus.PENDING,
        scheduled_at__isnull=False,
        scheduled_at__lte=timezone.now(),
    ).values_list("id", flat=True)

    for notification_id in pending_ids:
        process_notification.delay(str(notification_id))


@shared_task
def retry_failed_notifications():
    failed_ids = Notification.objects.filter(
        status=NotificationStatus.FAILED,
        next_retry_at__isnull=False,
        next_retry_at__lte=timezone.now(),
        retry_count__lt=F("max_retries"),
    ).values_list("id", flat=True)

    for notification_id in failed_ids:
        process_notification.delay(str(notification_id))


@shared_task
def daily_due_reminders():
    today = timezone.localdate()
    contribution_reminders = 0
    installment_reminders = 0
    installment_stage_counts = {
        "pre_due": 0,
        "due_today": 0,
        "overdue_escalation": 0,
    }

    active_contribution_types = ContributionType.objects.select_related("chama").filter(
        is_active=True,
        chama__status="active",
    )

    for contribution_type in active_contribution_types:
        period_start, period_end = _period_bounds(contribution_type.frequency, today)
        paid_member_ids = set(
            Contribution.objects.filter(
                chama=contribution_type.chama,
                contribution_type=contribution_type,
                date_paid__gte=period_start,
                date_paid__lte=period_end,
            ).values_list("member_id", flat=True)
        )

        due_memberships = Membership.objects.select_related("user", "chama").filter(
            chama=contribution_type.chama,
            is_active=True,
            is_approved=True,
        )
        if paid_member_ids:
            due_memberships = due_memberships.exclude(user_id__in=paid_member_ids)

        for membership in due_memberships:
            idempotency_key = f"due-contribution:{contribution_type.id}:{membership.user_id}:{today.isoformat()}"
            existed = NotificationEvent.objects.filter(event_key=idempotency_key).exists()
            NotificationService.publish_event(
                chama=membership.chama,
                event_key=idempotency_key,
                event_type=NotificationType.CONTRIBUTION_REMINDER,
                target=NotificationTarget.USER,
                target_user_ids=[str(membership.user_id)],
                channels=["sms", "email", "in_app"],
                subject="Contribution due reminder",
                message=(
                    f"Reminder: {contribution_type.name} contribution of KES "
                    f"{contribution_type.default_amount} is due."
                ),
                category=NotificationCategory.CONTRIBUTIONS,
                payload={
                    "contribution_type_id": str(contribution_type.id),
                    "amount": str(contribution_type.default_amount),
                    "member_id": str(membership.user_id),
                },
                enforce_once_daily=True,
            )
            if not existed:
                contribution_reminders += 1

    tracked_installments = InstallmentSchedule.objects.select_related(
        "loan",
        "loan__member",
        "loan__chama",
    ).filter(
        status__in=[InstallmentStatus.DUE, InstallmentStatus.OVERDUE],
        loan__status__in=[LoanStatus.APPROVED, LoanStatus.DISBURSED, LoanStatus.ACTIVE],
        loan__chama__status="active",
    )

    for installment in tracked_installments:
        days_to_due = (installment.due_date - today).days
        stage = None
        message = ""
        subject = ""
        if days_to_due == 3:
            stage = "pre_due"
            message = (
                f"Reminder: Loan installment of KES {installment.expected_amount} is "
                f"due on {installment.due_date:%Y-%m-%d}."
            )
            subject = "Loan installment due in 3 days"
        elif days_to_due == 0:
            stage = "due_today"
            message = (
                f"Reminder: Loan installment of KES {installment.expected_amount} is "
                f"due today ({installment.due_date:%Y-%m-%d})."
            )
            subject = "Loan installment due today"
        elif days_to_due <= -3:
            stage = "overdue_escalation"
            message = (
                f"Your loan installment of KES {installment.expected_amount} is overdue "
                f"since {installment.due_date:%Y-%m-%d}. Please settle urgently."
            )
            subject = "Loan installment overdue"
        else:
            continue

        idempotency_key = f"loan-reminder:{stage}:{installment.id}:{today.isoformat()}"
        existed = NotificationEvent.objects.filter(event_key=idempotency_key).exists()
        NotificationService.publish_event(
            chama=installment.loan.chama,
            event_key=idempotency_key,
            event_type=NotificationType.LOAN_UPDATE,
            target=NotificationTarget.USER,
            target_user_ids=[str(installment.loan.member_id)],
            channels=["sms", "email", "in_app"],
            subject=subject,
            message=message,
            category=NotificationCategory.LOANS,
            payload={
                "loan_id": str(installment.loan_id),
                "installment_id": str(installment.id),
                "stage": stage,
            },
            enforce_once_daily=True,
        )
        if not existed:
            installment_reminders += 1
            installment_stage_counts[stage] += 1

        if stage != "overdue_escalation":
            continue

        escalation_key = f"loan-escalation:{installment.id}:{today.isoformat()}"
        NotificationService.publish_event(
            chama=installment.loan.chama,
            event_key=escalation_key,
            event_type=NotificationType.LOAN_UPDATE,
            target=NotificationTarget.ROLE,
            target_roles=[
                MembershipRole.CHAMA_ADMIN,
                MembershipRole.TREASURER,
                MembershipRole.SECRETARY,
            ],
            channels=["sms", "email", "in_app"],
            subject="Overdue loan escalation",
            message=(
                "Overdue alert: "
                f"{installment.loan.member.full_name} has an installment of KES "
                f"{installment.expected_amount} overdue since "
                f"{installment.due_date:%Y-%m-%d}."
            ),
            category=NotificationCategory.LOANS,
            payload={
                "loan_id": str(installment.loan_id),
                "installment_id": str(installment.id),
                "borrower_name": installment.loan.member.full_name,
            },
            enforce_once_daily=True,
        )

    return {
        "run_date": today.isoformat(),
        "contribution_reminders": contribution_reminders,
        "loan_installment_reminders": installment_reminders,
        "loan_installment_stage_counts": installment_stage_counts,
    }


@shared_task
def meeting_reminders():
    now = timezone.now()
    target = now + timedelta(hours=24)
    window_minutes = int(getattr(settings, "MEETING_REMINDER_WINDOW_MINUTES", 30))
    window = timedelta(minutes=window_minutes)

    meetings = Meeting.objects.select_related("chama").filter(
        chama__status="active",
        date__gte=target - window,
        date__lte=target + window,
    )

    created = 0
    for meeting in meetings:
        idempotency_key = f"meeting-reminder:{meeting.id}"
        existed = NotificationEvent.objects.filter(event_key=idempotency_key).exists()
        event = NotificationService.publish_event(
            chama=meeting.chama,
            event_key=idempotency_key,
            event_type=NotificationType.MEETING_NOTIFICATION,
            target=NotificationTarget.CHAMA,
            channels=["sms", "email", "in_app"],
            subject=f"Meeting Reminder: {meeting.title}",
            message=(
                f"Reminder: meeting '{meeting.title}' starts at "
                f"{timezone.localtime(meeting.date).strftime('%Y-%m-%d %H:%M')}."
            ),
            category=NotificationCategory.MEETINGS,
            payload={
                "meeting_id": str(meeting.id),
                "meeting_title": meeting.title,
            },
            enforce_once_daily=True,
        )
        if not existed:
            created += event.notification_count

    return {
        "window_minutes": window_minutes,
        "meeting_reminders": created,
        "evaluated_at": now.isoformat(),
    }


@shared_task
def behavioral_notification_throttle():
    """
    Prevent notification fatigue by delaying non-urgent pending messages
    and issuing a compact digest notice.
    """
    now = timezone.now()
    threshold = int(getattr(settings, "NOTIFICATION_BEHAVIORAL_THRESHOLD", 20))
    window_start = now - timedelta(hours=24)
    delayed_total = 0
    digests = 0

    heavy_users = (
        Notification.objects.filter(created_at__gte=window_start)
        .values("recipient_id", "chama_id")
        .annotate(total=Count("id"))
        .filter(total__gt=threshold)
    )

    for entry in heavy_users:
        recipient_id = entry["recipient_id"]
        chama_id = entry["chama_id"]
        pending_non_urgent = Notification.objects.filter(
            recipient_id=recipient_id,
            chama_id=chama_id,
            status=NotificationStatus.PENDING,
            priority__in=[NotificationPriority.LOW, NotificationPriority.NORMAL],
        )
        if not pending_non_urgent.exists():
            continue

        next_digest_time = timezone.localtime(now).replace(
            hour=7,
            minute=30,
            second=0,
            microsecond=0,
        )
        if next_digest_time <= now:
            next_digest_time = next_digest_time + timedelta(days=1)

        delayed_total += pending_non_urgent.count()
        pending_non_urgent.update(scheduled_at=next_digest_time, updated_at=now)

        latest = pending_non_urgent.select_related("recipient", "chama").first()
        if not latest:
            continue
        NotificationService.send_notification(
            user=latest.recipient,
            chama=latest.chama,
            channels=["in_app"],
            message=(
                "You have multiple updates queued. A condensed digest "
                "will be delivered in your next notification window."
            ),
            subject="Notification digest scheduled",
            notification_type=NotificationType.SYSTEM,
            priority=NotificationPriority.NORMAL,
            idempotency_key=(
                f"notification-digest-scheduled:{chama_id}:{recipient_id}:{timezone.localdate().isoformat()}"
            ),
        )
        digests += 1

    return {
        "window_start": window_start.isoformat(),
        "threshold": threshold,
        "users_flagged": heavy_users.count(),
        "pending_delayed": delayed_total,
        "digests_sent": digests,
    }


@shared_task
def weekly_smart_digest():
    """
    Sends a weekly, AI-assisted chama summary to governance roles.
    """
    generated = 0
    delivered = 0
    week_key = timezone.localdate().strftime("%Y-W%W")

    for chama in Chama.objects.filter(status=ChamaStatus.ACTIVE):
        try:
            insights = AIWorkflowService.weekly_insights_for_chama(
                chama_id=chama.id,
                actor=None,
            )
            generated += 1
        except Exception:  # noqa: BLE001
            logger.exception("Failed generating weekly digest insights for chama=%s", chama.id)
            continue

        portfolio = insights.get("portfolio", {}) if isinstance(insights, dict) else {}
        overdue = insights.get("overdue", {}) if isinstance(insights, dict) else {}
        suspicious = insights.get("suspicious", {}) if isinstance(insights, dict) else {}
        message = (
            "Weekly chama digest: "
            f"Outstanding loans KES {portfolio.get('outstanding', '0.00')}, "
            f"overdue installments {overdue.get('count', 0)}, "
            f"suspicious signal groups {len(suspicious.get('odd_withdrawals', []))}."
        )

        recipients = Membership.objects.select_related("user").filter(
            chama=chama,
            is_active=True,
            is_approved=True,
            role__in=[
                MembershipRole.CHAMA_ADMIN,
                MembershipRole.TREASURER,
                MembershipRole.SECRETARY,
                MembershipRole.AUDITOR,
            ],
        )
        for membership in recipients:
            NotificationService.send_notification(
                user=membership.user,
                chama=chama,
                channels=["in_app", "email"],
                message=message,
                subject="Weekly smart digest",
                notification_type=NotificationType.SYSTEM,
                priority=NotificationPriority.NORMAL,
                idempotency_key=(
                    f"weekly-smart-digest:{chama.id}:{membership.user_id}:{week_key}"
                ),
            )
            delivered += 1

    return {
        "generated": generated,
        "delivered": delivered,
        "week_key": week_key,
    }


@shared_task
def reconciliation_summary_to_treasurer():
    """
    Sends daily reconciliation summary to treasurer/admin per chama.
    """
    today = timezone.localdate()
    sent = 0
    evaluated = 0

    for chama in Chama.objects.filter(status=ChamaStatus.ACTIVE):
        run = (
            PaymentReconciliationRun.objects.filter(
                chama=chama,
                created_at__date=today,
            )
            .order_by("-created_at")
            .first()
        )
        if not run:
            continue
        evaluated += 1
        totals = run.totals or {}
        anomalies = run.anomalies or {}
        mismatch_count = len(
            (anomalies.get("missing_ledger_for_success_intents") or [])
        ) + len((anomalies.get("ledger_without_payment_intent") or []))
        advanced = anomalies.get("advanced") or {}
        mismatch_count += len(advanced.get("missing_internally") or [])
        mismatch_count += len(advanced.get("missing_provider_side") or [])

        recipients = Membership.objects.select_related("user").filter(
            chama=chama,
            is_active=True,
            is_approved=True,
            role__in=[MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER],
        )
        for membership in recipients:
            NotificationService.send_notification(
                user=membership.user,
                chama=chama,
                channels=["in_app", "email"],
                message=(
                    "Daily reconciliation summary: "
                    f"status={run.status}, "
                    f"success_intents={totals.get('intents_success', 0)}, "
                    f"mismatch_items={mismatch_count}."
                ),
                subject="Daily reconciliation summary",
                notification_type=NotificationType.SYSTEM,
                priority=NotificationPriority.HIGH
                if mismatch_count
                else NotificationPriority.NORMAL,
                idempotency_key=(
                    f"daily-reconciliation-summary:{chama.id}:{membership.user_id}:{today.isoformat()}"
                ),
            )
            sent += 1

    return {"evaluated_chamas": evaluated, "sent": sent, "run_date": today.isoformat()}
