from __future__ import annotations

from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone

from apps.security.models import DeviceSession, LoginAttempt
from apps.security.services import SecurityService


@shared_task
def security_clear_old_login_attempts(days: int = 30):
    cutoff = timezone.now() - timedelta(days=max(1, int(days)))
    deleted, _ = LoginAttempt.objects.filter(created_at__lt=cutoff).delete()
    return {"deleted": deleted, "cutoff": cutoff.isoformat()}


@shared_task
def security_unlock_expired_locks():
    deleted = SecurityService.clear_expired_locks()
    return {"unlocked": deleted, "time": timezone.now().isoformat()}


@shared_task
def security_suspicious_activity_scan():
    window_minutes = max(
        15,
        int(getattr(settings, "SECURITY_ANOMALY_WINDOW_MINUTES", 60)),
    )
    one_hour_ago = timezone.now() - timedelta(minutes=window_minutes)
    login_ip_threshold = max(
        3,
        int(getattr(settings, "SECURITY_FAILED_LOGINS_IP_THRESHOLD", 5)),
    )
    stk_threshold = max(
        3,
        int(getattr(settings, "SECURITY_STK_FAILURE_THRESHOLD", 5)),
    )
    payout_threshold = max(
        2,
        int(getattr(settings, "SECURITY_RAPID_PAYOUT_THRESHOLD", 5)),
    )
    role_change_threshold = max(
        2,
        int(getattr(settings, "SECURITY_ROLE_CHANGE_THRESHOLD", 4)),
    )

    failed_last_hour = LoginAttempt.objects.filter(
        success=False,
        created_at__gte=one_hour_ago,
    ).count()

    failed_by_ip = {}
    for item in LoginAttempt.objects.filter(
        success=False, created_at__gte=one_hour_ago
    ):
        key = item.ip_address or "unknown"
        failed_by_ip[key] = failed_by_ip.get(key, 0) + 1

    suspicious_ips = {
        ip: count for ip, count in failed_by_ip.items() if count >= login_ip_threshold
    }

    stale_sessions = DeviceSession.objects.filter(
        is_revoked=False,
        last_seen__lt=timezone.now() - timedelta(days=30),
    ).count()

    from apps.payments.models import (  # noqa: PLC0415
        MpesaSTKTransaction,
        PaymentIntent,
        PaymentIntentStatus,
        PaymentIntentType,
    )
    from apps.security.models import AuditLog as SecurityAuditLog  # noqa: PLC0415
    from core.models import AuditLog as CoreAuditLog  # noqa: PLC0415

    payout_rows = (
        PaymentIntent.objects.filter(
            created_at__gte=one_hour_ago,
            intent_type__in=[
                PaymentIntentType.WITHDRAWAL,
                PaymentIntentType.LOAN_DISBURSEMENT,
            ],
        )
        .values("chama_id")
        .annotate(total=Count("id"))
    )
    rapid_payout_attempts = {
        str(row["chama_id"]): row["total"]
        for row in payout_rows
        if row["total"] >= payout_threshold
    }

    stk_rows = (
        MpesaSTKTransaction.objects.filter(
            created_at__gte=one_hour_ago,
            status__in=[
                PaymentIntentStatus.FAILED,
                PaymentIntentStatus.EXPIRED,
                PaymentIntentStatus.CANCELLED,
            ],
        )
        .values("chama_id")
        .annotate(total=Count("id"))
    )
    repeated_stk_failures = {
        str(row["chama_id"]): row["total"]
        for row in stk_rows
        if row["total"] >= stk_threshold
    }

    role_change_counts: dict[str, int] = {}
    security_role_rows = (
        SecurityAuditLog.objects.filter(created_at__gte=one_hour_ago)
        .filter(
            Q(action_type__icontains="ROLE")
            | Q(action_type__in=["CHANGE_ROLE", "ROLE_CHANGE", "DELEGATE_ROLE"])
        )
        .values("chama_id")
        .annotate(total=Count("id"))
    )
    for row in security_role_rows:
        key = str(row["chama_id"]) if row["chama_id"] else "none"
        role_change_counts[key] = role_change_counts.get(key, 0) + row["total"]

    core_role_rows = (
        CoreAuditLog.objects.filter(created_at__gte=one_hour_ago)
        .filter(
            Q(action__icontains="role")
            | Q(action__in=["change_role", "delegate_role", "revoke_role"])
        )
        .values("chama_id")
        .annotate(total=Count("id"))
    )
    for row in core_role_rows:
        key = str(row["chama_id"]) if row["chama_id"] else "none"
        role_change_counts[key] = role_change_counts.get(key, 0) + row["total"]

    rapid_role_changes = {
        chama_id: total
        for chama_id, total in role_change_counts.items()
        if total >= role_change_threshold
    }

    risk_flags = []
    if suspicious_ips:
        risk_flags.append("failed_logins")
    if repeated_stk_failures:
        risk_flags.append("stk_failures")
    if rapid_payout_attempts:
        risk_flags.append("rapid_payouts")
    if rapid_role_changes:
        risk_flags.append("rapid_role_changes")

    return {
        "window_start": one_hour_ago.isoformat(),
        "window_minutes": window_minutes,
        "failed_logins_last_hour": failed_last_hour,
        "suspicious_ips": suspicious_ips,
        "stale_active_sessions": stale_sessions,
        "repeated_stk_failures": repeated_stk_failures,
        "rapid_payout_attempts": rapid_payout_attempts,
        "rapid_role_changes": rapid_role_changes,
        "risk_flags": risk_flags,
    }
