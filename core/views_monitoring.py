"""
Monitoring Dashboards for Digital Chama System
Basic monitoring setup with health checks and metrics
"""

import time
from datetime import datetime, timedelta

try:
    import psutil  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    psutil = None
from django.conf import settings
from django.core.cache import cache
from django.db import connection, models
from django.http import HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.analytics.services import posthog_service
from core.supabase import get_supabase_health_snapshot


@require_GET
@never_cache
def health_check(request):
    """
    Basic health check endpoint
    Returns JSON with service status
    """
    health_data = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": getattr(settings, 'VERSION', '1.0.0'),
        "services": {}
    }

    # Database check
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        health_data["services"]["database"] = {"status": "healthy"}
    except Exception as e:
        health_data["services"]["database"] = {"status": "unhealthy", "error": str(e)}
        health_data["status"] = "unhealthy"

    # Redis check
    try:
        cache.set('health_check', 'ok', 10)
        if cache.get('health_check') == 'ok':
            health_data["services"]["redis"] = {"status": "healthy"}
        else:
            health_data["services"]["redis"] = {"status": "unhealthy", "error": "Cache not working"}
            health_data["status"] = "unhealthy"
    except Exception as e:
        health_data["services"]["redis"] = {"status": "unhealthy", "error": str(e)}
        health_data["status"] = "unhealthy"

    supabase_health = get_supabase_health_snapshot()
    health_data["services"]["supabase"] = supabase_health
    if supabase_health["status"] == "unhealthy":
        health_data["status"] = "unhealthy"

    posthog_health = posthog_service.health_snapshot()
    health_data["services"]["posthog"] = posthog_health
    if posthog_health["status"] == "unhealthy":
        health_data["status"] = "degraded"

    health_data["services"]["openai"] = {
        "status": "healthy" if getattr(settings, "OPENAI_API_KEY", "") else "disabled",
        "model": getattr(settings, "AI_CHAT_MODEL", ""),
    }

    # System resources
    if psutil is not None:
        health_data["system"] = {
            "cpu_percent": psutil.cpu_percent(interval=1),
            "memory_percent": psutil.virtual_memory().percent,
            "disk_percent": psutil.disk_usage('/').percent,
        }
    else:
        health_data["system"] = {"status": "disabled", "reason": "psutil not installed"}

    status_code = 200 if health_data["status"] == "healthy" else 503
    return JsonResponse(health_data, status=status_code)


@require_GET
@never_cache
def notifications_health_check(request):
    """
    Health check for notification services (Email, SMS, Celery).
    
    Returns JSON with status of:
    - email: Email service configuration
    - sms: SMS service configuration  
    - celery: Celery worker availability
    """
    from django.conf import settings

    from apps.notifications.email import (
        DjangoEmailProvider,
        GmailSMTPProvider,
        MailgunEmailProvider,
        SendGridEmailProvider,
    )
    from apps.notifications.push import get_expo_provider, get_fcm_provider
    from apps.notifications.sms import AfricasTalkingSMSProvider
    
    health_data = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "email": "ok",
        "sms": "ok",
        "push": "ok",
        "celery": "ok"
    }
    
    # Check email configuration
    try:
        email_provider = settings.EMAIL_PROVIDER if hasattr(settings, 'EMAIL_PROVIDER') else 'django'
        if email_provider == 'mailgun':
            provider = MailgunEmailProvider()
        elif email_provider == 'sendgrid':
            provider = SendGridEmailProvider()
        elif email_provider == 'gmail':
            provider = GmailSMTPProvider()
        else:
            provider = DjangoEmailProvider()
        
        # Validate configuration
        if hasattr(provider, 'validate_configuration'):
            provider.validate_configuration()
        health_data["email"] = "ok"
    except Exception as e:
        health_data["email"] = f"error: {str(e)}"
        health_data["status"] = "degraded"
    
    # Check SMS configuration
    try:
        sms_provider = settings.SMS_PROVIDER if hasattr(settings, 'SMS_PROVIDER') else 'africastalking'
        if sms_provider == 'africastalking':
            provider = AfricasTalkingSMSProvider()
            if hasattr(provider, 'validate_configuration'):
                provider.validate_configuration()
        health_data["sms"] = "ok"
    except Exception as e:
        health_data["sms"] = f"error: {str(e)}"
        health_data["status"] = "degraded"

    try:
        fcm_provider = get_fcm_provider()
        expo_provider = get_expo_provider()
        push_messages = []
        if fcm_provider.enabled:
            push_messages.append("fcm")
        if expo_provider.enabled:
            push_messages.append("expo")
        if push_messages:
            health_data["push"] = f"ok ({', '.join(push_messages)})"
        else:
            health_data["push"] = "disabled"
    except Exception as e:
        health_data["push"] = f"error: {str(e)}"
        health_data["status"] = "degraded"
    
    # Check Celery workers
    try:
        from celery.app.control import Inspect
        from django.conf import settings

        # Use the project's celery app from config
        from config.celery import app as celery_app
        
        i = Inspect(app=celery_app)
        active_workers = i.active()
        
        if active_workers is None or len(active_workers) == 0:
            health_data["celery"] = "no workers"
            health_data["status"] = "degraded"
        else:
            health_data["celery"] = f"ok ({len(active_workers)} workers)"
    except Exception as e:
        health_data["celery"] = f"error: {str(e)}"
        health_data["status"] = "degraded"
    
    status_code = 200 if health_data["status"] == "healthy" else 503
    return JsonResponse(health_data, status=status_code)


@require_GET
@never_cache
def detailed_health_check(request):
    """
    Detailed health check with comprehensive metrics
    """
    start_time = time.time()

    health_data = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "response_time": None,
        "version": getattr(settings, 'VERSION', '1.0.0'),
        "services": {},
        "metrics": {}
    }

    # Database detailed check
    try:
        db_start = time.time()
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    COUNT(*) as total_users,
                    COUNT(CASE WHEN last_login > %s THEN 1 END) as active_users_30d
                FROM accounts_user
            """, [datetime.utcnow() - timedelta(days=30)])

            result = cursor.fetchone()
            db_time = time.time() - db_start

        health_data["services"]["database"] = {
            "status": "healthy",
            "response_time": round(db_time * 1000, 2),  # ms
            "total_users": result[0],
            "active_users_30d": result[1]
        }
    except Exception as e:
        health_data["services"]["database"] = {"status": "unhealthy", "error": str(e)}
        health_data["status"] = "unhealthy"

    # Redis detailed check
    try:
        redis_start = time.time()
        cache.set('health_check_detailed', 'ok', 10)
        cache.get('health_check_detailed')
        redis_time = time.time() - redis_start

        # Get Redis info
        redis_client = cache._cache.get_client()
        redis_info = redis_client.info()

        health_data["services"]["redis"] = {
            "status": "healthy",
            "response_time": round(redis_time * 1000, 2),
            "connected_clients": redis_info.get('connected_clients', 0),
            "used_memory_mb": round(redis_info.get('used_memory', 0) / 1024 / 1024, 2),
            "total_connections_received": redis_info.get('total_connections_received', 0)
        }
    except Exception as e:
        health_data["services"]["redis"] = {"status": "unhealthy", "error": str(e)}
        health_data["status"] = "unhealthy"

    supabase_health = get_supabase_health_snapshot()
    health_data["services"]["supabase"] = supabase_health
    if supabase_health["status"] == "unhealthy":
        health_data["status"] = "unhealthy"

    posthog_health = posthog_service.health_snapshot()
    health_data["services"]["posthog"] = posthog_health
    if posthog_health["status"] == "unhealthy":
        health_data["status"] = "degraded"

    health_data["services"]["openai"] = {
        "status": "healthy" if getattr(settings, "OPENAI_API_KEY", "") else "disabled",
        "model": getattr(settings, "AI_CHAT_MODEL", ""),
    }

    # System detailed metrics
    if psutil is not None:
        health_data["metrics"]["system"] = {
            "cpu_percent": psutil.cpu_percent(interval=1),
            "cpu_count": psutil.cpu_count(),
            "memory": {
                "total_mb": round(psutil.virtual_memory().total / 1024 / 1024, 2),
                "available_mb": round(psutil.virtual_memory().available / 1024 / 1024, 2),
                "percent": psutil.virtual_memory().percent,
            },
            "disk": {
                "total_gb": round(psutil.disk_usage('/').total / 1024 / 1024 / 1024, 2),
                "free_gb": round(psutil.disk_usage('/').free / 1024 / 1024 / 1024, 2),
                "percent": psutil.disk_usage('/').percent,
            },
            "network": {
                "bytes_sent_mb": round(psutil.net_io_counters().bytes_sent / 1024 / 1024, 2),
                "bytes_recv_mb": round(psutil.net_io_counters().bytes_recv / 1024 / 1024, 2),
            },
        }
    else:
        health_data["metrics"]["system"] = {"status": "disabled", "reason": "psutil not installed"}

    # Application metrics
    try:
        from apps.chama.models import Chama
        from apps.finance.models import Contribution, Loan

        # Chama stats
        total_chamas = Chama.objects.count()
        active_chamas = Chama.objects.filter(is_active=True).count()

        # Financial stats (last 30 days)
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        total_contributions = Contribution.objects.filter(
            created_at__gte=thirty_days_ago
        ).aggregate(total=models.Sum('amount'))['total'] or 0

        total_loans = Loan.objects.filter(
            created_at__gte=thirty_days_ago
        ).aggregate(total=models.Sum('amount'))['total'] or 0

        health_data["metrics"]["application"] = {
            "total_chamas": total_chamas,
            "active_chamas": active_chamas,
            "contributions_30d": float(total_contributions),
            "loans_30d": float(total_loans),
            "chama_growth_rate": round((active_chamas / max(total_chamas, 1)) * 100, 2)
        }
    except Exception as e:
        health_data["metrics"]["application"] = {"error": str(e)}

    # Calculate response time
    total_time = time.time() - start_time
    health_data["response_time"] = round(total_time * 1000, 2)

    status_code = 200 if health_data["status"] == "healthy" else 503
    return JsonResponse(health_data, status=status_code)


@require_GET
@never_cache
def supabase_health_check(request):
    health_data = {
        "timestamp": datetime.utcnow().isoformat(),
        "supabase": get_supabase_health_snapshot(),
    }
    status_code = 200 if health_data["supabase"]["status"] != "unhealthy" else 503
    return JsonResponse(health_data, status=status_code)


@require_GET
@never_cache
def metrics_prometheus(request):
    """
    Prometheus-compatible metrics endpoint
    """
    metrics = []

    # System metrics
    if psutil is None:
        return HttpResponse(
            "# psutil is not installed; system metrics disabled\n",
            content_type="text/plain",
            status=200,
        )

    cpu_percent = psutil.cpu_percent()
    memory_percent = psutil.virtual_memory().percent
    disk_percent = psutil.disk_usage('/').percent

    metrics.extend([
        '# HELP digital_chama_cpu_usage_percent CPU usage percentage',
        '# TYPE digital_chama_cpu_usage_percent gauge',
        f'digital_chama_cpu_usage_percent {cpu_percent}',
        '',
        '# HELP digital_chama_memory_usage_percent Memory usage percentage',
        '# TYPE digital_chama_memory_usage_percent gauge',
        f'digital_chama_memory_usage_percent {memory_percent}',
        '',
        '# HELP digital_chama_disk_usage_percent Disk usage percentage',
        '# TYPE digital_chama_disk_usage_percent gauge',
        f'digital_chama_disk_usage_percent {disk_percent}',
    ])

    # Application metrics
    try:
        from apps.accounts.models import User
        from apps.chama.models import Chama
        from apps.finance.models import Contribution, Loan

        total_users = User.objects.count()
        total_chamas = Chama.objects.count()
        active_chamas = Chama.objects.filter(is_active=True).count()

        # Recent activity (last 24 hours)
        yesterday = datetime.utcnow() - timedelta(days=1)
        recent_contributions = Contribution.objects.filter(created_at__gte=yesterday).count()
        recent_loans = Loan.objects.filter(created_at__gte=yesterday).count()

        metrics.extend([
            '# HELP digital_chama_total_users Total number of registered users',
            '# TYPE digital_chama_total_users gauge',
            f'digital_chama_total_users {total_users}',
            '',
            '# HELP digital_chama_total_chamas Total number of chamas',
            '# TYPE digital_chama_total_chamas gauge',
            f'digital_chama_total_chamas {total_chamas}',
            '',
            '# HELP digital_chama_active_chamas Number of active chamas',
            '# TYPE digital_chama_active_chamas gauge',
            f'digital_chama_active_chamas {active_chamas}',
            '',
            '# HELP digital_chama_recent_contributions Contributions in last 24 hours',
            '# TYPE digital_chama_recent_contributions gauge',
            f'digital_chama_recent_contributions {recent_contributions}',
            '',
            '# HELP digital_chama_recent_loans Loans in last 24 hours',
            '# TYPE digital_chama_recent_loans gauge',
            f'digital_chama_recent_loans {recent_loans}',
        ])
    except Exception as e:
        metrics.append(f'# Error collecting application metrics: {str(e)}')

    # Response
    response = '\n'.join(metrics) + '\n'
    return HttpResponse(response, content_type='text/plain; charset=utf-8')


@require_GET
@never_cache
def payments_health_check(request):
    """
    Health check for M-Pesa/Payments services.
    
    Returns JSON with status of:
    - M-Pesa configuration (sandbox vs production)
    - Callback signature validation enabled
    - IP allowlist configured
    - STK push status (if credentials provided)
    """
    from django.conf import settings
    
    health_data = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "mpesa": {}
    }
    
    try:
        # Check M-Pesa environment
        mpesa_env = getattr(settings, 'MPESA_ENVIRONMENT', 'sandbox')
        mpesa_use_stub = getattr(settings, 'MPESA_USE_STUB', True)
        
        health_data["mpesa"] = {
            "environment": mpesa_env,
            "using_stub": mpesa_use_stub,
            "credentials_configured": all([
                getattr(settings, 'MPESA_CONSUMER_KEY', None),
                getattr(settings, 'MPESA_CONSUMER_SECRET', None),
                getattr(settings, 'MPESA_SHORTCODE', None),
            ]),
            "callback_signature_enabled": getattr(settings, 'PAYMENTS_CALLBACK_REQUIRE_SIGNATURE', False),
            "ip_allowlist_enabled": getattr(settings, 'PAYMENTS_CALLBACK_REQUIRE_IP_ALLOWLIST', False),
        }
        
        # Check callback secret
        callback_secret = getattr(settings, 'MPESA_CALLBACK_SECRET', None)
        if callback_secret and len(callback_secret) > 10:
            health_data["mpesa"]["callback_secret_configured"] = True
        else:
            health_data["mpesa"]["callback_secret_configured"] = False
            health_data["status"] = "degraded"
        
    except Exception as e:
        health_data["mpesa"] = {"error": str(e)}
        health_data["status"] = "degraded"
    
    status_code = 200 if health_data["status"] == "healthy" else 503
    return JsonResponse(health_data, status=status_code)


@require_GET
def dev_otp_latest(request):
    """
    DEV-ONLY: Get the latest OTP code for testing.
    
    WARNING: This endpoint MUST be disabled in production!
    It only works when DEBUG=True and PRINT_OTP_IN_CONSOLE=True.
    
    Use this endpoint to retrieve OTPs during development without
    needing actual SMS/email delivery.
    """
    from django.conf import settings
    from django.http import JsonResponse
    
    # Security check - only allow in DEBUG mode
    if not settings.DEBUG:
        return JsonResponse(
            {"error": "This endpoint is only available in DEBUG mode"},
            status=403
        )
    
    # Check if print OTP is enabled
    print_otp = getattr(settings, 'PRINT_OTP_IN_CONSOLE', False)
    if not print_otp:
        return JsonResponse(
            {
                "error": "PRINT_OTP_IN_CONSOLE is not enabled",
                "hint": "Set PRINT_OTP_IN_CONSOLE=True in your .env file"
            },
            status=400
        )
    
    # Get the latest OTP from cache
    from django.core.cache import cache
    latest_otp = cache.get('dev_latest_otp')
    
    if latest_otp:
        return JsonResponse({
            "otp": latest_otp['code'],
            "purpose": latest_otp.get('purpose', 'unknown'),
            "created_at": latest_otp.get('created_at'),
            "expires_at": latest_otp.get('expires_at'),
            "channel": latest_otp.get('channel', 'unknown'),
        })
    else:
        return JsonResponse({
            "message": "No OTP generated yet. Request an OTP via the API first."
        }, status=404)
