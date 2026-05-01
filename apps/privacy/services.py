"""
Privacy Service

Implements data subject rights and consent management.
"""

import json
import logging
import secrets
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import MemberKYC, User

from .models import (
    ConsentCategory,
    ConsentStatus,
    DataSubjectRequest,
    DataSubjectRequestStatus,
    DataSubjectRequestType,
    PIIAccessEvent,
    PolicyAcceptance,
    PrivacyPolicyVersion,
    RetentionPolicy,
    RetentionSchedule,
    UserConsent,
)

logger = logging.getLogger(__name__)


class PrivacyService:
    """Service for privacy and data protection operations."""

    REQUEST_EXPIRY_DAYS = 30

    @staticmethod
    @transaction.atomic
    def record_consent(
        user: User,
        category: str,
        granted: bool,
        ip_address: str = None,
        user_agent: str = "",
        reason: str = "",
    ) -> UserConsent:
        """Record user consent for a category."""
        policy = PrivacyService._get_current_policy_version()

        consent, created = UserConsent.objects.update_or_create(
            user=user,
            category=category,
            defaults={
                "policy_version": policy.version if policy else "1.0",
            },
        )

        if granted:
            consent.grant(ip_address=ip_address, user_agent=user_agent)
            logger.info(f"Consent granted: {user.id} - {category}")
        else:
            consent.deny(reason=reason, ip_address=ip_address)
            logger.info(f"Consent denied: {user.id} - {category}")

        return consent

    @staticmethod
    def withdraw_consent(
        user: User,
        category: str,
        reason: str = "",
        ip_address: str = None,
    ) -> UserConsent:
        """Withdraw user consent."""
        try:
            consent = UserConsent.objects.get(user=user, category=category)
            consent.withdraw(reason=reason, ip_address=ip_address)
            logger.info(f"Consent withdrawn: {user.id} - {category}")
            return consent
        except UserConsent.DoesNotExist:
            logger.warning(f"Consent not found for withdrawal: {user.id} - {category}")
            return None

    @staticmethod
    def get_user_consents(user: User) -> dict:
        """Get all consents for a user."""
        consents = UserConsent.objects.filter(user=user)
        return {
            c.category: {
                "status": c.status,
                "granted_at": c.granted_at.isoformat() if c.granted_at else None,
                "withdrawn_at": c.withdrawn_at.isoformat() if c.withdrawn_at else None,
                "policy_version": c.policy_version,
            }
            for c in consents
        }

    @staticmethod
    @transaction.atomic
    def create_data_request(
        user: User,
        request_type: str,
        description: str = "",
    ) -> DataSubjectRequest:
        """Create a data subject rights request."""
        request = DataSubjectRequest.objects.create(
            user=user,
            request_type=request_type,
            description=description,
            request_token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timedelta(days=PrivacyService.REQUEST_EXPIRY_DAYS),
            requested_data={
                "request_type": request_type,
                "description": description,
            },
        )
        logger.info(f"Data request created: {request.id} - {request_type}")
        return request

    @staticmethod
    @transaction.atomic
    def process_access_request(request: DataSubjectRequest) -> dict:
        """Process a data access request."""
        request.start_processing()

        user = request.user
        data = {
            "profile": {
                "phone": PrivacyService._mask_phone(user.phone),
                "full_name": user.full_name,
                "email": PrivacyService._mask_email(user.email) if user.email else None,
                "date_joined": user.date_joined.isoformat(),
            },
            "consents": PrivacyService.get_user_consents(user),
            "kyc_status": PrivacyService._get_kyc_status(user),
            "financial_summary": PrivacyService._get_financial_summary(user),
        }

        request.complete(data)
        return data

    @staticmethod
    @transaction.atomic
    def process_deletion_request(request: DataSubjectRequest) -> dict:
        """Process a data deletion request with exceptions."""
        request.start_processing()

        user = request.user
        exceptions = []

        from apps.finance.models import Loan, Payment

        has_active_loans = Loan.objects.filter(
            member__user=user,
            status__in=[Loan.LoanStatus.ACTIVE, Loan.LoanStatus.DISBURSED],
        ).exists()

        if has_active_loans:
            request.financial_exception = True
            exceptions.append("Active loan - financial records retained per legal requirement")

        has_recent_kyc = MemberKYC.objects.filter(
            user=user,
            created_at__gte=timezone.now() - timedelta(days=365 * 5),
        ).exists()

        if has_recent_kyc:
            request.kyc_exception = True
            exceptions.append("KYC documents retained per AML regulations")

        request.save()

        data = {
            "status": "partial_completion",
            "exceptions": exceptions,
            "message": "Some data retained due to legal obligations",
        }

        request.complete(data)
        return data

    @staticmethod
    def log_pii_access(
        user: User,
        target_user: User,
        access_type: str,
        fields: list,
        purpose: str = "",
        ip_address: str = None,
        user_agent: str = "",
        session_key: str = "",
    ):
        """Log PII access event."""
        PIIAccessEvent.objects.create(
            user=user,
            target_user=target_user,
            access_type=access_type,
            fields_accessed=fields,
            purpose=purpose,
            ip_address=ip_address,
            user_agent=user_agent,
            session_key=session_key,
        )
        logger.info(f"PII access logged: {user.id} accessed {target_user.id} fields: {fields}")

    @staticmethod
    def _mask_phone(phone: str) -> str:
        """Mask phone number."""
        if not phone:
            return ""
        if len(phone) < 6:
            return "***"
        return phone[:5] + "*" * (len(phone) - 7) + phone[-2:]

    @staticmethod
    def _mask_email(email: str) -> str:
        """Mask email address."""
        if not email:
            return ""
        parts = email.split("@")
        if len(parts) != 2:
            return "***"
        local = parts[0]
        domain = parts[1]
        if len(local) <= 2:
            masked_local = "*" * len(local)
        else:
            masked_local = local[0] + "*" * (len(local) - 2) + local[-1]
        return f"{masked_local}@{domain}"

    @staticmethod
    def _get_kyc_status(user: User) -> dict:
        """Get KYC status summary."""
        kycs = MemberKYC.objects.filter(user=user)
        return {
            "total": kycs.count(),
            "records": [
                {
                    "chama_id": str(k.chama_id),
                    "status": k.status,
                    "tier": k.kyc_tier,
                    "submitted_at": k.created_at.isoformat() if k.created_at else None,
                }
                for k in kycs[:10]
            ],
        }

    @staticmethod
    def _get_financial_summary(user: User) -> dict:
        """Get financial data summary."""
        from apps.finance.models import Loan, Wallet, Payment

        try:
            wallets = Wallet.objects.filter(
                owner_type="USER",
                owner_id=user.id,
            )
            total_contributions = sum(w.total_contributions for w in wallets)
            total_loans = Loan.objects.filter(
                member__user=user,
                status__in=[Loan.LoanStatus.ACTIVE, Loan.LoanStatus.DISBURSED],
            ).count()
        except Exception:
            total_contributions = 0
            total_loans = 0

        return {
            "total_contributions": str(total_contributions),
            "active_loans": total_loans,
            "note": "Detailed financial records retained for legal compliance",
        }

    @staticmethod
    def _get_current_policy_version() -> PrivacyPolicyVersion | None:
        """Get current active privacy policy version."""
        return PrivacyPolicyVersion.objects.filter(is_active=True).first()


class RetentionService:
    """Service for data retention management."""

    @staticmethod
    def get_retention_period(data_category: str) -> int | None:
        """Get retention period in months for a data category."""
        schedule = RetentionSchedule.objects.filter(
            data_category=data_category,
            is_active=True,
        ).first()
        return schedule.retention_months if schedule else None

    @staticmethod
    def should_delete(data_category: str, created_at) -> bool:
        """Check if data should be deleted based on retention policy."""
        months = RetentionService.get_retention_period(data_category)
        if months is None:
            return False
        expiry = created_at + timedelta(days=months * 30)
        return timezone.now() >= expiry


class ConsentService:
    """Service for consent management operations."""

    REQUIRED_CONSENTS = [
        ConsentCategory.MARKETING,
        ConsentCategory.ANALYTICS,
    ]

    @staticmethod
    def has_valid_consent(user: User, category: str) -> bool:
        """Check if user has valid consent for category."""
        consent = UserConsent.objects.filter(
            user=user,
            category=category,
            status=ConsentStatus.GRANTED,
        ).first()
        return consent is not None

    @staticmethod
    def has_all_required_consents(user: User) -> bool:
        """Check if user has all required consents."""
        for category in ConsentService.REQUIRED_CONSENTS:
            if not ConsentService.has_valid_consent(user, category):
                return False
        return True

    @staticmethod
    @transaction.atomic
    def initialize_consents(user: User, ip_address: str = None):
        """Initialize default consent records for new user."""
        policy = PrivacyService._get_current_policy_version()
        version = policy.version if policy else "1.0"

        for category in ConsentCategory.values:
            UserConsent.objects.get_or_create(
                user=user,
                category=category,
                defaults={
                    "status": ConsentStatus.PENDING,
                    "policy_version": version,
                    "ip_address": ip_address,
                },
            )


__all__ = [
    "PrivacyService",
    "RetentionService",
    "ConsentService",
]
