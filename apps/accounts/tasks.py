from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

from apps.accounts.models import LoginEvent, MemberKYC, MemberKYCStatus, MemberKYCTier, OTPToken
from apps.accounts.services import KYCService
from apps.chama.models import MembershipRequest, MembershipRequestStatus
from apps.notifications.models import NotificationType
from apps.notifications.services import NotificationService
from apps.security.models import DeviceSession

logger = logging.getLogger(__name__)


@shared_task
def send_welcome_email_task(user_id):
    """
    Send a modern welcome email to new users after registration.
    Runs asynchronously via Celery.
    """
    from apps.accounts.models import User
    
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        logger.warning(f"User {user_id} not found for welcome email")
        return {"status": "user_not_found"}
    
    if not user.email:
        logger.warning(f"User {user_id} has no email address")
        return {"status": "no_email"}
    
    try:
        from apps.notifications.email import send_email_message
        from django.template.loader import render_to_string

        # Build email context
        context = {
            'user_name': user.get_display_name(),
            'app_url': getattr(settings, 'FRONTEND_URL', 'https://my-cham-a.app'),
            'dashboard_url': f"{getattr(settings, 'FRONTEND_URL', 'https://my-cham-a.app')}/dashboard",
            'logo_url': f"{getattr(settings, 'FRONTEND_URL', 'https://my-cham-a.app')}/logo.png",
        }
        
        # Try to load HTML template
        try:
            html_body = render_to_string('emails/auth/01-welcome.html', context)
        except Exception:
            # Fallback to minimal inline template if template not found
            html_body = f"""
            <html>
                <body style="font-family: Arial, sans-serif; padding: 20px; background-color: #F3F4F6;">
                    <div style="max-width: 600px; margin: 0 auto; background: white; border-radius: 16px; padding: 40px;">
                        <h1 style="font-size: 28px; font-weight: 700; color: #0A0A0A;">Welcome to MyChama, {context.get('user_name', 'User')}!</h1>
                        <p style="font-size: 16px; color: #4B5563; line-height: 1.7; margin: 24px 0;">
                            Thank you for joining MyChama – the modern platform for community savings groups. 
                            Your journey to financial empowerment and community building starts here.
                        </p>
                        <div style="margin: 32px 0; text-align: center;">
                            <a href="{context.get('dashboard_url')}" style="display: inline-block; padding: 14px 32px; background: #16A34A; color: white; font-size: 16px; font-weight: 600; text-decoration: none; border-radius: 10px; box-shadow: 0 4px 12px rgba(22, 163, 74, 0.3);">Get Started</a>
                        </div>
                        <p style="color: #6B7280; font-size: 14px; margin-top: 32px; border-top: 1px solid #E5E7EB; padding-top: 16px;">
                            MyChama Team<br/>
                            <a href="{context.get('app_url')}" style="color: #16A34A; text-decoration: none;">Visit MyChama</a>
                        </p>
                    </div>
                </body>
            </html>
            """
        
        result = send_email_message(
            subject="Welcome to MyChama – Your Community Savings Journey Starts Here",
            recipient_list=[user.email],
            body=f"Welcome to MyChama, {context.get('user_name', 'User')}! Thank you for joining us.",
            html_body=html_body,
        )
        
        logger.info(f"Welcome email sent to {user.email} (provider: {result.provider}, sent: {result.sent_count})")
        return {
            "status": "sent",
            "user_id": user_id,
            "email": user.email,
            "provider": result.provider,
            "sent_count": result.sent_count,
        }
    except Exception as exc:
        logger.exception(f"Failed to send welcome email to user {user_id}", exc_info=exc)
        return {
            "status": "failed",
            "user_id": user_id,
            "error": str(exc),
        }


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
def cleanup_stale_auth_sessions():
    """
    Purge expired OTPs, stale/revoked device sessions, and expired refresh tokens.
    """
    now = timezone.now()
    otp_deleted, _ = OTPToken.objects.filter(expires_at__lt=now).delete()

    stale_days = max(1, int(getattr(settings, "STALE_DEVICE_SESSION_DAYS", 30)))
    session_cutoff = now - timedelta(days=stale_days)
    revoked_deleted, _ = DeviceSession.objects.filter(
        is_revoked=True,
        last_seen__lt=session_cutoff,
    ).delete()
    stale_deleted, _ = DeviceSession.objects.filter(
        is_revoked=False,
        last_seen__lt=session_cutoff,
    ).delete()

    expired_tokens_deleted, _ = OutstandingToken.objects.filter(
        expires_at__lte=now
    ).delete()

    logger.info(
        "Auth cleanup otp_deleted=%s revoked_sessions_deleted=%s stale_sessions_deleted=%s expired_tokens_deleted=%s",
        otp_deleted,
        revoked_deleted,
        stale_deleted,
        expired_tokens_deleted,
    )
    return {
        "status": "completed",
        "otp_deleted": otp_deleted,
        "revoked_sessions_deleted": revoked_deleted,
        "stale_sessions_deleted": stale_deleted,
        "expired_tokens_deleted": expired_tokens_deleted,
        "time": now.isoformat(),
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
    
    for _chama_id, data in by_chama.items():
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


@shared_task
def kyc_daily_sanctions_screening():
    now = timezone.now()
    screened = 0
    flagged = 0
    queryset = MemberKYC.objects.select_related("user", "chama").filter(
        status=MemberKYCStatus.APPROVED
    )
    for kyc in queryset:
        screening = KYCService.run_screening_checks(user=kyc.user, id_number=kyc.id_number)
        kyc.last_sanctions_screened_at = now
        kyc.last_sanctions_screening_result = screening
        kyc.sanctions_match = screening.get("sanctions_match", False)
        kyc.pep_match = screening.get("pep_match", False)
        kyc.blacklist_match = screening.get("blacklist_match", False)
        screened += 1

        if kyc.sanctions_match or kyc.blacklist_match:
            flagged += 1
            kyc.account_frozen_for_compliance = True
            kyc.requires_reverification = True
            kyc.reverification_reason = "Compliance screening flag detected."
            kyc.status = MemberKYCStatus.REJECTED
            kyc.kyc_tier = MemberKYCTier.TIER_0
            kyc.review_note = "Compliance review required due to sanctions/blacklist screening."
            NotificationService.send_notification(
                user=kyc.user,
                chama=kyc.chama,
                channels=["in_app", "sms"],
                message="Your account is under compliance review. Please re-submit KYC documents.",
                subject="Compliance review required",
                notification_type=NotificationType.SECURITY_ALERT,
                idempotency_key=f"kyc-compliance-flag:{kyc.id}:{now.date().isoformat()}",
            )

        kyc.save(
            update_fields=[
                "last_sanctions_screened_at",
                "last_sanctions_screening_result",
                "sanctions_match",
                "pep_match",
                "blacklist_match",
                "account_frozen_for_compliance",
                "requires_reverification",
                "reverification_reason",
                "status",
                "kyc_tier",
                "review_note",
                "updated_at",
            ]
        )
    return {"screened": screened, "flagged": flagged, "time": now.isoformat()}


@shared_task
def kyc_renewal_and_expiry_reminders():
    today = timezone.localdate()
    renewal_cutoff = today + timedelta(days=30)
    reminded = 0

    expiring = MemberKYC.objects.select_related("user", "chama").filter(
        status=MemberKYCStatus.APPROVED,
        id_expiry_date__isnull=False,
        id_expiry_date__lte=renewal_cutoff,
    )
    for kyc in expiring:
        NotificationService.send_notification(
            user=kyc.user,
            chama=kyc.chama,
            channels=["in_app", "sms"],
            message="Your ID is nearing expiry. Please renew your KYC documents.",
            subject="KYC renewal reminder",
            notification_type=NotificationType.SYSTEM,
            idempotency_key=f"kyc-renewal:{kyc.id}:{today.isoformat()}",
        )
        kyc.requires_reverification = True
        kyc.reverification_reason = "ID expiry renewal required."
        kyc.next_reverification_due_at = kyc.id_expiry_date
        kyc.save(
            update_fields=[
                "requires_reverification",
                "reverification_reason",
                "next_reverification_due_at",
                "updated_at",
            ]
        )
        reminded += 1

    annual_due = MemberKYC.objects.select_related("user", "chama").filter(
        status=MemberKYCStatus.APPROVED,
        reviewed_at__isnull=False,
        reviewed_at__date__lte=today - timedelta(days=335),
    )
    for kyc in annual_due:
        NotificationService.send_notification(
            user=kyc.user,
            chama=kyc.chama,
            channels=["in_app"],
            message="Annual KYC renewal is due. Please review and re-submit your details.",
            subject="Annual KYC renewal due",
            notification_type=NotificationType.SYSTEM,
            idempotency_key=f"kyc-annual-renewal:{kyc.id}:{today.isoformat()}",
        )
        kyc.requires_reverification = True
        kyc.reverification_reason = "Annual KYC renewal required."
        kyc.next_reverification_due_at = kyc.next_reverification_due_at or today + timedelta(days=30)
        kyc.save(
            update_fields=[
                "requires_reverification",
                "reverification_reason",
                "next_reverification_due_at",
                "updated_at",
            ]
        )
        reminded += 1

    return {"reminded": reminded, "time": timezone.now().isoformat()}
