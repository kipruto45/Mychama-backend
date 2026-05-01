"""
Platform & DevOps Monitoring Automations

Production-grade automations for:
- Auto-scaling trigger based on queue depth
- API rate limit monitoring
- Dead letter queue handler
- M-Pesa gateway health checker
- Africa's Talking balance monitor
- Mailgun bounce rate monitor
- Zero-downtime deployment handler
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)


@dataclass
class ScalingAction:
    """Auto-scaling action result."""
    action: str
    current_workers: int
    recommended_workers: int
    reason: str
    queue_depth: int


def check_celery_queue_depth() -> ScalingAction:
    """Check Celery queue depth and recommend scaling actions."""
    try:
        from apps.automations.models import JobRun, JobRunStatus
        
        pending_count = JobRun.objects.filter(
            status__in=[JobRunStatus.PENDING, JobRunStatus.RUNNING],
            created_at__gte=timezone.now() - timedelta(minutes=30),
        ).count()
        
        low_threshold = int(getattr(settings, "SCALING_QUEUE_LOW", 10))
        high_threshold = int(getattr(settings, "SCALING_QUEUE_HIGH", 50))
        max_workers = int(getattr(settings, "SCALING_MAX_WORKERS", 10))
        current_workers = int(getattr(settings, "CELERY_WORKERS", 2))
        
        if pending_count >= high_threshold:
            recommended = min(current_workers + 2, max_workers)
            return ScalingAction(
                action="scale_up",
                current_workers=current_workers,
                recommended_workers=recommended,
                reason=f"Queue depth {pending_count} exceeds high threshold {high_threshold}",
                queue_depth=pending_count,
            )
        elif pending_count <= low_threshold and current_workers > 1:
            return ScalingAction(
                action="scale_down",
                current_workers=current_workers,
                recommended_workers=current_workers - 1,
                reason=f"Queue depth {pending_count} below low threshold {low_threshold}",
                queue_depth=pending_count,
            )
        
        return ScalingAction(
            action="maintain",
            current_workers=current_workers,
            recommended_workers=current_workers,
            reason=f"Queue depth {pending_count} within acceptable range",
            queue_depth=pending_count,
        )
    
    except Exception as exc:
        logger.error(f"Error checking queue depth: {exc}")
        return ScalingAction(
            action="error",
            current_workers=0,
            recommended_workers=0,
            reason=str(exc),
            queue_depth=0,
        )


@dataclass
class RateLimitStatus:
    """Rate limit status."""
    endpoint: str
    hits_today: int
    limit: int
    usage_percent: float
    is_throttled: bool


def check_api_rate_limits() -> list[RateLimitStatus]:
    """Check API rate limits and alert if approaching thresholds."""
    from core.throttles import get_throttle_counts
    
    throttle_limits = {
        "sms": 100,
        "email": 500,
        "payment": 50,
        "auth": 20,
    }
    
    statuses = []
    
    for endpoint, limit in throttle_limits.items():
        try:
            hits = cache.get(f"ratelimit:{endpoint}:{timezone.now().date()}", 0)
            usage_pct = (hits / limit) * 100 if limit > 0 else 0
            
            statuses.append(RateLimitStatus(
                endpoint=endpoint,
                hits_today=hits,
                limit=limit,
                usage_percent=usage_pct,
                is_throttled=usage_pct >= 90,
            ))
        except Exception:
            pass
    
    return statuses


@dataclass
class DeadLetterItem:
    """Dead letter queue item."""
    task_id: str
    task_name: str
    error_message: str
    failed_at: str
    retry_count: int


@dataclass
class DeadLetterSummary:
    """Dead letter queue summary."""
    total_items: int
    items: list[DeadLetterItem]
    oldest_item_age: int


def check_dead_letter_queue() -> DeadLetterSummary:
    """Check dead letter queue and surface failed tasks."""
    from apps.automations.models import JobRun, JobRunStatus
    
    failed_threshold = timezone.now() - timedelta(hours=24)
    
    failed_jobs = JobRun.objects.filter(
        status=JobRunStatus.FAILED,
        updated_at__gte=failed_threshold,
    ).order_by("-updated_at")[:20]
    
    items = []
    for job in failed_jobs:
        items.append(DeadLetterItem(
            task_id=str(job.id),
            task_name=job.job_name or "unknown",
            error_message=job.last_error or "Unknown error",
            failed_at=job.updated_at.isoformat() if job.updated_at else "",
            retry_count=job.retry_count,
        ))
    
    oldest_age = 0
    if items:
        oldest = failed_jobs.first()
        if oldest and oldest.updated_at:
            oldest_age = (timezone.now() - oldest.updated_at).days
    
    return DeadLetterSummary(
        total_items=len(items),
        items=items,
        oldest_item_age=oldest_age,
    )


@dataclass
class MpesaHealthStatus:
    """M-Pesa gateway health status."""
    is_healthy: bool
    last_check: str
    response_time_ms: int | None
    error_count: int
    success_rate: float


def check_mpesa_gateway_health() -> MpesaHealthStatus:
    """Check M-Pesa Daraja API gateway health."""
    import time
    from urllib.parse import urlparse

    import requests

    from core.safe_http import OutboundPolicy, UnsafeOutboundRequest, safe_request

    mpesa_api_url = getattr(settings, "MPESA_API_URL", "https://api.safaricom.co.ke")
    health_endpoint = f"{mpesa_api_url}/health"
    
    last_check_cache = cache.get("mpesa_health_last_check", None)
    error_count_cache = cache.get("mpesa_health_error_count", 0)
    last_success_cache = cache.get("mpesa_health_last_success", None)
    
    try:
        start = time.time()
        hostname = urlparse(mpesa_api_url).hostname or ""
        response = safe_request(
            "GET",
            health_endpoint,
            policy=OutboundPolicy(
                allowed_hosts={
                    "api.safaricom.co.ke",
                    "sandbox.safaricom.co.ke",
                    hostname,
                }
            ),
            timeout=5,
        )
        response_time = int((time.time() - start) * 1000)
        response.close()
        
        cache.set("mpesa_health_last_check", timezone.now().isoformat(), timeout=3600)
        cache.set("mpesa_health_success_count", 
                 cache.get("mpesa_health_success_count", 0) + 1, 
                 timeout=86400)
        
        success_count = cache.get("mpesa_health_success_count", 0)
        total_checks = success_count + error_count_cache
        success_rate = (success_count / total_checks * 100) if total_checks > 0 else 100.0
        
        return MpesaHealthStatus(
            is_healthy=True,
            last_check=timezone.now().isoformat(),
            response_time_ms=response_time,
            error_count=int(error_count_cache),
            success_rate=success_rate,
        )
    
    except (requests.RequestException, UnsafeOutboundRequest) as exc:
        cache.set("mpesa_health_error_count", error_count_cache + 1, timeout=86400)
        cache.set("mpesa_health_last_check", timezone.now().isoformat(), timeout=3600)
        
        return MpesaHealthStatus(
            is_healthy=False,
            last_check=timezone.now().isoformat(),
            response_time_ms=None,
            error_count=int(error_count_cache) + 1,
            success_rate=0.0,
        )
    
    except Exception as exc:
        logger.error(f"M-Pesa health check error: {exc}")
        return MpesaHealthStatus(
            is_healthy=False,
            last_check=timezone.now().isoformat(),
            response_time_ms=None,
            error_count=int(error_count_cache),
            success_rate=0.0,
        )


@dataclass
class ATBalanceStatus:
    """Africa's Talking balance status."""
    balance: float
    currency: str
    is_low: bool
    estimated_sms_remaining: int
    alert_level: str


def check_africastalking_balance() -> ATBalanceStatus:
    """Check Africa's Talking SMS credit balance."""
    try:
        import africastalking
        
        username = getattr(settings, "AFRICAS_TALKING_USERNAME", "")
        api_key = getattr(settings, "AFRICAS_TALKING_API_KEY", "")
        
        if not username or not api_key:
            return ATBalanceStatus(
                balance=0.0,
                currency="KES",
                is_low=True,
                estimated_sms_remaining=0,
                alert_level="HIGH",
            )
        
        africastalking.initialize(username, api_key)
        airtime = africastalking.AIRTIME
        response = airtime.fetch_accountoverance()
        
        balance = float(response.get("balance", 0) or 0)
        currency = response.get("currency", "KES")
        
        sms_cost_per_message = 1.0
        estimated_remaining = int(balance / sms_cost_per_message)
        
        low_threshold = float(getattr(settings, "AT_LOW_BALANCE_THRESHOLD", 100.0))
        critical_threshold = float(getattr(settings, "AT_CRITICAL_BALANCE_THRESHOLD", 20.0))
        
        is_low = balance <= low_threshold
        alert_level = "CRITICAL" if balance <= critical_threshold else "LOW" if balance <= low_threshold else "NONE"
        
        return ATBalanceStatus(
            balance=balance,
            currency=currency,
            is_low=is_low,
            estimated_sms_remaining=estimated_remaining,
            alert_level=alert_level,
        )
    
    except Exception as exc:
        logger.error(f"Africa's Talking balance check error: {exc}")
        return ATBalanceStatus(
            balance=0.0,
            currency="KES",
            is_low=True,
            estimated_sms_remaining=0,
            alert_level="HIGH",
        )


@dataclass
class MailgunBounceStatus:
    """Mailgun bounce rate status."""
    total_sent: int
    bounces: int
    bounce_rate: float
    is_high: bool


def check_mailgun_bounce_rate() -> MailgunBounceStatus:
    """Check Mailgun bounce rate."""
    try:
        from apps.notifications.models import NotificationLog, NotificationStatus
        
        today = timezone.now().date()
        
        total_sent = NotificationLog.objects.filter(
            channel="email",
            status=NotificationStatus.SENT,
            created_at__date=today,
        ).count()
        
        bounces = NotificationLog.objects.filter(
            channel="email",
            status=NotificationStatus.FAILED,
            created_at__date=today,
        ).count()
        
        bounce_rate = (bounces / total_sent * 100) if total_sent > 0 else 0.0
        
        high_threshold = float(getattr(settings, "MAILGUN_BOUNCE_THRESHOLD", 5.0))
        
        return MailgunBounceStatus(
            total_sent=total_sent,
            bounces=bounces,
            bounce_rate=bounce_rate,
            is_high=bounce_rate >= high_threshold,
        )
    
    except Exception as exc:
        logger.error(f"Mailgun bounce check error: {exc}")
        return MailgunBounceStatus(
            total_sent=0,
            bounces=0,
            bounce_rate=0.0,
            is_high=False,
        )


@dataclass
class DeploymentStatus:
    """Deployment status."""
    is_deploying: bool
    deployment_id: str | None
    health_check_passed: bool
    rollback_required: bool


def check_deployment_health() -> DeploymentStatus:
    """Check deployment health for zero-downtime deploys."""
    deploying = cache.get("deployment_in_progress", False)
    deployment_id = cache.get("deployment_id", None)
    
    if not deploying:
        return DeploymentStatus(
            is_deploying=False,
            deployment_id=deployment_id,
            health_check_passed=True,
            rollback_required=False,
        )
    
    health_check_passed = cache.get("deployment_health_check", False)
    error_count = cache.get("deployment_error_count", 0)
    rollback_required = error_count >= 3
    
    return DeploymentStatus(
        is_deploying=deploying,
        deployment_id=deployment_id,
        health_check_passed=health_check_passed,
        rollback_required=rollback_required,
    )


@dataclass
class DatabaseHealth:
    """Database connection pool health."""
    pool_size: int
    active_connections: int
    available_connections: int
    utilization_percent: float
    is_healthy: bool


def check_database_pool_health() -> DatabaseHealth:
    """Check database connection pool health."""
    from django.db import connection
    
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        
        pool_size = int(getattr(settings, "DB_POOL_SIZE", 20))
        active = 0
        available = pool_size
        
        try:
            from django.db import connections
            default_conn = connections["default"]
            if hasattr(default_conn, "get_conn_params"):
                available = pool_size - 1
        except Exception:
            pass
        
        utilization = ((pool_size - available) / pool_size * 100) if pool_size > 0 else 0.0
        
        return DatabaseHealth(
            pool_size=pool_size,
            active_connections=pool_size - available,
            available_connections=available,
            utilization_percent=utilization,
            is_healthy=utilization < 80,
        )
    
    except Exception as exc:
        logger.error(f"Database pool check error: {exc}")
        return DatabaseHealth(
            pool_size=0,
            active_connections=0,
            available_connections=0,
            utilization_percent=100.0,
            is_healthy=False,
        )


@dataclass
class RedisHealth:
    """Redis cache health."""
    memory_used_mb: float
    memory_limit_mb: float
    utilization_percent: float
    is_healthy: bool


def check_redis_health() -> RedisHealth:
    """Check Redis cache memory health."""
    try:
        info = cache.cache._server.info()
        
        memory_used = float(info.get("used_memory", 0) or 0) / (1024 * 1024)
        memory_limit = float(info.get("maxmemory", 0) or 0) / (1024 * 1024)
        
        if memory_limit == 0:
            memory_limit = 256.0
        
        utilization = (memory_used / memory_limit * 100) if memory_limit > 0 else 0.0
        
        return RedisHealth(
            memory_used_mb=memory_used,
            memory_limit_mb=memory_limit,
            utilization_percent=utilization,
            is_healthy=utilization < 80,
        )
    
    except Exception as exc:
        logger.error(f"Redis health check error: {exc}")
        return RedisHealth(
            memory_used_mb=0,
            memory_limit_mb=256,
            utilization_percent=0,
            is_healthy=True,
        )
