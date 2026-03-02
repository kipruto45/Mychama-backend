from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.accounts.models import LoginEvent, OTPToken
from apps.chama.models import MembershipRequest, MembershipRequestStatus
from apps.notifications.models import NotificationType

logger = logging.getLogger(__name__)


@shared_task
def cleanup_expired_otps():
    """
    Delete expired OTP records.
    Run daily to clean up old OTP tokens.
    """
    deleted_count = OTPToken.objects.filter(
        expires_at__lt=timezone.now()
    ).delete()
    
    logger.info(f"Cleaned up {deleted_count[0]} expired OTP records")
    return {
        "status": "completed",
        "deleted_count": deleted_count[0],
        "time": timezone.now().isoformat(),
    }


@shared_task
def expire_pending_membership_requests():
    """
    Expire pending membership requests after N days.
    Default: 7 days. Run daily at 1:00 AM.
    """
    expiry_days = max(
        1,
        int(getattr(settings, "MEMBERSHIP_REQUEST_EXPIRY_DAYS", 7)),
    )
    cutoff = timezone.now() - timedelta(days=expiry_days)
    
    expired = MembershipRequest.objects.filter(
        status=MembershipRequestStatus.PENDING,
        created_at__lt=cutoff
    )
    
    # Update status
    expired_ids = list(expired.values_list('id', flat=True))
    expired.update(status=MembershipRequestStatus.EXPIRED)
    
    # Notify users
    for request in MembershipRequest.objects.filter(id__in=expired_ids):
        try:
            from apps.notifications.services import NotificationService
            NotificationService.send_notification(
                user=request.user,
                message=f"Your request to join {request.chama.name} has expired.",
                channels=["sms", "in_app"],
                notification_type=NotificationType.SYSTEM,
                category="membership",
            )
        except Exception as e:
            logger.error(f"Failed to notify user {request.user_id}: {e}")
    
    logger.info(f"Expired {len(expired_ids)} pending membership requests")
    return {
        "status": "completed",
        "expired_count": len(expired_ids),
        "time": timezone.now().isoformat(),
    }


@shared_task
def send_pending_approval_reminders():
    """
    Send reminders to secretaries about pending membership requests.
    Run daily at 9:00 AM. Notifies after reminder threshold hours.
    """
    reminder_hours = max(
        1,
        int(getattr(settings, "MEMBERSHIP_REVIEW_REMINDER_HOURS", 24)),
    )
    pending_requests = MembershipRequest.objects.filter(
        status=MembershipRequestStatus.PENDING,
        created_at__lt=timezone.now() - timedelta(hours=reminder_hours),
    ).select_related('chama', 'user')
    
    # Group by chama
    by_chama = {}
    for req in pending_requests:
        if req.chama_id not in by_chama:
            by_chama[req.chama_id] = {
                'chama': req.chama,
                'requests': []
            }
        by_chama[req.chama_id]['requests'].append(req)
    
    # Notify secretaries
    from apps.chama.models import Membership, MembershipRole, MemberStatus
    total_notified = 0
    
    for chama_id, data in by_chama.items():
        chama = data['chama']
        requests = data['requests']
        
        secretaries = Membership.objects.filter(
            chama=chama,
            role__in=[MembershipRole.SECRETARY, MembershipRole.ADMIN],
            status=MemberStatus.ACTIVE,
            is_active=True
        ).select_related('user')
        
        for sec in secretaries:
            try:
                from apps.notifications.services import NotificationService
                NotificationService.send_notification(
                    user=sec.user,
                    message=f"You have {len(requests)} pending membership requests for {chama.name}",
                    channels=["sms", "in_app"],
                    notification_type=NotificationType.SYSTEM,
                    category="membership",
                )
                total_notified += 1
            except Exception as e:
                logger.error(f"Failed to notify secretary {sec.user_id}: {e}")
    
    logger.info(f"Sent {total_notified} pending approval reminders")
    return {
        "status": "completed",
        "secretaries_notified": total_notified,
        "pending_requests": sum(len(r['requests']) for r in by_chama.values()),
        "time": timezone.now().isoformat(),
    }


@shared_task
def security_lockout_cleanup():
    # Lockout data relies on TTL-based cache keys from login view.
    return {
        "status": "ok",
        "detail": "Lockout entries are TTL-managed.",
        "time": timezone.now().isoformat(),
    }


@shared_task
def security_suspicious_activity_scan():
    one_hour_ago = timezone.now() - timedelta(hours=1)
    failed = LoginEvent.objects.filter(success=False, created_at__gte=one_hour_ago).count()
    by_ip = {}
    for event in LoginEvent.objects.filter(success=False, created_at__gte=one_hour_ago):
        key = event.ip_address or "unknown"
        by_ip[key] = by_ip.get(key, 0) + 1

    suspicious_ips = {ip: count for ip, count in by_ip.items() if count >= 5}
    return {
        "failed_last_hour": failed,
        "suspicious_ips": suspicious_ips,
        "time": timezone.now().isoformat(),
    }


@shared_task
def security_new_device_alert(user_id: str, ip_address: str, user_agent: str):
    # Event-driven placeholder; login view already dispatches in real-time.
    cache_key = f"security:new-device:{user_id}:{ip_address}:{timezone.localdate().isoformat()}"
    cache.set(cache_key, {"ua": user_agent}, timeout=86400)
    return {"status": "recorded", "cache_key": cache_key}
