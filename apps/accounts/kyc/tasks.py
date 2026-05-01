from __future__ import annotations

import json
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.accounts.kyc.notifications import notify_member_status, notify_system_admins
from apps.accounts.kyc.services import KYCWorkflowService, sync_user_access_state
from apps.accounts.models import MemberKYC, MemberKYCStatus, OTPToken, User
from apps.accounts.services import OTPService
from core.encryption import field_encryption_service


@shared_task
def cleanup_expired_otp_sessions():
    cutoff = timezone.now()
    deleted, _ = OTPToken.objects.filter(expires_at__lt=cutoff).delete()
    return {"deleted": deleted}


@shared_task
def process_kyc_submission(kyc_id: str):
    kyc = MemberKYC.objects.select_related("user").get(id=kyc_id)
    KYCWorkflowService.process_submission(kyc)
    return {"kyc_id": kyc_id, "status": kyc.status}


@shared_task
def provider_poll_status(kyc_id: str):
    kyc = MemberKYC.objects.get(id=kyc_id)
    return {"kyc_id": kyc_id, "provider_reference": kyc.auto_verification_reference}


@shared_task
def daily_sanctions_rescreen():
    screened = 0
    for kyc in MemberKYC.objects.select_related("user").filter(status=MemberKYCStatus.APPROVED):
        KYCWorkflowService.sanctions_rescreen_only(kyc_record=kyc)
        screened += 1
    return {"screened": screened}


@shared_task
def annual_renewal_reminders():
    today = timezone.localdate()
    target_date = today + timedelta(days=30)
    reminders = 0
    for kyc in MemberKYC.objects.select_related("user").filter(expires_at__date=target_date):
        notify_member_status(
            kyc,
            subject="KYC renewal reminder",
            message="Your KYC verification is due for renewal soon.",
        )
        reminders += 1
    return {"reminders": reminders}


@shared_task
def id_expiry_tracker():
    today = timezone.localdate()
    expiring = MemberKYC.objects.select_related("user").filter(id_expiry_date__lte=today + timedelta(days=30))
    count = 0
    for kyc in expiring:
        notify_member_status(
            kyc,
            subject="ID expiry warning",
            message="Your identification document is about to expire. Please update it to avoid interruptions.",
        )
        count += 1
    return {"expiring": count}


@shared_task
def unlock_access_after_kyc_approval(kyc_id: str):
    kyc = MemberKYC.objects.select_related("user").get(id=kyc_id)
    sync_user_access_state(kyc.user)
    return {"kyc_id": kyc_id, "status": kyc.status}


@shared_task
def schedule_retry_reminders():
    now = timezone.now()
    reminded = 0
    queryset = MemberKYC.objects.select_related("user").filter(
        status=MemberKYCStatus.RESUBMIT_REQUIRED,
        retry_allowed=True,
        updated_at__lte=now - timedelta(hours=6),
    )
    for kyc in queryset:
        notify_member_status(
            kyc,
            subject="KYC retry required",
            message="Your KYC was rejected. Please review the reason and resubmit.",
        )
        reminded += 1
    return {"reminded": reminded}


@shared_task
def provider_webhook_followup(kyc_id: str):
    kyc = MemberKYC.objects.select_related("user").get(id=kyc_id)
    payload: dict = {}
    if kyc.provider_result_encrypted:
        decrypted = field_encryption_service.decrypt(kyc.provider_result_encrypted)
        if decrypted:
            try:
                parsed = json.loads(decrypted)
                if isinstance(parsed, dict):
                    payload = parsed
            except Exception:  # noqa: BLE001
                payload = {}
    if not payload and isinstance(kyc.provider_result, dict):
        payload = kyc.provider_result
    if payload:
        KYCWorkflowService.process_provider_payload(kyc_record=kyc, payload=payload)
    return {"kyc_id": kyc_id, "status": kyc.status}


@shared_task
def phone_change_rekyc_trigger(user_id: str):
    user = User.objects.get(id=user_id)
    record = KYCWorkflowService.mark_rekyc_required(user=user, reason="Phone number changed.")
    return {"user_id": user_id, "kyc_id": str(record.id) if record else None}


@shared_task
def suspicious_activity_rekyc_trigger(user_id: str, reason: str = "Suspicious activity detected."):
    user = User.objects.get(id=user_id)
    record = KYCWorkflowService.mark_rekyc_required(user=user, reason=reason)
    return {"user_id": user_id, "kyc_id": str(record.id) if record else None}


@shared_task
def significant_account_change_rekyc_trigger(user_id: str, reason: str = "Significant account change detected."):
    user = User.objects.get(id=user_id)
    record = KYCWorkflowService.mark_rekyc_required(user=user, reason=reason)
    return {"user_id": user_id, "kyc_id": str(record.id) if record else None}


@shared_task
def stale_kyc_session_cleanup():
    cutoff = timezone.now() - timedelta(days=2)
    updated = MemberKYC.objects.filter(status=MemberKYCStatus.DRAFT, updated_at__lt=cutoff).update(status=MemberKYCStatus.RESUBMIT_REQUIRED)
    return {"updated": updated}
