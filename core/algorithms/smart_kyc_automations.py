"""
KYC Automations

Production-grade automations for:
- KYC document queue handler
- KYC auto-verifier (Smile Identity)
- KYC approval access unlocker
- KYC rejection notifier
- KYC re-submission tracker
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count
from django.utils import timezone

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.chama.models import Chama


@dataclass
class KYCQueueItem:
    """KYC queue item."""
    kyc_id: str
    user_id: str
    user_name: str
    phone: str
    chama_id: str
    document_type: str
    submitted_at: str
    resubmission_count: int
    needs_manual_review: bool


@dataclass
class KYCVerificationResult:
    """KYC verification result."""
    is_verified: bool
    kyc_tier: str
    confidence_score: float
    rejection_reason: str | None
    requires_manual_review: bool
    smile_job_id: str | None


@dataclass
class KYCAccessUpdate:
    """KYC access update result."""
    user_id: str
    tier_upgraded_to: str
    permissions_granted: list[str]
    notification_sent: bool


def queue_kyc_document(
    user: "User",
    chama: "Chama",
    document_type: str,
    document_url: str,
    selfie_url: str,
) -> KYCQueueItem:
    """Queue KYC document for verification."""
    from apps.accounts.models import MemberKYC, MemberKYCStatus
    
    kyc, created = MemberKYC.objects.get_or_create(
        user=user,
        chama=chama,
        defaults={
            "status": MemberKYCStatus.PENDING,
            "id_document_type": document_type,
            "id_document_url": document_url,
            "selfie_url": selfie_url,
        },
    )
    
    resub_count = kyc.submission_count or 0
    needs_manual = resub_count >= 2
    
    logger.info(
        "KYC queued for user %s in chama %s: doc=%s, resub=%s",
        user.id,
        chama.id,
        document_type,
        resub_count,
    )
    
    return KYCQueueItem(
        kyc_id=str(kyc.id),
        user_id=str(user.id),
        user_name=user.full_name or "Unknown",
        phone=user.phone or "",
        chama_id=str(chama.id),
        document_type=document_type,
        submitted_at=kyc.created_at.isoformat() if kyc.created_at else timezone.now().isoformat(),
        resubmission_count=resub_count,
        needs_manual_review=needs_manual,
    )


def run_kyc_auto_verification(
    kyc_id: str,
    provider: str = "smile_identity",
) -> KYCVerificationResult:
    """Run auto-KYC verification via Smile Identity."""
    try:
        import requests
    except Exception:
        pass
    
    from apps.accounts.models import MemberKYC, MemberKYCStatus, MemberKYCTier
    
    try:
        kyc = MemberKYC.objects.get(id=kyc_id)
    except MemberKYC.DoesNotExist:
        return KYCVerificationResult(
            is_verified=False,
            kyc_tier=MemberKYCTier.UNVERIFIED,
            confidence_score=0.0,
            rejection_reason="KYC record not found",
            requires_manual_review=False,
            smile_job_id=None,
        )
    
    smile_job_id = f"smile_{kyc_id}_{timezone.now().strftime('%Y%m%d%H%M%S')}"
    
    if provider == "smile_identity":
        result = _verify_with_smile_identity(kyc)
    else:
        result = {"status": "manual_review", "confidence": 0.5}
    
    is_verified = result.get("status") == "verified"
    confidence = float(result.get("confidence", 0.0))
    
    rejection_reason = None
    requires_manual = result.get("status") == "manual_review"
    
    if result.get("status") == "rejected":
        rejection_reason = result.get("reason", "Identity verification failed")
        requires_manual = True
    
    new_status = (
        MemberKYCStatus.APPROVED
        if is_verified and not requires_manual
        else MemberKYCStatus.PENDING
        if requires_manual
        else MemberKYCStatus.REJECTED
    )
    
    new_tier = (
        MemberKYCTier.FULL
        if is_verified and confidence >= 0.85
        else MemberKYCTier.BASIC
        if is_verified
        else MemberKYCTier.UNVERIFIED
    )
    
    kyc.status = new_status
    kyc.kyc_tier = new_tier
    kyc.confidence_score = confidence
    kyc.verification_provider = provider
    kyc.external_job_id = smile_job_id
    kyc.verified_at = timezone.now()
    kyc.save()
    
    logger.info(
        "KYC verification result for %s: status=%s, tier=%s, confidence=%.2f",
        kyc_id,
        new_status,
        new_tier,
        confidence,
    )
    
    return KYCVerificationResult(
        is_verified=is_verified,
        kyc_tier=new_tier,
        confidence_score=confidence,
        rejection_reason=rejection_reason,
        requires_manual_review=requires_manual,
        smile_job_id=smile_job_id,
    )


def _verify_with_smile_identity(kyc) -> dict:
    """Verify KYC via Smile Identity API."""
    try:
        import requests
        
        api_key = getattr(settings, "SMILE_IDENTITY_API_KEY", "")
        app_id = getattr(settings, "SMILE_IDENTITY_APP_ID", "")
        
        if not api_key or not app_id:
            logger.warning("Smile Identity credentials not configured")
            return {"status": "manual_review", "confidence": 0.5}
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "job_type": 5,
            "nation_id": kyc.id_number or "",
            "selfie": kyc.selfie_url or "",
            "id_document": kyc.id_document_url or "",
        }
        
        response = requests.post(
            "https://api.smileidentity.com/v1/verification",
            json=payload,
            headers=headers,
            timeout=30,
        )
        
        if response.status_code == 200:
            data = response.json()
            return {
                "status": "verified" if data.get("verified") else "rejected",
                "confidence": float(data.get("confidence", 0.0)),
                "reason": data.get("reason"),
            }
        
        return {"status": "manual_review", "confidence": 0.5}
    
    except Exception as exc:
        logger.error(f"Smile Identity API error: {exc}")
        return {"status": "manual_review", "confidence": 0.5}


def unlock_kyc_access(user: "User", chama: "Chama") -> KYCAccessUpdate:
    """Unlock member access on KYC approval."""
    from apps.accounts.models import MemberKYC, MemberKYCTier
    from apps.chama.models import Membership, MemberStatus
    from apps.notifications.services import NotificationService
    
    try:
        kyc = MemberKYC.objects.get(user=user, chama=chama)
    except MemberKYC.DoesNotExist:
        return KYCAccessUpdate(
            user_id=str(user.id),
            tier_upgraded_to=MemberKYCTier.UNVERIFIED,
            permissions_granted=[],
            notification_sent=False,
        )
    
    kyc.status = MemberKYC.objects.model.status.field.remote_field.model.REJECTED
    new_tier = kyc.kyc_tier
    
    permissions = []
    if new_tier == MemberKYCTier.BASIC:
        permissions = ["view_chama", "make_contributions", "view_own_history"]
    elif new_tier == MemberKYCTier.FULL:
        permissions = [
            "view_chama",
            "make_contributions",
            "view_own_history",
            "apply_loans",
            "participate_votes",
            "view_attendance",
        ]
    
    try:
        membership = Membership.objects.get(user=user, chama=chama)
        membership.is_approved = True
        membership.status = MemberStatus.ACTIVE
        membership.save()
    except Membership.DoesNotExist:
        pass
    
    message = f"KYC verified! You now have {new_tier} access."
    if permissions:
        message += f" Permissions: {', '.join(permissions)}."
    
    try:
        NotificationService.send_notification(
            user=user,
            message=message,
            channels=["push", "sms"],
            notification_type="kyc",
            priority="high",
        )
        notification_sent = True
    except Exception:
        notification_sent = False
    
    logger.info("KYC access unlocked for user %s: tier=%s", user.id, new_tier)
    
    return KYCAccessUpdate(
        user_id=str(user.id),
        tier_upgraded_to=new_tier,
        permissions_granted=permissions,
        notification_sent=notification_sent,
    )


def notify_kyc_rejection(
    user: "User",
    chama: "Chama",
    reason: str,
    resubmission_allowed: bool = True,
) -> None:
    """Notify member of KYC rejection with specific reason."""
    from apps.notifications.services import NotificationService
    
    base_message = f"Your KYC verification was unsuccessful. Reason: {reason}."
    
    if resubmission_allowed:
        base_message += (
            " Please resubmit your documents with correct information. "
            "Ensure your ID is clearly visible and selfie matches your ID photo."
        )
    else:
        base_message += (
            " Manual review has been requested. "
            "Our team will review your documents within 48 hours."
        )
    
    try:
        NotificationService.send_notification(
            user=user,
            message=base_message,
            channels=["push", "sms"],
            notification_type="kyc",
            priority="high",
        )
    except Exception as exc:
        logger.error(f"Failed to send KYC rejection notification: {exc}")


def track_kyc_resubmission(
    kyc_id: str,
    submission_count: int | None = None,
) -> dict:
    """Track KYC resubmission attempts and escalate if needed."""
    from apps.accounts.models import MemberKYC
    
    try:
        kyc = MemberKYC.objects.get(id=kyc_id)
    except MemberKYC.DoesNotExist:
        return {"status": "not_found", "escalated": False}
    
    new_count = submission_count if submission_count is not None else (kyc.submission_count or 0) + 1
    kyc.submission_count = new_count
    kyc.save()
    
    escalated = new_count >= 3
    escalation_level = None
    
    if escalated:
        escalation_level = "system_admin"
        logger.warning(
            "KYC %s escalated to System Admin after %s failures",
            kyc_id,
            new_count,
        )
    
    return {
        "kyc_id": kyc_id,
        "submission_count": new_count,
        "escalated": escalated,
        "escalation_level": escalation_level,
        "can_auto_verify": new_count < 2,
    }


def get_kyc_queue(limit: int = 50) -> list[KYCQueueItem]:
    """Get KYC queue items needing manual review."""
    from apps.accounts.models import MemberKYC, MemberKYCStatus
    
    needs_review = MemberKYC.objects.filter(
        status=MemberKYCStatus.PENDING,
        submission_count__gte=2,
    ).select_related("user", "chama")[:limit]
    
    queue = []
    for kyc in needs_review:
        queue.append(KYCQueueItem(
            kyc_id=str(kyc.id),
            user_id=str(kyc.user_id),
            user_name=kyc.user.full_name if kyc.user else "Unknown",
            phone=kyc.user.phone if kyc.user else "",
            chama_id=str(kyc.chama_id),
            document_type=kyc.id_document_type or "unknown",
            submitted_at=kyc.updated_at.isoformat() if kyc.updated_at else "",
            resubmission_count=kyc.submission_count or 0,
            needs_manual_review=True,
        ))
    
    return queue


def check_kyc_expiry(days_before_expiry: int = 30) -> list[dict]:
    """Check for KYC documents expiring soon."""
    from apps.accounts.models import MemberKYC, MemberKYCStatus
    
    today = timezone.now().date()
    warning_date = today + timedelta(days=days_before_expiry)
    
    expiring = MemberKYC.objects.filter(
        status=MemberKYCStatus.APPROVED,
        expiry_date__lte=warning_date,
        expiry_date__gte=today,
    ).select_related("user", "chama")
    
    alerts = []
    for kyc in expiring:
        days_until = (kyc.expiry_date - today).days if kyc.expiry_date else 0
        alerts.append({
            "kyc_id": str(kyc.id),
            "user_id": str(kyc.user_id),
            "user_name": kyc.user.full_name if kyc.user else "Unknown",
            "chama_id": str(kyc.chama_id),
            "expiry_date": str(kyc.expiry_date),
            "days_until_expiry": days_until,
            "alert_level": "HIGH" if days_until <= 7 else "MEDIUM",
        })
    
    return alerts