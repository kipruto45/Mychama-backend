from __future__ import annotations

import base64
from dataclasses import dataclass

from apps.accounts.kyc_integration_service import (
    EnhancedKYCService,
    KYCDocumentType,
    SmileIdentityService,
)
from apps.accounts.models import MemberKYC


DOCUMENT_TYPE_MAP = {
    "national_id": KYCDocumentType.KENYA_NATIONAL_ID,
    "passport": KYCDocumentType.KENYA_PASSPORT,
    "alien_id": KYCDocumentType.ALIEN_ID,
    "military_id": KYCDocumentType.MILITARY_ID,
}


@dataclass
class SmileProviderResult:
    provider_reference: str
    provider_payload: dict
    provider_result: dict
    document_authentic: bool
    face_matched: bool
    liveness_passed: bool
    name_match: bool
    dob_match: bool
    id_number_valid: bool
    iprs_match: bool
    face_match_score: int


class SmileIdentityKYCProvider:
    @staticmethod
    def _read_base64(file_field) -> str | None:
        if not file_field:
            return None
        with file_field.open("rb") as handle:
            return base64.b64encode(handle.read()).decode("utf-8")

    @classmethod
    def submit_verification(cls, kyc_record: MemberKYC) -> SmileProviderResult:
        document_type = DOCUMENT_TYPE_MAP.get(
            kyc_record.document_type,
            KYCDocumentType.KENYA_NATIONAL_ID,
        )
        request = EnhancedKYCService.KYCVerificationRequest(
            user_id=str(kyc_record.user_id),
            chama_id=str(kyc_record.chama_id or "platform"),
            id_number=kyc_record.id_number,
            document_type=document_type,
            id_document_image=cls._read_base64(kyc_record.id_front_image) or "",
            id_back_image=cls._read_base64(kyc_record.id_back_image),
            selfie_image=cls._read_base64(kyc_record.selfie_image) or "",
            first_name=(kyc_record.legal_name or kyc_record.user.full_name).split(" ")[0],
            last_name=" ".join((kyc_record.legal_name or kyc_record.user.full_name).split(" ")[1:]),
            phone_number=kyc_record.phone_number or kyc_record.user.phone,
            mpesa_registered_name=kyc_record.mpesa_registered_name or None,
            proof_of_address=cls._read_base64(kyc_record.proof_of_address_image),
            location_latitude=float(kyc_record.location_latitude) if kyc_record.location_latitude is not None else None,
            location_longitude=float(kyc_record.location_longitude) if kyc_record.location_longitude is not None else None,
        )
        verification = EnhancedKYCService.verify_identity(request)
        raw_result = {
            "success": verification.success,
            "eligible_for_loans": verification.eligible_for_loans,
            "id_verified": verification.id_verified,
            "face_matched": verification.face_matched,
            "liveness_passed": verification.liveness_passed,
            "mpesa_name_matched": verification.mpesa_name_matched,
            "government_verified": verification.government_verified,
            "warnings": verification.warnings,
            "errors": verification.errors,
            "next_steps": verification.next_steps,
            "verification_level": verification.kyc_level.value,
            "reference_id": verification.reference_id,
            "provider": "smile_identity",
        }
        face_match_score = 95 if verification.face_matched else 25
        return SmileProviderResult(
            provider_reference=verification.reference_id,
            provider_payload={
                "document_type": kyc_record.document_type,
                "onboarding_path": kyc_record.onboarding_path,
            },
            provider_result=raw_result,
            document_authentic=verification.id_verified,
            face_matched=verification.face_matched,
            liveness_passed=verification.liveness_passed,
            name_match=verification.mpesa_name_matched or verification.id_verified,
            dob_match=verification.government_verified or verification.id_verified,
            id_number_valid=SmileIdentityService.verify_id_number(
                kyc_record.id_number,
                document_type,
            )["valid"],
            iprs_match=verification.government_verified,
            face_match_score=face_match_score,
        )

    @classmethod
    def parse_verification_payload(cls, kyc_record: MemberKYC, payload: dict) -> SmileProviderResult:
        """
        Best-effort parsing of webhook/poll provider payloads into SmileProviderResult.

        Supports:
        - Our own persisted provider_result structure (from submit_verification)
        - Smile Identity-style webhook payloads when mapped by integration layer
        """
        raw = payload if isinstance(payload, dict) else {}
        reference_id = str(
            raw.get("reference_id")
            or raw.get("referenceId")
            or raw.get("job_id")
            or raw.get("jobId")
            or kyc_record.auto_verification_reference
            or ""
        )

        document_authentic = bool(
            raw.get("id_verified")
            or raw.get("document_verified")
            or raw.get("document_authentic")
            or raw.get("document_authenticity")
        )
        face_matched = bool(raw.get("face_matched") or raw.get("faceMatched"))
        liveness_passed = bool(raw.get("liveness_passed") or raw.get("livenessPassed") or raw.get("liveness"))
        government_verified = bool(raw.get("government_verified") or raw.get("governmentVerified") or raw.get("iprs_match"))
        mpesa_match = bool(raw.get("mpesa_name_matched") or raw.get("mpesaNameMatched") or raw.get("name_match"))

        face_match_score_raw = raw.get("face_match_score") or raw.get("faceMatchScore")
        try:
            face_match_score = int(face_match_score_raw) if face_match_score_raw is not None else (95 if face_matched else 25)
        except Exception:  # noqa: BLE001
            face_match_score = 95 if face_matched else 25

        document_type = DOCUMENT_TYPE_MAP.get(
            kyc_record.document_type,
            KYCDocumentType.KENYA_NATIONAL_ID,
        )

        id_number_valid = SmileIdentityService.verify_id_number(
            kyc_record.id_number,
            document_type,
        )["valid"]

        provider_result = raw
        if "provider" not in provider_result:
            provider_result = {**provider_result, "provider": "smile_identity"}

        return SmileProviderResult(
            provider_reference=reference_id,
            provider_payload={
                "document_type": kyc_record.document_type,
                "onboarding_path": kyc_record.onboarding_path,
                "source": "webhook_or_poll",
            },
            provider_result=provider_result,
            document_authentic=document_authentic,
            face_matched=face_matched,
            liveness_passed=liveness_passed,
            name_match=mpesa_match or document_authentic,
            dob_match=government_verified or document_authentic,
            id_number_valid=id_number_valid,
            iprs_match=government_verified,
            face_match_score=face_match_score,
        )
