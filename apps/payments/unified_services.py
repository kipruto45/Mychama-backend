"""
Unified Payment Service for MyChama.

Orchestrates the active payment methods (M-Pesa and cash)
through a single, clean interface.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.chama.models import (
    Chama,
    ChamaStatus,
    Membership,
    MembershipRole,
    MemberStatus,
    PaymentProviderConfig,
)
from apps.finance.models import (
    Contribution,
    ContributionFrequency,
    ContributionType,
    JournalEntrySource,
    LedgerDirection,
    LedgerEntry,
    LedgerEntryType,
    LedgerStatus,
    Loan,
    MethodChoices,
    Penalty,
    PenaltyStatus,
    Repayment,
    Wallet,
    WalletOwnerType,
)
from apps.finance.services import (
    FinanceService,
    FinanceServiceError,
    IdempotencyConflictError,
)
from apps.notifications.models import (
    NotificationCategory,
    NotificationPriority,
    NotificationType,
)
from apps.notifications.services import create_notification
from apps.payments.models import (
    PaymentDispute,
    PaymentDisputeCategory,
    PaymentDisputeStatus,
    PaymentRefund,
    PaymentRefundStatus,
)
from apps.payments.providers.factory import PaymentProviderFactory
from apps.payments.providers.unified_base import (
    PaymentProvider,
    PaymentProviderError,
    PaymentResult,
)
from apps.payments.unified_models import (
    BankPaymentDetails,
    CardPaymentDetails,
    CashPaymentDetails,
    ManualPaymentApprovalPolicy,
    MpesaPaymentDetails,
    PaymentAuditLog,
    PaymentIntent,
    PaymentMethod,
    PaymentPurpose,
    PaymentReceipt,
    PaymentReconciliationCase,
    PaymentSettlement,
    PaymentSettlementAllocation,
    PaymentStatementImport,
    PaymentStatementLine,
    PaymentStatus,
    PaymentTransaction,
    PaymentWebhook,
    ReconciliationCaseStatus,
    ReconciliationMismatchType,
    SettlementStatus,
    StatementImportStatus,
    StatementLineMatchStatus,
    TransactionStatus,
)
from core.constants import CurrencyChoices
from core.utils import parse_iso_date, to_decimal

logger = logging.getLogger(__name__)
ZERO = Decimal("0.00")

FINANCE_MANAGEMENT_ROLES = {
    MembershipRole.SUPERADMIN,
    MembershipRole.ADMIN,
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.TREASURER,
}

DEFAULT_CASH_RECORDER_ROLES = {
    MembershipRole.TREASURER,
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.ADMIN,
    MembershipRole.SUPERADMIN,
}
DEFAULT_CASH_VERIFIER_ROLES = {
    MembershipRole.TREASURER,
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.ADMIN,
    MembershipRole.SUPERADMIN,
}
DEFAULT_BANK_VERIFIER_ROLES = {
    MembershipRole.TREASURER,
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.ADMIN,
    MembershipRole.SUPERADMIN,
}
DEFAULT_RECONCILIATION_ROLES = {
    MembershipRole.TREASURER,
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.ADMIN,
    MembershipRole.SUPERADMIN,
    MembershipRole.AUDITOR,
}

ALLOWED_INTENT_STATUS_TRANSITIONS = {
    PaymentStatus.INITIATED: {
        PaymentStatus.PENDING,
        PaymentStatus.PENDING_AUTHENTICATION,
        PaymentStatus.PENDING_VERIFICATION,
        PaymentStatus.SUCCESS,
        PaymentStatus.FAILED,
        PaymentStatus.CANCELLED,
        PaymentStatus.EXPIRED,
    },
    PaymentStatus.PENDING: {
        PaymentStatus.PENDING_AUTHENTICATION,
        PaymentStatus.SUCCESS,
        PaymentStatus.FAILED,
        PaymentStatus.CANCELLED,
        PaymentStatus.EXPIRED,
    },
    PaymentStatus.PENDING_AUTHENTICATION: {
        PaymentStatus.PENDING,
        PaymentStatus.SUCCESS,
        PaymentStatus.FAILED,
        PaymentStatus.CANCELLED,
        PaymentStatus.EXPIRED,
    },
    PaymentStatus.PENDING_VERIFICATION: {
        PaymentStatus.SUCCESS,
        PaymentStatus.FAILED,
        PaymentStatus.CANCELLED,
        PaymentStatus.EXPIRED,
    },
    PaymentStatus.SUCCESS: {
        PaymentStatus.RECONCILED,
        PaymentStatus.PARTIALLY_REFUNDED,
        PaymentStatus.REFUNDED,
    },
    PaymentStatus.PARTIALLY_REFUNDED: {PaymentStatus.REFUNDED},
    PaymentStatus.FAILED: set(),
    PaymentStatus.CANCELLED: set(),
    PaymentStatus.EXPIRED: set(),
    PaymentStatus.RECONCILED: {
        PaymentStatus.PARTIALLY_REFUNDED,
        PaymentStatus.REFUNDED,
    },
    PaymentStatus.REFUNDED: set(),
}


class PaymentServiceError(Exception):
    """Base exception for payment service errors."""
    pass


class UnifiedPaymentService:
    """
    Unified Payment Service.
    
    Handles all payment operations through a single interface,
    routing to appropriate provider based on payment method.
    """

    ENABLED_PAYMENT_METHODS = {
        PaymentMethod.MPESA,
        PaymentMethod.CASH,
        PaymentMethod.BANK,
        PaymentMethod.WALLET,
    }
    STATEMENT_IMPORT_METHODS = {
        PaymentMethod.MPESA,
        PaymentMethod.BANK,
    }
    SETTLEMENT_METHODS = {
        PaymentMethod.MPESA,
    }

    @classmethod
    def _assert_enabled_payment_method(cls, payment_method: str) -> None:
        if payment_method not in cls.ENABLED_PAYMENT_METHODS:
            raise PaymentServiceError(
                "This payment method is not available. Please use M-Pesa, cash, bank transfer, or wallet."
            )

    @staticmethod
    def generate_idempotency_key(
        chama_id: uuid.UUID,
        user_id: uuid.UUID,
        amount: Decimal,
        purpose: str,
        payment_method: str,
    ) -> str:
        """Generate a unique idempotency key for a payment."""
        base = f"{payment_method}:{chama_id}:{user_id}:{amount}:{purpose}:{uuid.uuid4().hex}"
        if len(base) <= 100:
            return base
        digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:40]
        return f"{payment_method}:{purpose}:{digest}"

    @classmethod
    def _validate_payment_request(
        cls,
        chama: Chama,
        user: Any,
        amount: Decimal,
        currency: str,
        purpose: str,
        payment_method: str,
    ) -> None:
        """Validate payment request."""
        if amount <= Decimal("0.00"):
            raise PaymentServiceError("Amount must be greater than zero")

        if chama.status != ChamaStatus.ACTIVE:
            raise PaymentServiceError("Chama is not active")

        membership = Membership.objects.filter(
            chama=chama,
            user=user,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
        ).first()

        if not membership:
            raise PaymentServiceError("User is not an active member of this chama")

        if currency not in [c.value for c in CurrencyChoices]:
            raise PaymentServiceError(f"Unsupported currency: {currency}")

        if payment_method not in [m.value for m in PaymentMethod]:
            raise PaymentServiceError(f"Unsupported payment method: {payment_method}")
        cls._assert_enabled_payment_method(payment_method)

    @staticmethod
    def _assert_status_transition(current_status: str, new_status: str) -> None:
        if current_status == new_status:
            return
        allowed_statuses = ALLOWED_INTENT_STATUS_TRANSITIONS.get(current_status, set())
        if new_status not in allowed_statuses:
            raise PaymentServiceError(
                f"Invalid payment status transition from {current_status} to {new_status}"
            )

    @staticmethod
    def _get_membership(chama: Chama, user: Any) -> Membership | None:
        if not user or not getattr(user, "is_authenticated", False):
            return None
        return Membership.objects.filter(
            chama=chama,
            user=user,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
        ).first()

    @classmethod
    def _get_actor_role(cls, *, chama: Chama, actor: Any) -> str | None:
        if getattr(actor, "is_superuser", False):
            return MembershipRole.SUPERADMIN
        if getattr(actor, "is_staff", False):
            return MembershipRole.ADMIN
        membership = cls._get_membership(chama, actor)
        return membership.role if membership else None

    @staticmethod
    def _normalize_roles(raw_roles: Any, default_roles: set[str]) -> set[str]:
        roles = {str(role).strip().upper() for role in (raw_roles or []) if str(role).strip()}
        return roles or set(default_roles)

    @classmethod
    def _get_manual_approval_policy(cls, *, chama: Chama) -> ManualPaymentApprovalPolicy:
        policy, _ = ManualPaymentApprovalPolicy.objects.get_or_create(
            chama=chama,
            defaults={
                "created_by": None,
                "updated_by": None,
            },
        )
        return policy

    @classmethod
    def _assert_manual_role_allowed(
        cls,
        *,
        chama: Chama,
        actor: Any,
        allowed_roles: set[str],
        action_label: str,
    ) -> None:
        role = cls._get_actor_role(chama=chama, actor=actor)
        if role in allowed_roles:
            return
        raise PaymentServiceError(f"You are not allowed to {action_label} for this chama")

    @classmethod
    def _assert_finance_management_permission(cls, *, chama: Chama, actor: Any) -> None:
        if getattr(actor, "is_staff", False) or getattr(actor, "is_superuser", False):
            return
        membership = cls._get_membership(chama, actor)
        if not membership or membership.role not in FINANCE_MANAGEMENT_ROLES:
            raise PaymentServiceError("You are not allowed to verify or reconcile this payment")

    @staticmethod
    def _enforce_manual_maker_checker(
        *,
        actor: Any,
        recorded_by_id: uuid.UUID | None = None,
        payer_user_id: uuid.UUID | None = None,
    ) -> None:
        if getattr(actor, "is_staff", False) or getattr(actor, "is_superuser", False):
            return
        actor_id = getattr(actor, "id", None)
        if recorded_by_id and actor_id == recorded_by_id:
            raise PaymentServiceError("Maker-checker rule prevents approving a payment you recorded yourself")
        if payer_user_id and actor_id == payer_user_id:
            raise PaymentServiceError("Maker-checker rule prevents approving your own manual payment")

    @classmethod
    def _get_provider(cls, payment_method: str, provider_name: str | None = None) -> PaymentProvider:
        """Get payment provider instance."""
        cls._assert_enabled_payment_method(payment_method)
        provider_name = str(provider_name or "").strip() or None
        try:
            return PaymentProviderFactory.get_provider(payment_method, provider_name)
        except PaymentProviderError as e:
            raise PaymentServiceError(f"Provider error: {e}")

    @staticmethod
    def _map_provider_status(status: str | None) -> str:
        """Map provider-specific status values to unified payment status."""
        normalized = (status or "").lower()
        status_map = {
            "success": PaymentStatus.SUCCESS,
            "successful": PaymentStatus.SUCCESS,
            "succeeded": PaymentStatus.SUCCESS,
            "partially_refunded": PaymentStatus.PARTIALLY_REFUNDED,
            "refunded": PaymentStatus.REFUNDED,
            "pending": PaymentStatus.PENDING,
            "processing": PaymentStatus.PENDING,
            "pending_authentication": PaymentStatus.PENDING_AUTHENTICATION,
            "pending_verification": PaymentStatus.PENDING_VERIFICATION,
            "failed": PaymentStatus.FAILED,
            "cancelled": PaymentStatus.CANCELLED,
            "canceled": PaymentStatus.CANCELLED,
            "expired": PaymentStatus.EXPIRED,
            "reconciled": PaymentStatus.RECONCILED,
        }
        return status_map.get(normalized, PaymentStatus.PENDING)

    @staticmethod
    def _map_transaction_status(payment_status: str) -> str:
        """Translate unified payment status to transaction status."""
        if payment_status == PaymentStatus.SUCCESS:
            return TransactionStatus.VERIFIED
        if payment_status == PaymentStatus.PARTIALLY_REFUNDED:
            return TransactionStatus.PARTIALLY_REFUNDED
        if payment_status == PaymentStatus.REFUNDED:
            return TransactionStatus.REFUNDED
        if payment_status == PaymentStatus.FAILED:
            return TransactionStatus.FAILED
        return TransactionStatus.RECEIVED

    @staticmethod
    def _map_payment_method_to_finance_method(payment_method: str) -> str:
        return {
            PaymentMethod.MPESA: MethodChoices.MPESA,
            PaymentMethod.CASH: MethodChoices.CASH,
            PaymentMethod.BANK: MethodChoices.BANK_TRANSFER,
            PaymentMethod.WALLET: MethodChoices.WALLET,
        }.get(payment_method, MethodChoices.OTHER)

    @staticmethod
    def _original_ledger_accounts_for_intent(intent: PaymentIntent) -> tuple[str, str]:
        debit_account_key = {
            PaymentMethod.MPESA: "mpesa_clearing",
            PaymentMethod.CASH: "cash_on_hand",
            PaymentMethod.BANK: "bank_account",
            PaymentMethod.WALLET: "wallet_clearing",
        }.get(intent.payment_method, "cash_on_hand")
        credit_account_key = {
            PaymentPurpose.CONTRIBUTION: "contributions_account",
            PaymentPurpose.FINE: "penalty_receivable",
            PaymentPurpose.LOAN_REPAYMENT: "loan_receivable",
            PaymentPurpose.MEETING_FEE: "meeting_fee_income",
            PaymentPurpose.SPECIAL_CONTRIBUTION: "special_contributions",
        }.get(intent.purpose, "contributions_account")
        return debit_account_key, credit_account_key

    @staticmethod
    def _settlement_clearing_account_key(payment_method: str) -> str:
        account_key = {
            PaymentMethod.MPESA: "mpesa_clearing",
        }.get(payment_method)
        if not account_key:
            raise PaymentServiceError(
                "Settlement posting is currently supported for M-Pesa only"
            )
        return account_key

    @staticmethod
    def _manual_payment_requires_dual_approval(
        *,
        policy: ManualPaymentApprovalPolicy,
        intent: PaymentIntent,
    ) -> bool:
        return to_decimal(policy.dual_approval_threshold) > Decimal("0.00") and to_decimal(intent.amount) >= to_decimal(policy.dual_approval_threshold)

    @staticmethod
    def _notification_payload(intent: PaymentIntent) -> dict[str, Any]:
        return {
            "payment_intent_id": str(intent.id),
            "reference": intent.reference,
            "amount": str(intent.amount),
            "currency": intent.currency,
            "provider": intent.provider,
            "status": intent.status,
            "purpose": intent.purpose,
            "payment_method": intent.payment_method,
        }

    @classmethod
    def _notify(
        cls,
        *,
        intent: PaymentIntent,
        event: str,
        receipt: PaymentReceipt | None = None,
    ) -> None:
        if not intent.user:
            return

        payload = cls._notification_payload(intent)
        if receipt is not None:
            payload.update(
                {
                    "receipt_number": receipt.receipt_number,
                    "receipt_reference": receipt.reference_number,
                }
            )

        config = {
            "initiated": {
                "title": "Payment initiated",
                "message": (
                    f"We started your {intent.payment_method} payment for "
                    f"{intent.currency} {intent.amount:,.2f}."
                ),
                "priority": NotificationPriority.NORMAL,
                "send_email": False,
            },
            "success": {
                "title": "Payment successful",
                "message": (
                    f"Your {intent.payment_method} payment of "
                    f"{intent.currency} {intent.amount:,.2f} was confirmed."
                ),
                "priority": NotificationPriority.HIGH,
                "send_email": False,
            },
            "failed": {
                "title": "Payment failed",
                "message": (
                    f"Your {intent.payment_method} payment of "
                    f"{intent.currency} {intent.amount:,.2f} did not complete."
                ),
                "priority": NotificationPriority.HIGH,
                "send_email": False,
            },
            "receipt": {
                "title": "Payment receipt",
                "message": (
                    f"Payment confirmed. Receipt {receipt.receipt_number if receipt else ''} is ready."
                ),
                "priority": NotificationPriority.NORMAL,
                "send_email": True,
            },
        }.get(event)
        if not config:
            return

        try:
            create_notification(
                recipient=intent.user,
                chama=intent.chama,
                notification_type=NotificationType.PAYMENT_CONFIRMATION,
                title=config["title"],
                message=config["message"],
                priority=config["priority"],
                category=NotificationCategory.PAYMENTS,
                action_url=f"/payments/{intent.id}",
                metadata=payload,
                send_email=config["send_email"],
                send_sms=False,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to send payment notification for %s (%s)",
                intent.id,
                event,
            )

    @staticmethod
    def _validate_provider_result(*, intent: PaymentIntent, result: Any) -> None:
        verified_amount = result.amount if result.amount and result.amount > 0 else intent.amount
        if Decimal(str(verified_amount)) != Decimal(str(intent.amount)):
            raise PaymentServiceError("Provider amount mismatch")

        if (result.currency or intent.currency).upper() != intent.currency.upper():
            raise PaymentServiceError("Provider currency mismatch")

        provider_metadata = result.provider_metadata or {}
        provider_reference = (
            provider_metadata.get("reference")
            or provider_metadata.get("tx_ref")
            or provider_metadata.get("session_reference")
        )
        if provider_reference and provider_reference != intent.reference:
            raise PaymentServiceError("Provider reference mismatch")

    @classmethod
    def _flag_reconciliation_issue(
        cls,
        *,
        intent: PaymentIntent,
        issue_type: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
        expected_amount: Decimal | None = None,
        received_amount: Decimal | None = None,
        expected_reference: str = "",
        received_reference: str = "",
        payment_transaction: PaymentTransaction | None = None,
        webhook: PaymentWebhook | None = None,
        assigned_to=None,
    ) -> None:
        intent.metadata = {
            **(intent.metadata or {}),
            "reconciliation": {
                "flagged": True,
                "issue_type": issue_type,
                "summary": summary,
                "metadata": metadata or {},
                "flagged_at": timezone.now().isoformat(),
            },
        }
        intent.save(update_fields=["metadata", "updated_at"])
        PaymentReconciliationCase.objects.create(
            chama=intent.chama,
            payment_intent=intent,
            payment_transaction=payment_transaction,
            webhook=webhook,
            mismatch_type=issue_type,
            case_status=ReconciliationCaseStatus.OPEN,
            expected_amount=expected_amount,
            received_amount=received_amount,
            expected_reference=expected_reference,
            received_reference=received_reference,
            assigned_to=assigned_to,
            resolution_notes="",
            metadata={"summary": summary, **(metadata or {})},
            created_by=assigned_to if getattr(assigned_to, "is_authenticated", False) else None,
            updated_by=assigned_to if getattr(assigned_to, "is_authenticated", False) else None,
        )

    @classmethod
    def _record_failure(
        cls,
        *,
        intent: PaymentIntent,
        previous_status: str,
        failure_reason: str,
        failure_code: str = "",
        metadata: dict[str, Any] | None = None,
        actor=None,
    ) -> None:
        cls._assert_status_transition(intent.status, PaymentStatus.FAILED)
        intent.status = PaymentStatus.FAILED
        intent.failure_reason = (failure_reason or "Payment failed")[:500]
        intent.failure_code = (failure_code or "")[:50]
        intent.save(
            update_fields=[
                "status",
                "failure_reason",
                "failure_code",
                "updated_at",
            ]
        )

        PaymentAuditLog.objects.create(
            payment_intent=intent,
            actor=actor,
            event="payment_failed",
            previous_status=previous_status,
            new_status=PaymentStatus.FAILED,
            metadata=metadata or {},
        )

        cls._notify(intent=intent, event="failed")

    @staticmethod
    def _resolve_contribution_type(intent: PaymentIntent) -> ContributionType:
        if intent.contribution_id and intent.contribution:
            return intent.contribution.contribution_type

        contribution_type_id = (intent.metadata or {}).get("contribution_type_id")
        if contribution_type_id:
            try:
                return ContributionType.objects.get(
                    id=contribution_type_id,
                    chama=intent.chama,
                    is_active=True,
                )
            except ContributionType.DoesNotExist as exc:
                raise PaymentServiceError("Contribution type not found") from exc

        contribution_type = (
            ContributionType.objects.filter(chama=intent.chama, is_active=True)
            .order_by("created_at")
            .first()
        )
        if contribution_type:
            return contribution_type

        return ContributionType.objects.create(
            chama=intent.chama,
            name="General Contribution",
            frequency=ContributionFrequency.MONTHLY,
            default_amount=intent.amount,
            is_active=True,
            created_by=intent.user,
            updated_by=intent.user,
        )

    @classmethod
    def _finalize_business_event(
        cls,
        *,
        intent: PaymentIntent,
        transaction_record: PaymentTransaction,
    ) -> dict[str, Any]:
        actor = transaction_record.verified_by or intent.user
        if actor is None:
            raise PaymentServiceError("Payment actor unavailable for business finalization")

        if intent.contribution_id and intent.contribution:
            debit_account_key, credit_account_key = cls._original_ledger_accounts_for_intent(intent)
            return {
                "record": intent.contribution,
                "ledger_handled": False,
                "metadata_updates": {
                    "business_record_type": "contribution",
                    "business_record_id": str(intent.contribution.id),
                    "debit_account_key": debit_account_key,
                    "credit_account_key": credit_account_key,
                },
                "receipt_metadata": {
                    "business_record_type": "contribution",
                    "business_record_id": str(intent.contribution.id),
                },
            }

        if intent.purpose == PaymentPurpose.CONTRIBUTION:
            contribution_type = cls._resolve_contribution_type(intent)
            result = FinanceService.post_contribution(
                {
                    "chama_id": str(intent.chama_id),
                    "member_id": str(intent.user_id),
                    "contribution_type_id": str(contribution_type.id),
                    "amount": str(intent.amount),
                    "date_paid": timezone.localdate().isoformat(),
                    "method": cls._map_payment_method_to_finance_method(intent.payment_method),
                    "receipt_code": transaction_record.provider_reference or intent.reference,
                    "idempotency_key": f"payment-contribution:{intent.id}",
                },
                actor,
            )
            contribution = result.created
            intent.contribution = contribution
            intent.save(update_fields=["contribution", "updated_at"])
            debit_account_key, credit_account_key = cls._original_ledger_accounts_for_intent(intent)
            return {
                "record": contribution,
                "ledger_handled": True,
                "metadata_updates": {
                    "business_record_type": "contribution",
                    "business_record_id": str(contribution.id),
                    "ledger_entry_id": str(result.ledger_entry.id) if result.ledger_entry else "",
                    "debit_account_key": debit_account_key,
                    "credit_account_key": credit_account_key,
                },
                "receipt_metadata": {
                    "business_record_type": "contribution",
                    "business_record_id": str(contribution.id),
                },
            }

        if intent.purpose == PaymentPurpose.FINE:
            if not intent.purpose_id:
                raise PaymentServiceError("Penalty payment requires purpose_id")
            try:
                penalty = Penalty.objects.select_for_update().get(
                    id=intent.purpose_id,
                    chama=intent.chama,
                    member=intent.user,
                )
            except Penalty.DoesNotExist as exc:
                raise PaymentServiceError("Penalty not found for this payment") from exc
            if penalty.status not in {PenaltyStatus.UNPAID, PenaltyStatus.PARTIAL}:
                raise PaymentServiceError("Penalty is already settled")
            if Decimal(str(intent.amount)) > penalty.outstanding_amount:
                raise PaymentServiceError("Penalty payment amount exceeds the outstanding penalty balance")
            try:
                result = FinanceService.mark_penalty_paid(
                    penalty.id,
                    {
                        "amount": str(intent.amount),
                        "idempotency_key": f"payment-penalty:{intent.id}",
                        "method": cls._map_payment_method_to_finance_method(intent.payment_method),
                    },
                    actor,
                )
            except FinanceServiceError as exc:
                raise PaymentServiceError(str(exc)) from exc
            debit_account_key, credit_account_key = cls._original_ledger_accounts_for_intent(intent)
            return {
                "record": result.created,
                "ledger_handled": True,
                "metadata_updates": {
                    "business_record_type": "penalty",
                    "business_record_id": str(result.created.id),
                    "ledger_entry_id": str(result.ledger_entry.id) if result.ledger_entry else "",
                    "debit_account_key": debit_account_key,
                    "credit_account_key": credit_account_key,
                    "penalty_status": result.created.status,
                    "penalty_amount_paid": str(result.created.amount_paid),
                },
                "receipt_metadata": {
                    "business_record_type": "penalty",
                    "business_record_id": str(result.created.id),
                },
            }

        if intent.purpose == PaymentPurpose.LOAN_REPAYMENT:
            if not intent.purpose_id:
                raise PaymentServiceError("Loan repayment requires purpose_id")
            try:
                loan = Loan.objects.select_for_update().get(
                    id=intent.purpose_id,
                    chama=intent.chama,
                    member=intent.user,
                )
            except Loan.DoesNotExist as exc:
                raise PaymentServiceError("Loan not found for this repayment") from exc
            try:
                result = FinanceService.post_repayment(
                    loan.id,
                    {
                        "amount": str(intent.amount),
                        "date_paid": timezone.localdate().isoformat(),
                        "method": cls._map_payment_method_to_finance_method(intent.payment_method),
                        "receipt_code": transaction_record.provider_reference or intent.reference,
                        "idempotency_key": f"payment-loan-repayment:{intent.id}",
                    },
                    actor,
                )
            except FinanceServiceError as exc:
                raise PaymentServiceError(str(exc)) from exc
            repayment: Repayment = result.created
            debit_account_key, credit_account_key = cls._original_ledger_accounts_for_intent(intent)
            return {
                "record": repayment,
                "ledger_handled": True,
                "metadata_updates": {
                    "business_record_type": "repayment",
                    "business_record_id": str(repayment.id),
                    "ledger_entry_id": str(result.ledger_entry.id) if result.ledger_entry else "",
                    "debit_account_key": debit_account_key,
                    "credit_account_key": credit_account_key,
                    "allocation_breakdown": repayment.allocation_breakdown,
                    "loan_id": str(loan.id),
                },
                "receipt_metadata": {
                    "business_record_type": "repayment",
                    "business_record_id": str(repayment.id),
                    "allocation_breakdown": repayment.allocation_breakdown,
                    "loan_id": str(loan.id),
                },
            }

        if (
            intent.purpose == PaymentPurpose.OTHER
            and str((intent.metadata or {}).get("wallet_flow_kind") or "").lower() == "deposit"
        ):
            wallet = cls._post_wallet_deposit_to_member_wallet(
                intent=intent,
                transaction_record=transaction_record,
                actor=actor,
            )
            return {
                "record": wallet,
                "ledger_handled": True,
                "metadata_updates": {
                    "business_record_type": "member_wallet",
                    "business_record_id": str(wallet.id),
                    "wallet_flow_kind": "deposit",
                    "target_label": "My wallet",
                },
                "receipt_metadata": {
                    "business_record_type": "member_wallet",
                    "business_record_id": str(wallet.id),
                    "wallet_flow_kind": "deposit",
                },
            }

        debit_account_key, credit_account_key = cls._original_ledger_accounts_for_intent(intent)
        return {
            "record": None,
            "ledger_handled": False,
            "metadata_updates": {
                "debit_account_key": debit_account_key,
                "credit_account_key": credit_account_key,
            },
            "receipt_metadata": {},
        }

    @classmethod
    def _upsert_transaction(
        cls,
        *,
        intent: PaymentIntent,
        provider_reference: str,
        provider_name: str,
        amount: Decimal,
        currency: str,
        status: str,
        payer_reference: str = "",
        raw_response: dict[str, Any] | None = None,
        verified_by=None,
        verified_at=None,
        failed_at=None,
    ) -> PaymentTransaction:
        duplicate = (
            PaymentTransaction.objects.select_for_update()
            .filter(provider_reference=provider_reference)
            .exclude(payment_intent=intent)
            .first()
        )
        if duplicate:
            cls._flag_reconciliation_issue(
                intent=intent,
                issue_type=ReconciliationMismatchType.DUPLICATE_PROVIDER_REFERENCE,
                summary="Provider reference is already attached to another payment intent.",
                metadata={
                    "provider_reference": provider_reference,
                    "duplicate_intent_id": str(duplicate.payment_intent_id),
                },
                expected_amount=intent.amount,
                expected_reference=intent.reference,
                received_reference=provider_reference,
            )
            raise PaymentServiceError("Duplicate provider reference detected")

        transaction_record = intent.transactions.order_by("-created_at").first()
        if transaction_record is None:
            transaction_record = PaymentTransaction(
                payment_intent=intent,
                provider_reference=provider_reference,
            )

        transaction_record.provider_reference = provider_reference
        transaction_record.provider_name = provider_name
        transaction_record.payment_method = intent.payment_method
        transaction_record.amount = amount
        transaction_record.currency = currency
        transaction_record.status = status
        transaction_record.payer_reference = payer_reference or ""
        transaction_record.raw_response = raw_response or {}
        transaction_record.verified_by = verified_by
        transaction_record.verified_at = verified_at
        transaction_record.failed_at = failed_at
        transaction_record.save()
        return transaction_record

    @classmethod
    def create_payment_intent(
        cls,
        chama: Chama,
        user: Any,
        amount: Decimal,
        currency: str,
        payment_method: str,
        purpose: str,
        purpose_id: uuid.UUID | None = None,
        description: str = "",
        contribution_id: uuid.UUID | None = None,
        provider_name: str | None = None,
        idempotency_key: str | None = None,
        metadata: dict[str, Any] | None = None,
        **method_specific_kwargs,
    ) -> PaymentIntent:
        """
        Create a payment intent.

        Args:
            chama: Chama instance
            user: User instance
            amount: Payment amount
            currency: Currency code
            payment_method: Payment method (mpesa or cash)
            purpose: Payment purpose
            description: Payment description
            contribution_id: Optional contribution ID
            provider_name: Optional provider name
            idempotency_key: Optional idempotency key
            metadata: Optional metadata
            **method_specific_kwargs: Method-specific arguments

        Returns:
            PaymentIntent instance

        Raises:
            PaymentServiceError: If intent creation fails
        """
        cls._validate_payment_request(chama, user, amount, currency, purpose, payment_method)
        if payment_method == PaymentMethod.CASH:
            policy = cls._get_manual_approval_policy(chama=chama)
            allowed_roles = cls._normalize_roles(
                policy.allowed_cash_recorder_roles,
                DEFAULT_CASH_RECORDER_ROLES,
            )
            cls._assert_manual_role_allowed(
                chama=chama,
                actor=user,
                allowed_roles=allowed_roles,
                action_label="record cash payments",
            )

        if idempotency_key is None:
            idempotency_key = cls.generate_idempotency_key(
                chama.id, user.id, amount, purpose, payment_method
            )
        provider = cls._get_provider(payment_method, provider_name)

        try:
            with transaction.atomic():
                # Create payment intent
                intent = PaymentIntent.objects.create(
                    chama=chama,
                    user=user,
                    contribution_id=contribution_id,
                    amount=amount,
                    currency=currency.upper(),
                    purpose=purpose,
                    purpose_id=purpose_id,
                    description=description or f"{purpose.title()} payment",
                    payment_method=payment_method,
                    provider=provider.provider_name,
                    provider_intent_id=f"pending_{uuid.uuid4().hex}",
                    idempotency_key=idempotency_key,
                    reference=f"PAY-{uuid.uuid4().hex[:12].upper()}",
                    metadata={
                        **(metadata or {}),
                        **(
                            {"contribution_type_id": str(method_specific_kwargs.get("contribution_type_id"))}
                            if method_specific_kwargs.get("contribution_type_id")
                            else {}
                        ),
                    },
                    expires_at=timezone.now() + timezone.timedelta(hours=24),
                )

                # Create method-specific details
                if payment_method == PaymentMethod.MPESA:
                    cls._create_mpesa_details(intent, provider, method_specific_kwargs)
                elif payment_method == PaymentMethod.CASH:
                    cls._create_cash_details(intent, provider, method_specific_kwargs)
                elif payment_method == PaymentMethod.BANK:
                    cls._create_bank_details(intent, provider, method_specific_kwargs)
                elif payment_method == PaymentMethod.WALLET:
                    cls._create_wallet_details(intent, provider, method_specific_kwargs)
                else:
                    raise PaymentServiceError(
                        "This payment method is not available. Please use M-Pesa, cash, bank transfer, or wallet."
                    )

                # Create audit log
                PaymentAuditLog.objects.create(
                    payment_intent=intent,
                    actor=user,
                    event="intent_created",
                    new_status=intent.status,
                    metadata={
                        "payment_method": payment_method,
                        "provider": provider.provider_name,
                    },
                )

                logger.info(
                    "Payment intent created: %s for chama %s, user %s, method %s",
                    intent.id,
                    chama.id,
                    user.id,
                    payment_method,
                )

                cls._notify(intent=intent, event="initiated")

                # Auto-finalize instantly-settled providers (e.g., internal wallet).
                if intent.status == PaymentStatus.SUCCESS and not hasattr(intent, "receipt"):
                    now = timezone.now()
                    provider_reference = intent.provider_intent_id or f"{intent.reference}"
                    payer_reference = getattr(user, "phone", "") or ""
                    transaction_record = cls._upsert_transaction(
                        intent=intent,
                        provider_reference=provider_reference,
                        provider_name=intent.provider or "internal",
                        amount=intent.amount,
                        currency=intent.currency,
                        status=TransactionStatus.VERIFIED,
                        payer_reference=payer_reference,
                        raw_response={"verification_type": "instant"},
                        verified_by=user,
                        verified_at=now,
                    )
                    PaymentAuditLog.objects.create(
                        payment_intent=intent,
                        actor=user,
                        event="payment_auto_settled",
                        previous_status=PaymentStatus.INITIATED,
                        new_status=intent.status,
                        metadata={"payment_method": payment_method},
                    )
                    cls._handle_successful_payment(intent, transaction_record)
                    cls._notify(intent=intent, event="success")
                return intent

        except IntegrityError as e:
            logger.error("Payment intent creation failed (integrity): %s", e)
            raise PaymentServiceError("Payment intent already exists")
        except Exception as e:
            logger.error("Payment intent creation failed: %s", e)
            raise PaymentServiceError(f"Failed to create payment intent: {e}")

    @classmethod
    def _create_mpesa_details(
        cls,
        intent: PaymentIntent,
        provider: PaymentProvider,
        kwargs: dict[str, Any],
    ) -> None:
        """Create M-Pesa specific details."""
        phone = kwargs.get("phone", "")
        if not phone:
            raise PaymentServiceError("Phone number is required for M-Pesa payments")

        provider_intent = provider.create_payment(
            amount=intent.amount,
            currency=intent.currency,
            reference=intent.reference,
            description=intent.description,
            payer_phone=phone,
            payer_email=getattr(intent.user, "email", None),
            metadata={
                "intent_id": str(intent.id),
                "chama_id": str(intent.chama_id),
                "user_id": str(intent.user_id),
            },
            idempotency_key=intent.idempotency_key,
        )

        MpesaPaymentDetails.objects.create(
            payment_intent=intent,
            phone=phone,
            checkout_request_id=provider_intent.provider_intent_id,
        )

        intent.provider_intent_id = provider_intent.provider_intent_id
        next_status = cls._map_provider_status(provider_intent.status)
        cls._assert_status_transition(intent.status, next_status)
        intent.status = next_status
        intent.save(update_fields=["provider_intent_id", "status", "updated_at"])

    @classmethod
    def _create_card_details(
        cls,
        intent: PaymentIntent,
        provider: PaymentProvider,
        kwargs: dict[str, Any],
    ) -> None:
        """Legacy helper for disabled card-payment details."""
        provider_intent = provider.create_payment(
            amount=intent.amount,
            currency=intent.currency,
            reference=intent.reference,
            description=intent.description,
            payer_phone=kwargs.get("payer_phone") or getattr(intent.user, "phone", None),
            payer_email=kwargs.get("payer_email") or getattr(intent.user, "email", None),
            metadata={
                "intent_id": str(intent.id),
                "chama_id": str(intent.chama_id),
                "user_id": str(intent.user_id),
            },
            idempotency_key=intent.idempotency_key,
        )

        CardPaymentDetails.objects.create(
            payment_intent=intent,
            provider_intent_id=provider_intent.provider_intent_id,
            client_secret=provider_intent.client_secret or "",
            checkout_url=provider_intent.checkout_url or "",
        )

        intent.provider_intent_id = provider_intent.provider_intent_id
        next_status = cls._map_provider_status(
            provider_intent.status
            or (
                "pending_authentication"
                if provider_intent.client_secret or provider_intent.checkout_url
                else "pending"
            )
        )
        cls._assert_status_transition(intent.status, next_status)
        intent.status = next_status
        intent.save(update_fields=["provider_intent_id", "status", "updated_at"])

    @classmethod
    def _create_cash_details(
        cls,
        intent: PaymentIntent,
        provider: PaymentProvider,
        kwargs: dict[str, Any],
    ) -> None:
        """Create cash specific details."""
        received_by = kwargs.get("received_by")

        provider_intent = provider.create_payment(
            amount=intent.amount,
            currency=intent.currency,
            reference=intent.reference,
            description=intent.description,
            payer_email=getattr(intent.user, "email", None),
            metadata={
                "intent_id": str(intent.id),
                "chama_id": str(intent.chama_id),
                "user_id": str(intent.user_id),
                "notes": kwargs.get("notes", ""),
            },
            idempotency_key=intent.idempotency_key,
        )

        CashPaymentDetails.objects.create(
            payment_intent=intent,
            received_by=received_by,
            notes=kwargs.get("notes", ""),
        )

        intent.provider_intent_id = provider_intent.provider_intent_id
        next_status = cls._map_provider_status(provider_intent.status)
        cls._assert_status_transition(intent.status, next_status)
        intent.status = next_status
        intent.save(update_fields=["provider_intent_id", "status", "updated_at"])

    @classmethod
    def _create_bank_details(
        cls,
        intent: PaymentIntent,
        provider: PaymentProvider,
        kwargs: dict[str, Any],
    ) -> None:
        """Create bank transfer payment details sourced from chama provider config."""
        config = (
            PaymentProviderConfig.objects.filter(chama=intent.chama, is_active=True)
            .exclude(bank_account_number="")
            .order_by("-created_at")
            .first()
        )
        if not config or not str(config.bank_account_number or "").strip():
            raise PaymentServiceError("Bank transfer details are not configured for this chama.")

        bank_name = str(config.bank_name or "").strip() or "Bank Transfer"
        account_number = str(config.bank_account_number or "").strip()
        account_name = str(intent.chama.name or "").strip()
        transfer_reference = str(kwargs.get("transfer_reference") or intent.reference or "").strip()[:100]
        notes = str(kwargs.get("notes") or "").strip()

        provider_intent = provider.create_payment(
            amount=intent.amount,
            currency=intent.currency,
            reference=intent.reference,
            description=intent.description,
            payer_email=getattr(intent.user, "email", None),
            metadata={
                "intent_id": str(intent.id),
                "chama_id": str(intent.chama_id),
                "user_id": str(intent.user_id),
                "bank_name": bank_name,
                "account_number": account_number,
                "account_name": account_name,
                "transfer_reference": transfer_reference,
                "notes": notes,
            },
            idempotency_key=intent.idempotency_key,
        )

        BankPaymentDetails.objects.create(
            payment_intent=intent,
            bank_name=bank_name,
            account_number=account_number,
            account_name=account_name,
            transfer_reference=transfer_reference,
            notes=notes,
        )

        intent.provider_intent_id = provider_intent.provider_intent_id
        next_status = cls._map_provider_status(provider_intent.status)
        cls._assert_status_transition(intent.status, next_status)
        intent.status = next_status
        intent.metadata = {
            **(intent.metadata or {}),
            "payment_instructions": {
                "kind": "bank_transfer",
                "bank_name": bank_name,
                "account_number": account_number,
                "account_name": account_name,
                "transfer_reference": transfer_reference,
                "notes": notes,
            },
        }
        intent.save(update_fields=["provider_intent_id", "status", "metadata", "updated_at"])

    @classmethod
    def _create_wallet_details(
        cls,
        intent: PaymentIntent,
        provider: PaymentProvider,
        kwargs: dict[str, Any],
    ) -> None:
        """Create internal wallet payment details (instant settlement)."""
        metadata = intent.metadata or {}
        if str(metadata.get("wallet_flow_kind") or "").strip().lower() == "deposit":
            raise PaymentServiceError("Wallet payment method cannot be used for wallet deposits.")

        if not intent.user_id:
            raise PaymentServiceError("Wallet payments require an authenticated user.")

        member_wallet, _ = Wallet.objects.select_for_update().get_or_create(
            owner_type=WalletOwnerType.USER,
            owner_id=str(intent.user_id),
            defaults={
                "available_balance": ZERO,
                "locked_balance": ZERO,
                "currency": intent.currency or "KES",
            },
        )
        chama_wallet, _ = Wallet.objects.select_for_update().get_or_create(
            owner_type=WalletOwnerType.CHAMA,
            owner_id=str(intent.chama_id),
            defaults={
                "available_balance": ZERO,
                "locked_balance": ZERO,
                "currency": intent.currency or "KES",
            },
        )

        if member_wallet.available_balance < intent.amount:
            raise PaymentServiceError("Insufficient wallet balance. Please top up your wallet first.")

        entry_type = {
            PaymentPurpose.CONTRIBUTION: LedgerEntryType.CONTRIBUTION,
            PaymentPurpose.SPECIAL_CONTRIBUTION: LedgerEntryType.CONTRIBUTION,
            PaymentPurpose.LOAN_REPAYMENT: LedgerEntryType.LOAN_REPAYMENT,
            PaymentPurpose.FINE: LedgerEntryType.PENALTY,
            PaymentPurpose.MEETING_FEE: LedgerEntryType.FEE,
        }.get(intent.purpose, LedgerEntryType.WALLET_TRANSFER)

        debit_key = f"wallet-payment:{intent.id}:debit"
        credit_key = f"wallet-payment:{intent.id}:credit"

        if not LedgerEntry.objects.filter(chama=intent.chama, idempotency_key=debit_key).exists():
            LedgerEntry.objects.create(
                wallet=member_wallet,
                chama=intent.chama,
                entry_type=entry_type,
                direction=LedgerDirection.DEBIT,
                amount=intent.amount,
                debit=intent.amount,
                credit=ZERO,
                currency=intent.currency or "KES",
                status=LedgerStatus.SUCCESS,
                provider="internal",
                provider_reference=intent.reference,
                idempotency_key=debit_key,
                related_payment=intent,
                narration=intent.description or "Wallet payment",
                meta={
                    "payment_intent_id": str(intent.id),
                    "wallet_flow_kind": "wallet_payment",
                    "payment_purpose": str(intent.purpose),
                },
                created_by=intent.user,
                updated_by=intent.user,
            )
            member_wallet.available_balance = Decimal(str(member_wallet.available_balance or ZERO)) - intent.amount
            member_wallet.save(update_fields=["available_balance", "updated_at"])

        if not LedgerEntry.objects.filter(chama=intent.chama, idempotency_key=credit_key).exists():
            LedgerEntry.objects.create(
                wallet=chama_wallet,
                chama=intent.chama,
                entry_type=entry_type,
                direction=LedgerDirection.CREDIT,
                amount=intent.amount,
                debit=ZERO,
                credit=intent.amount,
                currency=intent.currency or "KES",
                status=LedgerStatus.SUCCESS,
                provider="internal",
                provider_reference=intent.reference,
                idempotency_key=credit_key,
                related_payment=intent,
                narration=intent.description or "Wallet payment received",
                meta={
                    "payment_intent_id": str(intent.id),
                    "wallet_flow_kind": "wallet_payment",
                    "payment_purpose": str(intent.purpose),
                },
                created_by=intent.user,
                updated_by=intent.user,
            )
            chama_wallet.available_balance = Decimal(str(chama_wallet.available_balance or ZERO)) + intent.amount
            chama_wallet.save(update_fields=["available_balance", "updated_at"])

        provider_intent = provider.create_payment(
            amount=intent.amount,
            currency=intent.currency,
            reference=intent.reference,
            description=intent.description,
            payer_email=getattr(intent.user, "email", None),
            metadata={
                "intent_id": str(intent.id),
                "chama_id": str(intent.chama_id),
                "user_id": str(intent.user_id),
                **(kwargs.get("metadata") or {}),
            },
            idempotency_key=intent.idempotency_key,
        )
        intent.provider_intent_id = provider_intent.provider_intent_id
        next_status = cls._map_provider_status(provider_intent.status)
        cls._assert_status_transition(intent.status, next_status)
        intent.status = next_status
        intent.save(update_fields=["provider_intent_id", "status", "updated_at"])

    @classmethod
    def get_payment_status(cls, intent_id: uuid.UUID) -> PaymentIntent:
        """
        Get payment intent status.

        Args:
            intent_id: Payment intent ID

        Returns:
            PaymentIntent instance

        Raises:
            PaymentServiceError: If intent not found
        """
        try:
            intent = PaymentIntent.objects.get(id=intent_id)
            return intent
        except PaymentIntent.DoesNotExist:
            raise PaymentServiceError("Payment intent not found")

    @classmethod
    def verify_payment(cls, intent_id: uuid.UUID) -> PaymentIntent:
        """
        Verify payment status with provider.

        Args:
            intent_id: Payment intent ID

        Returns:
            Updated PaymentIntent instance

        Raises:
            PaymentServiceError: If verification fails
        """
        try:
            intent = PaymentIntent.objects.get(id=intent_id)

            if intent.is_terminal:
                return intent

            # Get provider and verify
            provider = cls._get_provider(intent.payment_method, intent.provider)
            result = provider.verify_payment(intent.provider_intent_id)
            try:
                cls._validate_provider_result(intent=intent, result=result)
            except PaymentServiceError as exc:
                cls._flag_reconciliation_issue(
                    intent=intent,
                    issue_type=ReconciliationMismatchType.PROVIDER_VERIFICATION_MISMATCH,
                    summary=str(exc),
                    metadata={
                        "provider": intent.provider,
                        "provider_intent_id": intent.provider_intent_id,
                    },
                    expected_amount=intent.amount,
                    received_amount=result.amount if getattr(result, "amount", None) else None,
                    expected_reference=intent.reference,
                    received_reference=result.provider_reference or intent.provider_intent_id,
                )
                raise

            with transaction.atomic():
                old_status = intent.status
                provider_reference = result.provider_reference or intent.provider_intent_id
                verified_amount = result.amount if result.amount and result.amount > 0 else intent.amount
                verified_currency = (result.currency or intent.currency).upper()
                next_status = cls._map_provider_status(result.status)
                cls._assert_status_transition(old_status, next_status)
                intent.status = next_status

                if result.failure_reason:
                    intent.failure_reason = result.failure_reason

                if intent.status == PaymentStatus.SUCCESS:
                    intent.completed_at = timezone.now()

                intent.save()
                cls._sync_method_specific_details(intent, result)

                # Create transaction record
                transaction_record = cls._upsert_transaction(
                    intent=intent,
                    provider_reference=provider_reference,
                    provider_name=intent.provider,
                    amount=verified_amount,
                    currency=verified_currency,
                    status=cls._map_transaction_status(intent.status),
                    payer_reference=result.payer_reference or "",
                    raw_response=result.provider_metadata or {},
                    verified_at=timezone.now() if intent.status == PaymentStatus.SUCCESS else None,
                    failed_at=timezone.now() if intent.status == PaymentStatus.FAILED else None,
                )

                # Create audit log
                PaymentAuditLog.objects.create(
                    payment_intent=intent,
                    event="payment_verified",
                    previous_status=old_status,
                    new_status=intent.status,
                    metadata={
                        "provider_reference": result.provider_reference,
                        "payer_reference": result.payer_reference,
                    },
                )

                # Handle successful payment
                if intent.status == PaymentStatus.SUCCESS and not hasattr(intent, "receipt"):
                    cls._handle_successful_payment(intent, transaction_record)
                    cls._notify(intent=intent, event="success")
                elif intent.status == PaymentStatus.FAILED:
                    cls._notify(intent=intent, event="failed")

                logger.info(
                    "Payment verified: %s, status: %s",
                    intent.id,
                    intent.status,
                )

                return intent

        except PaymentIntent.DoesNotExist:
            raise PaymentServiceError("Payment intent not found")
        except PaymentProviderError as e:
            logger.error("Payment verification failed: %s", e)
            raise PaymentServiceError(f"Provider verification error: {e}")
        except PaymentServiceError:
            raise
        except Exception as e:
            logger.error("Payment verification failed: %s", e)
            raise PaymentServiceError(f"Failed to verify payment: {e}")

    @classmethod
    def _handle_successful_payment(
        cls,
        intent: PaymentIntent,
        transaction_record: PaymentTransaction,
    ) -> None:
        """
        Handle successful payment.

        Posts ledger entries and generates receipt.
        """
        try:
            with transaction.atomic():
                business_event = cls._finalize_business_event(
                    intent=intent,
                    transaction_record=transaction_record,
                )
                business_record = business_event.get("record")
                ledger_handled = bool(business_event.get("ledger_handled"))
                metadata_updates = business_event.get("metadata_updates") or {}
                receipt_metadata = business_event.get("receipt_metadata") or {}

                if metadata_updates:
                    intent.metadata = {**(intent.metadata or {}), **metadata_updates}
                    intent.save(update_fields=["metadata", "updated_at"])

                # Post ledger entries
                if not ledger_handled and intent.purpose in [
                    PaymentPurpose.CONTRIBUTION,
                    PaymentPurpose.LOAN_REPAYMENT,
                    PaymentPurpose.FINE,
                    PaymentPurpose.MEETING_FEE,
                    PaymentPurpose.SPECIAL_CONTRIBUTION,
                ]:
                    cls._post_ledger_entries(
                        intent,
                        transaction_record,
                        source_record=business_record,
                    )

                # Generate receipt
                receipt = PaymentReceipt.objects.create(
                    payment_intent=intent,
                    transaction=transaction_record,
                    amount=intent.amount,
                    currency=intent.currency,
                    payment_method=intent.payment_method,
                    issued_by=intent.user,
                    metadata={
                        "provider_reference": transaction_record.provider_reference,
                        "payer_reference": transaction_record.payer_reference,
                        **receipt_metadata,
                    },
                )

                # Create audit log
                PaymentAuditLog.objects.create(
                    payment_intent=intent,
                    event="payment_completed",
                    new_status=PaymentStatus.SUCCESS,
                    metadata={
                        "receipt_number": receipt.receipt_number,
                        "reference_number": receipt.reference_number,
                    },
                )

                logger.info(
                    "Payment completed: %s, receipt: %s",
                    intent.id,
                    receipt.receipt_number,
                )
                cls._notify(intent=intent, event="receipt", receipt=receipt)

        except Exception as e:
            logger.error("Failed to handle successful payment %s: %s", intent.id, e)
            raise

    @classmethod
    def _post_ledger_entries(
        cls,
        intent: PaymentIntent,
        transaction_record: PaymentTransaction,
        source_record: Contribution | None = None,
    ) -> None:
        """
        Post ledger entries for payment.

        Debit: Payment method clearing account
        Credit: Purpose-specific account
        """
        try:
            idempotency_key = f"ledger:{intent.id}:{transaction_record.id}"
            debit_account_key = {
                PaymentMethod.MPESA: "mpesa_clearing",
                PaymentMethod.CASH: "cash_on_hand",
            }.get(intent.payment_method, "cash_on_hand")
            credit_account_key = {
                PaymentPurpose.CONTRIBUTION: "contributions_account",
                PaymentPurpose.FINE: "penalty_receivable",
                PaymentPurpose.LOAN_REPAYMENT: "loan_receivable",
                PaymentPurpose.MEETING_FEE: "meeting_fee_income",
                PaymentPurpose.SPECIAL_CONTRIBUTION: "special_contributions",
            }.get(intent.purpose, "contributions_account")
            entry_type = {
                PaymentPurpose.CONTRIBUTION: LedgerEntryType.CONTRIBUTION,
                PaymentPurpose.FINE: LedgerEntryType.PENALTY,
                PaymentPurpose.LOAN_REPAYMENT: LedgerEntryType.LOAN_REPAYMENT,
                PaymentPurpose.MEETING_FEE: LedgerEntryType.FEE,
                PaymentPurpose.SPECIAL_CONTRIBUTION: LedgerEntryType.CONTRIBUTION,
            }.get(intent.purpose, LedgerEntryType.ADJUSTMENT)
            source_type = {
                PaymentPurpose.CONTRIBUTION: JournalEntrySource.CONTRIBUTION,
                PaymentPurpose.FINE: JournalEntrySource.PENALTY,
                PaymentPurpose.LOAN_REPAYMENT: JournalEntrySource.LOAN_REPAYMENT,
                PaymentPurpose.MEETING_FEE: JournalEntrySource.PAYMENT,
                PaymentPurpose.SPECIAL_CONTRIBUTION: JournalEntrySource.PAYMENT,
            }.get(intent.purpose, JournalEntrySource.PAYMENT)

            debit_account = FinanceService._get_or_create_account(intent.chama, debit_account_key)
            credit_account = FinanceService._get_or_create_account(intent.chama, credit_account_key)
            actor = transaction_record.verified_by or intent.user
            if actor is None:
                raise PaymentServiceError("Payment actor unavailable for ledger posting")

            try:
                _journal, debit_line, _credit_line = FinanceService._create_balanced_journal(
                    chama=intent.chama,
                    actor=actor,
                    reference=intent.reference,
                    description=intent.description or f"{intent.purpose.replace('_', ' ').title()} payment",
                    source_type=source_type,
                    source_id=getattr(source_record, "id", None) or intent.id,
                    idempotency_key=idempotency_key,
                    entry_type=entry_type,
                    debit_account=debit_account,
                    credit_account=credit_account,
                    amount=intent.amount,
                    metadata={
                        "payment_intent_id": str(intent.id),
                        "payment_method": intent.payment_method,
                        "provider": intent.provider,
                        "provider_reference": transaction_record.provider_reference,
                        "payer_reference": transaction_record.payer_reference,
                    },
                )
                intent.metadata = {
                    **(intent.metadata or {}),
                    "ledger_entry_id": str(debit_line.id),
                    "debit_account_key": debit_account_key,
                    "credit_account_key": credit_account_key,
                }
                intent.save(update_fields=["metadata", "updated_at"])
            except IdempotencyConflictError:
                logger.warning("Ledger entry already exists for payment %s", intent.id)

            PaymentAuditLog.objects.create(
                payment_intent=intent,
                actor=actor,
                event="ledger_posted",
                metadata={
                    "debit_account": debit_account_key,
                    "credit_account": credit_account_key,
                    "idempotency_key": idempotency_key,
                },
            )

        except Exception as e:
            logger.error("Failed to post ledger for payment %s: %s", intent.id, e)
            raise

    @classmethod
    def _sync_method_specific_details(
        cls,
        intent: PaymentIntent,
        result: Any,
    ) -> None:
        """Persist method-specific provider details after verification."""
        if intent.payment_method == PaymentMethod.MPESA and hasattr(intent, "mpesa_details"):
            mpesa_details = intent.mpesa_details
            metadata = result.provider_metadata or {}
            mpesa_receipt = metadata.get("mpesa_receipt_number")
            if mpesa_receipt:
                mpesa_details.mpesa_receipt_number = mpesa_receipt
                mpesa_details.callback_received_at = timezone.now()
                mpesa_details.raw_callback = metadata
                mpesa_details.save(
                    update_fields=[
                        "mpesa_receipt_number",
                        "callback_received_at",
                        "raw_callback",
                        "updated_at",
                    ]
                )

    @classmethod
    def process_webhook(
        cls,
        payment_method: str,
        provider_name: str,
        payload: bytes,
        signature: str | None,
        headers: dict[str, str] | None = None,
        source_ip: str | None = None,
    ) -> PaymentWebhook:
        """
        Process webhook from payment provider.

        Args:
            payment_method: Payment method
            provider_name: Provider name
            payload: Raw webhook payload
            signature: Signature header
            headers: All request headers
            source_ip: Source IP address

        Returns:
            PaymentWebhook instance

        Raises:
            PaymentServiceError: If webhook processing fails
        """
        import json

        try:
            provider = cls._get_provider(payment_method, provider_name)

            verification_result = provider.verify_webhook_signature(
                payload, signature, headers
            )

            webhook_log = PaymentWebhook.objects.create(
                provider=provider_name,
                payment_method=payment_method,
                event_type=verification_result.event_type or "unknown",
                provider_reference=verification_result.provider_reference or "",
                signature_valid=verification_result.is_valid,
                signature=signature or "",
                payload=json.loads(payload) if verification_result.is_valid else {},
                headers=headers or {},
                source_ip=source_ip,
            )

            if not verification_result.is_valid:
                logger.warning(
                    "Webhook signature verification failed for %s: %s",
                    provider_name,
                    verification_result.error,
                )
                webhook_log.processing_error = f"Signature verification failed: {verification_result.error}"
                webhook_log.save()
                return webhook_log

            event_type, provider_reference, metadata = provider.parse_webhook_event(
                verification_result.payload
            )

            webhook_log.event_type = event_type
            webhook_log.provider_reference = provider_reference or ""
            webhook_log.save()

            # Handle success events
            if event_type in [
                "payment_intent.succeeded",
                "charge.succeeded",
                "payment.successful",
                "mpesa.success",
            ]:
                cls._handle_webhook_success(provider_reference, metadata, webhook_log)
            # Handle failure events
            elif event_type in [
                "payment_intent.payment_failed",
                "charge.failed",
                "payment.failed",
                "mpesa.failed",
            ]:
                cls._handle_webhook_failure(provider_reference, metadata, webhook_log)
            elif cls._is_dispute_event(event_type, metadata):
                cls._handle_webhook_dispute(
                    event_type=event_type,
                    provider_reference=provider_reference,
                    metadata=metadata,
                    webhook_log=webhook_log,
                )

            webhook_log.processed = True
            webhook_log.processed_at = timezone.now()
            webhook_log.save()

            logger.info(
                "Webhook processed: %s %s for %s",
                provider_name,
                event_type,
                provider_reference,
            )

            return webhook_log

        except json.JSONDecodeError as e:
            logger.error("Webhook JSON decode error: %s", e)
            raise PaymentServiceError(f"Invalid webhook payload: {e}")
        except Exception as e:
            logger.error("Webhook processing failed: %s", e)
            raise PaymentServiceError(f"Failed to process webhook: {e}")

    @classmethod
    def _handle_webhook_success(
        cls,
        provider_reference: str | None,
        metadata: dict[str, Any],
        webhook_log: PaymentWebhook,
    ) -> None:
        """Handle successful payment webhook."""
        if not provider_reference:
            logger.warning("Webhook success without provider reference")
            return

        try:
            intent = cls._resolve_intent_for_webhook(provider_reference, metadata)
            if not intent:
                raise PaymentIntent.DoesNotExist

            if intent.is_terminal:
                logger.info("Payment %s already in terminal state", intent.id)
                return

            if webhook_log.event_type == "mpesa.success" and intent.payment_method == PaymentMethod.MPESA:
                cls._finalize_mpesa_callback_success(
                    intent=intent,
                    provider_reference=provider_reference,
                    metadata=metadata,
                    webhook_log=webhook_log,
                )
                return

            cls.verify_payment(intent.id)

        except PaymentIntent.DoesNotExist:
            logger.warning(
                "Payment intent not found for provider reference: %s",
                provider_reference,
            )
            fallback_intent = (
                PaymentIntent.objects.filter(reference=metadata.get("reference"))
                .order_by("-created_at")
                .first()
            )
            if fallback_intent:
                cls._flag_reconciliation_issue(
                    intent=fallback_intent,
                    issue_type=ReconciliationMismatchType.ORPHAN_WEBHOOK,
                    summary="Webhook provider reference could not be matched to an intent.",
                    metadata=metadata,
                    expected_reference=fallback_intent.reference,
                    received_reference=provider_reference or "",
                    webhook=webhook_log,
                )
        except Exception as e:
            logger.error("Failed to handle webhook success: %s", e)
            webhook_log.processing_error = str(e)
            webhook_log.save()

    @classmethod
    def _handle_webhook_failure(
        cls,
        provider_reference: str | None,
        metadata: dict[str, Any],
        webhook_log: PaymentWebhook,
    ) -> None:
        """Handle failed payment webhook."""
        if not provider_reference:
            logger.warning("Webhook failure without provider reference")
            return

        try:
            intent = cls._resolve_intent_for_webhook(provider_reference, metadata)
            if not intent:
                raise PaymentIntent.DoesNotExist

            if intent.is_terminal:
                logger.info("Payment %s already in terminal state", intent.id)
                return

            if webhook_log.event_type == "mpesa.failed" and intent.payment_method == PaymentMethod.MPESA:
                cls._finalize_mpesa_callback_failure(
                    intent=intent,
                    provider_reference=provider_reference,
                    metadata=metadata,
                    webhook_log=webhook_log,
                )
                return

            with transaction.atomic():
                old_status = intent.status
                cls._assert_status_transition(old_status, PaymentStatus.FAILED)
                intent.status = PaymentStatus.FAILED
                intent.failure_reason = metadata.get("failure_reason", "Payment failed")
                intent.failure_code = metadata.get("failure_code", "unknown")
                intent.save()

                PaymentAuditLog.objects.create(
                    payment_intent=intent,
                    event="payment_failed",
                    previous_status=old_status,
                    new_status=PaymentStatus.FAILED,
                    metadata=metadata,
                )

                logger.info("Payment %s marked as failed via webhook", intent.id)

        except PaymentIntent.DoesNotExist:
            logger.warning(
                "Payment intent not found for provider reference: %s",
                provider_reference,
            )
            fallback_intent = (
                PaymentIntent.objects.filter(reference=metadata.get("reference"))
                .order_by("-created_at")
                .first()
            )
            if fallback_intent:
                cls._flag_reconciliation_issue(
                    intent=fallback_intent,
                    issue_type=ReconciliationMismatchType.ORPHAN_WEBHOOK,
                    summary="Failed-payment webhook could not be matched to an intent.",
                    metadata=metadata,
                    expected_reference=fallback_intent.reference,
                    received_reference=provider_reference or "",
                    webhook=webhook_log,
                )
        except Exception as e:
            logger.error("Failed to handle webhook failure: %s", e)
            webhook_log.processing_error = str(e)
            webhook_log.save()

    @classmethod
    def _finalize_mpesa_callback_success(
        cls,
        *,
        intent: PaymentIntent,
        provider_reference: str,
        metadata: dict[str, Any],
        webhook_log: PaymentWebhook,
    ) -> None:
        actor = cls._webhook_actor_for_intent(intent)
        now = timezone.now()
        with transaction.atomic():
            locked_intent = PaymentIntent.objects.select_for_update().get(id=intent.id)
            if locked_intent.is_terminal or hasattr(locked_intent, "receipt"):
                return

            old_status = locked_intent.status
            cls._assert_status_transition(old_status, PaymentStatus.SUCCESS)
            locked_intent.status = PaymentStatus.SUCCESS
            locked_intent.completed_at = locked_intent.completed_at or now
            locked_intent.failure_reason = ""
            locked_intent.failure_code = ""
            locked_intent.save(
                update_fields=[
                    "status",
                    "completed_at",
                    "failure_reason",
                    "failure_code",
                    "updated_at",
                ]
            )

            payer_reference = str(metadata.get("phone") or metadata.get("payer_reference") or "").strip()
            transaction_record = cls._upsert_transaction(
                intent=locked_intent,
                provider_reference=str(provider_reference),
                provider_name=locked_intent.provider or "safaricom",
                amount=locked_intent.amount,
                currency=locked_intent.currency,
                status=TransactionStatus.VERIFIED,
                payer_reference=payer_reference,
                raw_response={
                    "event_type": webhook_log.event_type,
                    **metadata,
                },
                verified_by=actor,
                verified_at=now,
            )

            cls._sync_method_specific_details(
                locked_intent,
                PaymentResult(
                    provider_reference=str(provider_reference),
                    status="success",
                    amount=locked_intent.amount,
                    currency=locked_intent.currency,
                    payer_reference=payer_reference,
                    provider_metadata={**metadata},
                ),
            )

            PaymentAuditLog.objects.create(
                payment_intent=locked_intent,
                actor=actor,
                event="provider_callback_confirmed",
                previous_status=old_status,
                new_status=locked_intent.status,
                metadata={
                    "provider_reference": provider_reference,
                    "event_type": webhook_log.event_type,
                    **metadata,
                },
            )

            cls._handle_successful_payment(locked_intent, transaction_record)
            cls._notify(intent=locked_intent, event="success")

    @classmethod
    def _finalize_mpesa_callback_failure(
        cls,
        *,
        intent: PaymentIntent,
        provider_reference: str,
        metadata: dict[str, Any],
        webhook_log: PaymentWebhook,
    ) -> None:
        actor = cls._webhook_actor_for_intent(intent)
        now = timezone.now()
        with transaction.atomic():
            locked_intent = PaymentIntent.objects.select_for_update().get(id=intent.id)
            if locked_intent.is_terminal:
                return

            old_status = locked_intent.status
            cls._assert_status_transition(old_status, PaymentStatus.FAILED)
            locked_intent.status = PaymentStatus.FAILED
            locked_intent.failure_reason = str(metadata.get("result_desc") or metadata.get("failure_reason") or "Payment failed")
            locked_intent.failure_code = str(metadata.get("result_code") or metadata.get("failure_code") or "mpesa_callback")
            locked_intent.save(
                update_fields=[
                    "status",
                    "failure_reason",
                    "failure_code",
                    "updated_at",
                ]
            )

            cls._upsert_transaction(
                intent=locked_intent,
                provider_reference=str(provider_reference),
                provider_name=locked_intent.provider or "safaricom",
                amount=locked_intent.amount,
                currency=locked_intent.currency,
                status=TransactionStatus.FAILED,
                payer_reference=str(metadata.get("phone") or "").strip(),
                raw_response={
                    "event_type": webhook_log.event_type,
                    **metadata,
                },
                verified_by=actor,
                failed_at=now,
            )

            if locked_intent.payment_method == PaymentMethod.MPESA and hasattr(locked_intent, "mpesa_details"):
                mpesa_details = locked_intent.mpesa_details
                mpesa_details.callback_received_at = now
                mpesa_details.raw_callback = {**metadata}
                mpesa_details.save(update_fields=["callback_received_at", "raw_callback", "updated_at"])

            PaymentAuditLog.objects.create(
                payment_intent=locked_intent,
                actor=actor,
                event="provider_callback_failed",
                previous_status=old_status,
                new_status=locked_intent.status,
                metadata={
                    "provider_reference": provider_reference,
                    "event_type": webhook_log.event_type,
                    **metadata,
                },
            )
            cls._notify(intent=locked_intent, event="failed")

    @staticmethod
    def _is_dispute_event(event_type: str, metadata: dict[str, Any]) -> bool:
        lowered_event = str(event_type or "").lower()
        if "dispute" in lowered_event or "chargeback" in lowered_event:
            return True
        dispute_status = str(metadata.get("dispute_status") or "").lower()
        return dispute_status in {"warning_needs_response", "warning_under_review", "under_review", "won", "lost"}

    @staticmethod
    def _derive_dispute_status_from_webhook(event_type: str, metadata: dict[str, Any]) -> str:
        lowered_event = str(event_type or "").lower()
        dispute_status = str(metadata.get("dispute_status") or "").lower()
        if any(token in lowered_event for token in ("won", "funds_reinstated")) or dispute_status in {
            "won",
            "warning_closed",
            "closed_won",
        }:
            return PaymentDisputeStatus.WON
        if any(token in lowered_event for token in ("lost",)) or dispute_status in {
            "lost",
            "charge_refunded",
            "closed_lost",
        }:
            return PaymentDisputeStatus.LOST
        if "closed" in lowered_event:
            return PaymentDisputeStatus.RESOLVED
        if any(token in lowered_event for token in ("created", "opened")):
            return PaymentDisputeStatus.OPEN
        return PaymentDisputeStatus.IN_REVIEW

    @staticmethod
    def _derive_dispute_category_from_webhook(event_type: str, metadata: dict[str, Any]) -> str:
        lowered_event = str(event_type or "").lower()
        lowered_reason = str(metadata.get("dispute_reason") or "").lower()
        if "fraud" in lowered_reason:
            return PaymentDisputeCategory.FRAUD
        if "chargeback" in lowered_event:
            return PaymentDisputeCategory.CHARGEBACK
        return PaymentDisputeCategory.PROVIDER_DISPUTE

    @staticmethod
    def _webhook_actor_for_intent(intent: PaymentIntent) -> Any:
        return intent.updated_by or intent.created_by or intent.user

    @classmethod
    def _resolve_intent_for_webhook(
        cls,
        provider_reference: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> PaymentIntent | None:
        metadata = metadata or {}
        candidate_refs = [
            provider_reference,
            metadata.get("payment_intent_id"),
            metadata.get("provider_payment_intent_id"),
            metadata.get("provider_transaction_reference"),
            metadata.get("charge_reference"),
        ]

        for candidate in candidate_refs:
            if not candidate:
                continue
            intent = (
                PaymentIntent.objects.filter(provider_intent_id=str(candidate))
                .order_by("-created_at")
                .first()
            )
            if intent:
                return intent
            transaction = (
                PaymentTransaction.objects.select_related("payment_intent")
                .filter(provider_reference=str(candidate))
                .order_by("-created_at")
                .first()
            )
            if transaction:
                return transaction.payment_intent

        reference = str(metadata.get("reference") or "").strip()
        if reference:
            return (
                PaymentIntent.objects.filter(reference=reference)
                .order_by("-created_at")
                .first()
            )
        return None

    @classmethod
    def _handle_webhook_dispute(
        cls,
        *,
        event_type: str,
        provider_reference: str | None,
        metadata: dict[str, Any],
        webhook_log: PaymentWebhook,
    ) -> None:
        if not provider_reference and not metadata.get("reference"):
            logger.warning("Webhook dispute without a resolvable reference")
            return

        try:
            with transaction.atomic():
                intent = cls._resolve_intent_for_webhook(provider_reference, metadata)
                if not intent:
                    raise PaymentIntent.DoesNotExist

                actor = cls._webhook_actor_for_intent(intent)
                dispute_status = cls._derive_dispute_status_from_webhook(event_type, metadata)
                dispute_category = cls._derive_dispute_category_from_webhook(event_type, metadata)
                dispute_amount_raw = metadata.get("amount")
                dispute_amount = to_decimal(dispute_amount_raw) if dispute_amount_raw not in (None, "") else None
                provider_case_reference = str(
                    metadata.get("provider_case_reference")
                    or metadata.get("dispute_id")
                    or ""
                ).strip()

                dispute = None
                if provider_case_reference:
                    dispute = (
                        PaymentDispute.objects.select_for_update()
                        .filter(
                            chama=intent.chama,
                            payment_intent=intent,
                            provider_case_reference=provider_case_reference,
                        )
                        .order_by("-created_at")
                        .first()
                    )
                if not dispute:
                    dispute = (
                        PaymentDispute.objects.select_for_update()
                        .filter(
                            chama=intent.chama,
                            payment_intent=intent,
                            category__in=[
                                PaymentDisputeCategory.CHARGEBACK,
                                PaymentDisputeCategory.PROVIDER_DISPUTE,
                                PaymentDisputeCategory.FRAUD,
                            ],
                            status__in=[
                                PaymentDisputeStatus.OPEN,
                                PaymentDisputeStatus.IN_REVIEW,
                            ],
                        )
                        .order_by("-created_at")
                        .first()
                    )

                reason = (
                    str(metadata.get("dispute_reason") or "").strip()
                    or f"Provider dispute webhook received: {event_type}"
                )

                if not dispute:
                    dispute = PaymentDispute.objects.create(
                        chama=intent.chama,
                        payment_intent=intent,
                        opened_by=intent.user,
                        category=dispute_category,
                        amount=dispute_amount,
                        reason=reason,
                        reference=intent.reference,
                        provider_case_reference=provider_case_reference,
                        status=PaymentDisputeStatus.OPEN,
                        metadata={
                            "opened_via": "provider_webhook",
                            "provider": webhook_log.provider,
                            "payment_method": intent.payment_method,
                            "webhook_event_type": event_type,
                        },
                        created_by=actor,
                        updated_by=actor,
                    )
                    PaymentAuditLog.objects.create(
                        payment_intent=intent,
                        actor=actor,
                        event="dispute_opened_via_webhook",
                        previous_status=intent.status,
                        new_status=intent.status,
                        metadata={
                            "dispute_id": str(dispute.id),
                            "event_type": event_type,
                            "provider_case_reference": provider_case_reference,
                        },
                    )

                dispute.metadata = {
                    **(dispute.metadata or {}),
                    "provider": webhook_log.provider,
                    "payment_method": intent.payment_method,
                    "last_webhook_event_type": event_type,
                    "last_webhook_id": str(webhook_log.id),
                    "last_webhook_received_at": timezone.now().isoformat(),
                    "dispute_status_hint": str(metadata.get("dispute_status") or ""),
                }
                if dispute_amount is not None:
                    dispute.amount = dispute_amount
                if provider_case_reference:
                    dispute.provider_case_reference = provider_case_reference
                if reason:
                    dispute.reason = reason
                dispute.updated_by = actor
                dispute.save(
                    update_fields=[
                        "amount",
                        "provider_case_reference",
                        "reason",
                        "metadata",
                        "updated_by",
                        "updated_at",
                    ]
                )

                if dispute_status in {
                    PaymentDisputeStatus.OPEN,
                    PaymentDisputeStatus.IN_REVIEW,
                } and dispute.status != dispute_status:
                    dispute.status = dispute_status
                    dispute.resolved_by = None
                    dispute.resolved_at = None
                    dispute.updated_by = actor
                    dispute.save(update_fields=["status", "resolved_by", "resolved_at", "updated_by", "updated_at"])
                elif dispute_status in {
                    PaymentDisputeStatus.WON,
                    PaymentDisputeStatus.LOST,
                    PaymentDisputeStatus.RESOLVED,
                }:
                    cls.resolve_dispute(
                        dispute_id=dispute.id,
                        actor=actor,
                        status=dispute_status,
                        resolution_notes=f"Resolved from provider webhook: {event_type}",
                        amount=dispute_amount,
                        provider_case_reference=provider_case_reference,
                        allow_system_action=True,
                    )
        except PaymentIntent.DoesNotExist:
            fallback_intent = (
                PaymentIntent.objects.filter(reference=metadata.get("reference"))
                .order_by("-created_at")
                .first()
            )
            if fallback_intent:
                cls._flag_reconciliation_issue(
                    intent=fallback_intent,
                    issue_type=ReconciliationMismatchType.ORPHAN_WEBHOOK,
                    summary="Dispute webhook could not be matched to an intent.",
                    metadata=metadata,
                    expected_reference=fallback_intent.reference,
                    received_reference=provider_reference or "",
                    webhook=webhook_log,
                )
        except Exception as e:
            logger.error("Failed to handle webhook dispute: %s", e)
            webhook_log.processing_error = str(e)
            webhook_log.save()

    @classmethod
    def get_payment_receipt(cls, intent_id: uuid.UUID) -> PaymentReceipt:
        """
        Get payment receipt.

        Args:
            intent_id: Payment intent ID

        Returns:
            PaymentReceipt instance

        Raises:
            PaymentServiceError: If receipt not found
        """
        try:
            intent = PaymentIntent.objects.get(id=intent_id)

            if intent.status not in {PaymentStatus.SUCCESS, PaymentStatus.RECONCILED}:
                raise PaymentServiceError("Payment not successful")

            receipt = PaymentReceipt.objects.get(payment_intent=intent)
            return receipt

        except PaymentIntent.DoesNotExist:
            raise PaymentServiceError("Payment intent not found")
        except PaymentReceipt.DoesNotExist:
            raise PaymentServiceError("Receipt not found")

    @classmethod
    def get_user_payments(
        cls,
        user: Any,
        chama_id: uuid.UUID | None = None,
        payment_method: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[PaymentIntent]:
        """
        Get user's payments.

        Args:
            user: User instance
            chama_id: Optional chama ID filter
            payment_method: Optional payment method filter
            status: Optional status filter
            limit: Maximum number of results

        Returns:
            List of PaymentIntent instances
        """
        queryset = PaymentIntent.objects.filter(user=user)

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)

        if payment_method:
            queryset = queryset.filter(payment_method=payment_method)

        if status:
            queryset = queryset.filter(status=status)

        return list(queryset.order_by("-created_at")[:limit])

    @classmethod
    def get_chama_payments(
        cls,
        chama: Chama,
        payment_method: str | None = None,
        status: str | None = None,
        start_date: Any | None = None,
        end_date: Any | None = None,
        limit: int = 100,
    ) -> list[PaymentIntent]:
        """
        Get chama's payments.

        Args:
            chama: Chama instance
            payment_method: Optional payment method filter
            status: Optional status filter
            start_date: Optional start date filter
            end_date: Optional end date filter
            limit: Maximum number of results

        Returns:
            List of PaymentIntent instances
        """
        queryset = PaymentIntent.objects.filter(chama=chama)

        if payment_method:
            queryset = queryset.filter(payment_method=payment_method)

        if status:
            queryset = queryset.filter(status=status)

        if start_date:
            queryset = queryset.filter(created_at__gte=start_date)

        if end_date:
            queryset = queryset.filter(created_at__lte=end_date)

        return list(queryset.order_by("-created_at")[:limit])

    @classmethod
    def approve_cash_payment(
        cls,
        *,
        intent_id: uuid.UUID,
        actor: Any,
        receipt_number: str = "",
        notes: str = "",
    ) -> PaymentIntent:
        try:
            with transaction.atomic():
                intent = PaymentIntent.objects.select_for_update().get(id=intent_id)
                if intent.payment_method != PaymentMethod.CASH:
                    raise PaymentServiceError("This payment is not a cash payment")
                cls._assert_finance_management_permission(chama=intent.chama, actor=actor)
                policy = cls._get_manual_approval_policy(chama=intent.chama)
                allowed_roles = cls._normalize_roles(
                    policy.allowed_cash_verifier_roles,
                    DEFAULT_CASH_VERIFIER_ROLES,
                )
                cls._assert_manual_role_allowed(
                    chama=intent.chama,
                    actor=actor,
                    allowed_roles=allowed_roles,
                    action_label="verify cash payments",
                )

                old_status = intent.status
                now = timezone.now()
                provider_reference = receipt_number or f"CASH-{intent.reference}"
                payer_reference = getattr(intent.user, "phone", "") or ""

                cash_details = intent.cash_details
                if policy.cash_maker_checker_enabled:
                    cls._enforce_manual_maker_checker(
                        actor=actor,
                        recorded_by_id=cash_details.received_by_id,
                        payer_user_id=intent.user_id if policy.block_payer_self_approval else None,
                    )
                if policy.require_cash_receipt_number and not receipt_number and not cash_details.receipt_number:
                    raise PaymentServiceError("Cash approval requires a receipt number by policy")
                if policy.require_cash_proof and not bool(cash_details.proof_photo):
                    raise PaymentServiceError("Cash approval requires proof photo by policy")
                if receipt_number:
                    cash_details.receipt_number = receipt_number
                if notes:
                    cash_details.notes = notes
                if not cash_details.received_by:
                    cash_details.received_by = actor

                requires_dual_approval = cls._manual_payment_requires_dual_approval(
                    policy=policy,
                    intent=intent,
                )
                if requires_dual_approval and not cash_details.first_verified_by_id:
                    cash_details.first_verified_by = actor
                    cash_details.first_verified_at = now
                    cash_details.save(
                        update_fields=[
                            "receipt_number",
                            "notes",
                            "received_by",
                            "first_verified_by",
                            "first_verified_at",
                            "updated_at",
                        ]
                    )
                    PaymentAuditLog.objects.create(
                        payment_intent=intent,
                        actor=actor,
                        event="cash_payment_first_approved",
                        previous_status=old_status,
                        new_status=intent.status,
                        metadata={"receipt_number": receipt_number, "notes": notes},
                    )
                    return intent
                if requires_dual_approval and cash_details.first_verified_by_id == getattr(actor, "id", None):
                    raise PaymentServiceError("A different approver is required for final cash verification")

                cash_details.verified_by = actor
                cash_details.verified_at = now
                cash_details.save()

                cls._assert_status_transition(old_status, PaymentStatus.SUCCESS)
                intent.status = PaymentStatus.SUCCESS
                intent.completed_at = now
                intent.failure_reason = ""
                intent.failure_code = ""
                intent.save(
                    update_fields=[
                        "status",
                        "completed_at",
                        "failure_reason",
                        "failure_code",
                        "updated_at",
                    ]
                )

                transaction_record = cls._upsert_transaction(
                    intent=intent,
                    provider_reference=provider_reference,
                    provider_name=intent.provider or "manual",
                    amount=intent.amount,
                    currency=intent.currency,
                    status=TransactionStatus.VERIFIED,
                    payer_reference=payer_reference,
                    raw_response={
                        "verification_type": "manual",
                        "receipt_number": receipt_number,
                        "notes": notes,
                    },
                    verified_by=actor,
                    verified_at=now,
                )

                PaymentAuditLog.objects.create(
                    payment_intent=intent,
                    actor=actor,
                    event="cash_payment_approved",
                    previous_status=old_status,
                    new_status=intent.status,
                    metadata={"receipt_number": receipt_number, "notes": notes},
                )

                if not hasattr(intent, "receipt"):
                    cls._handle_successful_payment(intent, transaction_record)
                    cls._notify(intent=intent, event="success")

                return intent
        except PaymentIntent.DoesNotExist as exc:
            raise PaymentServiceError("Payment intent not found") from exc

    @classmethod
    def reject_cash_payment(
        cls,
        *,
        intent_id: uuid.UUID,
        actor: Any,
        notes: str = "",
    ) -> PaymentIntent:
        try:
            with transaction.atomic():
                intent = PaymentIntent.objects.select_for_update().get(id=intent_id)
                if intent.payment_method != PaymentMethod.CASH:
                    raise PaymentServiceError("This payment is not a cash payment")
                cls._assert_finance_management_permission(chama=intent.chama, actor=actor)
                previous_status = intent.status
                cls._record_failure(
                    intent=intent,
                    previous_status=previous_status,
                    failure_reason=notes or "Cash payment rejected during manual review",
                    failure_code="cash_rejected",
                    metadata={"notes": notes},
                    actor=actor,
                )
                PaymentAuditLog.objects.create(
                    payment_intent=intent,
                    actor=actor,
                    event="cash_payment_rejected",
                    previous_status=previous_status,
                    new_status=intent.status,
                    metadata={"notes": notes},
                )
                return intent
        except PaymentIntent.DoesNotExist as exc:
            raise PaymentServiceError("Payment intent not found") from exc

    @classmethod
    def upload_bank_transfer_proof(
        cls,
        *,
        intent_id: uuid.UUID,
        actor: Any,
        transfer_reference: str = "",
        notes: str = "",
        proof_document=None,
    ) -> PaymentIntent:
        try:
            with transaction.atomic():
                intent = PaymentIntent.objects.select_for_update().get(id=intent_id)
                if intent.payment_method != PaymentMethod.BANK:
                    raise PaymentServiceError("This payment is not a bank transfer")

                bank_details = intent.bank_details
                policy = cls._get_manual_approval_policy(chama=intent.chama)

                actor_id = getattr(actor, "id", None)
                is_owner = bool(actor_id and intent.user_id and actor_id == intent.user_id)
                if not is_owner:
                    cls._assert_finance_management_permission(chama=intent.chama, actor=actor)

                cleaned_reference = str(transfer_reference or "").strip()[:100]
                if cleaned_reference:
                    bank_details.transfer_reference = cleaned_reference
                if notes:
                    bank_details.notes = str(notes)[:2000]
                if proof_document is not None:
                    bank_details.proof_document = proof_document

                if policy.require_bank_transfer_reference and not bank_details.transfer_reference:
                    raise PaymentServiceError("Transfer reference is required for bank transfer payments")
                if policy.require_bank_proof_document and not bool(bank_details.proof_document):
                    raise PaymentServiceError("Proof document is required for bank transfer payments")

                bank_details.save(update_fields=["transfer_reference", "notes", "proof_document", "updated_at"])

                PaymentAuditLog.objects.create(
                    payment_intent=intent,
                    actor=actor if getattr(actor, "is_authenticated", False) else None,
                    event="bank_transfer_proof_uploaded",
                    previous_status=intent.status,
                    new_status=intent.status,
                    metadata={"transfer_reference": bank_details.transfer_reference},
                )
                return intent
        except PaymentIntent.DoesNotExist as exc:
            raise PaymentServiceError("Payment intent not found") from exc

    @classmethod
    def verify_bank_payment(
        cls,
        *,
        intent_id: uuid.UUID,
        actor: Any,
        transfer_reference: str = "",
        notes: str = "",
    ) -> PaymentIntent:
        try:
            with transaction.atomic():
                intent = PaymentIntent.objects.select_for_update().get(id=intent_id)
                if intent.payment_method != PaymentMethod.BANK:
                    raise PaymentServiceError("This payment is not a bank transfer")
                cls._assert_finance_management_permission(chama=intent.chama, actor=actor)
                policy = cls._get_manual_approval_policy(chama=intent.chama)
                allowed_roles = cls._normalize_roles(
                    policy.allowed_bank_verifier_roles,
                    DEFAULT_BANK_VERIFIER_ROLES,
                )
                cls._assert_manual_role_allowed(
                    chama=intent.chama,
                    actor=actor,
                    allowed_roles=allowed_roles,
                    action_label="verify bank transfer payments",
                )

                bank_details = intent.bank_details
                old_status = intent.status
                now = timezone.now()

                cleaned_reference = str(transfer_reference or "").strip()[:100]
                if cleaned_reference:
                    bank_details.transfer_reference = cleaned_reference
                if notes:
                    bank_details.notes = str(notes)[:2000]

                if policy.require_bank_transfer_reference and not bank_details.transfer_reference:
                    raise PaymentServiceError("Transfer reference is required for bank transfer payments")
                if policy.require_bank_proof_document and not bool(bank_details.proof_document):
                    raise PaymentServiceError("Proof document is required for bank transfer payments")

                requires_dual_approval = cls._manual_payment_requires_dual_approval(
                    policy=policy,
                    intent=intent,
                )
                if policy.bank_maker_checker_enabled:
                    cls._enforce_manual_maker_checker(
                        actor=actor,
                        recorded_by_id=bank_details.first_verified_by_id,
                        payer_user_id=intent.user_id if policy.block_payer_self_approval else None,
                    )

                if requires_dual_approval and not bank_details.first_verified_by_id:
                    bank_details.first_verified_by = actor
                    bank_details.first_verified_at = now
                    bank_details.save(
                        update_fields=[
                            "transfer_reference",
                            "notes",
                            "first_verified_by",
                            "first_verified_at",
                            "updated_at",
                        ]
                    )
                    PaymentAuditLog.objects.create(
                        payment_intent=intent,
                        actor=actor,
                        event="bank_payment_first_approved",
                        previous_status=old_status,
                        new_status=intent.status,
                        metadata={"transfer_reference": bank_details.transfer_reference, "notes": notes},
                    )
                    return intent

                if requires_dual_approval and bank_details.first_verified_by_id == getattr(actor, "id", None):
                    raise PaymentServiceError("A different approver is required for final bank verification")

                bank_details.verified_by = actor
                bank_details.verified_at = now
                bank_details.save(update_fields=["transfer_reference", "notes", "verified_by", "verified_at", "updated_at"])

                cls._assert_status_transition(old_status, PaymentStatus.SUCCESS)
                intent.status = PaymentStatus.SUCCESS
                intent.completed_at = now
                intent.failure_reason = ""
                intent.failure_code = ""
                intent.save(
                    update_fields=[
                        "status",
                        "completed_at",
                        "failure_reason",
                        "failure_code",
                        "updated_at",
                    ]
                )

                provider_reference = bank_details.transfer_reference or f"BANK-{intent.reference}"
                payer_reference = getattr(intent.user, "phone", "") or ""
                transaction_record = cls._upsert_transaction(
                    intent=intent,
                    provider_reference=provider_reference,
                    provider_name=intent.provider or "manual",
                    amount=intent.amount,
                    currency=intent.currency,
                    status=TransactionStatus.VERIFIED,
                    payer_reference=payer_reference,
                    raw_response={
                        "verification_type": "manual",
                        "transfer_reference": provider_reference,
                        "notes": notes,
                    },
                    verified_by=actor,
                    verified_at=now,
                )

                PaymentAuditLog.objects.create(
                    payment_intent=intent,
                    actor=actor,
                    event="bank_payment_approved",
                    previous_status=old_status,
                    new_status=intent.status,
                    metadata={"transfer_reference": provider_reference, "notes": notes},
                )

                if not hasattr(intent, "receipt"):
                    cls._handle_successful_payment(intent, transaction_record)
                    cls._notify(intent=intent, event="success")

                return intent
        except PaymentIntent.DoesNotExist as exc:
            raise PaymentServiceError("Payment intent not found") from exc

    @classmethod
    def reject_bank_payment(
        cls,
        *,
        intent_id: uuid.UUID,
        actor: Any,
        notes: str = "",
    ) -> PaymentIntent:
        try:
            with transaction.atomic():
                intent = PaymentIntent.objects.select_for_update().get(id=intent_id)
                if intent.payment_method != PaymentMethod.BANK:
                    raise PaymentServiceError("This payment is not a bank transfer")
                cls._assert_finance_management_permission(chama=intent.chama, actor=actor)
                previous_status = intent.status
                cls._record_failure(
                    intent=intent,
                    previous_status=previous_status,
                    failure_reason=notes or "Bank transfer rejected during manual review",
                    failure_code="bank_rejected",
                    metadata={"notes": notes},
                    actor=actor,
                )
                PaymentAuditLog.objects.create(
                    payment_intent=intent,
                    actor=actor,
                    event="bank_payment_rejected",
                    previous_status=previous_status,
                    new_status=intent.status,
                    metadata={"notes": notes},
                )
                return intent
        except PaymentIntent.DoesNotExist as exc:
            raise PaymentServiceError("Payment intent not found") from exc

    @classmethod
    def _refund_notification(
        cls,
        *,
        refund: PaymentRefund,
        title: str,
        message: str,
    ) -> None:
        intent = refund.payment_intent
        if not intent.user:
            return
        try:
            create_notification(
                recipient=intent.user,
                chama=intent.chama,
                notification_type=NotificationType.PAYMENT_CONFIRMATION,
                title=title,
                message=message,
                priority=NotificationPriority.HIGH,
                category=NotificationCategory.PAYMENTS,
                action_url=f"/payments/{intent.id}",
                metadata={
                    **cls._notification_payload(intent),
                    "refund_id": str(refund.id),
                    "refund_amount": str(refund.amount),
                    "refund_status": refund.status,
                },
                send_email=True,
                send_sms=False,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to send refund notification for %s", refund.id)

    @staticmethod
    def _refundable_amount(intent: PaymentIntent) -> Decimal:
        refunded_total = (
            PaymentRefund.objects.filter(
                payment_intent=intent,
                status__in=[
                    PaymentRefundStatus.REQUESTED,
                    PaymentRefundStatus.APPROVED,
                    PaymentRefundStatus.PROCESSED,
                ],
            )
            .exclude(status=PaymentRefundStatus.REJECTED)
            .aggregate(total=Sum("amount"))
            .get("total")
            or Decimal("0.00")
        )
        return max(to_decimal(intent.amount) - to_decimal(refunded_total), Decimal("0.00"))

    @staticmethod
    def _get_business_record_id(intent: PaymentIntent) -> str:
        metadata = intent.metadata or {}
        return str(metadata.get("business_record_id") or "").strip()

    @classmethod
    def request_refund(
        cls,
        *,
        intent_id: uuid.UUID,
        actor: Any,
        amount: Decimal | None = None,
        reason: str = "",
        idempotency_key: str = "",
    ) -> PaymentRefund:
        with transaction.atomic():
            intent = PaymentIntent.objects.select_for_update().get(id=intent_id)
            if intent.status not in {
                PaymentStatus.SUCCESS,
                PaymentStatus.RECONCILED,
                PaymentStatus.PARTIALLY_REFUNDED,
            }:
                raise PaymentServiceError("Only successful payments can be refunded")

            actor_role = cls._get_actor_role(chama=intent.chama, actor=actor)
            own_payment = intent.user_id == getattr(actor, "id", None)
            if not own_payment and actor_role not in FINANCE_MANAGEMENT_ROLES:
                raise PaymentServiceError("You are not allowed to request a refund for this payment")
            if actor_role == MembershipRole.MEMBER and not own_payment:
                raise PaymentServiceError("Members can only request refunds for their own payments")
            if actor_role == MembershipRole.AUDITOR:
                raise PaymentServiceError("Auditors cannot request refunds")
            refund_amount = to_decimal(amount or intent.amount)
            if refund_amount <= Decimal("0.00"):
                raise PaymentServiceError("Refund amount must be greater than zero")
            available_amount = cls._refundable_amount(intent)
            if refund_amount > available_amount:
                raise PaymentServiceError("Refund amount exceeds refundable balance")
            if intent.purpose == PaymentPurpose.LOAN_REPAYMENT:
                repayment_id = cls._get_business_record_id(intent)
                if not repayment_id:
                    raise PaymentServiceError("Repayment record was not captured for this payment")
                try:
                    repayment = Repayment.objects.select_related("loan").get(
                        id=repayment_id,
                        loan__chama=intent.chama,
                        loan__member=intent.user,
                    )
                except Repayment.DoesNotExist as exc:
                    raise PaymentServiceError("Repayment record not found for this payment") from exc
                latest_repayment = (
                    Repayment.objects.filter(loan=repayment.loan)
                    .order_by("-date_paid", "-created_at")
                    .first()
                )
                if not latest_repayment or latest_repayment.id != repayment.id:
                    raise PaymentServiceError("Only the latest loan repayment can be refunded safely")

            refund = PaymentRefund.objects.create(
                chama=intent.chama,
                payment_intent=intent,
                amount=refund_amount,
                reason=(reason or "Refund requested").strip(),
                status=PaymentRefundStatus.REQUESTED,
                idempotency_key=idempotency_key or f"refund:{intent.id}:{uuid.uuid4().hex[:12]}",
                requested_by=actor,
                created_by=actor,
                updated_by=actor,
            )
            PaymentAuditLog.objects.create(
                payment_intent=intent,
                actor=actor,
                event="refund_requested",
                previous_status=intent.status,
                new_status=intent.status,
                metadata={"refund_id": str(refund.id), "amount": str(refund.amount)},
            )
            cls._refund_notification(
                refund=refund,
                title="Refund requested",
                message=f"Your refund request for {intent.currency} {refund.amount:,.2f} is awaiting review.",
            )
            return refund

    @classmethod
    def approve_refund(
        cls,
        *,
        refund_id: uuid.UUID,
        actor: Any,
        approve: bool = True,
        note: str = "",
    ) -> PaymentRefund:
        with transaction.atomic():
            refund = PaymentRefund.objects.select_for_update().select_related(
                "payment_intent",
                "chama",
                "requested_by",
            ).get(id=refund_id)
            allowed_roles = {
                MembershipRole.CHAMA_ADMIN,
                MembershipRole.ADMIN,
                MembershipRole.SUPERADMIN,
            }
            cls._assert_manual_role_allowed(
                chama=refund.chama,
                actor=actor,
                allowed_roles=allowed_roles,
                action_label="approve refunds",
            )
            if refund.requested_by_id == getattr(actor, "id", None):
                raise PaymentServiceError("Maker-checker rule prevents approving your own refund request")
            if refund.status not in {PaymentRefundStatus.REQUESTED, PaymentRefundStatus.APPROVED}:
                raise PaymentServiceError("Refund is not pending approval")

            refund.status = PaymentRefundStatus.APPROVED if approve else PaymentRefundStatus.REJECTED
            refund.approved_by = actor if approve else refund.approved_by
            refund.notes = note
            refund.updated_by = actor
            refund.save(
                update_fields=["status", "approved_by", "notes", "updated_by", "updated_at"]
            )
            PaymentAuditLog.objects.create(
                payment_intent=refund.payment_intent,
                actor=actor,
                event="refund_approved" if approve else "refund_rejected",
                previous_status=refund.payment_intent.status,
                new_status=refund.payment_intent.status,
                metadata={"refund_id": str(refund.id), "note": note},
            )
            cls._refund_notification(
                refund=refund,
                title="Refund approved" if approve else "Refund rejected",
                message=(
                    f"Your refund request for {refund.payment_intent.currency} {refund.amount:,.2f} "
                    f"was {'approved' if approve else 'rejected'}."
                ),
            )
            return refund

    @classmethod
    def _apply_business_refund(cls, *, refund: PaymentRefund, actor: Any) -> None:
        intent = refund.payment_intent
        if intent.purpose == PaymentPurpose.CONTRIBUTION:
            contribution_id = cls._get_business_record_id(intent) or str(intent.contribution_id or "")
            if not contribution_id:
                raise PaymentServiceError("Contribution record was not captured for this payment")
            try:
                result = FinanceService.reverse_contribution(
                    contribution_id,
                    {
                        "amount": str(refund.amount),
                        "idempotency_key": refund.idempotency_key,
                        "reason": refund.reason,
                    },
                    actor,
                )
            except FinanceServiceError as exc:
                raise PaymentServiceError(str(exc)) from exc
            refund.ledger_reversal_entry = result.ledger_entry

    @classmethod
    def _post_financial_reversal_for_intent(
        cls,
        *,
        intent: PaymentIntent,
        amount: Decimal,
        actor: Any,
        idempotency_key: str,
        reason: str,
        reference: str,
        source_id: uuid.UUID,
        metadata: dict[str, Any] | None = None,
    ):
        metadata = metadata or {}
        reversal_amount = to_decimal(amount)
        if reversal_amount <= Decimal("0.00"):
            raise PaymentServiceError("Reversal amount must be greater than zero")

        if intent.purpose == PaymentPurpose.CONTRIBUTION:
            contribution_id = cls._get_business_record_id(intent) or str(intent.contribution_id or "")
            if not contribution_id:
                raise PaymentServiceError("Contribution record was not captured for this payment")
            try:
                result = FinanceService.reverse_contribution(
                    contribution_id,
                    {
                        "amount": str(reversal_amount),
                        "idempotency_key": idempotency_key,
                        "reason": reason,
                    },
                    actor,
                )
            except FinanceServiceError as exc:
                raise PaymentServiceError(str(exc)) from exc
            return result.ledger_entry

        if intent.purpose == PaymentPurpose.LOAN_REPAYMENT:
            repayment_id = cls._get_business_record_id(intent)
            if not repayment_id:
                raise PaymentServiceError("Repayment record was not captured for this payment")
            try:
                result = FinanceService.reverse_repayment(
                    repayment_id,
                    {
                        "amount": str(reversal_amount),
                        "idempotency_key": idempotency_key,
                        "reason": reason,
                    },
                    actor,
                )
            except FinanceServiceError as exc:
                raise PaymentServiceError(str(exc)) from exc
            return result.ledger_entry

        debit_account_key = str((intent.metadata or {}).get("credit_account_key") or "")
        credit_account_key = str((intent.metadata or {}).get("debit_account_key") or "")
        if not debit_account_key or not credit_account_key:
            original_debit, original_credit = cls._original_ledger_accounts_for_intent(intent)
            debit_account_key = original_credit
            credit_account_key = original_debit

        _journal, debit_line, _credit_line = FinanceService._create_balanced_journal(
            chama=intent.chama,
            actor=actor,
            reference=reference,
            description=f"Reversal for payment {intent.reference}",
            source_type=JournalEntrySource.ADJUSTMENT,
            source_id=source_id,
            idempotency_key=idempotency_key,
            entry_type=LedgerEntryType.ADJUSTMENT,
            debit_account=FinanceService._get_or_create_account(intent.chama, debit_account_key),
            credit_account=FinanceService._get_or_create_account(intent.chama, credit_account_key),
            amount=reversal_amount,
            metadata={
                "payment_intent_id": str(intent.id),
                "payment_method": intent.payment_method,
                "reversal_reason": reason,
                **metadata,
            },
        )

        if intent.purpose == PaymentPurpose.FINE and intent.purpose_id:
            penalty = Penalty.objects.select_for_update().get(id=intent.purpose_id, chama=intent.chama)
            if reversal_amount > to_decimal(penalty.amount_paid):
                raise PaymentServiceError("Reversal amount exceeds settled penalty balance")
            penalty.amount_paid = to_decimal(penalty.amount_paid - reversal_amount)
            if penalty.amount_paid <= Decimal("0.00"):
                penalty.amount_paid = Decimal("0.00")
                penalty.status = PenaltyStatus.UNPAID
                penalty.resolved_at = None
                penalty.resolved_by = None
            elif penalty.amount_paid < penalty.amount:
                penalty.status = PenaltyStatus.PARTIAL
                penalty.resolved_at = None
                penalty.resolved_by = None
            else:
                penalty.status = PenaltyStatus.PAID
            penalty.updated_by = actor
            penalty.save(
                update_fields=[
                    "amount_paid",
                    "status",
                    "resolved_at",
                    "resolved_by",
                    "updated_by",
                    "updated_at",
                ]
            )

        FinanceService._refresh_financial_snapshot(intent.chama, timezone.localdate())
        return debit_line

    @classmethod
    def _update_intent_after_reversal(
        cls,
        *,
        intent: PaymentIntent,
        transaction_record: PaymentTransaction,
        actor: Any,
        reversal_amount: Decimal,
        source_label: str,
        source_metadata: dict[str, Any] | None = None,
    ) -> None:
        source_metadata = source_metadata or {}
        refunded_total = (
            PaymentRefund.objects.filter(
                payment_intent=intent,
                status=PaymentRefundStatus.PROCESSED,
            ).aggregate(total=Sum("amount"))["total"]
            or Decimal("0.00")
        )
        chargeback_total = to_decimal((intent.metadata or {}).get("chargeback_total") or Decimal("0.00"))
        returned_total = to_decimal(refunded_total + chargeback_total)
        previous_intent_status = intent.status
        intent.status = (
            PaymentStatus.REFUNDED
            if returned_total >= to_decimal(intent.amount)
            else PaymentStatus.PARTIALLY_REFUNDED
        )
        if previous_intent_status != intent.status:
            cls._assert_status_transition(previous_intent_status, intent.status)

        if source_label == "chargeback" and returned_total >= to_decimal(transaction_record.amount):
            transaction_record.status = TransactionStatus.REVERSED
        elif returned_total >= to_decimal(transaction_record.amount):
            transaction_record.status = TransactionStatus.REFUNDED
        else:
            transaction_record.status = TransactionStatus.PARTIALLY_REFUNDED
        transaction_record.verified_by = actor
        transaction_record.verified_at = timezone.now()
        transaction_record.raw_response = {
            **(transaction_record.raw_response or {}),
            **source_metadata,
            "returned_total": str(returned_total),
            "transaction_status": transaction_record.status,
        }
        transaction_record.save(
            update_fields=["status", "verified_by", "verified_at", "raw_response", "updated_at"]
        )

        intent.metadata = {
            **(intent.metadata or {}),
            "refunded_total": str(refunded_total),
            "chargeback_total": str(chargeback_total),
            "returned_total": str(returned_total),
            "refundable_balance": str(
                max(to_decimal(intent.amount) - to_decimal(returned_total), Decimal("0.00"))
            ),
            "refund_status": intent.status,
            f"{source_label}_last_processed_at": timezone.now().isoformat(),
        }
        intent.save(update_fields=["status", "metadata", "updated_at"])

    @classmethod
    def process_refund(
        cls,
        *,
        refund_id: uuid.UUID,
        actor: Any,
    ) -> PaymentRefund:
        with transaction.atomic():
            refund = PaymentRefund.objects.select_for_update().select_related(
                "payment_intent",
                "chama",
                "approved_by",
            ).get(id=refund_id)
            intent = refund.payment_intent
            cls._assert_manual_role_allowed(
                chama=refund.chama,
                actor=actor,
                allowed_roles=DEFAULT_CASH_VERIFIER_ROLES,
                action_label="process refunds",
            )
            if refund.status == PaymentRefundStatus.PROCESSED:
                return refund
            if refund.status != PaymentRefundStatus.APPROVED:
                raise PaymentServiceError("Only approved refunds can be processed")
            if refund.approved_by_id == getattr(actor, "id", None):
                raise PaymentServiceError("Maker-checker rule prevents the approver from processing the same refund")

            transaction_record = intent.transactions.order_by("-created_at").first()
            if not transaction_record:
                raise PaymentServiceError("No payment transaction found for this refund")

            if intent.payment_method == PaymentMethod.MPESA:
                provider = cls._get_provider(intent.payment_method, intent.provider)
                try:
                    provider.refund_payment(
                        provider_reference=transaction_record.provider_reference or intent.provider_intent_id,
                        amount=refund.amount,
                        reason=refund.reason,
                        idempotency_key=refund.idempotency_key,
                    )
                except PaymentProviderError as exc:
                    refund.status = PaymentRefundStatus.FAILED
                    refund.notes = str(exc)[:300]
                    refund.updated_by = actor
                    refund.save(update_fields=["status", "notes", "updated_by", "updated_at"])
                    raise PaymentServiceError("Provider refund failed") from exc

            debit_line = cls._post_financial_reversal_for_intent(
                intent=intent,
                amount=refund.amount,
                actor=actor,
                idempotency_key=refund.idempotency_key,
                reason=refund.reason,
                reference=f"refund:{refund.id}",
                source_id=refund.id,
                metadata={
                    "refund_id": str(refund.id),
                    "reversal_origin": "refund",
                },
            )

            refund.status = PaymentRefundStatus.PROCESSED
            refund.processed_by = actor
            refund.processed_at = timezone.now()
            if debit_line and not refund.ledger_reversal_entry_id:
                refund.ledger_reversal_entry = debit_line
            refund.updated_by = actor
            refund.save(
                update_fields=[
                    "status",
                    "processed_by",
                    "processed_at",
                    "ledger_reversal_entry",
                    "updated_by",
                    "updated_at",
                ]
            )

            previous_intent_status = intent.status
            cls._update_intent_after_reversal(
                intent=intent,
                transaction_record=transaction_record,
                actor=actor,
                reversal_amount=refund.amount,
                source_label="refund",
                source_metadata={
                    "refund_id": str(refund.id),
                    "refund_amount": str(refund.amount),
                    "refund_status": "processed",
                },
            )

            PaymentAuditLog.objects.create(
                payment_intent=intent,
                actor=actor,
                event="refund_processed",
                previous_status=previous_intent_status,
                new_status=intent.status,
                metadata={"refund_id": str(refund.id), "ledger_entry_id": str(debit_line.id)},
            )
            cls._refund_notification(
                refund=refund,
                title="Refund completed",
                message=f"Your refund of {intent.currency} {refund.amount:,.2f} has been completed.",
            )
            return refund

    @classmethod
    def list_refunds(
        cls,
        *,
        chama: Chama,
        actor: Any,
        status_filter: str | None = None,
        limit: int = 100,
    ) -> list[PaymentRefund]:
        cls._assert_finance_management_permission(chama=chama, actor=actor)
        queryset = PaymentRefund.objects.select_related(
            "payment_intent",
            "requested_by",
            "approved_by",
            "processed_by",
        ).filter(chama=chama)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        return list(queryset.order_by("-created_at")[:limit])

    @classmethod
    def open_dispute(
        cls,
        *,
        chama: Chama,
        actor: Any,
        intent_id: uuid.UUID | None = None,
        category: str = PaymentDisputeCategory.OTHER,
        amount: Decimal | None = None,
        reason: str,
        reference: str = "",
        provider_case_reference: str = "",
    ) -> PaymentDispute:
        actor_role = cls._get_actor_role(chama=chama, actor=actor)
        if not actor_role:
            raise PaymentServiceError("Membership required to open disputes for this chama")
        if actor_role == MembershipRole.AUDITOR:
            raise PaymentServiceError("Auditors cannot open payment disputes")

        intent = None
        if intent_id:
            try:
                intent = PaymentIntent.objects.get(id=intent_id, chama=chama)
            except PaymentIntent.DoesNotExist as exc:
                raise PaymentServiceError("Payment intent not found for this dispute") from exc
            own_payment = intent.user_id == getattr(actor, "id", None)
            if actor_role == MembershipRole.MEMBER and not own_payment:
                raise PaymentServiceError("Members can only dispute their own payments")

        dispute = PaymentDispute.objects.create(
            chama=chama,
            payment_intent=intent,
            opened_by=actor,
            category=category,
            amount=to_decimal(amount) if amount is not None else None,
            reason=reason,
            reference=reference.strip(),
            provider_case_reference=provider_case_reference.strip(),
            status=PaymentDisputeStatus.OPEN,
            metadata={
                "opened_via": "unified_payments",
                "payment_method": intent.payment_method if intent else "",
                "intent_status": intent.status if intent else "",
            },
            created_by=actor,
            updated_by=actor,
        )
        if intent:
            PaymentAuditLog.objects.create(
                payment_intent=intent,
                actor=actor,
                event="dispute_opened",
                previous_status=intent.status,
                new_status=intent.status,
                metadata={
                    "dispute_id": str(dispute.id),
                    "category": dispute.category,
                    "provider_case_reference": dispute.provider_case_reference,
                },
            )
        return dispute

    @classmethod
    def list_disputes(
        cls,
        *,
        chama: Chama,
        actor: Any,
        intent_id: uuid.UUID | None = None,
        status_filter: str | None = None,
        limit: int = 100,
    ) -> list[PaymentDispute]:
        actor_role = cls._get_actor_role(chama=chama, actor=actor)
        if not actor_role:
            raise PaymentServiceError("Membership required to view disputes for this chama")

        queryset = PaymentDispute.objects.select_related(
            "payment_intent",
            "opened_by",
            "resolved_by",
            "financial_reversal_entry",
        ).filter(chama=chama)
        if actor_role == MembershipRole.MEMBER:
            queryset = queryset.filter(
                Q(payment_intent__user=actor) | Q(opened_by=actor)
            )
        if intent_id:
            queryset = queryset.filter(payment_intent_id=intent_id)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        return list(queryset.order_by("-created_at")[:limit])

    @classmethod
    def resolve_dispute(
        cls,
        *,
        dispute_id: uuid.UUID,
        actor: Any,
        status: str,
        resolution_notes: str = "",
        amount: Decimal | None = None,
        provider_case_reference: str = "",
        allow_system_action: bool = False,
    ) -> PaymentDispute:
        with transaction.atomic():
            try:
                dispute = PaymentDispute.objects.select_for_update().select_related(
                    "chama",
                    "payment_intent",
                    "payment_intent__user",
                ).get(id=dispute_id)
            except PaymentDispute.DoesNotExist as exc:
                raise PaymentServiceError("Payment dispute not found") from exc

            allowed_roles = {
                MembershipRole.SECRETARY,
                MembershipRole.TREASURER,
                MembershipRole.CHAMA_ADMIN,
                MembershipRole.ADMIN,
                MembershipRole.SUPERADMIN,
            }
            if not allow_system_action:
                cls._assert_manual_role_allowed(
                    chama=dispute.chama,
                    actor=actor,
                    allowed_roles=allowed_roles,
                    action_label="resolve payment disputes",
                )

            dispute.status = status
            dispute.resolution_notes = resolution_notes
            if provider_case_reference.strip():
                dispute.provider_case_reference = provider_case_reference.strip()
            if amount is not None:
                dispute.amount = to_decimal(amount)

            if status in {
                PaymentDisputeStatus.RESOLVED,
                PaymentDisputeStatus.REJECTED,
                PaymentDisputeStatus.WON,
                PaymentDisputeStatus.LOST,
            }:
                dispute.resolved_by = actor
                dispute.resolved_at = timezone.now()
            else:
                dispute.resolved_by = None
                dispute.resolved_at = None

            if status == PaymentDisputeStatus.LOST:
                intent = dispute.payment_intent
                if not intent:
                    raise PaymentServiceError("A lost dispute must be linked to a payment intent")
                if dispute.category not in {
                    PaymentDisputeCategory.CHARGEBACK,
                    PaymentDisputeCategory.PROVIDER_DISPUTE,
                    PaymentDisputeCategory.FRAUD,
                }:
                    raise PaymentServiceError("Only provider disputes, fraud cases, and chargebacks can be marked as lost")

                if intent.status not in {
                    PaymentStatus.SUCCESS,
                    PaymentStatus.RECONCILED,
                    PaymentStatus.PARTIALLY_REFUNDED,
                }:
                    raise PaymentServiceError("Only successful payments can absorb a chargeback loss")

                remaining_refundable = cls._refundable_amount(intent)
                chargeback_amount = to_decimal(dispute.amount or amount or remaining_refundable)
                if chargeback_amount <= Decimal("0.00"):
                    raise PaymentServiceError("Chargeback loss amount must be greater than zero")
                if chargeback_amount > remaining_refundable:
                    raise PaymentServiceError("Chargeback loss amount exceeds the remaining reversible balance")

                transaction_record = intent.transactions.order_by("-created_at").first()
                if not transaction_record:
                    raise PaymentServiceError("No payment transaction found for this dispute")

                debit_line = cls._post_financial_reversal_for_intent(
                    intent=intent,
                    amount=chargeback_amount,
                    actor=actor,
                    idempotency_key=f"dispute:{dispute.id}:chargeback",
                    reason=resolution_notes or "Chargeback loss",
                    reference=f"dispute:{dispute.id}:chargeback",
                    source_id=dispute.id,
                    metadata={
                        "dispute_id": str(dispute.id),
                        "reversal_origin": "chargeback_loss",
                        "provider_case_reference": dispute.provider_case_reference,
                    },
                )

                prior_chargeback_total = to_decimal(
                    (intent.metadata or {}).get("chargeback_total") or Decimal("0.00")
                )
                intent.metadata = {
                    **(intent.metadata or {}),
                    "chargeback_total": str(to_decimal(prior_chargeback_total + chargeback_amount)),
                    "chargeback_last_processed_at": timezone.now().isoformat(),
                    "chargeback_case_reference": dispute.provider_case_reference,
                }
                intent.save(update_fields=["metadata", "updated_at"])
                cls._update_intent_after_reversal(
                    intent=intent,
                    transaction_record=transaction_record,
                    actor=actor,
                    reversal_amount=chargeback_amount,
                    source_label="chargeback",
                    source_metadata={
                        "dispute_id": str(dispute.id),
                        "chargeback_amount": str(chargeback_amount),
                        "chargeback_case_reference": dispute.provider_case_reference,
                    },
                )
                dispute.amount = chargeback_amount
                dispute.financial_reversal_entry = debit_line

            dispute.metadata = {
                **(dispute.metadata or {}),
                "resolved_via": "unified_payments",
                "resolved_status": dispute.status,
            }
            dispute.updated_by = actor
            dispute.save(
                update_fields=[
                    "status",
                    "amount",
                    "provider_case_reference",
                    "resolution_notes",
                    "financial_reversal_entry",
                    "metadata",
                    "resolved_by",
                    "resolved_at",
                    "updated_by",
                    "updated_at",
                ]
            )

            if dispute.payment_intent_id:
                PaymentAuditLog.objects.create(
                    payment_intent=dispute.payment_intent,
                    actor=actor,
                    event="dispute_resolved",
                    previous_status=dispute.payment_intent.status,
                    new_status=dispute.payment_intent.status,
                    metadata={
                        "dispute_id": str(dispute.id),
                        "status": dispute.status,
                        "provider_case_reference": dispute.provider_case_reference,
                        "financial_reversal_entry_id": str(dispute.financial_reversal_entry_id or ""),
                    },
                )
            return dispute

    @classmethod
    def record_settlement(
        cls,
        *,
        chama: Chama,
        actor: Any,
        payment_method: str,
        settlement_reference: str,
        gross_amount: Decimal,
        fee_amount: Decimal = Decimal("0.00"),
        settlement_date=None,
        provider_name: str = "",
        currency: str = CurrencyChoices.KES,
        statement_import_id: uuid.UUID | None = None,
        transaction_ids: list[uuid.UUID] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PaymentSettlement:
        if payment_method not in cls.SETTLEMENT_METHODS:
            raise PaymentServiceError("Settlement recording is currently available for M-Pesa only")
        policy = cls._get_manual_approval_policy(chama=chama)
        allowed_roles = cls._normalize_roles(
            policy.allowed_reconciliation_roles,
            DEFAULT_RECONCILIATION_ROLES,
        )
        cls._assert_manual_role_allowed(
            chama=chama,
            actor=actor,
            allowed_roles=allowed_roles,
            action_label="record settlement postings",
        )

        gross_amount = to_decimal(gross_amount)
        fee_amount = to_decimal(fee_amount or Decimal("0.00"))
        if gross_amount <= Decimal("0.00"):
            raise PaymentServiceError("Settlement gross amount must be greater than zero")
        if fee_amount < Decimal("0.00"):
            raise PaymentServiceError("Settlement fee amount cannot be negative")
        if fee_amount > gross_amount:
            raise PaymentServiceError("Settlement fee amount cannot exceed gross amount")

        settlement_reference = str(settlement_reference or "").strip()
        if not settlement_reference:
            raise PaymentServiceError("Settlement reference is required")

        clearing_account_key = cls._settlement_clearing_account_key(payment_method)
        destination_account_key = "bank_account"
        fee_account_key = "payment_processing_fees"
        settlement_date = settlement_date or timezone.localdate()
        metadata = metadata or {}

        with transaction.atomic():
            statement_import = None
            if statement_import_id:
                statement_import = PaymentStatementImport.objects.select_for_update().get(
                    id=statement_import_id,
                    chama=chama,
                    payment_method=payment_method,
                )

            linked_transactions_qs = PaymentTransaction.objects.select_for_update().select_related(
                "payment_intent"
            ).filter(
                payment_intent__chama=chama,
                payment_method=payment_method,
                status__in=[
                    TransactionStatus.VERIFIED,
                    TransactionStatus.PARTIALLY_REFUNDED,
                    TransactionStatus.REFUNDED,
                ],
            )
            if provider_name:
                linked_transactions_qs = linked_transactions_qs.filter(provider_name=provider_name)
            if transaction_ids:
                linked_transactions_qs = linked_transactions_qs.filter(id__in=transaction_ids)
            elif statement_import:
                matched_transaction_ids = list(
                    statement_import.lines.filter(
                        match_status=StatementLineMatchStatus.MATCHED,
                        matched_transaction__isnull=False,
                    ).values_list("matched_transaction_id", flat=True)
                )
                if not matched_transaction_ids:
                    raise PaymentServiceError("Statement import has no matched transactions ready for settlement")
                linked_transactions_qs = linked_transactions_qs.filter(id__in=matched_transaction_ids)
            else:
                raise PaymentServiceError("Settlement requires statement import rows or selected transactions")

            linked_transactions = list(linked_transactions_qs.order_by("created_at"))
            if not linked_transactions:
                raise PaymentServiceError("No verified transactions were found for this settlement")

            duplicate_allocations = list(
                PaymentSettlementAllocation.objects.filter(
                    payment_transaction__in=linked_transactions,
                    settlement__status__in=[
                        SettlementStatus.PENDING,
                        SettlementStatus.POSTED,
                        SettlementStatus.RECONCILED,
                    ],
                ).values_list("payment_transaction_id", flat=True)
            )
            if duplicate_allocations:
                raise PaymentServiceError("One or more transactions have already been linked to a settlement")

            expected_gross = to_decimal(
                sum((to_decimal(tx.amount) for tx in linked_transactions), Decimal("0.00"))
            )
            if expected_gross != gross_amount:
                PaymentReconciliationCase.objects.create(
                    chama=chama,
                    mismatch_type=ReconciliationMismatchType.MANUAL_REVIEW,
                    case_status=ReconciliationCaseStatus.OPEN,
                    expected_amount=expected_gross,
                    received_amount=gross_amount,
                    received_reference=settlement_reference,
                    assigned_to=actor if cls._get_actor_role(chama=chama, actor=actor) else None,
                    resolution_notes="Settlement gross did not match the selected verified transactions.",
                    metadata={
                        "summary": "Settlement total does not match the linked transaction gross amount.",
                        "provider_name": provider_name,
                        "payment_method": payment_method,
                        "transaction_count": len(linked_transactions),
                    },
                    created_by=actor,
                    updated_by=actor,
                )
                raise PaymentServiceError("Settlement gross amount does not match the linked transaction total")

            settlement, created = PaymentSettlement.objects.get_or_create(
                settlement_reference=settlement_reference,
                defaults={
                    "chama": chama,
                    "statement_import": statement_import,
                    "payment_method": payment_method,
                    "provider_name": provider_name,
                    "settlement_date": settlement_date,
                    "currency": currency,
                    "gross_amount": gross_amount,
                    "fee_amount": fee_amount,
                    "net_amount": to_decimal(gross_amount - fee_amount),
                    "status": SettlementStatus.RECONCILED if statement_import else SettlementStatus.POSTED,
                    "clearing_account_key": clearing_account_key,
                    "destination_account_key": destination_account_key,
                    "fee_account_key": fee_account_key,
                    "posted_by": actor,
                    "posted_at": timezone.now(),
                    "metadata": metadata,
                    "created_by": actor,
                    "updated_by": actor,
                },
            )
            if not created:
                return settlement

            bank_journal, _debit_line, _credit_line = FinanceService._create_balanced_journal(
                chama=chama,
                actor=actor,
                reference=f"settlement:{settlement_reference}:net",
                description=f"{payment_method.title()} settlement {settlement_reference}",
                source_type=JournalEntrySource.ADJUSTMENT,
                source_id=settlement.id,
                idempotency_key=f"settlement:{settlement.id}:net",
                entry_type=LedgerEntryType.ADJUSTMENT,
                debit_account=FinanceService._get_or_create_account(chama, destination_account_key),
                credit_account=FinanceService._get_or_create_account(chama, clearing_account_key),
                amount=settlement.net_amount,
                metadata={
                    "settlement_id": str(settlement.id),
                    "payment_method": payment_method,
                    "provider_name": provider_name,
                    "settlement_reference": settlement_reference,
                    "transaction_count": len(linked_transactions),
                },
            )
            settlement.journal_entry = bank_journal

            if fee_amount > Decimal("0.00"):
                fee_journal, _fee_debit, _fee_credit = FinanceService._create_balanced_journal(
                    chama=chama,
                    actor=actor,
                    reference=f"settlement:{settlement_reference}:fee",
                    description=f"{payment_method.title()} settlement fee {settlement_reference}",
                    source_type=JournalEntrySource.ADJUSTMENT,
                    source_id=settlement.id,
                    idempotency_key=f"settlement:{settlement.id}:fee",
                    entry_type=LedgerEntryType.ADJUSTMENT,
                    debit_account=FinanceService._get_or_create_account(chama, fee_account_key),
                    credit_account=FinanceService._get_or_create_account(chama, clearing_account_key),
                    amount=fee_amount,
                    metadata={
                        "settlement_id": str(settlement.id),
                        "payment_method": payment_method,
                        "provider_name": provider_name,
                        "settlement_reference": settlement_reference,
                        "fee_amount": str(fee_amount),
                    },
                )
                settlement.metadata = {
                    **(settlement.metadata or {}),
                    "fee_journal_entry_id": str(fee_journal.id),
                }

            settlement.save(update_fields=["journal_entry", "metadata", "updated_at"])

            for tx in linked_transactions:
                PaymentSettlementAllocation.objects.create(
                    settlement=settlement,
                    payment_transaction=tx,
                    settled_amount=tx.amount,
                    metadata={
                        "payment_intent_id": str(tx.payment_intent_id),
                        "provider_reference": tx.provider_reference,
                    },
                    created_by=actor,
                    updated_by=actor,
                )
                PaymentAuditLog.objects.create(
                    payment_intent=tx.payment_intent,
                    actor=actor,
                    event="payment_settled",
                    previous_status=tx.payment_intent.status,
                    new_status=tx.payment_intent.status,
                    metadata={
                        "settlement_id": str(settlement.id),
                        "settlement_reference": settlement_reference,
                        "settled_amount": str(tx.amount),
                    },
                )
                tx.payment_intent.metadata = {
                    **(tx.payment_intent.metadata or {}),
                    "settlement_reference": settlement_reference,
                    "settled_at": timezone.now().isoformat(),
                    "settlement_id": str(settlement.id),
                }
                tx.payment_intent.save(update_fields=["metadata", "updated_at"])

            if statement_import:
                statement_import.metadata = {
                    **(statement_import.metadata or {}),
                    "settlement_id": str(settlement.id),
                    "settlement_reference": settlement_reference,
                }
                statement_import.save(update_fields=["metadata", "updated_at"])

            return settlement

    @classmethod
    def list_settlements(
        cls,
        *,
        chama: Chama,
        actor: Any,
        payment_method: str | None = None,
        limit: int = 100,
    ) -> list[PaymentSettlement]:
        policy = cls._get_manual_approval_policy(chama=chama)
        allowed_roles = cls._normalize_roles(
            policy.allowed_reconciliation_roles,
            DEFAULT_RECONCILIATION_ROLES,
        )
        cls._assert_manual_role_allowed(
            chama=chama,
            actor=actor,
            allowed_roles=allowed_roles,
            action_label="view settlement records",
        )
        queryset = PaymentSettlement.objects.filter(chama=chama).select_related(
            "posted_by",
            "statement_import",
            "journal_entry",
        ).prefetch_related("allocations__payment_transaction")
        if payment_method:
            queryset = queryset.filter(payment_method=payment_method)
        return list(queryset.order_by("-settlement_date", "-created_at")[:limit])

    @classmethod
    def get_manual_approval_policy(
        cls,
        *,
        chama: Chama,
        actor: Any,
    ) -> ManualPaymentApprovalPolicy:
        cls._assert_finance_management_permission(chama=chama, actor=actor)
        return cls._get_manual_approval_policy(chama=chama)

    @classmethod
    def update_manual_approval_policy(
        cls,
        *,
        chama: Chama,
        actor: Any,
        payload: dict[str, Any],
    ) -> ManualPaymentApprovalPolicy:
        allowed_roles = {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.ADMIN,
            MembershipRole.SUPERADMIN,
            MembershipRole.TREASURER,
        }
        cls._assert_manual_role_allowed(
            chama=chama,
            actor=actor,
            allowed_roles=allowed_roles,
            action_label="manage manual payment policy",
        )
        policy = cls._get_manual_approval_policy(chama=chama)
        for field, value in payload.items():
            setattr(policy, field, value)
        policy.updated_by = actor
        if not policy.created_by_id:
            policy.created_by = actor
        policy.save()
        return policy

    @staticmethod
    def _extract_statement_line_value(row: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = row.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""

    @classmethod
    def import_statement(
        cls,
        *,
        chama: Chama,
        actor: Any,
        payment_method: str,
        provider_name: str = "",
        source_name: str = "",
        statement_date=None,
        csv_text: str = "",
        rows: list[dict[str, Any]] | None = None,
    ) -> PaymentStatementImport:
        cls._assert_finance_management_permission(chama=chama, actor=actor)
        if payment_method not in cls.STATEMENT_IMPORT_METHODS:
            raise PaymentServiceError("Statement imports are currently available for M-Pesa only")

        parsed_rows: list[dict[str, Any]]
        if rows:
            parsed_rows = rows
        elif csv_text.strip():
            reader = csv.DictReader(io.StringIO(csv_text.strip()))
            parsed_rows = [dict(row) for row in reader]
        else:
            raise PaymentServiceError("Provide statement rows or csv_text")

        statement_import = PaymentStatementImport.objects.create(
            chama=chama,
            imported_by=actor,
            payment_method=payment_method,
            provider_name=provider_name,
            source_name=source_name,
            statement_date=parse_iso_date(statement_date) if statement_date else None,
            total_rows=len(parsed_rows),
            created_by=actor,
            updated_by=actor,
        )

        matched_rows = 0
        mismatch_rows = 0
        unmatched_rows = 0

        for index, row in enumerate(parsed_rows, start=1):
            reference = cls._extract_statement_line_value(
                row,
                "provider_reference",
                "reference",
                "external_reference",
                "transaction_reference",
                "transfer_reference",
                "receipt_number",
                "mpesa_receipt_number",
            )
            payer_reference = cls._extract_statement_line_value(
                row,
                "payer_reference",
                "phone",
                "account_number",
                "card_last4",
            )
            amount_value = cls._extract_statement_line_value(row, "amount", "gross_amount", "net_amount")
            if not amount_value:
                raise PaymentServiceError(f"Statement line {index} is missing amount")
            amount = to_decimal(amount_value)
            currency = cls._extract_statement_line_value(row, "currency") or CurrencyChoices.KES
            tx_date = cls._extract_statement_line_value(row, "transaction_date", "date", "paid_at")
            parsed_statement_datetime = None
            if tx_date:
                try:
                    parsed_statement_datetime = datetime.fromisoformat(tx_date)
                    if timezone.is_naive(parsed_statement_datetime):
                        parsed_statement_datetime = timezone.make_aware(parsed_statement_datetime)
                except ValueError:
                    parsed_date = parse_iso_date(tx_date)
                    if parsed_date:
                        parsed_statement_datetime = timezone.make_aware(
                            datetime.combine(parsed_date, datetime.min.time())
                        )

            line = PaymentStatementLine.objects.create(
                statement_import=statement_import,
                line_number=index,
                external_reference=reference,
                payer_reference=payer_reference,
                amount=amount,
                currency=currency.upper(),
                transaction_date=parsed_statement_datetime,
                raw_payload=row,
                created_by=actor,
                updated_by=actor,
            )

            transaction_matches = (
                PaymentTransaction.objects.filter(
                    Q(provider_reference=reference)
                    | Q(payment_intent__reference=reference)
                    | Q(payment_intent__bank_details__transfer_reference=reference)
                )
                .select_related("payment_intent")
                .distinct()
            )
            if payment_method:
                transaction_matches = transaction_matches.filter(payment_method=payment_method)

            match_count = transaction_matches.count()
            if match_count > 1:
                case = PaymentReconciliationCase.objects.create(
                    chama=chama,
                    mismatch_type=ReconciliationMismatchType.DUPLICATE_PROVIDER_REFERENCE,
                    case_status=ReconciliationCaseStatus.OPEN,
                    expected_amount=amount,
                    received_amount=amount,
                    received_reference=reference,
                    metadata={
                        "summary": "Statement line matched more than one payment record.",
                        "statement_import_id": str(statement_import.id),
                        "line_id": str(line.id),
                    },
                    created_by=actor,
                    updated_by=actor,
                )
                line.match_status = StatementLineMatchStatus.DUPLICATE
                line.reconciliation_case = case
                line.save(update_fields=["match_status", "reconciliation_case", "updated_at"])
                mismatch_rows += 1
                continue

            match = transaction_matches.first()
            if not match:
                intent_matches = (
                    PaymentIntent.objects.filter(chama=chama)
                    .filter(
                        Q(reference=reference)
                        | Q(provider_intent_id=reference)
                        | Q(bank_details__transfer_reference=reference)
                    )
                    .distinct()
                )
                if payment_method:
                    intent_matches = intent_matches.filter(payment_method=payment_method)

                intent_match_count = intent_matches.count()
                if intent_match_count > 1:
                    case = PaymentReconciliationCase.objects.create(
                        chama=chama,
                        mismatch_type=ReconciliationMismatchType.DUPLICATE_PROVIDER_REFERENCE,
                        case_status=ReconciliationCaseStatus.OPEN,
                        expected_amount=amount,
                        received_amount=amount,
                        received_reference=reference,
                        metadata={
                            "summary": "Statement line matched more than one payment intent.",
                            "statement_import_id": str(statement_import.id),
                            "line_id": str(line.id),
                        },
                        created_by=actor,
                        updated_by=actor,
                    )
                    line.match_status = StatementLineMatchStatus.DUPLICATE
                    line.reconciliation_case = case
                    line.save(update_fields=["match_status", "reconciliation_case", "updated_at"])
                    mismatch_rows += 1
                    continue

                intent_match = intent_matches.first()
                if not intent_match:
                    case = PaymentReconciliationCase.objects.create(
                        chama=chama,
                        mismatch_type=ReconciliationMismatchType.MANUAL_REVIEW,
                        case_status=ReconciliationCaseStatus.OPEN,
                        expected_amount=amount,
                        received_amount=amount,
                        received_reference=reference,
                        metadata={
                            "summary": "Statement line did not match any known payment.",
                            "statement_import_id": str(statement_import.id),
                            "line_id": str(line.id),
                        },
                        created_by=actor,
                        updated_by=actor,
                    )
                    line.match_status = StatementLineMatchStatus.UNMATCHED
                    line.reconciliation_case = case
                    line.save(update_fields=["match_status", "reconciliation_case", "updated_at"])
                    unmatched_rows += 1
                    continue

                match = cls._upsert_transaction(
                    intent=intent_match,
                    provider_reference=reference,
                    provider_name=provider_name or intent_match.provider or statement_import.provider_name or payment_method,
                    amount=amount,
                    currency=currency.upper(),
                    status=TransactionStatus.RECEIVED,
                    payer_reference=payer_reference,
                    raw_response={
                        "source": "statement_import",
                        "statement_import_id": str(statement_import.id),
                        "statement_line_id": str(line.id),
                        "statement_row": row,
                    },
                )

            line.matched_transaction = match
            line.matched_payment_intent = match.payment_intent
            expected_amount = to_decimal(match.payment_intent.amount)
            expected_currency = str(match.payment_intent.currency).upper()
            if expected_amount != amount or expected_currency != currency.upper():
                case = PaymentReconciliationCase.objects.create(
                    chama=chama,
                    payment_intent=match.payment_intent,
                    payment_transaction=match,
                    mismatch_type=ReconciliationMismatchType.PROVIDER_VERIFICATION_MISMATCH,
                    case_status=ReconciliationCaseStatus.OPEN,
                    expected_amount=match.payment_intent.amount,
                    received_amount=amount,
                    expected_reference=match.payment_intent.reference,
                    received_reference=reference,
                    metadata={
                        "summary": "Statement amount or currency does not match the internal payment record.",
                        "statement_import_id": str(statement_import.id),
                        "line_id": str(line.id),
                    },
                    created_by=actor,
                    updated_by=actor,
                )
                line.match_status = StatementLineMatchStatus.MISMATCH
                line.reconciliation_case = case
                line.save(
                    update_fields=[
                        "matched_transaction",
                        "matched_payment_intent",
                        "match_status",
                        "reconciliation_case",
                        "updated_at",
                    ]
                )
                mismatch_rows += 1
                continue

            if match.payment_intent.status in {PaymentStatus.PENDING_VERIFICATION, PaymentStatus.PENDING}:
                case = PaymentReconciliationCase.objects.create(
                    chama=chama,
                    payment_intent=match.payment_intent,
                    payment_transaction=match,
                    mismatch_type=ReconciliationMismatchType.MANUAL_REVIEW,
                    case_status=ReconciliationCaseStatus.OPEN,
                    expected_amount=match.amount,
                    received_amount=amount,
                    expected_reference=match.payment_intent.reference,
                    received_reference=reference,
                    metadata={
                        "summary": "Statement line matches a payment still awaiting verification.",
                        "statement_import_id": str(statement_import.id),
                        "line_id": str(line.id),
                    },
                    created_by=actor,
                    updated_by=actor,
                )
                line.match_status = StatementLineMatchStatus.PENDING_REVIEW
                line.reconciliation_case = case
                line.save(
                    update_fields=[
                        "matched_transaction",
                        "matched_payment_intent",
                        "match_status",
                        "reconciliation_case",
                        "updated_at",
                    ]
                )
                mismatch_rows += 1
                continue

            line.match_status = StatementLineMatchStatus.MATCHED
            line.save(
                update_fields=[
                    "matched_transaction",
                    "matched_payment_intent",
                    "match_status",
                    "updated_at",
                ]
            )
            matched_rows += 1

        statement_import.status = StatementImportStatus.PROCESSED
        statement_import.matched_rows = matched_rows
        statement_import.mismatch_rows = mismatch_rows
        statement_import.unmatched_rows = unmatched_rows
        statement_import.updated_by = actor
        statement_import.save(
            update_fields=[
                "status",
                "matched_rows",
                "mismatch_rows",
                "unmatched_rows",
                "updated_by",
                "updated_at",
            ]
        )
        return statement_import

    @classmethod
    def list_statement_imports(
        cls,
        *,
        chama: Chama,
        actor: Any,
        limit: int = 50,
    ) -> list[PaymentStatementImport]:
        cls._assert_finance_management_permission(chama=chama, actor=actor)
        return list(
            PaymentStatementImport.objects.filter(chama=chama)
            .select_related("imported_by")
            .order_by("-created_at")[:limit]
        )

    @classmethod
    def get_reconciliation_queue(
        cls,
        *,
        chama: Chama,
        actor: Any,
        payment_method: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        policy = cls._get_manual_approval_policy(chama=chama)
        allowed_roles = cls._normalize_roles(
            policy.allowed_reconciliation_roles,
            DEFAULT_RECONCILIATION_ROLES,
        )
        cls._assert_manual_role_allowed(
            chama=chama,
            actor=actor,
            allowed_roles=allowed_roles,
            action_label="access reconciliation tools",
        )

        issues: list[dict[str, Any]] = []
        case_qs = PaymentReconciliationCase.objects.select_related(
            "payment_intent",
            "payment_transaction",
            "assigned_to",
        ).filter(chama=chama)
        if payment_method:
            case_qs = case_qs.filter(
                Q(payment_intent__payment_method=payment_method)
                | Q(payment_transaction__payment_method=payment_method)
            )
        for case in case_qs.order_by("-created_at")[:limit]:
            issues.append(
                {
                    "id": case.id,
                    "issue_type": case.mismatch_type,
                    "severity": "high" if case.case_status == ReconciliationCaseStatus.OPEN else "medium",
                    "summary": case.metadata.get("summary", "Payment requires reconciliation review."),
                    "payment_intent_id": case.payment_intent_id,
                    "provider_reference": case.received_reference
                    or (case.payment_transaction.provider_reference if case.payment_transaction else ""),
                    "payment_method": (
                        case.payment_intent.payment_method
                        if case.payment_intent
                        else (case.payment_transaction.payment_method if case.payment_transaction else "")
                    ),
                    "status": case.case_status,
                    "amount": str(case.expected_amount or case.received_amount or Decimal("0.00")),
                    "currency": case.payment_intent.currency if case.payment_intent else "",
                    "reference": case.expected_reference,
                    "metadata": case.metadata,
                    "created_at": case.created_at,
                }
            )

        intents = PaymentIntent.objects.filter(
            chama=chama,
            status__in=[
                PaymentStatus.PENDING,
                PaymentStatus.PENDING_AUTHENTICATION,
                PaymentStatus.PENDING_VERIFICATION,
            ],
        )
        if payment_method:
            intents = intents.filter(payment_method=payment_method)
        for intent in intents.order_by("-created_at")[:limit]:
            issues.append(
                {
                    "id": str(intent.id),
                    "issue_type": "pending_payment",
                    "severity": "medium",
                    "summary": "Payment is still pending and requires verification or follow-up.",
                    "payment_intent_id": intent.id,
                    "provider_reference": intent.provider_intent_id,
                    "payment_method": intent.payment_method,
                    "status": intent.status,
                    "amount": str(intent.amount),
                    "currency": intent.currency,
                    "reference": intent.reference,
                    "metadata": (intent.metadata or {}).get("reconciliation", {}),
                    "created_at": intent.created_at,
                }
            )

        unresolved_webhooks = PaymentWebhook.objects.filter(
            processed=False,
            provider_reference__gt="",
        ).order_by("-created_at")[: min(limit, 50)]
        for webhook in unresolved_webhooks:
            issues.append(
                {
                    "id": str(webhook.id),
                    "issue_type": ReconciliationMismatchType.WEBHOOK_PROCESSING_ERROR,
                    "severity": "high",
                    "summary": "Webhook was received but not fully processed.",
                    "provider_reference": webhook.provider_reference,
                    "payment_method": webhook.payment_method,
                    "status": "unprocessed",
                    "metadata": {
                        "event_type": webhook.event_type,
                        "processing_error": webhook.processing_error,
                        "provider": webhook.provider,
                    },
                    "created_at": webhook.created_at,
                }
            )

        issues.sort(key=lambda item: item.get("created_at") or timezone.now(), reverse=True)
        return issues[:limit]

    @classmethod
    def resolve_reconciliation_issue(
        cls,
        *,
        case_id: uuid.UUID,
        actor: Any,
        action: str,
        notes: str = "",
    ) -> PaymentReconciliationCase:
        try:
            with transaction.atomic():
                case = (
                    PaymentReconciliationCase.objects.select_for_update()
                    .select_related("payment_intent", "payment_transaction")
                    .get(id=case_id)
                )
                policy = cls._get_manual_approval_policy(chama=case.chama)
                allowed_roles = cls._normalize_roles(
                    policy.allowed_reconciliation_roles,
                    DEFAULT_RECONCILIATION_ROLES,
                )
                cls._assert_manual_role_allowed(
                    chama=case.chama,
                    actor=actor,
                    allowed_roles=allowed_roles,
                    action_label="resolve reconciliation issues",
                )
                intent = case.payment_intent

                if action == "retry_verification":
                    if not intent:
                        raise PaymentServiceError("This reconciliation case is not linked to a payment intent")
                    if intent.payment_method == PaymentMethod.CASH:
                        raise PaymentServiceError("Manual payments must be resolved through approval or verification flows")
                    cls.verify_payment(intent.id)
                    case.case_status = ReconciliationCaseStatus.IN_REVIEW
                    case.resolution_notes = notes
                    case.assigned_to = actor
                    case.updated_by = actor
                    case.save(
                        update_fields=[
                            "case_status",
                            "resolution_notes",
                            "assigned_to",
                            "updated_by",
                            "updated_at",
                        ]
                    )
                    return case

                if action == "mark_failed":
                    if not intent:
                        raise PaymentServiceError("This reconciliation case is not linked to a payment intent")
                    cls._record_failure(
                        intent=intent,
                        previous_status=intent.status,
                        failure_reason=notes or "Marked failed during reconciliation",
                        failure_code="reconciliation_failed",
                        metadata={"resolved_by": str(getattr(actor, "id", ""))},
                        actor=actor,
                    )
                    case.case_status = ReconciliationCaseStatus.RESOLVED
                    case.resolution_notes = notes
                    case.assigned_to = actor
                    case.resolved_at = timezone.now()
                    case.updated_by = actor
                    case.save(
                        update_fields=[
                            "case_status",
                            "resolution_notes",
                            "assigned_to",
                            "resolved_at",
                            "updated_by",
                            "updated_at",
                        ]
                    )
                    return case

                if action == "confirm_payment":
                    if not intent:
                        raise PaymentServiceError("This reconciliation case is not linked to a payment intent")
                    if intent.payment_method == PaymentMethod.CASH:
                        raise PaymentServiceError("Cash payments must be resolved through the cash verification flow")

                    now = timezone.now()
                    provider_reference = (
                        str(case.received_reference or "").strip()
                        or (str(case.payment_transaction.provider_reference or "").strip() if case.payment_transaction_id else "")
                        or str(intent.provider_intent_id or "").strip()
                        or str(intent.reference or "").strip()
                    )
                    provider_reference = provider_reference[:120] or str(intent.reference or "")[:120]
                    payer_reference = ""
                    if case.payment_transaction_id:
                        payer_reference = str(case.payment_transaction.payer_reference or "").strip()

                    transaction_record = cls._upsert_transaction(
                        intent=intent,
                        provider_reference=provider_reference,
                        provider_name=intent.provider or "manual",
                        amount=intent.amount,
                        currency=intent.currency,
                        status=TransactionStatus.VERIFIED,
                        payer_reference=payer_reference,
                        raw_response={
                            "verification_type": "reconciliation_manual",
                            "case_id": str(case.id),
                        },
                        verified_by=actor,
                        verified_at=now,
                    )

                    previous_status = intent.status
                    if intent.status not in {PaymentStatus.SUCCESS, PaymentStatus.RECONCILED}:
                        cls._assert_status_transition(previous_status, PaymentStatus.SUCCESS)
                        intent.status = PaymentStatus.SUCCESS
                        intent.failure_reason = ""
                        intent.failure_code = ""
                        intent.completed_at = now
                        intent.save(
                            update_fields=[
                                "status",
                                "failure_reason",
                                "failure_code",
                                "completed_at",
                                "updated_at",
                            ]
                        )
                        PaymentAuditLog.objects.create(
                            payment_intent=intent,
                            actor=actor,
                            event="payment_confirmed_via_reconciliation",
                            previous_status=previous_status,
                            new_status=PaymentStatus.SUCCESS,
                            metadata={"case_id": str(case.id), "notes": notes},
                        )

                    if not hasattr(intent, "receipt"):
                        cls._handle_successful_payment(intent, transaction_record)
                        cls._notify(intent=intent, event="success")

                    case.case_status = ReconciliationCaseStatus.RESOLVED
                    case.resolution_notes = notes
                    case.assigned_to = actor
                    case.resolved_at = now
                    case.updated_by = actor
                    case.save(
                        update_fields=[
                            "case_status",
                            "resolution_notes",
                            "assigned_to",
                            "resolved_at",
                            "updated_by",
                            "updated_at",
                        ]
                    )
                    return case

                if action == "mark_reconciled":
                    if not intent:
                        raise PaymentServiceError("This reconciliation case is not linked to a payment intent")
                    previous_status = intent.status
                    cls._assert_status_transition(previous_status, PaymentStatus.RECONCILED)
                    intent.status = PaymentStatus.RECONCILED
                    intent.metadata = {
                        **(intent.metadata or {}),
                        "reconciliation": {
                            "flagged": False,
                            "resolved": True,
                            "resolved_at": timezone.now().isoformat(),
                            "notes": notes,
                        },
                    }
                    intent.save(update_fields=["status", "metadata", "updated_at"])
                    PaymentAuditLog.objects.create(
                        payment_intent=intent,
                        actor=actor,
                        event="payment_reconciled",
                        previous_status=previous_status,
                        new_status=PaymentStatus.RECONCILED,
                        metadata={"notes": notes},
                    )
                    case.case_status = ReconciliationCaseStatus.RESOLVED
                    case.resolution_notes = notes
                    case.assigned_to = actor
                    case.resolved_at = timezone.now()
                    case.updated_by = actor
                    case.save(
                        update_fields=[
                            "case_status",
                            "resolution_notes",
                            "assigned_to",
                            "resolved_at",
                            "updated_by",
                            "updated_at",
                        ]
                    )
                    return case

                raise PaymentServiceError(f"Unknown reconciliation action: {action}")
        except PaymentReconciliationCase.DoesNotExist as exc:
            raise PaymentServiceError("Reconciliation case not found") from exc

    @classmethod
    def _post_wallet_deposit_to_member_wallet(
        cls,
        *,
        intent: PaymentIntent,
        transaction_record: PaymentTransaction,
        actor: Any,
    ) -> Wallet:
        wallet, _ = Wallet.objects.get_or_create(
            owner_type=WalletOwnerType.USER,
            owner_id=intent.user_id,
            defaults={
                "available_balance": ZERO,
                "locked_balance": ZERO,
                "currency": intent.currency or "KES",
            },
        )
        idempotency_key = f"wallet-topup:{intent.id}"
        existing_entry = LedgerEntry.objects.filter(
            chama=intent.chama,
            idempotency_key=idempotency_key,
        ).first()
        if existing_entry:
            return wallet

        LedgerEntry.objects.create(
            wallet=wallet,
            chama=intent.chama,
            entry_type=LedgerEntryType.WALLET_TOPUP,
            direction=LedgerDirection.CREDIT,
            amount=intent.amount,
            debit=ZERO,
            credit=intent.amount,
            currency=intent.currency or "KES",
            status="success",
            provider=intent.payment_method,
            provider_reference=transaction_record.provider_reference or intent.reference,
            idempotency_key=idempotency_key,
            related_payment=intent,
            narration="Member wallet deposit completed.",
            created_by=actor,
            updated_by=actor,
            meta={
                "wallet_flow_kind": "deposit",
                "payment_intent_id": str(intent.id),
            },
        )
        wallet.available_balance = Decimal(str(wallet.available_balance or ZERO)) + intent.amount
        wallet.save(update_fields=["available_balance", "updated_at"])
        return wallet
