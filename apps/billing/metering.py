"""
Usage metering utilities for billing limits.
"""
from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from .models import UsageMetric


METRIC_LIMIT_KEYS = {
    UsageMetric.MEMBERS: 'seat_limit',
    UsageMetric.SMS: 'sms_limit',
    UsageMetric.OTP_SMS: 'otp_sms_limit',
    UsageMetric.STORAGE_MB: 'storage_limit_mb',
    UsageMetric.STK_PUSHES: 'monthly_stk_limit',
}


def _next_cycle_end(metric_key: str):
    if metric_key in {UsageMetric.SMS, UsageMetric.OTP_SMS, UsageMetric.STK_PUSHES}:
        return timezone.now() + timedelta(days=30)
    return None


def resolve_usage_limit(chama, metric_key: str, entitlements: dict | None = None) -> int:
    from .services import get_entitlements

    resolved_entitlements = entitlements or get_entitlements(chama)
    limit_key = METRIC_LIMIT_KEYS.get(metric_key)
    if not limit_key:
        return 0
    return int(resolved_entitlements.get(limit_key, 0) or 0)


def ensure_usage_metric(chama, metric_key: str, *, limit_quantity: int | None = None) -> UsageMetric:
    defaults = {
        'used_quantity': 0,
        'limit_quantity': limit_quantity if limit_quantity is not None else resolve_usage_limit(chama, metric_key),
        'period_started_at': timezone.now(),
        'period_ends_at': _next_cycle_end(metric_key),
        'reset_at': _next_cycle_end(metric_key),
    }
    metric, created = UsageMetric.objects.get_or_create(
        chama=chama,
        metric_key=metric_key,
        defaults=defaults,
    )
    if created:
        return metric

    updated_fields = []
    target_limit = limit_quantity if limit_quantity is not None else resolve_usage_limit(chama, metric_key)
    if metric.limit_quantity != target_limit:
        metric.limit_quantity = target_limit
        updated_fields.append('limit_quantity')
    if metric.metric_key in {UsageMetric.SMS, UsageMetric.OTP_SMS, UsageMetric.STK_PUSHES} and metric.period_ends_at is None:
        next_cycle = _next_cycle_end(metric.metric_key)
        metric.period_ends_at = next_cycle
        metric.reset_at = next_cycle
        updated_fields.extend(['period_ends_at', 'reset_at'])
    if updated_fields:
        metric.save(update_fields=[*updated_fields, 'updated_at'])
    return metric


def sync_usage_limits(chama) -> dict[str, dict]:
    from .services import get_entitlements, get_seat_usage

    entitlements = get_entitlements(chama)
    metrics: dict[str, dict] = {}

    members_used = get_seat_usage(chama)
    members_limit = resolve_usage_limit(chama, UsageMetric.MEMBERS, entitlements)
    members_metric = ensure_usage_metric(chama, UsageMetric.MEMBERS, limit_quantity=members_limit)
    if members_metric.used_quantity != members_used:
        members_metric.used_quantity = members_used
        members_metric.save(update_fields=['used_quantity', 'updated_at'])

    for metric_key in (
        UsageMetric.MEMBERS,
        UsageMetric.SMS,
        UsageMetric.OTP_SMS,
        UsageMetric.STORAGE_MB,
        UsageMetric.STK_PUSHES,
    ):
        metric = ensure_usage_metric(
            chama,
            metric_key,
            limit_quantity=resolve_usage_limit(chama, metric_key, entitlements),
        )
        metrics[metric_key] = {
            'used': metric.used_quantity,
            'limit': metric.limit_quantity,
            'remaining': max(0, metric.limit_quantity - metric.used_quantity) if metric.limit_quantity else 0,
            'reset_at': metric.reset_at.isoformat() if metric.reset_at else None,
        }
    return metrics


def set_usage(chama, metric_key: str, quantity: int) -> UsageMetric:
    metric = ensure_usage_metric(chama, metric_key)
    metric.used_quantity = max(0, int(quantity))
    metric.save(update_fields=['used_quantity', 'updated_at'])
    return metric


def increment_usage(chama, metric_key: str, quantity: int = 1) -> UsageMetric:
    metric = ensure_usage_metric(chama, metric_key)
    metric.used_quantity += max(0, int(quantity))
    metric.save(update_fields=['used_quantity', 'updated_at'])
    return metric


def usage_within_limit(chama, metric_key: str, requested: int = 1) -> dict:
    metric = ensure_usage_metric(chama, metric_key)
    projected = metric.used_quantity + max(0, int(requested))
    return {
        'allowed': metric.limit_quantity <= 0 or projected <= metric.limit_quantity,
        'current': metric.used_quantity,
        'projected': projected,
        'limit': metric.limit_quantity,
    }


def reset_due_usage_metrics(now=None) -> int:
    now = now or timezone.now()
    processed = 0
    for metric in UsageMetric.objects.filter(reset_at__isnull=False, reset_at__lte=now):
        next_cycle = _next_cycle_end(metric.metric_key)
        metric.used_quantity = 0
        metric.period_started_at = now
        metric.period_ends_at = next_cycle
        metric.reset_at = next_cycle
        metric.save(
            update_fields=[
                'used_quantity',
                'period_started_at',
                'period_ends_at',
                'reset_at',
                'updated_at',
            ]
        )
        processed += 1
    return processed
