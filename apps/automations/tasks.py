from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.accounts.tasks import (
    expire_pending_membership_requests as expire_pending_membership_requests_core,
)
from apps.accounts.tasks import (
    send_pending_approval_reminders as send_pending_approval_reminders_core,
)
from apps.automations.services import AutomationJobRunner
from apps.chama.models import Membership, MembershipRole, MemberStatus
from apps.notifications.tasks import (
    daily_due_reminders,
    meeting_reminders,
    process_scheduled_notifications,
    retry_failed_notifications,
)
from apps.security.tasks import (
    security_suspicious_activity_scan as security_suspicious_activity_scan_core,
)
from apps.security.tasks import (
    security_unlock_expired_locks as security_unlock_expired_locks_core,
)

logger = logging.getLogger(__name__)


@shared_task
def security_lockout_cleanup():
    def callback():
        unlocked = security_unlock_expired_locks_core()
        return {
            "detail": "Expired account locks cleaned.",
            "result": unlocked,
        }

    return AutomationJobRunner.run_job(
        name="security_lockout_cleanup",
        schedule="hourly",
        description="Checks lockout footprint and ensures stale locks expire.",
        callback=callback,
    )


@shared_task
def security_suspicious_activity_scan():
    def callback():
        return security_suspicious_activity_scan_core()

    return AutomationJobRunner.run_job(
        name="security_suspicious_activity_scan",
        schedule="hourly",
        description="Scans for suspicious login patterns.",
        callback=callback,
    )


@shared_task
def security_new_device_alert_event(user_id: str, ip_address: str, user_agent: str):
    from django.contrib.auth import get_user_model

    from apps.automations.services import AutomationService
    from apps.notifications.models import NotificationType

    User = get_user_model()
    user = User.objects.filter(id=user_id, is_active=True).first()
    if not user:
        return {"status": "user_not_found", "user_id": user_id}

    memberships = (
        Membership.objects.select_related("chama")
        .filter(
            user=user,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        )
        .order_by("joined_at")
    )
    if not memberships.exists():
        return {"status": "no_active_membership", "user_id": user_id}

    channels = list(getattr(settings, "LOGIN_NEW_DEVICE_ALERT_CHANNELS", ["email"]))
    now = timezone.localtime(timezone.now())
    user_agent_short = (user_agent or "Unknown device").strip()[:120]
    notified = 0
    for membership in memberships:
        message = (
            "New device login detected on your account. "
            f"Time: {now.strftime('%Y-%m-%d %H:%M %Z')}. "
            f"IP: {ip_address or 'unknown'}. Device: {user_agent_short}."
        )
        try:
            result = AutomationService.send_notification_with_policy(
                user=user,
                chama=membership.chama,
                message=message,
                channels=channels,
                subject="New device sign-in alert",
                notification_type=NotificationType.SECURITY_ALERT,
                idempotency_key=(
                    f"security:new-device:{user.id}:{membership.chama_id}:{now.date().isoformat()}:{ip_address or 'unknown'}"
                ),
            )
            notified += int(result.get("sent", 0))
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to send new device alert user=%s chama=%s",
                user.id,
                membership.chama_id,
            )

    return {
        "status": "processed",
        "user_id": user_id,
        "notified": notified,
        "memberships": memberships.count(),
    }


@shared_task
def clear_automation_locks():
    # Optional utility task for operational recovery.
    cache.clear()
    return {"status": "cleared"}


@shared_task
def notifications_daily_due_reminders_job():
    return AutomationJobRunner.run_job(
        name="notifications_daily_due_reminders",
        schedule="0 18 * * *",
        description="Daily due reminders for contributions and installments.",
        callback=daily_due_reminders,
    )


@shared_task
def notifications_meeting_reminders_job():
    return AutomationJobRunner.run_job(
        name="notifications_meeting_reminders",
        schedule="*/30 * * * *",
        description="Reminder scan for meetings in the next 24 hours.",
        callback=meeting_reminders,
    )


@shared_task
def notifications_process_scheduled_job():
    return AutomationJobRunner.run_job(
        name="notifications_process_scheduled",
        schedule="*/5 * * * *",
        description="Dispatches pending scheduled notifications.",
        callback=process_scheduled_notifications,
    )


@shared_task
def notifications_retry_failed_job():
    return AutomationJobRunner.run_job(
        name="notifications_retry_failed",
        schedule="*/10 * * * *",
        description="Retries failed notifications with retry windows due.",
        callback=retry_failed_notifications,
    )


@shared_task
def memberships_expire_pending_requests_job():
    return AutomationJobRunner.run_job(
        name="memberships_expire_pending_requests",
        schedule="0 1 * * *",
        description="Expires stale pending membership requests.",
        callback=expire_pending_membership_requests_core,
    )


@shared_task
def memberships_pending_approval_reminders_job():
    return AutomationJobRunner.run_job(
        name="memberships_pending_approval_reminders",
        schedule="0 9 * * *",
        description="Notifies reviewers about pending membership approvals.",
        callback=send_pending_approval_reminders_core,
    )


@shared_task
def security_failed_login_alerts_job():
    def callback():
        from apps.automations.services import AutomationService
        from apps.notifications.models import NotificationType

        scan = security_suspicious_activity_scan_core() or {}
        suspicious_ips = scan.get("suspicious_ips") or {}
        risk_flags = scan.get("risk_flags") or []

        if not suspicious_ips:
            return {
                "status": "no_alert",
                "reason": "no_failed_login_spike",
                "scan": scan,
            }

        message = (
            "Security alert: unusual failed login attempts detected. "
            f"IPs flagged: {len(suspicious_ips)}. "
            f"Risk flags: {', '.join(risk_flags) or 'failed_logins'}."
        )

        approvals = Membership.objects.select_related("user", "chama").filter(
            role__in=[
                MembershipRole.CHAMA_ADMIN,
                MembershipRole.ADMIN,
                
                MembershipRole.SECRETARY,
            ],
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        )

        alerts_sent = 0
        alert_targets = 0
        for membership in approvals:
            alert_targets += 1
            try:
                result = AutomationService.send_notification_with_policy(
                    user=membership.user,
                    chama=membership.chama,
                    message=message,
                    channels=["in_app", "email"],
                    subject="Security failed-login alert",
                    notification_type=NotificationType.SECURITY_ALERT,
                    idempotency_key=(
                        "security-failed-logins:"
                        f"{membership.chama_id}:{membership.user_id}:{timezone.localdate().isoformat()}"
                    ),
                )
                alerts_sent += int(result.get("sent", 0))
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to send failed-login alert user=%s chama=%s",
                    membership.user_id,
                    membership.chama_id,
                )

        return {
            "status": "alerted",
            "alerts_sent": alerts_sent,
            "targets": alert_targets,
            "suspicious_ips": suspicious_ips,
            "risk_flags": risk_flags,
        }

    return AutomationJobRunner.run_job(
        name="security_failed_login_alerts",
        schedule="*/30 * * * *",
        description="Sends alerts when failed-login spikes are detected.",
        callback=callback,
    )
