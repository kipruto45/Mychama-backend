from __future__ import annotations

import json
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.accounts.kyc.audit import log_kyc_event
from apps.accounts.kyc import notifications as kyc_notifications
from apps.accounts.kyc.providers.smile_identity import SmileIdentityKYCProvider
from apps.accounts.kyc.rules import decide_kyc_outcome
from apps.accounts.kyc.scoring import compute_confidence_score
from apps.accounts.models import (
    AccessTier,
    MemberKYC,
    MemberKYCDocumentType,
    MemberKYCStatus,
    MemberKYCTier,
    User,
    UserKYCState,
)
from apps.accounts.services import KYCService

from core.encryption import field_encryption_service

logger = logging.getLogger(__name__)

REJECTION_REASON_MAP = {
    "quality_front": "Your ID image is blurry. Please retake it in better lighting.",
    "quality_back": "Your ID image is blurry. Please retake it in better lighting.",
    "liveness": "Your selfie did not pass liveness verification. Please try again.",
    "duplicate": "Your KYC was rejected. Please contact support for help.",
    "high_risk": "Your account is under compliance review.",
    "sanctions": "Your account has been restricted due to a compliance check.",
    "retry": "Your KYC was rejected. Please review the reason and resubmit.",
}


def _latest_kyc_record(user: User) -> MemberKYC | None:
    platform = (
        MemberKYC.objects.filter(user=user, chama__isnull=True)
        .order_by("-approved_at", "-processed_at", "-updated_at", "-created_at")
        .first()
    )
    if platform:
        return platform
    return (
        MemberKYC.objects.filter(user=user)
        .order_by("-approved_at", "-processed_at", "-updated_at", "-created_at")
        .first()
    )


def sync_user_access_state(user: User) -> User:
    kyc = _latest_kyc_record(user)
    update_fields: list[str] = []

    user.otp_verified = bool(user.phone_verified)
    update_fields.append("otp_verified")

    if user.account_frozen or (kyc and kyc.account_frozen_for_compliance):
        user.account_frozen = True
        user.tier_access = AccessTier.RESTRICTED
        user.kyc_status = UserKYCState.FROZEN
        user.financial_access_enabled = False
        update_fields.extend(["account_frozen", "tier_access", "kyc_status", "financial_access_enabled"])
    elif kyc and kyc.requires_reverification:
        user.tier_access = AccessTier.TIER_0_VIEW_ONLY if user.phone_verified else AccessTier.UNVERIFIED
        user.kyc_status = UserKYCState.REKYC_REQUIRED if user.phone_verified else UserKYCState.NOT_STARTED
        user.financial_access_enabled = False
        update_fields.extend(["tier_access", "kyc_status", "financial_access_enabled"])
    elif kyc and kyc.status == MemberKYCStatus.APPROVED:
        user.tier_access = AccessTier.TIER_2_FULL
        user.kyc_status = UserKYCState.APPROVED
        user.kyc_verified_at = kyc.approved_at or kyc.auto_verified_at or timezone.now()
        user.financial_access_enabled = True
        update_fields.extend(["tier_access", "kyc_status", "kyc_verified_at", "financial_access_enabled"])
    elif user.phone_verified:
        user.tier_access = AccessTier.TIER_0_VIEW_ONLY
        if kyc and kyc.status in {
            MemberKYCStatus.PENDING,
            MemberKYCStatus.QUEUED,
            MemberKYCStatus.PROCESSING,
        }:
            user.kyc_status = UserKYCState.PENDING
        elif kyc and kyc.status == MemberKYCStatus.UNDER_REVIEW:
            user.kyc_status = UserKYCState.UNDER_REVIEW
        elif kyc and kyc.status in {MemberKYCStatus.REJECTED, MemberKYCStatus.RESUBMIT_REQUIRED}:
            user.kyc_status = UserKYCState.REJECTED
        else:
            user.kyc_status = UserKYCState.NOT_STARTED
        user.financial_access_enabled = False
        update_fields.extend(["tier_access", "kyc_status", "financial_access_enabled"])
    else:
        user.tier_access = AccessTier.UNVERIFIED
        user.kyc_status = UserKYCState.NOT_STARTED
        user.kyc_verified_at = None
        user.financial_access_enabled = False
        update_fields.extend(["tier_access", "kyc_status", "kyc_verified_at", "financial_access_enabled"])

    if user.account_locked_until and user.account_locked_until > timezone.now():
        user.locked_until = user.account_locked_until
        update_fields.append("locked_until")

    user.save(update_fields=list(dict.fromkeys(update_fields)))
    return user


class KYCWorkflowService:
    @staticmethod
    def _sanitize_provider_result(raw: dict) -> dict:
        """
        Persist only non-sensitive provider summaries in plain JSON.
        Full payloads must go to encrypted fields.
        """
        if not isinstance(raw, dict):
            return {}
        allowed_keys = {
            "success",
            "eligible_for_loans",
            "id_verified",
            "face_matched",
            "liveness_passed",
            "mpesa_name_matched",
            "government_verified",
            "warnings",
            "errors",
            "next_steps",
            "verification_level",
            "reference_id",
            "provider",
        }
        return {key: raw.get(key) for key in allowed_keys if key in raw}

    @staticmethod
    def _encrypt_json_payload(payload: dict) -> str:
        try:
            serialized = json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=False)
        except Exception:  # noqa: BLE001
            serialized = json.dumps({"_error": "serialization_failed"})
        return field_encryption_service.encrypt(serialized)

    @staticmethod
    def _flag_duplicate_records(*, kyc_record: MemberKYC, duplicates_qs) -> None:
        for dup in duplicates_qs.select_related("user")[:10]:
            dup.duplicate_id_detected = True
            dup.requires_reverification = True
            dup.reverification_reason = "Duplicate identity detected."
            dup.retry_allowed = False
            if dup.status == MemberKYCStatus.APPROVED:
                dup.status = MemberKYCStatus.UNDER_REVIEW
            dup.review_reason = "duplicate_identity"
            dup.review_note = "Duplicate identity detected. Compliance investigation required."
            dup.save(
                update_fields=[
                    "duplicate_id_detected",
                    "requires_reverification",
                    "reverification_reason",
                    "retry_allowed",
                    "status",
                    "review_reason",
                    "review_note",
                    "updated_at",
                ]
            )
            log_kyc_event(
                kyc_record=dup,
                event_type="duplicate_detected",
                code="KYC_DUPLICATE_IDENTITY",
                message="Duplicate identity detected.",
                actor=kyc_record.user,
                metadata={"duplicate_with_kyc_id": str(kyc_record.id)},
            )
            kyc_notifications.notify_system_admins(
                dup,
                subject="Duplicate identity alert",
                message=f"Duplicate identity detected for ID number on user {dup.user.phone}.",
            )
            sync_user_access_state(dup.user)

    @staticmethod
    def _apply_decision(
        *,
        kyc_record: MemberKYC,
        decision,
        confidence_score: int,
        duplicate_detected: bool,
        screening: dict,
        provider_result,
    ) -> MemberKYC:
        user = kyc_record.user

        kyc_record.provider = "smile_identity"
        kyc_record.provider_payload = provider_result.provider_payload
        kyc_record.provider_result = KYCWorkflowService._sanitize_provider_result(provider_result.provider_result)
        kyc_record.provider_result_encrypted = KYCWorkflowService._encrypt_json_payload(provider_result.provider_result)
        kyc_record.verification_result = kyc_record.provider_result
        kyc_record.verification_result_encrypted = kyc_record.provider_result_encrypted
        kyc_record.auto_verification_provider = "smile_identity"
        kyc_record.auto_verification_reference = provider_result.provider_reference
        kyc_record.face_match_score = provider_result.face_match_score
        kyc_record.liveness_passed = provider_result.liveness_passed
        kyc_record.duplicate_id_detected = duplicate_detected
        kyc_record.pep_match = screening["pep_match"]
        kyc_record.sanctions_match = screening["sanctions_match"]
        kyc_record.blacklist_match = screening["blacklist_match"]
        kyc_record.iprs_match_status = "matched" if provider_result.iprs_match else "not_matched"
        kyc_record.confidence_score = confidence_score
        kyc_record.verification_score = confidence_score
        kyc_record.retry_allowed = decision.retry_allowed
        kyc_record.review_reason = decision.code.lower()

        if decision.status == "approved":
            kyc_record.status = MemberKYCStatus.APPROVED
            kyc_record.kyc_tier = MemberKYCTier.TIER_2
            kyc_record.approved_at = timezone.now()
            kyc_record.auto_verified_at = timezone.now()
            kyc_record.review_note = decision.message
            user.account_frozen = False
            kyc_notifications.notify_member_status(kyc_record, subject="KYC approved", message=decision.message)
            log_kyc_event(kyc_record=kyc_record, event_type="approved", code=decision.code, message=decision.message, actor=user)
        elif decision.status == "under_review":
            kyc_record.status = MemberKYCStatus.UNDER_REVIEW
            kyc_record.kyc_tier = MemberKYCTier.TIER_0
            kyc_record.review_note = decision.message
            kyc_notifications.notify_member_status(kyc_record, subject="KYC review", message=decision.message)
            kyc_notifications.notify_system_admins(kyc_record, subject="KYC escalation", message=f"{user.phone} requires investigation.")
            log_kyc_event(kyc_record=kyc_record, event_type="escalated", code=decision.code, message=decision.message, actor=user)
        elif decision.status == "frozen":
            kyc_record.status = MemberKYCStatus.FROZEN
            kyc_record.kyc_tier = MemberKYCTier.TIER_0
            kyc_record.account_frozen_for_compliance = True
            kyc_record.rejected_at = timezone.now()
            kyc_record.review_note = decision.message
            user.account_frozen = True
            kyc_notifications.notify_member_status(kyc_record, subject="Account restricted", message=decision.message)
            kyc_notifications.notify_system_admins(kyc_record, subject="Sanctions alert", message=f"{user.phone} has been frozen after sanctions screening.")
            log_kyc_event(kyc_record=kyc_record, event_type="frozen", code=decision.code, message=decision.message, actor=user)
        else:
            kyc_record.status = MemberKYCStatus.RESUBMIT_REQUIRED if decision.retry_allowed else MemberKYCStatus.UNDER_REVIEW
            kyc_record.kyc_tier = MemberKYCTier.TIER_0
            kyc_record.rejected_at = timezone.now()
            kyc_record.review_note = decision.message
            # Prefer actionable member-facing reasons without leaking provider details.
            if not provider_result.liveness_passed:
                kyc_record.last_rejection_reason = REJECTION_REASON_MAP["liveness"]
            elif duplicate_detected:
                kyc_record.last_rejection_reason = REJECTION_REASON_MAP["duplicate"]
            elif screening["pep_match"] or screening["blacklist_match"]:
                kyc_record.last_rejection_reason = REJECTION_REASON_MAP["high_risk"]
            else:
                kyc_record.last_rejection_reason = decision.message
            kyc_record.rejection_attempts += 1
            kyc_notifications.notify_member_status(kyc_record, subject="KYC update", message=kyc_record.last_rejection_reason or decision.message)
            if decision.escalate:
                kyc_notifications.notify_system_admins(kyc_record, subject="KYC escalation", message=f"{user.phone} exceeded automated retry thresholds.")
            log_kyc_event(kyc_record=kyc_record, event_type="rejected", code=decision.code, message=decision.message, actor=user)

        if kyc_record.rejection_attempts >= 3 and not kyc_record.escalated_to_system_admin_at:
            kyc_record.escalated_to_system_admin_at = timezone.now()

        kyc_record.processed_at = timezone.now()
        kyc_record.last_sanctions_screened_at = timezone.now()
        kyc_record.last_sanctions_screening_result = {
            "pep_match": screening["pep_match"],
            "blacklist_match": screening["blacklist_match"],
            "sanctions_match": screening["sanctions_match"],
        }
        if decision.status == "approved":
            kyc_record.requires_reverification = False
            kyc_record.reverification_reason = ""
            kyc_record.next_reverification_due_at = timezone.localdate() + timedelta(days=365)
        kyc_record.save()
        sync_user_access_state(user)
        return kyc_record

    @staticmethod
    @transaction.atomic
    def start_session(*, user: User, onboarding_path: str, chama_id: str | None = None) -> MemberKYC:
        kyc, _created = MemberKYC.objects.get_or_create(
            user=user,
            chama_id=chama_id,
            defaults={
                "provider": "smile_identity",
                "onboarding_path": onboarding_path,
                "status": MemberKYCStatus.DRAFT,
                "phone_number": user.phone,
                "legal_name": user.full_name,
            },
        )
        kyc.onboarding_path = onboarding_path
        kyc.phone_number = user.phone
        kyc.legal_name = kyc.legal_name or user.full_name
        kyc.status = kyc.status or MemberKYCStatus.DRAFT
        kyc.save(update_fields=["onboarding_path", "phone_number", "legal_name", "status", "updated_at"])
        log_kyc_event(
            kyc_record=kyc,
            event_type="started",
            code="KYC_SESSION_STARTED",
            message="KYC session started.",
            actor=user,
            metadata={"onboarding_path": onboarding_path},
        )
        sync_user_access_state(user)
        return kyc

    @staticmethod
    @transaction.atomic
    def update_profile(kyc_record: MemberKYC, *, payload: dict) -> MemberKYC:
        for field in ["legal_name", "date_of_birth", "gender", "nationality", "location_label"]:
            if field in payload:
                setattr(kyc_record, field, payload[field])
        if "document_type" in payload:
            kyc_record.document_type = payload["document_type"]
        if "id_number" in payload:
            kyc_record.id_number = payload["id_number"]
        if "phone_number" in payload:
            kyc_record.phone_number = payload["phone_number"]
        # New-style keys (kept for forward/backward compatibility)
        if "latitude" in payload:
            kyc_record.location_latitude = payload["latitude"]
        if "longitude" in payload:
            kyc_record.location_longitude = payload["longitude"]
        if "location_latitude" in payload:
            kyc_record.location_latitude = payload["location_latitude"]
        if "location_longitude" in payload:
            kyc_record.location_longitude = payload["location_longitude"]
        if "chama_id" in payload:
            kyc_record.chama_id = payload["chama_id"]
        kyc_record.save()
        return kyc_record

    @staticmethod
    def attach_document(kyc_record: MemberKYC, *, field_name: str, upload) -> tuple[MemberKYC, list[str], dict]:
        mime_ok, mime_error = KYCService.validate_id_image(upload, field_name=field_name.replace("_", " ").title())
        if not mime_ok:
            return kyc_record, [mime_error], {}

        valid, errors, metrics = KYCService.assess_image_quality(upload, field_name=field_name.replace("_", " ").title())
        setattr(kyc_record, field_name, upload)
        if field_name == "id_front_image":
            kyc_record.quality_front_passed = valid
        if field_name == "id_back_image":
            kyc_record.quality_back_passed = valid
        kyc_record.save()
        log_kyc_event(
            kyc_record=kyc_record,
            event_type="document_uploaded",
            code="KYC_DOCUMENT_UPLOADED",
            message=f"{field_name} uploaded.",
            actor=kyc_record.user,
            metadata={"field_name": field_name, "quality_passed": valid, "metrics": metrics},
        )
        return kyc_record, errors, metrics

    @staticmethod
    @transaction.atomic
    def submit(kyc_record: MemberKYC) -> MemberKYC:
        kyc_record.status = MemberKYCStatus.QUEUED
        kyc_record.submitted_at = timezone.now()
        kyc_record.last_submitted_at = timezone.now()
        kyc_record.expires_at = timezone.now() + timedelta(days=365)
        kyc_record.submission_attempts += 1
        kyc_record.save(update_fields=["status", "submitted_at", "last_submitted_at", "expires_at", "submission_attempts", "updated_at"])
        log_kyc_event(
            kyc_record=kyc_record,
            event_type="submitted",
            code="KYC_SUBMITTED",
            message="Your KYC documents have been submitted.",
            actor=kyc_record.user,
        )
        return kyc_record

    @staticmethod
    @transaction.atomic
    def process_submission(kyc_record: MemberKYC, *, force: bool = False) -> MemberKYC:
        user = kyc_record.user
        if not force and kyc_record.status in {
            MemberKYCStatus.APPROVED,
            MemberKYCStatus.FROZEN,
            MemberKYCStatus.UNDER_REVIEW,
        }:
            sync_user_access_state(user)
            return kyc_record

        kyc_record.status = MemberKYCStatus.PROCESSING
        kyc_record.processed_at = timezone.now()
        kyc_record.phone_number = kyc_record.phone_number or user.phone
        kyc_record.legal_name = kyc_record.legal_name or user.full_name
        kyc_record.save()
        log_kyc_event(
            kyc_record=kyc_record,
            event_type="processing",
            code="KYC_QUEUED",
            message="We are verifying your information.",
            actor=user,
        )

        if not user.phone_verified:
            kyc_record.status = MemberKYCStatus.RESUBMIT_REQUIRED
            kyc_record.last_rejection_reason = "Phone verification is required before KYC submission."
            kyc_record.review_reason = "otp_required"
            kyc_record.retry_allowed = True
            kyc_record.save(update_fields=["status", "last_rejection_reason", "review_reason", "retry_allowed", "updated_at"])
            sync_user_access_state(user)
            return kyc_record

        front_ok = bool(kyc_record.id_front_image) and kyc_record.quality_front_passed
        back_required = kyc_record.document_type != MemberKYCDocumentType.PASSPORT
        back_ok = (not back_required) or (bool(kyc_record.id_back_image) and kyc_record.quality_back_passed)
        selfie_ok = bool(kyc_record.selfie_image)
        if not front_ok or not back_ok or not selfie_ok:
            kyc_record.status = MemberKYCStatus.RESUBMIT_REQUIRED
            kyc_record.retry_allowed = kyc_record.rejection_attempts < 2
            kyc_record.review_reason = "quality_front" if not front_ok else "quality_back" if not back_ok else "liveness"
            kyc_record.last_rejection_reason = REJECTION_REASON_MAP[kyc_record.review_reason]
            kyc_record.rejected_at = timezone.now()
            kyc_record.rejection_attempts += 1
            kyc_record.save()
            log_kyc_event(
                kyc_record=kyc_record,
                event_type="quality_failed",
                code="KYC_QUALITY_FAILED",
                message=kyc_record.last_rejection_reason,
                actor=user,
            )
            kyc_notifications.notify_member_status(kyc_record, subject="KYC update", message=kyc_record.last_rejection_reason)
            sync_user_access_state(user)
            return kyc_record

        screening = KYCService.run_screening_checks(user=user, id_number=kyc_record.id_number)
        duplicates = (
            MemberKYC.objects.filter(
                id_number=kyc_record.id_number,
                document_type=kyc_record.document_type,
            )
            .exclude(user=user)
            .exclude(id=kyc_record.id)
        )
        duplicate_detected = duplicates.exists()
        if duplicate_detected:
            KYCWorkflowService._flag_duplicate_records(kyc_record=kyc_record, duplicates_qs=duplicates)

        try:
            provider_result = SmileIdentityKYCProvider.submit_verification(kyc_record)
        except Exception as exc:  # noqa: BLE001
            logger.exception("KYC provider submission failed kyc_id=%s user_id=%s", kyc_record.id, user.id)
            kyc_record.status = MemberKYCStatus.UNDER_REVIEW
            kyc_record.review_reason = "provider_error"
            kyc_record.review_note = "Your account is under compliance review."
            kyc_record.save(update_fields=["status", "review_reason", "review_note", "updated_at"])
            kyc_notifications.notify_member_status(kyc_record, subject="KYC update", message="We are verifying your information.")
            kyc_notifications.notify_system_admins(kyc_record, subject="KYC provider error", message=f"Provider submission failed for {user.phone}: {type(exc).__name__}")
            sync_user_access_state(user)
            return kyc_record

        confidence_score = compute_confidence_score(
            document_authentic=provider_result.document_authentic,
            face_matched=provider_result.face_matched,
            liveness_passed=provider_result.liveness_passed,
            name_match=provider_result.name_match,
            dob_match=provider_result.dob_match,
            id_number_valid=provider_result.id_number_valid,
            iprs_match=provider_result.iprs_match,
            duplicate_detected=duplicate_detected,
            pep_flag=screening["pep_match"],
            blacklist_flag=screening["blacklist_match"],
            sanctions_flag=screening["sanctions_match"],
            quality_front_passed=kyc_record.quality_front_passed,
            quality_back_passed=kyc_record.quality_back_passed or kyc_record.document_type == MemberKYCDocumentType.PASSPORT,
            face_match_score=provider_result.face_match_score,
        )
        decision = decide_kyc_outcome(
            confidence_score=confidence_score,
            duplicate_detected=duplicate_detected,
            blacklist_flag=screening["blacklist_match"],
            pep_flag=screening["pep_match"],
            sanctions_flag=screening["sanctions_match"],
            submission_attempts=kyc_record.rejection_attempts + 1,
        )
        return KYCWorkflowService._apply_decision(
            kyc_record=kyc_record,
            decision=decision,
            confidence_score=confidence_score,
            duplicate_detected=duplicate_detected,
            screening=screening,
            provider_result=provider_result,
        )

    @staticmethod
    @transaction.atomic
    def process_provider_payload(*, kyc_record: MemberKYC, payload: dict) -> MemberKYC:
        """
        Apply a provider webhook/poll payload without resubmitting verification.
        """
        user = kyc_record.user
        screening = KYCService.run_screening_checks(user=user, id_number=kyc_record.id_number)
        duplicates = (
            MemberKYC.objects.filter(
                id_number=kyc_record.id_number,
                document_type=kyc_record.document_type,
            )
            .exclude(user=user)
            .exclude(id=kyc_record.id)
        )
        duplicate_detected = duplicates.exists()
        if duplicate_detected:
            KYCWorkflowService._flag_duplicate_records(kyc_record=kyc_record, duplicates_qs=duplicates)

        provider_result = SmileIdentityKYCProvider.parse_verification_payload(kyc_record, payload)
        confidence_score = compute_confidence_score(
            document_authentic=provider_result.document_authentic,
            face_matched=provider_result.face_matched,
            liveness_passed=provider_result.liveness_passed,
            name_match=provider_result.name_match,
            dob_match=provider_result.dob_match,
            id_number_valid=provider_result.id_number_valid,
            iprs_match=provider_result.iprs_match,
            duplicate_detected=duplicate_detected,
            pep_flag=screening["pep_match"],
            blacklist_flag=screening["blacklist_match"],
            sanctions_flag=screening["sanctions_match"],
            quality_front_passed=kyc_record.quality_front_passed,
            quality_back_passed=kyc_record.quality_back_passed or kyc_record.document_type == MemberKYCDocumentType.PASSPORT,
            face_match_score=provider_result.face_match_score,
        )
        decision = decide_kyc_outcome(
            confidence_score=confidence_score,
            duplicate_detected=duplicate_detected,
            blacklist_flag=screening["blacklist_match"],
            pep_flag=screening["pep_match"],
            sanctions_flag=screening["sanctions_match"],
            submission_attempts=kyc_record.rejection_attempts + 1,
        )
        return KYCWorkflowService._apply_decision(
            kyc_record=kyc_record,
            decision=decision,
            confidence_score=confidence_score,
            duplicate_detected=duplicate_detected,
            screening=screening,
            provider_result=provider_result,
        )

    @staticmethod
    @transaction.atomic
    def sanctions_rescreen_only(*, kyc_record: MemberKYC) -> MemberKYC:
        """
        Daily sanctions/blacklist/PEP rescreen without re-running provider verification.
        """
        if kyc_record.status != MemberKYCStatus.APPROVED:
            return kyc_record
        screening = KYCService.run_screening_checks(user=kyc_record.user, id_number=kyc_record.id_number)
        kyc_record.last_sanctions_screened_at = timezone.now()
        kyc_record.last_sanctions_screening_result = screening
        kyc_record.sanctions_match = screening.get("sanctions_match", False)
        kyc_record.pep_match = screening.get("pep_match", False)
        kyc_record.blacklist_match = screening.get("blacklist_match", False)

        if kyc_record.sanctions_match:
            kyc_record.status = MemberKYCStatus.FROZEN
            kyc_record.account_frozen_for_compliance = True
            kyc_record.review_reason = "sanctions_rescreen"
            kyc_record.review_note = "Sanctions screening triggered a restriction."
            kyc_record.user.account_frozen = True
            kyc_notifications.notify_member_status(
                kyc_record,
                subject="Account restricted",
                message="Your account has been restricted due to a compliance check.",
            )
            kyc_notifications.notify_system_admins(
                kyc_record,
                subject="Sanctions alert",
                message=f"{kyc_record.user.phone} has been frozen after daily sanctions screening.",
            )
            log_kyc_event(
                kyc_record=kyc_record,
                event_type="sanctions_flagged",
                code="KYC_SANCTIONS_MATCH",
                message="Sanctions screening flagged this account.",
                actor=kyc_record.user,
                metadata={"screening": screening},
            )
        elif kyc_record.blacklist_match or kyc_record.pep_match:
            kyc_record.requires_reverification = True
            kyc_record.reverification_reason = "Compliance rescreen required."
            kyc_record.review_reason = "compliance_rescreen"
            kyc_record.review_note = "Compliance rescreen required."
            kyc_record.user.financial_access_enabled = False

        kyc_record.save(
            update_fields=[
                "last_sanctions_screened_at",
                "last_sanctions_screening_result",
                "sanctions_match",
                "pep_match",
                "blacklist_match",
                "status",
                "account_frozen_for_compliance",
                "requires_reverification",
                "reverification_reason",
                "review_reason",
                "review_note",
                "updated_at",
            ]
        )
        kyc_record.user.save(update_fields=["account_frozen", "financial_access_enabled"])
        sync_user_access_state(kyc_record.user)
        return kyc_record

    @staticmethod
    @transaction.atomic
    def mark_rekyc_required(*, user: User, reason: str) -> MemberKYC | None:
        kyc_record = _latest_kyc_record(user)
        if not kyc_record:
            return None
        kyc_record.requires_reverification = True
        kyc_record.reverification_reason = reason
        kyc_record.next_reverification_due_at = timezone.localdate()
        kyc_record.last_rekyc_at = timezone.now()
        kyc_record.status = MemberKYCStatus.RESUBMIT_REQUIRED
        kyc_record.kyc_tier = MemberKYCTier.TIER_0
        kyc_record.retry_allowed = True
        kyc_record.save()
        log_kyc_event(
            kyc_record=kyc_record,
            event_type="rekyc_triggered",
            code="KYC_REKYC_TRIGGERED",
            message="Re-verification required.",
            actor=user,
            metadata={"reason": reason},
        )
        kyc_notifications.notify_member_status(kyc_record, subject="Re-verification required", message="Please complete KYC again to keep full access.")
        sync_user_access_state(user)
        return kyc_record
