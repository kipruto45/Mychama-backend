from __future__ import annotations

from dataclasses import dataclass


AUTO_APPROVE_MIN = 81
AUTO_REJECT_MAX = 69
MANUAL_REVIEW_MIN = 70
MANUAL_REVIEW_MAX = 80
MAX_RESUBMISSIONS_BEFORE_ESCALATION = 3


@dataclass(frozen=True)
class KYCDecisionOutcome:
    status: str
    code: str
    message: str
    freeze_account: bool = False
    escalate: bool = False
    retry_allowed: bool = False


def decide_kyc_outcome(
    *,
    confidence_score: int,
    duplicate_detected: bool,
    blacklist_flag: bool,
    pep_flag: bool,
    sanctions_flag: bool,
    submission_attempts: int,
) -> KYCDecisionOutcome:
    if sanctions_flag:
        return KYCDecisionOutcome(
            status="frozen",
            code="KYC_SANCTIONS_MATCH",
            message="Your account has been restricted due to a compliance check.",
            freeze_account=True,
            escalate=True,
        )

    if duplicate_detected:
        return KYCDecisionOutcome(
            status="rejected",
            code="KYC_DUPLICATE_IDENTITY",
            message="Your KYC was rejected. Please contact support for help.",
            escalate=True,
        )

    if blacklist_flag or pep_flag:
        return KYCDecisionOutcome(
            status="rejected",
            code="KYC_HIGH_RISK_MATCH",
            message="Your account is under compliance review.",
            escalate=True,
        )

    if confidence_score >= AUTO_APPROVE_MIN:
        return KYCDecisionOutcome(
            status="approved",
            code="KYC_AUTO_APPROVED",
            message="Your KYC has been approved. Full access is now unlocked.",
        )

    if MANUAL_REVIEW_MIN <= confidence_score <= MANUAL_REVIEW_MAX:
        return KYCDecisionOutcome(
            status="under_review",
            code="KYC_ESCALATED_REVIEW",
            message="Your account is under compliance review.",
            escalate=True,
        )

    retry_allowed = submission_attempts < MAX_RESUBMISSIONS_BEFORE_ESCALATION
    return KYCDecisionOutcome(
        status="resubmit_required" if retry_allowed else "under_review",
        code="KYC_RETRY_REQUIRED" if retry_allowed else "KYC_ESCALATED_AFTER_RETRIES",
        message=(
            "Your KYC was rejected. Please review the reason and resubmit."
            if retry_allowed
            else "Your account is under compliance review."
        ),
        retry_allowed=retry_allowed,
        escalate=not retry_allowed,
    )
