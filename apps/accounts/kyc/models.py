from apps.accounts.models import (
    AccessTier,
    KYCEvent,
    MemberKYC,
    MemberKYCDocumentType,
    MemberKYCStatus,
    MemberKYCTier,
    UserKYCState,
)

KYCVerification = MemberKYC

__all__ = [
    "AccessTier",
    "KYCEvent",
    "KYCVerification",
    "MemberKYCDocumentType",
    "MemberKYCStatus",
    "MemberKYCTier",
    "UserKYCState",
]
