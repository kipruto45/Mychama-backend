from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import MemberKYC, MemberKYCTier, MemberKYCStatus
from apps.automations.domain_services import notify_kyc_result
from core.audit import create_audit_log

logger = logging.getLogger(__name__)


class KYCWebhookService:
    PROVIDER_SECRET_SETTINGS = {
        "smile": "SMILE_WEBHOOK_SECRET",
        "smile_identity": "SMILE_WEBHOOK_SECRET",
        "onfido": "ONFIDO_WEBHOOK_SECRET",
    }

    @staticmethod
    def _normalized_provider(provider: str | None) -> str:
        return str(provider or "generic").strip().lower() or "generic"

    @classmethod
    def _provider_secret(cls, provider: str | None) -> str:
        normalized = cls._normalized_provider(provider)
        setting_name = cls.PROVIDER_SECRET_SETTINGS.get(normalized, "KYC_WEBHOOK_SECRET")
        return str(getattr(settings, setting_name, "") or getattr(settings, "KYC_WEBHOOK_SECRET", "") or "").strip()

    @classmethod
    def verify_signature(
        cls,
        *,
        provider: str | None,
        payload_bytes: bytes,
        received_signature: str | None,
    ) -> bool:
        secret = cls._provider_secret(provider)
        if not secret:
            return True
        if not received_signature:
            return False
        expected = hmac.new(
            secret.encode("utf-8"),
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, received_signature)

    @staticmethod
    def _extract_reference(payload: dict) -> str:
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        candidates = [
            metadata.get("kyc_id"),
            metadata.get("reference_id"),
            payload.get("kyc_id"),
            payload.get("reference_id"),
            payload.get("applicant_id"),
            payload.get("job_id"),
            payload.get("user_id"),
            payload.get("external_user_id"),
        ]
        for value in candidates:
            text = str(value or "").strip()
            if text:
                return text
        return ""

    @staticmethod
    def _extract_status(payload: dict) -> tuple[str, str]:
        candidates = [
            payload.get("status"),
            payload.get("decision"),
            payload.get("result"),
            payload.get("outcome"),
            payload.get("review_result"),
            payload.get("event"),
        ]
        raw_status = ""
        for candidate in candidates:
            text = str(candidate or "").strip()
            if text:
                raw_status = text
                break

        normalized = raw_status.lower()
        approved_terms = {"approved", "pass", "passed", "verified", "clear", "completed", "success"}
        rejected_terms = {"rejected", "reject", "fail", "failed", "declined", "denied"}
        pending_terms = {"pending", "processing", "review", "manual_review"}

        if normalized in approved_terms or any(term in normalized for term in approved_terms):
            return MemberKYCStatus.APPROVED, raw_status
        if normalized in rejected_terms or any(term in normalized for term in rejected_terms):
            return MemberKYCStatus.REJECTED, raw_status
        if normalized in pending_terms or any(term in normalized for term in pending_terms):
            return MemberKYCStatus.PENDING, raw_status
        return MemberKYCStatus.PENDING, raw_status or "pending"

    @staticmethod
    def _extract_reason(payload: dict) -> str:
        candidates = [
            payload.get("reason"),
            payload.get("message"),
            payload.get("review_note"),
            payload.get("failure_reason"),
            payload.get("error"),
        ]
        for candidate in candidates:
            text = str(candidate or "").strip()
            if text:
                return text
        return ""

    @classmethod
    @transaction.atomic
    def process_callback(cls, *, provider: str | None, payload: dict) -> dict:
        provider_name = cls._normalized_provider(provider)
        reference = cls._extract_reference(payload)
        if not reference:
            return {"status": "ignored", "reason": "missing_reference"}

        reference_filters = models.Q(auto_verification_reference=reference)
        try:
            reference_filters |= models.Q(id=uuid.UUID(str(reference)))
        except (ValueError, TypeError, AttributeError):
            pass

        kyc = (
            MemberKYC.objects.select_related("user", "chama")
            .filter(reference_filters)
            .first()
        )
        if not kyc:
            return {"status": "ignored", "reason": "kyc_not_found", "reference": reference}

        resolved_status, provider_status = cls._extract_status(payload)
        reason = cls._extract_reason(payload)
        now = timezone.now()

        verification_payload = {
            "provider": provider_name,
            "provider_status": provider_status,
            "reason": reason,
            "received_at": now.isoformat(),
            "payload": payload,
        }
        update_fields = [
            "verification_result",
            "status",
            "review_note",
            "reviewed_at",
            "auto_verification_provider",
            "updated_at",
        ]
        kyc.verification_result = verification_payload
        kyc.status = resolved_status
        kyc.review_note = reason
        kyc.reviewed_at = now
        kyc.auto_verification_provider = provider_name
        if not kyc.auto_verification_reference:
            kyc.auto_verification_reference = reference
            update_fields.append("auto_verification_reference")

        if resolved_status == MemberKYCStatus.APPROVED:
            kyc.kyc_tier = MemberKYCTier.TIER_2
            kyc.verification_score = max(int(payload.get("score") or 81), 81)
            kyc.auto_verified_at = now
            update_fields.extend(["kyc_tier", "verification_score", "auto_verified_at"])
        elif resolved_status == MemberKYCStatus.REJECTED:
            kyc.kyc_tier = MemberKYCTier.TIER_0
            kyc.last_rejection_reason = reason
            kyc.rejection_attempts = (kyc.rejection_attempts or 0) + 1
            update_fields.extend(["kyc_tier", "last_rejection_reason", "rejection_attempts"])

        kyc.save(update_fields=update_fields)

        notify_kyc_result(kyc_record=kyc, actor=None)

        create_audit_log(
            actor=None,
            chama_id=kyc.chama_id,
            action="kyc_webhook_processed",
            entity_type="MemberKYC",
            entity_id=kyc.id,
            metadata={
                "provider": provider_name,
                "provider_status": provider_status,
                "resolved_status": resolved_status,
                "reference": reference,
                "reason": reason,
            },
        )

        logger.info(
            "Processed KYC webhook provider=%s kyc=%s status=%s",
            provider_name,
            kyc.id,
            resolved_status,
        )
        return {
            "status": "processed",
            "kyc_id": str(kyc.id),
            "resolved_status": resolved_status,
            "provider_status": provider_status,
        }

    @classmethod
    def dump_payload_for_audit(cls, payload: dict) -> str:
        try:
            return json.dumps(payload, sort_keys=True)
        except TypeError:
            return str(payload)
