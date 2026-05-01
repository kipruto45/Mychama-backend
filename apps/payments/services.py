from __future__ import annotations

import hashlib
import hmac
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus
from apps.chama.permissions import get_membership
from apps.chama.services import get_effective_role
from apps.finance.models import (
    Contribution,
    ContributionType,
    LedgerDirection,
    LedgerEntry,
    Loan,
    LoanStatus,
    Repayment,
)
from apps.finance.services import (
    FinanceService,
    FinanceServiceError,
    IdempotencyConflictError,
)
from apps.payments.models import (
    CallbackKind,
    CallbackLog,
    MpesaB2CPayout,
    MpesaB2CStatus,
    MpesaC2BProcessingStatus,
    MpesaC2BTransaction,
    MpesaPurpose,
    MpesaSTKTransaction,
    MpesaTransaction,
    MpesaTransactionStatus,
    MpesaTransactionType,
    PaymentActivityEvent,
    PaymentActivityLog,
    PaymentAllocationRule,
    PaymentAllocationStrategy,
    PaymentDispute,
    PaymentDisputeCategory,
    PaymentDisputeStatus,
    PaymentIntent,
    PaymentIntentStatus,
    PaymentIntentType,
    PaymentPurpose,
    PaymentReconciliationRun,
    PaymentRefund,
    PaymentRefundStatus,
    PaymentTransaction,
    ReconciliationRunStatus,
    WithdrawalApprovalLog,
    WithdrawalApprovalStep,
)
from apps.payments.mpesa_client import MpesaClient
from core.audit import create_activity_log
from core.constants import CurrencyChoices, MethodChoices
from core.utils import normalize_kenyan_phone

logger = logging.getLogger(__name__)


class MpesaServiceError(Exception):
    pass


def _run_task_inline() -> bool:
    return bool(
        getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)
        or os.getenv("PYTEST_CURRENT_TEST")
    )


@dataclass
class FinancePostingResult:
    posted: bool
    reason: str = ""


@dataclass
class ReconciliationResult:
    run_date: str
    total_success_transactions: int
    matched_transactions: int
    missing_in_finance: list[dict]
    missing_in_mpesa: list[str]


class MpesaService:
    @staticmethod
    def initiate_stk_push(transaction: MpesaTransaction) -> dict:
        if getattr(settings, "MPESA_USE_STUB", True):
            return {
                "MerchantRequestID": f"MR_{uuid.uuid4().hex[:20]}",
                "CheckoutRequestID": f"ws_CO_{uuid.uuid4().hex[:24]}",
                "ResponseCode": "0",
                "ResponseDescription": "Success. Request accepted for processing",
                "CustomerMessage": "Success. Request accepted for processing",
            }

        try:
            client = MpesaClient()
            return client.initiate_stk_push(
                phone_number=transaction.phone,
                amount=str(transaction.amount),
                account_reference=str(transaction.reference or transaction.id),
                transaction_desc=f"{transaction.purpose} payment",
            )
        except Exception as exc:  # noqa: BLE001
            raise MpesaServiceError("Unable to initiate STK push.") from exc

    @staticmethod
    def is_source_ip_allowed(source_ip: str | None) -> bool:
        raw_allowlist = getattr(settings, "MPESA_CALLBACK_IP_ALLOWLIST", [])
        allowlist = {str(item).strip() for item in raw_allowlist if str(item).strip()}
        if not allowlist:
            return True
        return bool(source_ip and source_ip in allowlist)

    @staticmethod
    def verify_callback_signature(
        payload_bytes: bytes,
        received_signature: str | None,
    ) -> bool | None:
        secret = getattr(settings, "MPESA_CALLBACK_SECRET", "")
        if not secret:
            return None
        if not received_signature:
            return False

        expected = hmac.new(
            secret.encode("utf-8"),
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, received_signature)

    @staticmethod
    def _finance_idempotency_key(
        transaction: MpesaTransaction,
        receipt_number: str,
    ) -> str:
        base = f"mpesa:{transaction.chama_id}:{transaction.purpose}:{receipt_number.strip()}"
        if len(base) <= 100:
            return base
        digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:40]
        return f"mpesa:{transaction.purpose}:{digest}"

    @staticmethod
    def _parse_reference_uuid(transaction: MpesaTransaction, target: str) -> uuid.UUID:
        if not transaction.reference:
            raise MpesaServiceError(f"Missing {target} reference for transaction.")

        try:
            return uuid.UUID(str(transaction.reference))
        except ValueError as exc:
            raise MpesaServiceError(
                f"Invalid {target} reference supplied on transaction."
            ) from exc

    @staticmethod
    def _resolve_actor(transaction: MpesaTransaction):
        candidate = (
            transaction.initiated_by
            or transaction.member
            or transaction.created_by
            or transaction.chama.created_by
        )
        if candidate and candidate.is_active:
            return candidate

        membership = (
            Membership.objects.select_related("user")
            .filter(
                chama=transaction.chama,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
                user__is_active=True,
            )
            .order_by("created_at")
            .first()
        )
        if membership:
            return membership.user

        raise MpesaServiceError("No active actor available for finance posting.")

    @staticmethod
    def is_receipt_already_processed(receipt_number: str) -> bool:
        if not receipt_number:
            return False
        return Contribution.objects.filter(receipt_code=receipt_number).exists() or (
            Repayment.objects.filter(receipt_code=receipt_number).exists()
        )

    @staticmethod
    def _post_contribution(transaction: MpesaTransaction, receipt_number: str):
        contribution_type_id = MpesaService._parse_reference_uuid(
            transaction, "contribution type"
        )
        if not transaction.member_id:
            raise MpesaServiceError("Contribution callback requires a member.")

        exists = ContributionType.objects.filter(
            id=contribution_type_id,
            chama=transaction.chama,
            is_active=True,
        ).exists()
        if not exists:
            raise MpesaServiceError(
                "Contribution type reference does not belong to the chama."
            )

        is_member = Membership.objects.filter(
            user_id=transaction.member_id,
            chama=transaction.chama,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
        ).exists()
        if not is_member:
            raise MpesaServiceError(
                "Member is not approved/active in chama for contribution posting."
            )

        payload = {
            "chama_id": transaction.chama_id,
            "member_id": transaction.member_id,
            "contribution_type_id": contribution_type_id,
            "amount": transaction.amount,
            "date_paid": timezone.localdate(),
            "method": MethodChoices.MPESA,
            "receipt_code": receipt_number,
            "idempotency_key": MpesaService._finance_idempotency_key(
                transaction,
                receipt_number,
            ),
        }
        FinanceService.post_contribution(
            payload,
            MpesaService._resolve_actor(transaction),
        )

    @staticmethod
    def _post_repayment(transaction: MpesaTransaction, receipt_number: str):
        loan_id = MpesaService._parse_reference_uuid(transaction, "loan")
        loan = Loan.objects.filter(id=loan_id, chama=transaction.chama).first()
        if not loan:
            raise MpesaServiceError("Loan reference does not belong to the chama.")

        if transaction.member_id and loan.member_id != transaction.member_id:
            raise MpesaServiceError(
                "Repayment reference does not match the transaction member."
            )

        payload = {
            "amount": transaction.amount,
            "date_paid": timezone.localdate(),
            "method": MethodChoices.MPESA,
            "receipt_code": receipt_number,
            "idempotency_key": MpesaService._finance_idempotency_key(
                transaction,
                receipt_number,
            ),
        }
        FinanceService.post_repayment(
            loan.id,
            payload,
            MpesaService._resolve_actor(transaction),
        )

    @staticmethod
    def post_success_to_finance(
        transaction: MpesaTransaction,
        receipt_number: str,
    ) -> FinancePostingResult:
        if not receipt_number:
            raise MpesaServiceError("Missing M-Pesa receipt number in callback.")

        if MpesaService.is_receipt_already_processed(receipt_number):
            return FinancePostingResult(
                posted=False,
                reason="Receipt already processed.",
            )

        try:
            if transaction.purpose == MpesaPurpose.CONTRIBUTION:
                MpesaService._post_contribution(transaction, receipt_number)
            elif transaction.purpose == MpesaPurpose.REPAYMENT:
                MpesaService._post_repayment(transaction, receipt_number)
            else:
                raise MpesaServiceError("Unsupported transaction purpose.")
        except IdempotencyConflictError:
            return FinancePostingResult(
                posted=False,
                reason="Receipt already processed.",
            )
        except IntegrityError as exc:
            if "receipt_code" in str(exc).lower() or "unique" in str(exc).lower():
                return FinancePostingResult(
                    posted=False,
                    reason="Receipt already processed.",
                )
            raise MpesaServiceError(
                "Unable to post M-Pesa callback to finance ledger."
            ) from exc
        except Http404 as exc:
            raise MpesaServiceError(
                "Referenced finance record could not be resolved."
            ) from exc
        except FinanceServiceError as exc:
            raise MpesaServiceError(str(exc)) from exc

        return FinancePostingResult(posted=True, reason="Posted to finance ledger.")

    @staticmethod
    def reconcile_successful_callbacks(
        *,
        on_date: date | None = None,
        chama_id=None,
    ) -> ReconciliationResult:
        run_date = on_date or timezone.localdate()
        transactions = MpesaTransaction.objects.filter(
            status=MpesaTransactionStatus.SUCCESS,
            callback_received_at__date=run_date,
        )
        if chama_id:
            transactions = transactions.filter(chama_id=chama_id)

        matched = 0
        missing_in_finance: list[dict] = []
        processed_receipts: set[str] = set()
        for tx in transactions:
            receipt = tx.receipt_number.strip()
            if not receipt:
                continue
            processed_receipts.add(receipt)
            if tx.purpose == MpesaPurpose.CONTRIBUTION:
                exists = Contribution.objects.filter(receipt_code=receipt).exists()
            elif tx.purpose == MpesaPurpose.REPAYMENT:
                exists = Repayment.objects.filter(receipt_code=receipt).exists()
            else:
                exists = Contribution.objects.filter(receipt_code=receipt).exists() or (
                    Repayment.objects.filter(receipt_code=receipt).exists()
                )

            if exists:
                matched += 1
                continue
            missing_in_finance.append(
                {
                    "transaction_id": str(tx.id),
                    "receipt_number": receipt,
                    "purpose": tx.purpose,
                    "amount": str(tx.amount),
                    "chama_id": str(tx.chama_id),
                }
            )

        finance_receipts = set(
            Contribution.objects.filter(
                date_paid=run_date,
                method=MethodChoices.MPESA,
            ).values_list("receipt_code", flat=True)
        ) | set(
            Repayment.objects.filter(
                date_paid=run_date,
                method=MethodChoices.MPESA,
            ).values_list("receipt_code", flat=True)
        )
        missing_in_mpesa = sorted(
            item for item in finance_receipts if item not in processed_receipts
        )

        return ReconciliationResult(
            run_date=run_date.isoformat(),
            total_success_transactions=transactions.count(),
            matched_transactions=matched,
            missing_in_finance=missing_in_finance,
            missing_in_mpesa=missing_in_mpesa,
        )


class PaymentWorkflowError(Exception):
    pass


@dataclass
class PaymentOperationResult:
    intent: PaymentIntent
    posted: bool = False
    message: str = ""


class PaymentWorkflowService:
    """Enterprise payment orchestration for C2B, STK, and B2C flows."""

    DEFAULT_STK_EXPIRY_MINUTES = 15
    DISBURSEMENT_ESCALATION_HOURS = 24

    @staticmethod
    def _to_decimal(value) -> Decimal:
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, TypeError) as exc:
            raise PaymentWorkflowError("Invalid amount provided.") from exc
        if amount <= Decimal("0.00"):
            raise PaymentWorkflowError("Amount must be greater than zero.")
        return amount.quantize(Decimal("0.01"))

    @staticmethod
    def _safe_phone(phone: str | None, fallback: str) -> str:
        raw = str(phone or fallback or "").strip()
        if not raw:
            raise PaymentWorkflowError("Phone number is required.")
        try:
            return normalize_kenyan_phone(raw)
        except ValueError as exc:
            raise PaymentWorkflowError(str(exc)) from exc

    @staticmethod
    def _parse_uuid(value, *, label: str):
        if value in (None, ""):
            return None
        try:
            return uuid.UUID(str(value))
        except ValueError as exc:
            raise PaymentWorkflowError(f"Invalid {label}.") from exc

    @staticmethod
    def _membership(user, chama: Chama) -> Membership | None:
        if not user or not user.is_authenticated:
            return None
        return Membership.objects.filter(
            user=user,
            chama=chama,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        ).first()

    @staticmethod
    def _require_active_member(user, chama: Chama) -> Membership:
        membership = PaymentWorkflowService._membership(user, chama)
        if not membership:
            raise PaymentWorkflowError(
                "Only approved active members can perform this action."
            )
        return membership

    @staticmethod
    def _require_treasurer_or_admin(user, chama: Chama) -> Membership:
        membership = PaymentWorkflowService._require_active_member(user, chama)
        effective_role = get_effective_role(user, chama.id, membership)
        if effective_role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
        }:
            raise PaymentWorkflowError("Only treasurer/admin can perform this action.")
        return membership

    @staticmethod
    def _enforce_billing_stk_access(chama: Chama):
        from apps.billing.metering import usage_within_limit
        from apps.billing.models import UsageMetric
        from apps.billing.services import get_access_status, has_feature

        access = get_access_status(chama)
        if access.get("requires_payment"):
            raise PaymentWorkflowError(
                "An active subscription is required to initiate M-Pesa STK payments."
            )

        if not has_feature(chama, "mpesa_stk"):
            raise PaymentWorkflowError(
                "Your current subscription plan does not include M-Pesa STK."
            )

        usage = usage_within_limit(chama, UsageMetric.STK_PUSHES, 1)
        if not usage["allowed"]:
            raise PaymentWorkflowError(
                "Your current plan has exhausted its monthly M-Pesa STK allocation."
            )

    @staticmethod
    def _consume_billing_usage(chama: Chama, metric_key: str, quantity: int = 1):
        from apps.billing.metering import increment_usage

        increment_usage(chama, metric_key, quantity)

    @staticmethod
    def _mask_phone(phone: str) -> str:
        if len(phone) < 6:
            return phone
        return f"{phone[:5]}****{phone[-3:]}"

    @staticmethod
    def _activity(
        intent: PaymentIntent,
        event: str,
        *,
        actor=None,
        meta: dict | None = None,
    ) -> PaymentActivityLog:
        log = PaymentActivityLog.objects.create(
            payment_intent=intent,
            actor=actor,
            event=event,
            meta=meta or {},
            created_by=actor,
            updated_by=actor,
        )
        create_activity_log(
            actor=actor,
            chama_id=intent.chama_id,
            action=f"payment_{str(event).lower()}",
            entity_type="PaymentIntent",
            entity_id=intent.id,
            metadata={
                "intent_type": intent.intent_type,
                "status": intent.status,
                "event": event,
                **(meta or {}),
            },
        )
        # Event-driven rule-based fraud scan hook. Fail-open to avoid blocking payments.
        try:
            from apps.payments.tasks import payments_fraud_pattern_detection_event

            if _run_task_inline():
                payments_fraud_pattern_detection_event(
                    chama_id=str(intent.chama_id),
                    event=str(event),
                    intent_id=str(intent.id),
                )
            else:
                payments_fraud_pattern_detection_event.delay(
                    chama_id=str(intent.chama_id),
                    event=str(event),
                    intent_id=str(intent.id),
                )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed queuing fraud pattern detection intent=%s event=%s",
                intent.id,
                event,
            )
        return log

    @staticmethod
    def _notify(
        *,
        user,
        chama,
        message: str,
        subject: str,
        notification_type: str,
        idempotency_key: str,
        channels: list[str] | None = None,
    ) -> None:
        channels = channels or ["sms", "email"]
        try:
            from apps.notifications.services import NotificationService

            NotificationService.send_notification(
                user=user,
                message=message,
                channels=channels,
                chama=chama,
                subject=subject,
                notification_type=notification_type,
                idempotency_key=idempotency_key,
            )
        except Exception:  # noqa: BLE001
            # Notification delivery should never block the money workflow.
            return

    @staticmethod
    def _is_callback_ip_allowed(source_ip: str | None) -> bool:
        allowlist = {
            item.strip()
            for item in getattr(settings, "MPESA_CALLBACK_IP_ALLOWLIST", [])
            if str(item).strip()
        }
        if not allowlist:
            return True
        return bool(source_ip and source_ip in allowlist)

    @staticmethod
    def _verify_callback_signature(payload_bytes: bytes, signature: str | None) -> bool | None:
        secret = str(getattr(settings, "MPESA_CALLBACK_SECRET", "") or "").strip()
        if not secret:
            return None
        if not signature:
            return False
        expected = hmac.new(
            secret.encode("utf-8"),
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    @staticmethod
    def verify_callback_request(
        *,
        source_ip: str | None,
        payload_bytes: bytes,
        signature: str | None,
    ) -> tuple[bool, str]:
        if getattr(settings, "PAYMENTS_CALLBACK_REQUIRE_IP_ALLOWLIST", False):
            allowlist = getattr(settings, "MPESA_CALLBACK_IP_ALLOWLIST", []) or []
            if not allowlist:
                return False, "Callback IP allowlist not configured"
            if not PaymentWorkflowService._is_callback_ip_allowed(source_ip):
                return False, "Source IP not allowlisted"
        elif not PaymentWorkflowService._is_callback_ip_allowed(source_ip):
            return False, "Source IP not allowlisted"

        signature_ok = PaymentWorkflowService._verify_callback_signature(
            payload_bytes,
            signature,
        )
        if getattr(settings, "PAYMENTS_CALLBACK_REQUIRE_SIGNATURE", False):
            if signature_ok is None:
                return False, "Callback signature secret not configured"
            if signature_ok is False:
                return False, "Invalid callback signature"
        if signature_ok is False:
            return False, "Invalid callback signature"
        return True, "ok"

    @staticmethod
    def _create_intent(
        *,
        chama: Chama,
        actor,
        intent_type: str,
        purpose: str,
        amount: Decimal,
        phone: str,
        reference_type: str,
        reference_id,
        idempotency_key: str,
        metadata: dict | None = None,
        expires_minutes: int | None = None,
    ) -> PaymentIntent:
        expires_at = None
        if expires_minutes:
            expires_at = timezone.now() + timedelta(minutes=expires_minutes)

        intent, created = PaymentIntent.objects.get_or_create(
            chama=chama,
            idempotency_key=idempotency_key,
            defaults={
                "intent_type": intent_type,
                "purpose": purpose,
                "reference_type": reference_type,
                "reference_id": reference_id,
                "amount": amount,
                "currency": CurrencyChoices.KES,
                "phone": phone,
                "user": actor,
                "status": PaymentIntentStatus.INITIATED,
                "expires_at": expires_at,
                "metadata": metadata or {},
                "created_by": actor,
                "updated_by": actor,
            },
        )
        if created:
            PaymentWorkflowService._activity(
                intent,
                PaymentActivityEvent.CREATED,
                actor=actor,
                meta={
                    "intent_type": intent_type,
                    "purpose": purpose,
                    "amount": str(amount),
                },
            )
        return intent

    @staticmethod
    def _sync_transaction_record(
        *,
        intent: PaymentIntent,
        reference: str,
        amount: Decimal,
        status_value: str,
        provider_response: dict | None = None,
    ) -> PaymentTransaction:
        transaction, _ = PaymentTransaction.objects.update_or_create(
            reference=reference,
            defaults={
                "payment_intent": intent,
                "provider": "mpesa",
                "amount": amount,
                "status": status_value,
                "provider_response": provider_response or {},
                "created_by": intent.updated_by or intent.created_by,
                "updated_by": intent.updated_by or intent.created_by,
            },
        )
        return transaction

    @staticmethod
    def _derive_finance_idempotency(intent: PaymentIntent, external_reference: str) -> str:
        raw = f"payment:{intent.id}:{external_reference}".strip()
        if len(raw) <= 100:
            return raw
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]
        return f"payment:{digest}"

    @staticmethod
    def _resolve_actor(intent: PaymentIntent):
        if intent.updated_by and intent.updated_by.is_active:
            return intent.updated_by
        if intent.created_by and intent.created_by.is_active:
            return intent.created_by

        membership = (
            Membership.objects.select_related("user")
            .filter(
                chama=intent.chama,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
                user__is_active=True,
            )
            .order_by("created_at")
            .first()
        )
        if membership:
            return membership.user

        if intent.chama.created_by and intent.chama.created_by.is_active:
            return intent.chama.created_by

        raise PaymentWorkflowError("Unable to resolve actor for finance posting.")

    @staticmethod
    def _ledger_balance(chama: Chama) -> Decimal:
        aggregates = LedgerEntry.objects.filter(chama=chama).aggregate(
            credits=Coalesce(
                Sum(
                    "amount",
                    filter=Q(direction=LedgerDirection.CREDIT),
                ),
                Value(Decimal("0.00"), output_field=DecimalField()),
            ),
            debits=Coalesce(
                Sum(
                    "amount",
                    filter=Q(direction=LedgerDirection.DEBIT),
                ),
                Value(Decimal("0.00"), output_field=DecimalField()),
            ),
        )
        return (aggregates["credits"] or Decimal("0.00")) - (
            aggregates["debits"] or Decimal("0.00")
        )

    @staticmethod
    def _ensure_sufficient_balance(chama: Chama, amount: Decimal):
        balance = PaymentWorkflowService._ledger_balance(chama)
        if balance < amount:
            raise PaymentWorkflowError(
                f"Insufficient chama balance. Available KES {balance}."
            )

    @staticmethod
    def _default_repayment_amount(loan: Loan) -> Decimal:
        next_due = loan.installments.filter(status__in=["due", "overdue"]).order_by(
            "due_date", "created_at"
        ).first()
        if next_due:
            return Decimal(next_due.expected_amount).quantize(Decimal("0.01"))

        total_due = loan.installments.aggregate(
            total=Coalesce(
                Sum("expected_amount"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        total_paid = loan.repayments.aggregate(
            total=Coalesce(
                Sum("amount"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        outstanding = Decimal(total_due) - Decimal(total_paid)
        if outstanding > Decimal("0.00"):
            return outstanding.quantize(Decimal("0.01"))
        return Decimal("0.00")

    @staticmethod
    def _validate_repayment_amount(loan: Loan, amount: Decimal):
        total_due = loan.installments.aggregate(
            total=Coalesce(
                Sum("expected_amount"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        total_paid = loan.repayments.aggregate(
            total=Coalesce(
                Sum("amount"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]

        outstanding = max(Decimal(total_due) - Decimal(total_paid), Decimal("0.00"))
        allowance = Decimal(
            str(getattr(settings, "LOAN_OVERPAYMENT_ALLOWANCE", "0.00"))
        )
        if amount > outstanding + allowance:
            raise PaymentWorkflowError(
                "Repayment amount exceeds outstanding balance allowance."
            )

    @staticmethod
    def _split_amounts(
        *,
        loan: Loan,
        total_amount: Decimal,
        strategy: str = "repayment_first",
        repayment_amount: Decimal | None = None,
    ) -> tuple[Decimal, Decimal]:
        outstanding = PaymentWorkflowService._default_repayment_amount(loan)
        outstanding = max(outstanding, Decimal("0.00"))

        resolved_strategy = str(strategy or "repayment_first").strip().lower()
        ratio_percent = Decimal("50.00")
        allocation_rule = None
        if resolved_strategy == "auto":
            allocation_rule = PaymentAllocationRule.objects.filter(
                chama=loan.chama,
                is_active=True,
            ).first()
            if allocation_rule:
                resolved_strategy = allocation_rule.strategy
                ratio_percent = Decimal(str(allocation_rule.repayment_ratio_percent))
            else:
                resolved_strategy = "repayment_first"

        if resolved_strategy == "custom":
            if repayment_amount is None:
                raise PaymentWorkflowError(
                    "repayment_amount is required for custom split strategy."
                )
            repay_component = PaymentWorkflowService._to_decimal(repayment_amount)
        elif resolved_strategy in {
            PaymentAllocationStrategy.RATIO,
            "ratio",
        }:
            repay_component = (
                (total_amount * ratio_percent) / Decimal("100")
            ).quantize(Decimal("0.01"))
            repay_component = min(repay_component, outstanding)
        elif resolved_strategy in {
            PaymentAllocationStrategy.WELFARE_FIRST,
            "welfare_first",
        }:
            # Keep a contribution-heavy split while still paying some loan balance.
            repay_component = min(
                outstanding,
                (total_amount * Decimal("0.30")).quantize(Decimal("0.01")),
            )
        else:
            repay_component = min(outstanding, total_amount)

        if repay_component > total_amount:
            raise PaymentWorkflowError("Repayment component cannot exceed total amount.")
        if repay_component > Decimal("0.00"):
            PaymentWorkflowService._validate_repayment_amount(loan, repay_component)

        contribution_component = (total_amount - repay_component).quantize(
            Decimal("0.01")
        )
        return repay_component, contribution_component

    @staticmethod
    def _parse_c2b_time(value: str | None):
        raw = str(value or "").strip()
        if not raw:
            return timezone.now()
        for fmt in ["%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S"]:
            try:
                parsed = datetime.strptime(raw, fmt)
                return timezone.make_aware(parsed, timezone.get_current_timezone())
            except ValueError:
                continue
        return timezone.now()

    @staticmethod
    def _extract_stk_receipt(stk_callback: dict) -> str:
        items = []
        metadata = stk_callback.get("CallbackMetadata")
        if isinstance(metadata, dict):
            items = metadata.get("Item", [])
        for item in items:
            if item.get("Name") == "MpesaReceiptNumber":
                return str(item.get("Value", "")).strip()
        return ""

    @staticmethod
    def _extract_stk_charge_amount(stk_callback: dict) -> Decimal:
        items = []
        metadata = stk_callback.get("CallbackMetadata")
        if isinstance(metadata, dict):
            items = metadata.get("Item", [])
        for item in items:
            if item.get("Name") in {"TransactionFee", "Charge", "MpesaCharge"}:
                try:
                    value = Decimal(str(item.get("Value")))
                    if value > Decimal("0.00"):
                        return value.quantize(Decimal("0.01"))
                except Exception:  # noqa: BLE001
                    return Decimal("0.00")
        return Decimal("0.00")

    @staticmethod
    def _extract_stk_paid_amount(stk_callback: dict) -> Decimal | None:
        items = []
        metadata = stk_callback.get("CallbackMetadata")
        if isinstance(metadata, dict):
            items = metadata.get("Item", [])
        for item in items:
            if item.get("Name") == "Amount":
                try:
                    return Decimal(str(item.get("Value"))).quantize(Decimal("0.01"))
                except Exception:  # noqa: BLE001
                    return None
        return None

    @staticmethod
    def _intent_member_id(intent: PaymentIntent):
        metadata_member_id = (intent.metadata or {}).get("member_id")
        if metadata_member_id:
            return metadata_member_id
        if intent.created_by_id:
            return intent.created_by_id
        raise PaymentWorkflowError("Unable to resolve member for intent.")

    @staticmethod
    @transaction.atomic
    def _post_intent_success(
        *,
        intent: PaymentIntent,
        external_reference: str,
        actor=None,
    ) -> PaymentOperationResult:
        if intent.status == PaymentIntentStatus.SUCCESS:
            return PaymentOperationResult(
                intent=intent,
                posted=False,
                message="Intent already posted.",
            )

        actor = actor or PaymentWorkflowService._resolve_actor(intent)
        finance_key = PaymentWorkflowService._derive_finance_idempotency(
            intent,
            external_reference,
        )

        ledger_entry_id = ""
        split_posted: dict[str, str] = {}
        try:
            if intent.intent_type == PaymentIntentType.DEPOSIT:
                if intent.purpose == PaymentPurpose.CONTRIBUTION:
                    result = FinanceService.post_contribution(
                        {
                            "chama_id": intent.chama_id,
                            "member_id": PaymentWorkflowService._intent_member_id(intent),
                            "contribution_type_id": intent.reference_id,
                            "amount": intent.amount,
                            "date_paid": timezone.localdate(),
                            "method": MethodChoices.MPESA,
                            "receipt_code": external_reference,
                            "idempotency_key": finance_key,
                        },
                        actor,
                    )
                    ledger_entry_id = str(result.ledger_entry.id)
                elif intent.purpose == PaymentPurpose.SPLIT_ALLOCATION:
                    metadata = dict(intent.metadata or {})
                    split = dict(metadata.get("split") or {})
                    loan_id = metadata.get("loan_id")
                    contribution_type_id = metadata.get("contribution_type_id")
                    if not loan_id or not contribution_type_id:
                        raise PaymentWorkflowError("Invalid split allocation metadata.")

                    repayment_amount = Decimal(str(split.get("repayment_amount", "0.00")))
                    contribution_amount = Decimal(
                        str(split.get("contribution_amount", "0.00"))
                    )
                    if repayment_amount <= Decimal("0.00") and contribution_amount <= Decimal(
                        "0.00"
                    ):
                        raise PaymentWorkflowError("Split allocation has no payable amount.")

                    if repayment_amount > Decimal("0.00"):
                        repayment_result = FinanceService.post_repayment(
                            loan_id,
                            {
                                "amount": repayment_amount,
                                "date_paid": timezone.localdate(),
                                "method": MethodChoices.MPESA,
                                "receipt_code": f"{external_reference}-R",
                                "idempotency_key": f"{finance_key}:R",
                            },
                            actor,
                        )
                        split_posted["repayment_ledger_entry_id"] = str(
                            repayment_result.ledger_entry.id
                        )

                    if contribution_amount > Decimal("0.00"):
                        contribution_result = FinanceService.post_contribution(
                            {
                                "chama_id": intent.chama_id,
                                "member_id": PaymentWorkflowService._intent_member_id(intent),
                                "contribution_type_id": contribution_type_id,
                                "amount": contribution_amount,
                                "date_paid": timezone.localdate(),
                                "method": MethodChoices.MPESA,
                                "receipt_code": f"{external_reference}-C",
                                "idempotency_key": f"{finance_key}:C",
                            },
                            actor,
                        )
                        split_posted["contribution_ledger_entry_id"] = str(
                            contribution_result.ledger_entry.id
                        )
                    ledger_entry_id = split_posted.get(
                        "repayment_ledger_entry_id",
                        split_posted.get("contribution_ledger_entry_id", ""),
                    )
                else:
                    raise PaymentWorkflowError("Unsupported deposit purpose.")

            elif intent.intent_type == PaymentIntentType.LOAN_REPAYMENT:
                result = FinanceService.post_repayment(
                    intent.reference_id,
                    {
                        "amount": intent.amount,
                        "date_paid": timezone.localdate(),
                        "method": MethodChoices.MPESA,
                        "receipt_code": external_reference,
                        "idempotency_key": finance_key,
                    },
                    actor,
                )
                ledger_entry_id = str(result.ledger_entry.id)

            elif intent.intent_type == PaymentIntentType.WITHDRAWAL:
                result = FinanceService.post_manual_adjustment(
                    {
                        "chama_id": intent.chama_id,
                        "amount": intent.amount,
                        "direction": LedgerDirection.DEBIT,
                        "reason": (
                            f"Member withdrawal via M-Pesa reference "
                            f"{external_reference}"
                        ),
                        "idempotency_key": finance_key,
                    },
                    actor,
                )
                ledger_entry_id = str(result.ledger_entry.id)

            elif intent.intent_type == PaymentIntentType.LOAN_DISBURSEMENT:
                result = FinanceService.disburse_loan(
                    intent.reference_id,
                    actor,
                    idempotency_key=finance_key,
                    disbursement_reference=external_reference,
                )
                loan = result.created
                loan.status = LoanStatus.ACTIVE
                loan.updated_by = actor
                loan.save(update_fields=["status", "updated_by", "updated_at"])
                ledger_entry_id = str(result.ledger_entry.id)

            else:
                raise PaymentWorkflowError("Unsupported payment intent type.")

        except IdempotencyConflictError:
            pass
        except FinanceServiceError as exc:
            raise PaymentWorkflowError(str(exc)) from exc

        metadata = dict(intent.metadata or {})
        metadata.update(
            {
                "external_reference": external_reference,
                "ledger_entry_id": ledger_entry_id,
                "posted_at": timezone.now().isoformat(),
            }
        )
        if split_posted:
            metadata["split_posted"] = split_posted
        intent.status = PaymentIntentStatus.SUCCESS
        intent.mpesa_receipt_number = external_reference
        intent.failure_reason = ""
        intent.metadata = metadata
        intent.updated_by = actor
        intent.save(
            update_fields=[
                "status",
                "mpesa_receipt_number",
                "failure_reason",
                "metadata",
                "updated_by",
                "updated_at",
            ]
        )
        PaymentWorkflowService._sync_transaction_record(
            intent=intent,
            reference=external_reference,
            amount=intent.amount,
            status_value=PaymentIntentStatus.SUCCESS,
            provider_response=metadata,
        )

        PaymentWorkflowService._activity(
            intent,
            PaymentActivityEvent.POSTED_TO_LEDGER,
            actor=actor,
            meta={
                "external_reference": external_reference,
                "ledger_entry_id": ledger_entry_id,
                "split_posted": split_posted,
            },
        )

        if intent.intent_type == PaymentIntentType.LOAN_DISBURSEMENT:
            PaymentWorkflowService._activity(
                intent,
                PaymentActivityEvent.LOAN_DISBURSED,
                actor=actor,
                meta={"external_reference": external_reference},
            )
        if intent.intent_type == PaymentIntentType.LOAN_REPAYMENT:
            PaymentWorkflowService._activity(
                intent,
                PaymentActivityEvent.LOAN_REPAYMENT_POSTED,
                actor=actor,
                meta={"external_reference": external_reference},
            )

        try:
            charge_amount = Decimal(
                str((intent.metadata or {}).get("mpesa_charge_amount", "0"))
            )
        except Exception:  # noqa: BLE001
            charge_amount = Decimal("0.00")
        if charge_amount > Decimal("0.00"):
            charge_result = FinanceService.post_manual_adjustment(
                {
                    "chama_id": intent.chama_id,
                    "amount": charge_amount,
                    "direction": LedgerDirection.DEBIT,
                    "reason": f"M-Pesa charge for reference {external_reference}",
                    "idempotency_key": f"{finance_key}:FEE",
                },
                actor,
            )
            metadata["mpesa_charge_ledger_entry_id"] = str(charge_result.ledger_entry.id)
            intent.metadata = metadata
            intent.save(update_fields=["metadata", "updated_at"])

        target_user = intent.created_by
        member_id = (intent.metadata or {}).get("member_id")
        if not target_user and member_id:
            target_user = (
                Membership.objects.select_related("user")
                .filter(
                    chama=intent.chama,
                    user_id=member_id,
                    is_active=True,
                    is_approved=True,
                    status=MemberStatus.ACTIVE,
                )
                .first()
            )
            target_user = target_user.user if target_user else None
        if target_user:
            try:
                from apps.notifications.models import NotificationType

                if intent.intent_type == PaymentIntentType.DEPOSIT:
                    message = (
                        f"Deposit confirmed. Amount KES {intent.amount}. "
                        f"Ref: {external_reference}."
                    )
                    notif_type = NotificationType.PAYMENT_CONFIRMATION
                    subject = "Deposit successful"
                elif intent.intent_type == PaymentIntentType.LOAN_REPAYMENT:
                    message = (
                        f"Loan repayment received. Amount KES {intent.amount}. "
                        f"Ref: {external_reference}."
                    )
                    notif_type = NotificationType.LOAN_UPDATE
                    subject = "Loan repayment successful"
                elif intent.intent_type == PaymentIntentType.LOAN_DISBURSEMENT:
                    message = (
                        f"Loan disbursed to M-Pesa ({PaymentWorkflowService._mask_phone(intent.phone)}). "
                        f"Amount KES {intent.amount}. Ref: {external_reference}."
                    )
                    notif_type = NotificationType.LOAN_UPDATE
                    subject = "Loan disbursement successful"
                else:
                    message = (
                        f"Withdrawal completed. Amount KES {intent.amount}. "
                        f"Ref: {external_reference}."
                    )
                    notif_type = NotificationType.PAYMENT_CONFIRMATION
                    subject = "Withdrawal successful"

                PaymentWorkflowService._notify(
                    user=target_user,
                    chama=intent.chama,
                    message=message,
                    subject=subject,
                    notification_type=notif_type,
                    idempotency_key=f"payment:success:{intent.id}:{external_reference}",
                )
            except Exception:  # noqa: BLE001
                pass

        return PaymentOperationResult(intent=intent, posted=True, message="Posted")

    @staticmethod
    def _mark_failed(intent: PaymentIntent, *, reason: str, actor=None, status: str = ""):
        metadata = dict(intent.metadata or {})
        metadata.update(
            {
                "failure_reason": reason,
                "failed_at": timezone.now().isoformat(),
            }
        )
        intent.status = status or PaymentIntentStatus.FAILED
        intent.failure_reason = reason
        intent.metadata = metadata
        intent.raw_response = metadata.get("raw_response", intent.raw_response)
        intent.updated_by = actor
        intent.save(
            update_fields=[
                "status",
                "failure_reason",
                "metadata",
                "raw_response",
                "updated_by",
                "updated_at",
            ]
        )
        PaymentWorkflowService._activity(
            intent,
            PaymentActivityEvent.FAILED,
            actor=actor,
            meta={"reason": reason},
        )

        target_user = intent.created_by
        if not target_user:
            member_id = (intent.metadata or {}).get("member_id")
            if member_id:
                membership = (
                    Membership.objects.select_related("user")
                    .filter(
                        chama=intent.chama,
                        user_id=member_id,
                        is_active=True,
                        status=MemberStatus.ACTIVE,
                        is_approved=True,
                    )
                    .first()
                )
                target_user = membership.user if membership else None
        if target_user:
            try:
                from apps.notifications.models import NotificationType

                if intent.intent_type == PaymentIntentType.LOAN_DISBURSEMENT:
                    subject = "Loan disbursement update"
                    notif_type = NotificationType.LOAN_UPDATE
                    message = (
                        "Loan disbursement was not completed. "
                        "Please contact your chama admin."
                    )
                elif intent.intent_type == PaymentIntentType.LOAN_REPAYMENT:
                    subject = "Loan repayment update"
                    notif_type = NotificationType.LOAN_UPDATE
                    message = "Loan repayment was not completed. Please retry."
                elif intent.intent_type == PaymentIntentType.DEPOSIT:
                    subject = "Deposit update"
                    notif_type = NotificationType.PAYMENT_CONFIRMATION
                    message = "Deposit was not completed. Please retry."
                else:
                    subject = "Withdrawal update"
                    notif_type = NotificationType.PAYMENT_CONFIRMATION
                    message = "Withdrawal request was not completed."

                PaymentWorkflowService._notify(
                    user=target_user,
                    chama=intent.chama,
                    message=message,
                    subject=subject,
                    notification_type=notif_type,
                    idempotency_key=f"payment:failed:{intent.id}:{intent.status}",
                )
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def ensure_loan_disbursement_intent(*, loan: Loan, actor) -> PaymentIntent:
        existing = PaymentIntent.objects.filter(
            chama=loan.chama,
            intent_type=PaymentIntentType.LOAN_DISBURSEMENT,
            reference_type="LOAN",
            reference_id=loan.id,
        ).exclude(
            status__in=[PaymentIntentStatus.CANCELLED, PaymentIntentStatus.FAILED]
        ).first()
        if existing:
            return existing

        phone = PaymentWorkflowService._safe_phone(loan.member.phone, loan.member.phone)
        intent = PaymentWorkflowService._create_intent(
            chama=loan.chama,
            actor=actor,
            intent_type=PaymentIntentType.LOAN_DISBURSEMENT,
            purpose=PaymentPurpose.OTHER,
            amount=PaymentWorkflowService._to_decimal(loan.principal),
            phone=phone,
            reference_type="LOAN",
            reference_id=loan.id,
            idempotency_key=f"loan-disbursement:{loan.id}",
            metadata={
                "member_id": str(loan.member_id),
                "loan_id": str(loan.id),
            },
        )

        WithdrawalApprovalLog.objects.get_or_create(
            chama=loan.chama,
            payment_intent=intent,
            step=WithdrawalApprovalStep.REQUESTED,
            defaults={
                "actor": actor,
                "notes": "Auto-created when loan approved.",
                "created_by": actor,
                "updated_by": actor,
            },
        )
        PaymentWorkflowService._activity(
            intent,
            PaymentActivityEvent.LOAN_DISBURSEMENT_REQUESTED,
            actor=actor,
            meta={"loan_id": str(loan.id)},
        )

        try:
            from apps.payments.tasks import payments_notify_loan_approved

            if _run_task_inline():
                payments_notify_loan_approved(str(intent.id))
            else:
                payments_notify_loan_approved.delay(str(intent.id))
        except Exception:  # noqa: BLE001
            pass

        return intent

    @staticmethod
    def initiate_deposit_stk(payload: dict, actor) -> dict:
        chama = get_object_or_404(Chama, id=payload["chama_id"])
        PaymentWorkflowService._require_active_member(actor, chama)

        purpose = payload.get("purpose", PaymentPurpose.CONTRIBUTION)
        if purpose != PaymentPurpose.CONTRIBUTION:
            raise PaymentWorkflowError("Only contribution deposits are supported.")

        reference_id = PaymentWorkflowService._parse_uuid(
            payload.get("reference_id"), label="reference_id"
        )
        if not ContributionType.objects.filter(
            id=reference_id,
            chama=chama,
            is_active=True,
        ).exists():
            raise PaymentWorkflowError("Contribution type not found for this chama.")

        amount = PaymentWorkflowService._to_decimal(payload["amount"])
        phone = PaymentWorkflowService._safe_phone(payload.get("phone"), actor.phone)
        idempotency_key = str(payload.get("idempotency_key") or "").strip() or (
            f"deposit-stk:{chama.id}:{actor.id}:{timezone.now().timestamp()}"
        )

        intent = PaymentWorkflowService._create_intent(
            chama=chama,
            actor=actor,
            intent_type=PaymentIntentType.DEPOSIT,
            purpose=PaymentPurpose.CONTRIBUTION,
            amount=amount,
            phone=phone,
            reference_type="CONTRIBUTION_TYPE",
            reference_id=reference_id,
            idempotency_key=idempotency_key,
            metadata={"member_id": str(actor.id)},
            expires_minutes=PaymentWorkflowService.DEFAULT_STK_EXPIRY_MINUTES,
        )

        existing_tx = intent.stk_transactions.order_by("-created_at").first()
        if existing_tx and intent.status in {
            PaymentIntentStatus.PENDING,
            PaymentIntentStatus.SUCCESS,
        }:
            return {
                "intent": intent,
                "stk_transaction": existing_tx,
                "created": False,
            }

        PaymentWorkflowService._enforce_billing_stk_access(chama)

        if getattr(settings, "MPESA_USE_STUB", True):
            response_payload = {
                "MerchantRequestID": f"MR_{uuid.uuid4().hex[:20]}",
                "CheckoutRequestID": f"ws_CO_{uuid.uuid4().hex[:24]}",
                "ResponseCode": "0",
                "ResponseDescription": "Accepted",
            }
        else:
            client = MpesaClient()
            response_payload = client.initiate_stk_push(
                phone_number=phone,
                amount=str(amount),
                account_reference=f"DEP-{intent.id.hex[:10]}",
                transaction_desc="Chama deposit",
            )

        checkout_request_id = str(response_payload.get("CheckoutRequestID") or "").strip()
        if not checkout_request_id:
            checkout_request_id = f"ws_CO_{uuid.uuid4().hex[:24]}"
        merchant_request_id = str(response_payload.get("MerchantRequestID") or "").strip()

        stk_tx = MpesaSTKTransaction.objects.create(
            chama=chama,
            intent=intent,
            phone=phone,
            amount=amount,
            merchant_request_id=merchant_request_id,
            checkout_request_id=checkout_request_id,
            status=PaymentIntentStatus.PENDING,
            created_by=actor,
            updated_by=actor,
        )

        intent.status = PaymentIntentStatus.PENDING
        intent.checkout_request_id = checkout_request_id
        intent.merchant_request_id = merchant_request_id
        intent.raw_response = response_payload
        metadata = dict(intent.metadata or {})
        metadata.update(
            {
                "checkout_request_id": checkout_request_id,
                "merchant_request_id": merchant_request_id,
            }
        )
        intent.metadata = metadata
        intent.updated_by = actor
        intent.save(
            update_fields=[
                "status",
                "checkout_request_id",
                "merchant_request_id",
                "raw_response",
                "metadata",
                "updated_by",
                "updated_at",
            ]
        )
        PaymentWorkflowService._sync_transaction_record(
            intent=intent,
            reference=checkout_request_id,
            amount=amount,
            status_value=PaymentIntentStatus.PENDING,
            provider_response=response_payload,
        )
        PaymentWorkflowService._consume_billing_usage(chama, "stk_pushes", 1)

        PaymentWorkflowService._activity(
            intent,
            PaymentActivityEvent.STK_SENT,
            actor=actor,
            meta={
                "checkout_request_id": checkout_request_id,
                "merchant_request_id": merchant_request_id,
            },
        )

        try:
            from apps.notifications.models import NotificationType

            PaymentWorkflowService._notify(
                user=actor,
                chama=chama,
                message=(
                    f"Deposit request received. Amount KES {amount}. "
                    f"Complete M-Pesa prompt to finish payment."
                ),
                subject="Deposit initiated",
                notification_type=NotificationType.PAYMENT_CONFIRMATION,
                idempotency_key=f"payment:deposit:init:{intent.id}",
            )
        except Exception:  # noqa: BLE001
            pass

        return {
            "intent": intent,
            "stk_transaction": stk_tx,
            "created": True,
        }

    @staticmethod
    def create_deposit_c2b_intent(payload: dict, actor) -> dict:
        chama = get_object_or_404(Chama, id=payload["chama_id"])
        PaymentWorkflowService._require_active_member(actor, chama)

        reference_id = PaymentWorkflowService._parse_uuid(
            payload.get("reference_id"), label="reference_id"
        )
        if not ContributionType.objects.filter(
            id=reference_id,
            chama=chama,
            is_active=True,
        ).exists():
            raise PaymentWorkflowError("Contribution type not found for this chama.")

        amount = PaymentWorkflowService._to_decimal(payload["amount"])
        phone = PaymentWorkflowService._safe_phone(payload.get("phone"), actor.phone)
        idempotency_key = str(payload.get("idempotency_key") or "").strip() or (
            f"deposit-c2b:{chama.id}:{actor.id}:{timezone.now().timestamp()}"
        )

        intent = PaymentWorkflowService._create_intent(
            chama=chama,
            actor=actor,
            intent_type=PaymentIntentType.DEPOSIT,
            purpose=PaymentPurpose.CONTRIBUTION,
            amount=amount,
            phone=phone,
            reference_type="CONTRIBUTION_TYPE",
            reference_id=reference_id,
            idempotency_key=idempotency_key,
            metadata={"member_id": str(actor.id)},
            expires_minutes=60,
        )

        account_reference = f"D{intent.id.hex[:10]}"
        metadata = dict(intent.metadata or {})
        metadata.update(
            {
                "account_reference": account_reference,
                "paybill": settings.DARAJA_SHORTCODE,
            }
        )
        intent.status = PaymentIntentStatus.PENDING
        intent.metadata = metadata
        intent.updated_by = actor
        intent.save(update_fields=["status", "metadata", "updated_by", "updated_at"])

        try:
            from apps.notifications.models import NotificationType

            PaymentWorkflowService._notify(
                user=actor,
                chama=chama,
                message=(
                    f"Deposit intent created for KES {amount}. "
                    "Use Paybill/Till with the generated account reference."
                ),
                subject="Deposit pending",
                notification_type=NotificationType.PAYMENT_CONFIRMATION,
                idempotency_key=f"payment:deposit:c2b:init:{intent.id}",
            )
        except Exception:  # noqa: BLE001
            pass

        return {
            "intent": intent,
            "instructions": {
                "shortcode": settings.DARAJA_SHORTCODE,
                "account_reference": account_reference,
                "amount": str(amount),
                "currency": CurrencyChoices.KES,
            },
        }

    @staticmethod
    def initiate_split_stk(payload: dict, actor) -> dict:
        chama = get_object_or_404(Chama, id=payload["chama_id"])
        membership = PaymentWorkflowService._require_active_member(actor, chama)
        effective_role = get_effective_role(actor, chama.id, membership) or membership.role
        if effective_role == MembershipRole.AUDITOR:
            raise PaymentWorkflowError("Auditor role is read-only.")

        loan = get_object_or_404(Loan, id=payload["loan_id"], chama=chama)
        if effective_role == MembershipRole.MEMBER and loan.member_id != actor.id:
            raise PaymentWorkflowError("Members can only split-pay their own loan.")

        contribution_type = get_object_or_404(
            ContributionType,
            id=payload["contribution_type_id"],
            chama=chama,
            is_active=True,
        )
        del contribution_type  # explicit validation target

        total_amount = PaymentWorkflowService._to_decimal(payload["amount"])
        strategy = str(payload.get("strategy", "repayment_first") or "repayment_first")
        repayment_amount = payload.get("repayment_amount")
        repayment_component, contribution_component = PaymentWorkflowService._split_amounts(
            loan=loan,
            total_amount=total_amount,
            strategy=strategy,
            repayment_amount=repayment_amount,
        )
        if repayment_component <= Decimal("0.00") and contribution_component <= Decimal(
            "0.00"
        ):
            raise PaymentWorkflowError("Split allocation produced zero amounts.")

        payer = loan.member if effective_role == MembershipRole.MEMBER else actor
        phone = PaymentWorkflowService._safe_phone(payload.get("phone"), payer.phone)
        idempotency_key = str(payload.get("idempotency_key") or "").strip() or (
            f"split-stk:{chama.id}:{payer.id}:{timezone.now().timestamp()}"
        )

        intent = PaymentWorkflowService._create_intent(
            chama=chama,
            actor=payer,
            intent_type=PaymentIntentType.DEPOSIT,
            purpose=PaymentPurpose.SPLIT_ALLOCATION,
            amount=total_amount,
            phone=phone,
            reference_type="SPLIT",
            reference_id=loan.id,
            idempotency_key=idempotency_key,
            metadata={
                "member_id": str(loan.member_id),
                "loan_id": str(loan.id),
                "contribution_type_id": str(payload["contribution_type_id"]),
                "strategy": strategy,
                "split": {
                    "repayment_amount": str(repayment_component),
                    "contribution_amount": str(contribution_component),
                },
            },
            expires_minutes=PaymentWorkflowService.DEFAULT_STK_EXPIRY_MINUTES,
        )

        existing_tx = intent.stk_transactions.order_by("-created_at").first()
        if existing_tx and intent.status in {
            PaymentIntentStatus.PENDING,
            PaymentIntentStatus.SUCCESS,
        }:
            return {
                "intent": intent,
                "stk_transaction": existing_tx,
                "created": False,
            }

        PaymentWorkflowService._enforce_billing_stk_access(chama)

        if getattr(settings, "MPESA_USE_STUB", True):
            response_payload = {
                "MerchantRequestID": f"MR_{uuid.uuid4().hex[:20]}",
                "CheckoutRequestID": f"ws_CO_{uuid.uuid4().hex[:24]}",
                "ResponseCode": "0",
                "ResponseDescription": "Accepted",
            }
        else:
            client = MpesaClient()
            response_payload = client.initiate_stk_push(
                phone_number=phone,
                amount=str(total_amount),
                account_reference=f"SP-{intent.id.hex[:8]}",
                transaction_desc="Split repayment + contribution",
            )

        checkout_request_id = str(response_payload.get("CheckoutRequestID") or "").strip()
        if not checkout_request_id:
            checkout_request_id = f"ws_CO_{uuid.uuid4().hex[:24]}"
        merchant_request_id = str(response_payload.get("MerchantRequestID") or "").strip()

        stk_tx = MpesaSTKTransaction.objects.create(
            chama=chama,
            intent=intent,
            phone=phone,
            amount=total_amount,
            merchant_request_id=merchant_request_id,
            checkout_request_id=checkout_request_id,
            status=PaymentIntentStatus.PENDING,
            created_by=actor,
            updated_by=actor,
        )

        intent.status = PaymentIntentStatus.PENDING
        intent.checkout_request_id = checkout_request_id
        intent.merchant_request_id = merchant_request_id
        intent.raw_response = response_payload
        metadata = dict(intent.metadata or {})
        metadata.update(
            {
                "checkout_request_id": checkout_request_id,
                "merchant_request_id": merchant_request_id,
            }
        )
        intent.metadata = metadata
        intent.updated_by = actor
        intent.save(
            update_fields=[
                "status",
                "checkout_request_id",
                "merchant_request_id",
                "raw_response",
                "metadata",
                "updated_by",
                "updated_at",
            ]
        )
        PaymentWorkflowService._sync_transaction_record(
            intent=intent,
            reference=checkout_request_id,
            amount=total_amount,
            status_value=PaymentIntentStatus.PENDING,
            provider_response=response_payload,
        )
        PaymentWorkflowService._consume_billing_usage(chama, "stk_pushes", 1)

        PaymentWorkflowService._activity(
            intent,
            PaymentActivityEvent.STK_SENT,
            actor=actor,
            meta={
                "checkout_request_id": checkout_request_id,
                "merchant_request_id": merchant_request_id,
                "split": metadata.get("split", {}),
            },
        )

        return {
            "intent": intent,
            "stk_transaction": stk_tx,
            "created": True,
        }

    @staticmethod
    def create_split_c2b_intent(payload: dict, actor) -> dict:
        chama = get_object_or_404(Chama, id=payload["chama_id"])
        membership = PaymentWorkflowService._require_active_member(actor, chama)
        effective_role = get_effective_role(actor, chama.id, membership) or membership.role
        if effective_role == MembershipRole.AUDITOR:
            raise PaymentWorkflowError("Auditor role is read-only.")

        loan = get_object_or_404(Loan, id=payload["loan_id"], chama=chama)
        if effective_role == MembershipRole.MEMBER and loan.member_id != actor.id:
            raise PaymentWorkflowError("Members can only split-pay their own loan.")

        contribution_type = get_object_or_404(
            ContributionType,
            id=payload["contribution_type_id"],
            chama=chama,
            is_active=True,
        )
        del contribution_type

        total_amount = PaymentWorkflowService._to_decimal(payload["amount"])
        strategy = str(payload.get("strategy", "repayment_first") or "repayment_first")
        repayment_amount = payload.get("repayment_amount")
        repayment_component, contribution_component = PaymentWorkflowService._split_amounts(
            loan=loan,
            total_amount=total_amount,
            strategy=strategy,
            repayment_amount=repayment_amount,
        )
        if repayment_component <= Decimal("0.00") and contribution_component <= Decimal(
            "0.00"
        ):
            raise PaymentWorkflowError("Split allocation produced zero amounts.")

        payer = loan.member if effective_role == MembershipRole.MEMBER else actor
        phone = PaymentWorkflowService._safe_phone(payload.get("phone"), payer.phone)
        idempotency_key = str(payload.get("idempotency_key") or "").strip() or (
            f"split-c2b:{chama.id}:{payer.id}:{timezone.now().timestamp()}"
        )

        intent = PaymentWorkflowService._create_intent(
            chama=chama,
            actor=payer,
            intent_type=PaymentIntentType.DEPOSIT,
            purpose=PaymentPurpose.SPLIT_ALLOCATION,
            amount=total_amount,
            phone=phone,
            reference_type="SPLIT",
            reference_id=loan.id,
            idempotency_key=idempotency_key,
            metadata={
                "member_id": str(loan.member_id),
                "loan_id": str(loan.id),
                "contribution_type_id": str(payload["contribution_type_id"]),
                "strategy": strategy,
                "split": {
                    "repayment_amount": str(repayment_component),
                    "contribution_amount": str(contribution_component),
                },
            },
            expires_minutes=60,
        )

        account_reference = f"SP{loan.id.hex[:6]}{intent.id.hex[:4]}"
        metadata = dict(intent.metadata or {})
        metadata.update(
            {
                "account_reference": account_reference,
                "paybill": settings.DARAJA_SHORTCODE,
            }
        )
        intent.status = PaymentIntentStatus.PENDING
        intent.metadata = metadata
        intent.updated_by = actor
        intent.save(update_fields=["status", "metadata", "updated_by", "updated_at"])

        return {
            "intent": intent,
            "instructions": {
                "shortcode": settings.DARAJA_SHORTCODE,
                "account_reference": account_reference,
                "amount": str(total_amount),
                "currency": CurrencyChoices.KES,
                "allocation": metadata.get("split", {}),
            },
        }

    @staticmethod
    def initiate_loan_repayment_stk(*, loan_id, payload: dict, actor) -> dict:
        loan = get_object_or_404(Loan, id=loan_id)
        chama = loan.chama
        membership = PaymentWorkflowService._require_active_member(actor, chama)
        effective_role = get_effective_role(actor, chama.id, membership) or membership.role
        if effective_role == MembershipRole.MEMBER and loan.member_id != actor.id:
            raise PaymentWorkflowError("You can only repay your own loans.")

        amount = payload.get("amount")
        if amount in (None, ""):
            amount = PaymentWorkflowService._default_repayment_amount(loan)
        amount = PaymentWorkflowService._to_decimal(amount)
        PaymentWorkflowService._validate_repayment_amount(loan, amount)

        payer = loan.member if effective_role == MembershipRole.MEMBER else actor
        phone = PaymentWorkflowService._safe_phone(payload.get("phone"), payer.phone)
        idempotency_key = str(payload.get("idempotency_key") or "").strip() or (
            f"loan-repay-stk:{loan.id}:{payer.id}:{timezone.now().timestamp()}"
        )

        intent = PaymentWorkflowService._create_intent(
            chama=chama,
            actor=payer,
            intent_type=PaymentIntentType.LOAN_REPAYMENT,
            purpose=PaymentPurpose.LOAN_REPAYMENT,
            amount=amount,
            phone=phone,
            reference_type="LOAN",
            reference_id=loan.id,
            idempotency_key=idempotency_key,
            metadata={"member_id": str(loan.member_id), "loan_id": str(loan.id)},
            expires_minutes=PaymentWorkflowService.DEFAULT_STK_EXPIRY_MINUTES,
        )

        existing_tx = intent.stk_transactions.order_by("-created_at").first()
        if existing_tx and intent.status in {
            PaymentIntentStatus.PENDING,
            PaymentIntentStatus.SUCCESS,
        }:
            return {"intent": intent, "stk_transaction": existing_tx, "created": False}

        PaymentWorkflowService._enforce_billing_stk_access(chama)

        if getattr(settings, "MPESA_USE_STUB", True):
            response_payload = {
                "MerchantRequestID": f"MR_{uuid.uuid4().hex[:20]}",
                "CheckoutRequestID": f"ws_CO_{uuid.uuid4().hex[:24]}",
                "ResponseCode": "0",
                "ResponseDescription": "Accepted",
            }
        else:
            client = MpesaClient()
            response_payload = client.initiate_stk_push(
                phone_number=phone,
                amount=str(amount),
                account_reference=f"LR-{loan.id.hex[:8]}",
                transaction_desc="Loan repayment",
            )

        checkout_request_id = str(response_payload.get("CheckoutRequestID") or "").strip()
        if not checkout_request_id:
            checkout_request_id = f"ws_CO_{uuid.uuid4().hex[:24]}"
        merchant_request_id = str(response_payload.get("MerchantRequestID") or "").strip()

        stk_tx = MpesaSTKTransaction.objects.create(
            chama=chama,
            intent=intent,
            phone=phone,
            amount=amount,
            merchant_request_id=merchant_request_id,
            checkout_request_id=checkout_request_id,
            status=PaymentIntentStatus.PENDING,
            created_by=actor,
            updated_by=actor,
        )

        intent.status = PaymentIntentStatus.PENDING
        intent.checkout_request_id = checkout_request_id
        intent.merchant_request_id = merchant_request_id
        intent.raw_response = response_payload
        metadata = dict(intent.metadata or {})
        metadata.update(
            {
                "checkout_request_id": checkout_request_id,
                "merchant_request_id": merchant_request_id,
            }
        )
        intent.metadata = metadata
        intent.updated_by = actor
        intent.save(
            update_fields=[
                "status",
                "checkout_request_id",
                "merchant_request_id",
                "raw_response",
                "metadata",
                "updated_by",
                "updated_at",
            ]
        )
        PaymentWorkflowService._sync_transaction_record(
            intent=intent,
            reference=checkout_request_id,
            amount=amount,
            status_value=PaymentIntentStatus.PENDING,
            provider_response=response_payload,
        )
        PaymentWorkflowService._consume_billing_usage(chama, "stk_pushes", 1)

        PaymentWorkflowService._activity(
            intent,
            PaymentActivityEvent.STK_SENT,
            actor=actor,
            meta={
                "checkout_request_id": checkout_request_id,
                "merchant_request_id": merchant_request_id,
                "loan_id": str(loan.id),
            },
        )

        try:
            from apps.notifications.models import NotificationType

            PaymentWorkflowService._notify(
                user=payer,
                chama=chama,
                message=(
                    f"Loan repayment initiated for KES {amount}. "
                    "Complete M-Pesa prompt to finish payment."
                ),
                subject="Loan repayment initiated",
                notification_type=NotificationType.LOAN_UPDATE,
                idempotency_key=f"payment:loan-repay:init:{intent.id}",
            )
        except Exception:  # noqa: BLE001
            pass

        return {"intent": intent, "stk_transaction": stk_tx, "created": True}

    @staticmethod
    def create_loan_repayment_c2b_intent(*, loan_id, payload: dict, actor) -> dict:
        loan = get_object_or_404(Loan, id=loan_id)
        chama = loan.chama
        membership = PaymentWorkflowService._require_active_member(actor, chama)
        effective_role = get_effective_role(actor, chama.id, membership) or membership.role
        if effective_role == MembershipRole.MEMBER and loan.member_id != actor.id:
            raise PaymentWorkflowError("You can only repay your own loans.")

        amount = payload.get("amount")
        if amount in (None, ""):
            amount = PaymentWorkflowService._default_repayment_amount(loan)
        amount = PaymentWorkflowService._to_decimal(amount)
        PaymentWorkflowService._validate_repayment_amount(loan, amount)

        payer = loan.member if effective_role == MembershipRole.MEMBER else actor
        phone = PaymentWorkflowService._safe_phone(payload.get("phone"), payer.phone)
        idempotency_key = str(payload.get("idempotency_key") or "").strip() or (
            f"loan-repay-c2b:{loan.id}:{payer.id}:{timezone.now().timestamp()}"
        )

        intent = PaymentWorkflowService._create_intent(
            chama=chama,
            actor=payer,
            intent_type=PaymentIntentType.LOAN_REPAYMENT,
            purpose=PaymentPurpose.LOAN_REPAYMENT,
            amount=amount,
            phone=phone,
            reference_type="LOAN",
            reference_id=loan.id,
            idempotency_key=idempotency_key,
            metadata={"member_id": str(loan.member_id), "loan_id": str(loan.id)},
            expires_minutes=60,
        )

        account_reference = f"LR{loan.id.hex[:6]}{intent.id.hex[:4]}"
        metadata = dict(intent.metadata or {})
        metadata.update(
            {
                "account_reference": account_reference,
                "paybill": settings.DARAJA_SHORTCODE,
            }
        )
        intent.status = PaymentIntentStatus.PENDING
        intent.metadata = metadata
        intent.updated_by = actor
        intent.save(update_fields=["status", "metadata", "updated_by", "updated_at"])

        return {
            "intent": intent,
            "instructions": {
                "shortcode": settings.DARAJA_SHORTCODE,
                "account_reference": account_reference,
                "amount": str(amount),
                "currency": CurrencyChoices.KES,
            },
        }

    @staticmethod
    def loan_repayment_status(*, loan_id, actor, chama_id=None) -> dict:
        loan = get_object_or_404(Loan, id=loan_id)
        if chama_id and str(loan.chama_id) != str(chama_id):
            raise PaymentWorkflowError("Loan does not belong to chama scope.")

        membership = PaymentWorkflowService._require_active_member(actor, loan.chama)
        effective_role = (
            get_effective_role(actor, loan.chama_id, membership) or membership.role
        )
        if effective_role == MembershipRole.MEMBER and loan.member_id != actor.id:
            raise PaymentWorkflowError("You can only view your own loan repayment status.")

        total_due = loan.installments.aggregate(
            total=Coalesce(
                Sum("expected_amount"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        total_paid = loan.repayments.aggregate(
            total=Coalesce(
                Sum("amount"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        outstanding = max(Decimal(total_due) - Decimal(total_paid), Decimal("0.00"))

        next_due = loan.installments.filter(status__in=["due", "overdue"]).order_by(
            "due_date", "created_at"
        ).first()

        recent_intents = PaymentIntent.objects.filter(
            intent_type=PaymentIntentType.LOAN_REPAYMENT,
            reference_type="LOAN",
            reference_id=loan.id,
        ).order_by("-created_at")[:10]

        return {
            "loan_id": str(loan.id),
            "loan_status": loan.status,
            "outstanding_balance": str(outstanding.quantize(Decimal("0.01"))),
            "next_due": {
                "amount": str(next_due.expected_amount) if next_due else None,
                "date": next_due.due_date.isoformat() if next_due else None,
                "status": next_due.status if next_due else None,
            },
            "recent_payments": [
                {
                    "intent_id": str(item.id),
                    "status": item.status,
                    "amount": str(item.amount),
                    "phone": PaymentWorkflowService._mask_phone(item.phone),
                    "created_at": item.created_at.isoformat(),
                    "reference": (item.metadata or {}).get("external_reference", ""),
                }
                for item in recent_intents
            ],
        }

    @staticmethod
    def list_my_transactions(*, actor, chama_id):
        chama = get_object_or_404(Chama, id=chama_id)
        PaymentWorkflowService._require_active_member(actor, chama)

        intents = (
            PaymentIntent.objects.filter(chama=chama, created_by=actor)
            .prefetch_related("stk_transactions", "c2b_transactions", "b2c_payouts")
            .order_by("-created_at")
        )
        return intents

    @staticmethod
    def request_withdrawal(payload: dict, actor) -> PaymentIntent:
        chama = get_object_or_404(Chama, id=payload["chama_id"])
        PaymentWorkflowService._require_treasurer_or_admin(actor, chama)

        amount = PaymentWorkflowService._to_decimal(payload["amount"])
        phone = PaymentWorkflowService._safe_phone(payload.get("phone"), actor.phone)
        reference_id = PaymentWorkflowService._parse_uuid(
            payload.get("reference_id"),
            label="reference_id",
        )
        purpose = payload.get("purpose", PaymentPurpose.OTHER)

        idempotency_key = str(payload.get("idempotency_key") or "").strip() or (
            f"withdraw:{chama.id}:{actor.id}:{timezone.now().timestamp()}"
        )

        intent = PaymentWorkflowService._create_intent(
            chama=chama,
            actor=actor,
            intent_type=PaymentIntentType.WITHDRAWAL,
            purpose=purpose,
            amount=amount,
            phone=phone,
            reference_type=str(payload.get("reference_type", "OTHER") or "OTHER"),
            reference_id=reference_id,
            idempotency_key=idempotency_key,
            metadata={
                "reason": str(payload.get("reason", ""))[:250],
                "beneficiary_name": str(payload.get("beneficiary_name", ""))[:160],
                "beneficiary_phone": str(payload.get("beneficiary_phone", ""))[:16],
                "beneficiary_details": str(payload.get("beneficiary_details", ""))[:500],
                "workflow": "withdrawal_approval",
            },
        )

        WithdrawalApprovalLog.objects.get_or_create(
            chama=chama,
            payment_intent=intent,
            step=WithdrawalApprovalStep.REQUESTED,
            defaults={
                "actor": actor,
                "notes": str(payload.get("reason", ""))[:250],
                "created_by": actor,
                "updated_by": actor,
            },
        )

        try:
            from apps.notifications.models import NotificationType

            PaymentWorkflowService._notify(
                user=actor,
                chama=chama,
                message=(
                    f"Withdrawal requested for KES {amount}. "
                    "Awaiting treasurer/admin approvals."
                ),
                subject="Withdrawal requested",
                notification_type=NotificationType.PAYMENT_CONFIRMATION,
                idempotency_key=f"payment:withdraw:requested:{intent.id}",
            )
            approvers = Membership.objects.select_related("user").filter(
                chama=chama,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
                role__in=[MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER],
            )
            for approver in approvers:
                if approver.user_id == actor.id:
                    continue
                PaymentWorkflowService._notify(
                    user=approver.user,
                    chama=chama,
                    message=(
                        f"Withdrawal request for KES {amount} needs approval. "
                        f"Beneficiary: {str(payload.get('beneficiary_name', '')).strip() or PaymentWorkflowService._mask_phone(phone)}."
                    ),
                    subject="Withdrawal approval required",
                    notification_type=NotificationType.PAYMENT_CONFIRMATION,
                    idempotency_key=f"payment:withdraw:review:{intent.id}:{approver.user_id}",
                    channels=["in_app", "email"],
                )
        except Exception:  # noqa: BLE001
            pass
        return intent

    @staticmethod
    def reject_withdrawal_intent(*, intent_id, actor, note: str = "") -> PaymentIntent:
        intent = get_object_or_404(PaymentIntent.objects.select_related("chama"), id=intent_id)
        PaymentWorkflowService._require_treasurer_or_admin(actor, intent.chama)

        if intent.intent_type != PaymentIntentType.WITHDRAWAL:
            raise PaymentWorkflowError("Intent is not a withdrawal.")
        if intent.status in {PaymentIntentStatus.SUCCESS, PaymentIntentStatus.CANCELLED}:
            raise PaymentWorkflowError("Intent can no longer be rejected.")

        intent.status = PaymentIntentStatus.CANCELLED
        intent.failure_reason = note or "Rejected by approver."
        metadata = dict(intent.metadata or {})
        metadata.update(
            {
                "rejected_by": str(actor.id),
                "rejected_at": timezone.now().isoformat(),
                "rejection_reason": note or "",
            }
        )
        intent.metadata = metadata
        intent.updated_by = actor
        intent.save(
            update_fields=[
                "status",
                "failure_reason",
                "metadata",
                "updated_by",
                "updated_at",
            ]
        )

        WithdrawalApprovalLog.objects.create(
            chama=intent.chama,
            payment_intent=intent,
            step=WithdrawalApprovalStep.REJECTED,
            actor=actor,
            notes=note,
            created_by=actor,
            updated_by=actor,
        )
        PaymentWorkflowService._activity(
            intent,
            PaymentActivityEvent.CANCELLED,
            actor=actor,
            meta={"rejection_note": note},
        )

        if intent.created_by:
            try:
                from apps.notifications.models import NotificationType

                PaymentWorkflowService._notify(
                    user=intent.created_by,
                    chama=intent.chama,
                    message=(
                        f"Withdrawal of KES {intent.amount} was rejected. "
                        f"Reason: {note or 'No reason provided.'}"
                    ),
                    subject="Withdrawal rejected",
                    notification_type=NotificationType.PAYMENT_CONFIRMATION,
                    idempotency_key=f"payment:withdraw:rejected:{intent.id}",
                )
            except Exception:  # noqa: BLE001
                pass
        return intent

    @staticmethod
    def _approval_state(intent: PaymentIntent) -> dict:
        logs = WithdrawalApprovalLog.objects.filter(payment_intent=intent)
        return {
            "treasurer": logs.filter(
                step=WithdrawalApprovalStep.TREASURER_APPROVED
            ).exists(),
            "admin": logs.filter(step=WithdrawalApprovalStep.ADMIN_APPROVED).exists(),
        }

    @staticmethod
    def approve_withdrawal_intent(*, intent_id, actor, note: str = "") -> PaymentIntent:
        intent = get_object_or_404(PaymentIntent.objects.select_related("chama"), id=intent_id)
        membership = PaymentWorkflowService._require_treasurer_or_admin(actor, intent.chama)
        effective_role = (
            get_effective_role(actor, intent.chama_id, membership) or membership.role
        )

        if intent.intent_type not in {
            PaymentIntentType.WITHDRAWAL,
            PaymentIntentType.LOAN_DISBURSEMENT,
        }:
            raise PaymentWorkflowError("Intent is not approval-based.")
        if intent.status in {PaymentIntentStatus.SUCCESS, PaymentIntentStatus.CANCELLED}:
            raise PaymentWorkflowError("Intent is no longer approvable.")

        step = (
            WithdrawalApprovalStep.ADMIN_APPROVED
            if effective_role == MembershipRole.CHAMA_ADMIN
            else WithdrawalApprovalStep.TREASURER_APPROVED
        )

        if WithdrawalApprovalLog.objects.filter(
            payment_intent=intent,
            step=step,
            actor=actor,
        ).exists():
            return intent

        if step == WithdrawalApprovalStep.ADMIN_APPROVED:
            if not WithdrawalApprovalLog.objects.filter(
                payment_intent=intent,
                step=WithdrawalApprovalStep.TREASURER_APPROVED,
            ).exists():
                raise PaymentWorkflowError(
                    "Treasurer approval is required before admin approval."
                )

        WithdrawalApprovalLog.objects.create(
            chama=intent.chama,
            payment_intent=intent,
            step=step,
            actor=actor,
            notes=note,
            created_by=actor,
            updated_by=actor,
        )
        approval_state = PaymentWorkflowService._approval_state(intent)
        metadata = dict(intent.metadata or {})
        metadata.update(
            {
                "approval_state": approval_state,
                "ready_for_payout": approval_state["treasurer"] and approval_state["admin"],
            }
        )
        intent.metadata = metadata
        intent.status = (
            PaymentIntentStatus.PENDING
            if metadata["ready_for_payout"]
            else PaymentIntentStatus.INITIATED
        )
        intent.updated_by = actor
        intent.save(update_fields=["metadata", "status", "updated_by", "updated_at"])
        PaymentWorkflowService._activity(
            intent,
            PaymentActivityEvent.CREATED,
            actor=actor,
            meta={"approval_step": step, "note": note},
        )

        if intent.created_by:
            try:
                from apps.notifications.models import NotificationType

                message = (
                    "Withdrawal approved by treasurer. Awaiting admin approval."
                    if step == WithdrawalApprovalStep.TREASURER_APPROVED
                    else "Withdrawal approved and ready for M-Pesa sending."
                )
                PaymentWorkflowService._notify(
                    user=intent.created_by,
                    chama=intent.chama,
                    message=message,
                    subject="Withdrawal approval update",
                    notification_type=NotificationType.PAYMENT_CONFIRMATION,
                    idempotency_key=f"payment:withdraw:approved:{intent.id}:{step}",
                )
            except Exception:  # noqa: BLE001
                pass
        return intent

    @staticmethod
    def reject_loan_disbursement_intent(*, intent_id, actor, note: str = "") -> PaymentIntent:
        """Reject a loan disbursement intent."""
        intent = get_object_or_404(PaymentIntent.objects.select_related("chama"), id=intent_id)
        PaymentWorkflowService._require_treasurer_or_admin(actor, intent.chama)
        
        if intent.intent_type != PaymentIntentType.LOAN_DISBURSEMENT:
            raise PaymentWorkflowError("Intent is not a loan disbursement.")
        if intent.status in {PaymentIntentStatus.SUCCESS, PaymentIntentStatus.CANCELLED}:
            raise PaymentWorkflowError("Intent can no longer be rejected.")
        
        # Cancel the intent
        intent.status = PaymentIntentStatus.CANCELLED
        intent.updated_by = actor
        intent.save()
        
        # Log the rejection
        WithdrawalApprovalLog.objects.create(
            chama=intent.chama,
            payment_intent=intent,
            step=WithdrawalApprovalStep.REJECTED,
            actor=actor,
            notes=note,
            created_by=actor,
            updated_by=actor,
        )
        
        PaymentWorkflowService._activity(
            intent,
            PaymentActivityEvent.CANCELLED,
            actor=actor,
            meta={"rejection_note": note},
        )
        
        # Notify the user
        if intent.created_by:
            try:
                from apps.notifications.models import NotificationType
                message = f"Your loan disbursement of Ksh {intent.amount} has been rejected. Reason: {note or 'No reason provided.'}"
                PaymentWorkflowService._notify(
                    user=intent.created_by,
                    chama=intent.chama,
                    message=message,
                    notification_type=NotificationType.LOAN_UPDATE,
                )
            except Exception:
                pass  # Don't fail the request if notification fails
        
        return intent

    @staticmethod
    def send_b2c_payout(*, intent_id, actor, payout_proof: str = "") -> MpesaB2CPayout:
        intent = get_object_or_404(
            PaymentIntent.objects.select_related("chama", "created_by"),
            id=intent_id,
        )
        PaymentWorkflowService._require_treasurer_or_admin(actor, intent.chama)

        if intent.intent_type not in {
            PaymentIntentType.WITHDRAWAL,
            PaymentIntentType.LOAN_DISBURSEMENT,
        }:
            raise PaymentWorkflowError("Intent is not a payout intent.")

        if intent.status == PaymentIntentStatus.SUCCESS:
            raise PaymentWorkflowError("Intent already settled.")

        state = PaymentWorkflowService._approval_state(intent)
        if not state["treasurer"] or not state["admin"]:
            raise PaymentWorkflowError(
                "Both treasurer and admin approvals are required before sending."
            )

        if intent.intent_type in {
            PaymentIntentType.WITHDRAWAL,
            PaymentIntentType.LOAN_DISBURSEMENT,
        }:
            PaymentWorkflowService._ensure_sufficient_balance(intent.chama, intent.amount)

        if intent.intent_type == PaymentIntentType.LOAN_DISBURSEMENT:
            loan = get_object_or_404(Loan, id=intent.reference_id, chama=intent.chama)
            if loan.status != LoanStatus.APPROVED:
                raise PaymentWorkflowError("Loan is not ready for disbursement.")
            if (
                loan.loan_product
                and loan.loan_product.require_separate_disburser
                and loan.approved_by_id == actor.id
            ):
                raise PaymentWorkflowError(
                    "Maker-checker enforcement: approver cannot send disbursement."
                )
            loan.status = LoanStatus.DISBURSING
            loan.updated_by = actor
            loan.save(update_fields=["status", "updated_by", "updated_at"])

        if getattr(settings, "MPESA_USE_STUB", True):
            response = {
                "ConversationID": f"AG_{uuid.uuid4().hex[:20]}",
                "OriginatorConversationID": f"OC_{uuid.uuid4().hex[:20]}",
                "ResponseCode": "0",
                "ResponseDescription": "Accepted for processing",
            }
        else:
            client = MpesaClient()
            response = client.send_b2c_payment(
                phone_number=intent.phone,
                amount=str(intent.amount),
                command_id="BusinessPayment",
                remarks=(intent.metadata or {}).get("reason", "Chama payout")[:100],
                occasion=str(intent.reference_id or "")[:100],
            )

        originator_id = str(response.get("OriginatorConversationID") or "").strip()
        if not originator_id:
            originator_id = f"OC_{uuid.uuid4().hex[:20]}"

        payout = MpesaB2CPayout.objects.create(
            chama=intent.chama,
            intent=intent,
            phone=intent.phone,
            amount=intent.amount,
            command_id="BusinessPayment",
            remarks=(intent.metadata or {}).get("reason", "Chama payout")[:150],
            occasion=str(intent.reference_id or "")[:120],
            originator_conversation_id=originator_id,
            conversation_id=str(response.get("ConversationID") or "")[:120],
            response_code=str(response.get("ResponseCode") or "")[:20],
            response_description=str(response.get("ResponseDescription") or "")[:1000],
            status=MpesaB2CStatus.PENDING,
            created_by=actor,
            updated_by=actor,
        )

        intent.status = PaymentIntentStatus.PENDING
        metadata = dict(intent.metadata or {})
        metadata.update(
            {
                "originator_conversation_id": originator_id,
                "sent_by": str(actor.id),
                "sent_at": timezone.now().isoformat(),
                "payout_proof": str(payout_proof or "")[:500],
            }
        )
        intent.metadata = metadata
        intent.updated_by = actor
        intent.save(update_fields=["status", "metadata", "updated_by", "updated_at"])

        PaymentWorkflowService._activity(
            intent,
            PaymentActivityEvent.B2C_REQUESTED,
            actor=actor,
            meta={
                "originator_conversation_id": originator_id,
                "conversation_id": payout.conversation_id,
            },
        )

        recipient_id = (intent.metadata or {}).get("member_id")
        if recipient_id:
            try:
                recipient = intent.chama.memberships.select_related("user").get(
                    user_id=recipient_id,
                    is_active=True,
                    is_approved=True,
                    status=MemberStatus.ACTIVE,
                ).user
            except Exception:  # noqa: BLE001
                recipient = None
            if recipient:
                try:
                    from apps.notifications.models import NotificationType

                    PaymentWorkflowService._notify(
                        user=recipient,
                        chama=intent.chama,
                        message=(
                            f"Payout of KES {intent.amount} has been sent to "
                            f"{PaymentWorkflowService._mask_phone(intent.phone)}."
                        ),
                        subject="M-Pesa payout initiated",
                        notification_type=NotificationType.PAYMENT_CONFIRMATION,
                        idempotency_key=f"payment:b2c:init:{intent.id}",
                    )
                except Exception:  # noqa: BLE001
                    pass

        return payout

    @staticmethod
    def request_refund(payload: dict, actor) -> PaymentRefund:
        chama = get_object_or_404(Chama, id=payload["chama_id"])
        membership = PaymentWorkflowService._require_active_member(actor, chama)
        effective_role = get_effective_role(actor, chama.id, membership) or membership.role
        intent = get_object_or_404(
            PaymentIntent.objects.select_related("created_by"),
            id=payload["intent_id"],
            chama=chama,
        )
        if intent.status != PaymentIntentStatus.SUCCESS:
            raise PaymentWorkflowError("Only successful payment intents can be refunded.")

        metadata_member_id = str((intent.metadata or {}).get("member_id") or "")
        own_intent = (
            intent.created_by_id == actor.id
            or metadata_member_id == str(actor.id)
            or actor.is_superuser
        )
        if effective_role == MembershipRole.MEMBER and not own_intent:
            raise PaymentWorkflowError("Members can only request refunds for their own payments.")
        if effective_role == MembershipRole.AUDITOR:
            raise PaymentWorkflowError("Auditor role is read-only.")

        amount = payload.get("amount") or intent.amount
        amount = PaymentWorkflowService._to_decimal(amount)

        already_refunded = PaymentRefund.objects.filter(
            payment_intent=intent,
            status__in=[
                PaymentRefundStatus.REQUESTED,
                PaymentRefundStatus.APPROVED,
                PaymentRefundStatus.PROCESSED,
            ],
        ).aggregate(
            total=Coalesce(
                Sum("amount"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        already_refunded = Decimal(already_refunded or Decimal("0.00"))
        if already_refunded + amount > Decimal(intent.amount):
            raise PaymentWorkflowError("Refund amount exceeds available refundable balance.")

        if intent.purpose == PaymentPurpose.SPLIT_ALLOCATION and amount != Decimal(
            intent.amount
        ):
            raise PaymentWorkflowError(
                "Partial refund is not supported for split allocation payments."
            )

        idempotency_key = str(payload.get("idempotency_key") or "").strip() or (
            f"refund:{intent.id}:{timezone.now().timestamp()}"
        )

        refund, _created = PaymentRefund.objects.get_or_create(
            chama=chama,
            idempotency_key=idempotency_key,
            defaults={
                "payment_intent": intent,
                "amount": amount,
                "reason": str(payload.get("reason", "")).strip() or "Refund requested",
                "status": PaymentRefundStatus.REQUESTED,
                "requested_by": actor,
                "created_by": actor,
                "updated_by": actor,
            },
        )
        PaymentWorkflowService._activity(
            intent,
            PaymentActivityEvent.REFUND_REQUESTED,
            actor=actor,
            meta={"refund_id": str(refund.id), "amount": str(refund.amount)},
        )
        return refund

    @staticmethod
    def approve_refund(*, refund_id, actor, approve: bool = True, note: str = "") -> PaymentRefund:
        refund = get_object_or_404(
            PaymentRefund.objects.select_related("chama", "payment_intent", "requested_by"),
            id=refund_id,
        )
        membership = PaymentWorkflowService._require_treasurer_or_admin(actor, refund.chama)
        effective_role = (
            get_effective_role(actor, refund.chama_id, membership) or membership.role
        )
        if effective_role != MembershipRole.CHAMA_ADMIN:
            raise PaymentWorkflowError("Only chama admin can approve or reject refunds.")
        if refund.status not in {PaymentRefundStatus.REQUESTED, PaymentRefundStatus.APPROVED}:
            raise PaymentWorkflowError("Refund is not pending approval.")
        if refund.requested_by_id == actor.id:
            raise PaymentWorkflowError(
                "Maker-checker: requester cannot approve the same refund."
            )

        if approve:
            refund.status = PaymentRefundStatus.APPROVED
            refund.approved_by = actor
            event = PaymentActivityEvent.REFUND_APPROVED
        else:
            refund.status = PaymentRefundStatus.REJECTED
            event = PaymentActivityEvent.REFUND_REJECTED
        refund.notes = note
        refund.updated_by = actor
        refund.save(
            update_fields=[
                "status",
                "approved_by",
                "notes",
                "updated_by",
                "updated_at",
            ]
        )
        PaymentWorkflowService._activity(
            refund.payment_intent,
            event,
            actor=actor,
            meta={"refund_id": str(refund.id), "note": note},
        )
        return refund

    @staticmethod
    @transaction.atomic
    def process_refund(*, refund_id, actor) -> PaymentRefund:
        refund = get_object_or_404(
            PaymentRefund.objects.select_for_update().select_related(
                "chama",
                "payment_intent",
                "approved_by",
            ),
            id=refund_id,
        )
        membership = PaymentWorkflowService._require_treasurer_or_admin(actor, refund.chama)
        effective_role = (
            get_effective_role(actor, refund.chama_id, membership) or membership.role
        )
        if effective_role not in {MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER}:
            raise PaymentWorkflowError("Insufficient role to process refund.")
        if refund.status == PaymentRefundStatus.PROCESSED:
            return refund
        if refund.status != PaymentRefundStatus.APPROVED:
            raise PaymentWorkflowError("Only approved refunds can be processed.")
        if refund.approved_by_id == actor.id:
            raise PaymentWorkflowError(
                "Maker-checker: approver cannot process the same refund."
            )

        intent = refund.payment_intent
        metadata = dict(intent.metadata or {})
        reversal_entries = []

        if intent.purpose == PaymentPurpose.SPLIT_ALLOCATION:
            split_posted = dict(metadata.get("split_posted") or {})
            entry_ids = [
                split_posted.get("repayment_ledger_entry_id"),
                split_posted.get("contribution_ledger_entry_id"),
            ]
            entry_ids = [item for item in entry_ids if item]
            if not entry_ids:
                raise PaymentWorkflowError("No posted ledger entries found for split refund.")
            for idx, entry_id in enumerate(entry_ids):
                result = FinanceService.reverse_ledger_entry(
                    entry_id,
                    {
                        "idempotency_key": f"{refund.idempotency_key}:S{idx}",
                        "reason": f"Refund {refund.id}",
                    },
                    actor,
                )
                reversal_entries.append(str(result.ledger_entry.id))
        else:
            ledger_entry_id = metadata.get("ledger_entry_id")
            if not ledger_entry_id:
                raise PaymentWorkflowError("No ledger entry found for this payment intent.")
            result = FinanceService.reverse_ledger_entry(
                ledger_entry_id,
                {
                    "idempotency_key": refund.idempotency_key,
                    "reason": f"Refund {refund.id}",
                },
                actor,
            )
            reversal_entries.append(str(result.ledger_entry.id))

        refund.status = PaymentRefundStatus.PROCESSED
        refund.processed_by = actor
        refund.processed_at = timezone.now()
        refund.updated_by = actor
        if reversal_entries:
            refund.ledger_reversal_entry_id = reversal_entries[0]
        refund.save(
            update_fields=[
                "status",
                "processed_by",
                "processed_at",
                "updated_by",
                "updated_at",
                "ledger_reversal_entry",
            ]
        )

        PaymentWorkflowService._activity(
            intent,
            PaymentActivityEvent.REFUND_PROCESSED,
            actor=actor,
            meta={"refund_id": str(refund.id), "reversal_entries": reversal_entries},
        )
        return refund

    @staticmethod
    def open_dispute(payload: dict, actor) -> PaymentDispute:
        chama = get_object_or_404(Chama, id=payload["chama_id"])
        membership = PaymentWorkflowService._require_active_member(actor, chama)
        effective_role = get_effective_role(actor, chama.id, membership) or membership.role
        if effective_role == MembershipRole.AUDITOR:
            raise PaymentWorkflowError("Auditor role is read-only.")

        intent = None
        intent_id = payload.get("intent_id")
        if intent_id:
            intent = get_object_or_404(PaymentIntent, id=intent_id, chama=chama)
            metadata_member_id = str((intent.metadata or {}).get("member_id") or "")
            own_intent = (
                intent.created_by_id == actor.id
                or metadata_member_id == str(actor.id)
                or actor.is_superuser
            )
            if effective_role == MembershipRole.MEMBER and not own_intent:
                raise PaymentWorkflowError(
                    "Members can only open disputes on their own payments."
                )

        dispute = PaymentDispute.objects.create(
            chama=chama,
            payment_intent=intent,
            opened_by=actor,
            category=payload.get("category", PaymentDisputeCategory.OTHER),
            reason=payload["reason"],
            reference=str(payload.get("reference", "")).strip(),
            status=PaymentDisputeStatus.OPEN,
            created_by=actor,
            updated_by=actor,
        )

        if intent:
            PaymentWorkflowService._activity(
                intent,
                PaymentActivityEvent.DISPUTE_OPENED,
                actor=actor,
                meta={"dispute_id": str(dispute.id), "category": dispute.category},
            )
        return dispute

    @staticmethod
    def resolve_dispute(*, dispute_id, payload: dict, actor) -> PaymentDispute:
        dispute = get_object_or_404(
            PaymentDispute.objects.select_related("chama", "payment_intent"),
            id=dispute_id,
        )
        membership = PaymentWorkflowService._require_active_member(actor, dispute.chama)
        effective_role = (
            get_effective_role(actor, dispute.chama_id, membership) or membership.role
        )
        if effective_role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
            MembershipRole.SECRETARY,
        }:
            raise PaymentWorkflowError("Only secretary, treasurer, or admin can resolve disputes.")
        if effective_role == MembershipRole.AUDITOR:
            raise PaymentWorkflowError("Auditor role is read-only.")

        dispute.status = payload["status"]
        dispute.resolution_notes = payload.get("resolution_notes", "")
        if dispute.status in {PaymentDisputeStatus.RESOLVED, PaymentDisputeStatus.REJECTED}:
            dispute.resolved_by = actor
            dispute.resolved_at = timezone.now()
        else:
            dispute.resolved_by = None
            dispute.resolved_at = None
        dispute.updated_by = actor
        dispute.save(
            update_fields=[
                "status",
                "resolution_notes",
                "resolved_by",
                "resolved_at",
                "updated_by",
                "updated_at",
            ]
        )

        if dispute.payment_intent_id:
            PaymentWorkflowService._activity(
                dispute.payment_intent,
                PaymentActivityEvent.DISPUTE_RESOLVED,
                actor=actor,
                meta={
                    "dispute_id": str(dispute.id),
                    "status": dispute.status,
                },
            )
        return dispute

    @staticmethod
    def pending_loan_disbursements(*, chama_id):
        chama = get_object_or_404(Chama, id=chama_id)
        return PaymentIntent.objects.filter(
            chama=chama,
            intent_type=PaymentIntentType.LOAN_DISBURSEMENT,
            status__in=[PaymentIntentStatus.INITIATED, PaymentIntentStatus.PENDING],
        ).order_by("created_at")

    @staticmethod
    def _lookup_intent_by_account_reference(reference: str) -> PaymentIntent | None:
        ref = str(reference or "").strip()
        if not ref:
            return None
        return PaymentIntent.objects.filter(
            metadata__account_reference=ref,
        ).order_by("-created_at").first()

    @staticmethod
    @transaction.atomic
    def process_c2b_validation(payload: dict, *, source_ip: str | None, headers: dict):
        callback_log = CallbackLog.objects.create(
            callback_type=CallbackKind.C2B_VALIDATION,
            source_ip=source_ip,
            payload=payload,
            headers=headers,
        )

        bill_ref = str(payload.get("BillRefNumber") or payload.get("account_reference") or "").strip()
        amount = payload.get("TransAmount") or payload.get("amount")
        try:
            amount_dec = PaymentWorkflowService._to_decimal(amount)
        except PaymentWorkflowError:
            callback_log.processing_error = "Invalid amount"
            callback_log.save(update_fields=["processing_error", "updated_at"])
            return {"ResultCode": "C2B00011", "ResultDesc": "Rejected"}

        intent = PaymentWorkflowService._lookup_intent_by_account_reference(bill_ref)
        if not intent:
            callback_log.processing_error = "Unknown account reference"
            callback_log.save(update_fields=["processing_error", "updated_at"])
            return {"ResultCode": "C2B00011", "ResultDesc": "Rejected"}

        if amount_dec <= Decimal("0.00"):
            callback_log.processing_error = "Non-positive amount"
            callback_log.save(update_fields=["processing_error", "updated_at"])
            return {"ResultCode": "C2B00011", "ResultDesc": "Rejected"}

        if intent.chama.status != "active":
            callback_log.processing_error = "Chama inactive"
            callback_log.save(update_fields=["processing_error", "updated_at"])
            return {"ResultCode": "C2B00011", "ResultDesc": "Rejected"}

        PaymentWorkflowService._activity(
            intent,
            PaymentActivityEvent.C2B_VALIDATED,
            actor=None,
            meta={"bill_ref": bill_ref, "amount": str(amount_dec)},
        )
        return {"ResultCode": 0, "ResultDesc": "Accepted"}

    @staticmethod
    @transaction.atomic
    def process_c2b_confirmation(payload: dict, *, source_ip: str | None, headers: dict):
        callback_log = CallbackLog.objects.create(
            callback_type=CallbackKind.C2B_CONFIRMATION,
            source_ip=source_ip,
            payload=payload,
            headers=headers,
        )

        trans_id = str(payload.get("TransID") or payload.get("trans_id") or "").strip()
        if not trans_id:
            callback_log.processing_error = "Missing trans_id"
            callback_log.save(update_fields=["processing_error", "updated_at"])
            return {"ResultCode": 0, "ResultDesc": "Accepted"}

        amount = PaymentWorkflowService._to_decimal(
            payload.get("TransAmount") or payload.get("amount")
        )
        phone = PaymentWorkflowService._safe_phone(
            payload.get("MSISDN") or payload.get("phone"),
            "+254700000000",
        )
        bill_ref = str(payload.get("BillRefNumber") or payload.get("account_reference") or "").strip()

        existing = MpesaC2BTransaction.objects.filter(trans_id=trans_id).first()
        if existing:
            existing.processing_status = MpesaC2BProcessingStatus.DUPLICATE
            existing.processed_at = timezone.now()
            existing.save(update_fields=["processing_status", "processed_at", "updated_at"])
            return {"ResultCode": 0, "ResultDesc": "Accepted"}

        intent = PaymentWorkflowService._lookup_intent_by_account_reference(bill_ref)
        if not intent:
            callback_log.processing_error = "Unknown account reference"
            callback_log.save(update_fields=["processing_error", "updated_at"])
            return {"ResultCode": 0, "ResultDesc": "Accepted"}
        if amount != intent.amount:
            callback_log.processing_error = "Amount mismatch"
            callback_log.save(update_fields=["processing_error", "updated_at"])
            PaymentWorkflowService._mark_failed(
                intent,
                reason=f"Provider amount mismatch. Expected {intent.amount}, received {amount}.",
            )
            PaymentWorkflowService._sync_transaction_record(
                intent=intent,
                reference=trans_id,
                amount=amount,
                status_value=PaymentIntentStatus.FAILED,
                provider_response=payload,
            )
            return {"ResultCode": 0, "ResultDesc": "Accepted"}

        tx = MpesaC2BTransaction.objects.create(
            chama=intent.chama,
            intent=intent,
            phone=phone,
            amount=amount,
            currency=CurrencyChoices.KES,
            transaction_type=MpesaTransactionType.PAYBILL,
            trans_id=trans_id,
            bill_ref_number=bill_ref,
            account_reference=bill_ref,
            first_name=str(payload.get("FirstName") or payload.get("first_name") or "")[:60],
            middle_name=str(payload.get("MiddleName") or payload.get("middle_name") or "")[:60],
            last_name=str(payload.get("LastName") or payload.get("last_name") or "")[:60],
            trans_time=PaymentWorkflowService._parse_c2b_time(
                payload.get("TransTime") or payload.get("trans_time")
            ),
            raw_payload=payload,
            processing_status=MpesaC2BProcessingStatus.RECEIVED,
        )

        PaymentWorkflowService._activity(
            intent,
            PaymentActivityEvent.C2B_CONFIRMED,
            actor=None,
            meta={"trans_id": trans_id, "amount": str(amount)},
        )

        charge_raw = (
            payload.get("TransactionFee")
            or payload.get("transaction_fee")
            or payload.get("Charge")
            or payload.get("charge")
        )
        if charge_raw not in (None, ""):
            try:
                charge_amount = Decimal(str(charge_raw))
                if charge_amount > Decimal("0.00"):
                    intent_metadata = dict(intent.metadata or {})
                    intent_metadata["mpesa_charge_amount"] = str(
                        charge_amount.quantize(Decimal("0.01"))
                    )
                    intent.metadata = intent_metadata
                    intent.save(update_fields=["metadata", "updated_at"])
            except Exception:  # noqa: BLE001
                pass

        if intent.status == PaymentIntentStatus.SUCCESS:
            tx.processing_status = MpesaC2BProcessingStatus.DUPLICATE
            tx.processed_at = timezone.now()
            tx.save(update_fields=["processing_status", "processed_at", "updated_at"])
            return {"ResultCode": 0, "ResultDesc": "Accepted"}

        try:
            PaymentWorkflowService._post_intent_success(
                intent=intent,
                external_reference=trans_id,
            )
            intent.status = PaymentIntentStatus.SUCCESS
            intent.mpesa_receipt_number = trans_id
            intent.raw_response = payload
            intent.failure_reason = ""
            intent.save(
                update_fields=[
                    "status",
                    "mpesa_receipt_number",
                    "raw_response",
                    "failure_reason",
                    "updated_at",
                ]
            )
            PaymentWorkflowService._sync_transaction_record(
                intent=intent,
                reference=trans_id,
                amount=amount,
                status_value=PaymentIntentStatus.SUCCESS,
                provider_response=payload,
            )
            tx.processing_status = MpesaC2BProcessingStatus.POSTED
            tx.processed_at = timezone.now()
            tx.save(update_fields=["processing_status", "processed_at", "updated_at"])
        except PaymentWorkflowError as exc:
            tx.processing_status = MpesaC2BProcessingStatus.REJECTED
            tx.processed_at = timezone.now()
            tx.save(update_fields=["processing_status", "processed_at", "updated_at"])
            PaymentWorkflowService._mark_failed(intent, reason=str(exc))
            PaymentWorkflowService._sync_transaction_record(
                intent=intent,
                reference=trans_id,
                amount=amount,
                status_value=PaymentIntentStatus.FAILED,
                provider_response=payload,
            )

        return {"ResultCode": 0, "ResultDesc": "Accepted"}

    @staticmethod
    @transaction.atomic
    def process_stk_callback(payload: dict, *, source_ip: str | None, headers: dict):
        callback_log = CallbackLog.objects.create(
            callback_type=CallbackKind.STK,
            source_ip=source_ip,
            payload=payload,
            headers=headers,
        )

        body = payload.get("Body", {}) if isinstance(payload, dict) else {}
        stk_callback = body.get("stkCallback", {})
        checkout_request_id = str(stk_callback.get("CheckoutRequestID") or "").strip()
        merchant_request_id = str(stk_callback.get("MerchantRequestID") or "").strip()
        result_code = int(stk_callback.get("ResultCode") or 0)
        result_desc = str(stk_callback.get("ResultDesc") or "")

        if not checkout_request_id:
            callback_log.processing_error = "Missing CheckoutRequestID"
            callback_log.save(update_fields=["processing_error", "updated_at"])
            return {"ResultCode": 0, "ResultDesc": "Accepted"}

        stk_tx = (
            MpesaSTKTransaction.objects.select_for_update()
            .select_related("intent", "intent__chama")
            .filter(checkout_request_id=checkout_request_id)
            .first()
        )
        if not stk_tx:
            callback_log.processing_error = "STK transaction not found"
            callback_log.save(update_fields=["processing_error", "updated_at"])
            return {"ResultCode": 0, "ResultDesc": "Accepted"}

        intent = stk_tx.intent
        PaymentWorkflowService._activity(
            intent,
            PaymentActivityEvent.CALLBACK_RECEIVED,
            actor=None,
            meta={
                "checkout_request_id": checkout_request_id,
                "result_code": result_code,
                "result_desc": result_desc,
            },
        )

        stk_tx.merchant_request_id = merchant_request_id or stk_tx.merchant_request_id
        stk_tx.result_code = result_code
        stk_tx.result_desc = result_desc
        stk_tx.raw_callback = payload
        receipt_number = PaymentWorkflowService._extract_stk_receipt(stk_callback)
        callback_amount = PaymentWorkflowService._extract_stk_paid_amount(stk_callback)
        charge_amount = PaymentWorkflowService._extract_stk_charge_amount(stk_callback)
        if charge_amount > Decimal("0.00"):
            intent_metadata = dict(intent.metadata or {})
            intent_metadata["mpesa_charge_amount"] = str(charge_amount)
            intent.metadata = intent_metadata
            intent.save(update_fields=["metadata", "updated_at"])

        if result_code == 0:
            if callback_amount is not None and callback_amount != intent.amount:
                stk_tx.status = PaymentIntentStatus.FAILED
                stk_tx.result_desc = (
                    f"Provider amount mismatch. Expected {intent.amount}, received {callback_amount}."
                )
                stk_tx.processed_at = timezone.now()
                PaymentWorkflowService._mark_failed(intent, reason=stk_tx.result_desc)
                stk_tx.save(
                    update_fields=[
                        "merchant_request_id",
                        "result_code",
                        "result_desc",
                        "raw_callback",
                        "status",
                        "processed_at",
                        "updated_at",
                    ]
                )
                PaymentWorkflowService._sync_transaction_record(
                    intent=intent,
                    reference=checkout_request_id,
                    amount=intent.amount,
                    status_value=PaymentIntentStatus.FAILED,
                    provider_response=payload,
                )
                return {"ResultCode": 0, "ResultDesc": "Accepted"}
            if receipt_number:
                duplicate = MpesaSTKTransaction.objects.filter(
                    chama=stk_tx.chama,
                    mpesa_receipt_number=receipt_number,
                    processed_at__isnull=False,
                ).exclude(id=stk_tx.id)
                if duplicate.exists() or intent.status == PaymentIntentStatus.SUCCESS:
                    stk_tx.status = PaymentIntentStatus.SUCCESS
                    stk_tx.mpesa_receipt_number = receipt_number
                    stk_tx.processed_at = timezone.now()
                    intent.status = PaymentIntentStatus.SUCCESS
                    intent.mpesa_receipt_number = receipt_number
                    intent.failure_reason = ""
                    intent.raw_response = payload
                    intent.save(
                        update_fields=[
                            "status",
                            "mpesa_receipt_number",
                            "failure_reason",
                            "raw_response",
                            "updated_at",
                        ]
                    )
                    PaymentWorkflowService._sync_transaction_record(
                        intent=intent,
                        reference=receipt_number or checkout_request_id,
                        amount=intent.amount,
                        status_value=PaymentIntentStatus.SUCCESS,
                        provider_response=payload,
                    )
                    stk_tx.save(
                        update_fields=[
                            "merchant_request_id",
                            "result_code",
                            "result_desc",
                            "raw_callback",
                            "status",
                            "mpesa_receipt_number",
                            "processed_at",
                            "updated_at",
                        ]
                    )
                    return {"ResultCode": 0, "ResultDesc": "Accepted"}

                try:
                    PaymentWorkflowService._post_intent_success(
                        intent=intent,
                        external_reference=receipt_number,
                    )
                    stk_tx.status = PaymentIntentStatus.SUCCESS
                    stk_tx.mpesa_receipt_number = receipt_number
                    stk_tx.transaction_date = timezone.now()
                    stk_tx.processed_at = timezone.now()
                    intent.status = PaymentIntentStatus.SUCCESS
                    intent.mpesa_receipt_number = receipt_number
                    intent.failure_reason = ""
                    intent.raw_response = payload
                    intent.save(
                        update_fields=[
                            "status",
                            "mpesa_receipt_number",
                            "failure_reason",
                            "raw_response",
                            "updated_at",
                        ]
                    )
                    PaymentWorkflowService._sync_transaction_record(
                        intent=intent,
                        reference=receipt_number,
                        amount=intent.amount,
                        status_value=PaymentIntentStatus.SUCCESS,
                        provider_response=payload,
                    )
                except PaymentWorkflowError as exc:
                    stk_tx.status = PaymentIntentStatus.FAILED
                    stk_tx.result_desc = str(exc)
                    stk_tx.processed_at = timezone.now()
                    PaymentWorkflowService._mark_failed(intent, reason=str(exc))
            else:
                stk_tx.status = PaymentIntentStatus.FAILED
                stk_tx.result_desc = "Missing M-Pesa receipt"
                stk_tx.processed_at = timezone.now()
                PaymentWorkflowService._mark_failed(intent, reason="Missing M-Pesa receipt")
        else:
            stk_tx.status = PaymentIntentStatus.FAILED
            stk_tx.processed_at = timezone.now()
            PaymentWorkflowService._mark_failed(intent, reason=result_desc or "Payment failed")
            intent.failure_reason = result_desc or "Payment failed"
            intent.raw_response = payload
            intent.save(update_fields=["failure_reason", "raw_response", "updated_at"])
            PaymentWorkflowService._sync_transaction_record(
                intent=intent,
                reference=checkout_request_id,
                amount=intent.amount,
                status_value=PaymentIntentStatus.FAILED,
                provider_response=payload,
            )

        stk_tx.save(
            update_fields=[
                "merchant_request_id",
                "result_code",
                "result_desc",
                "raw_callback",
                "status",
                "mpesa_receipt_number",
                "transaction_date",
                "processed_at",
                "updated_at",
            ]
        )

        return {"ResultCode": 0, "ResultDesc": "Accepted"}

    @staticmethod
    def _extract_b2c_result(payload: dict) -> dict:
        result = payload.get("Result", payload)
        params = {}
        raw_params = result.get("ResultParameters", {}).get("ResultParameter", [])
        if isinstance(raw_params, list):
            for item in raw_params:
                key = item.get("Key")
                if not key:
                    continue
                params[key] = item.get("Value")

        raw_result_code = result.get("ResultCode")
        result_code = "" if raw_result_code is None else str(raw_result_code)

        return {
            "originator_conversation_id": str(
                result.get("OriginatorConversationID")
                or payload.get("OriginatorConversationID")
                or ""
            ).strip(),
            "conversation_id": str(result.get("ConversationID") or "").strip(),
            "result_code": result_code,
            "result_desc": str(result.get("ResultDesc") or ""),
            "transaction_id": str(
                params.get("TransactionID")
                or result.get("TransactionID")
                or ""
            ).strip(),
            "receipt_number": str(
                params.get("TransactionReceipt")
                or params.get("ReceiverPartyPublicName")
                or ""
            ).strip(),
            "raw_result": result,
        }

    @staticmethod
    @transaction.atomic
    def process_b2c_result(payload: dict, *, source_ip: str | None, headers: dict):
        callback_log = CallbackLog.objects.create(
            callback_type=CallbackKind.B2C_RESULT,
            source_ip=source_ip,
            payload=payload,
            headers=headers,
        )

        parsed = PaymentWorkflowService._extract_b2c_result(payload)
        originator_id = parsed["originator_conversation_id"]
        if not originator_id:
            callback_log.processing_error = "Missing originator conversation id"
            callback_log.save(update_fields=["processing_error", "updated_at"])
            return {"ResultCode": 0, "ResultDesc": "Accepted"}

        payout = (
            MpesaB2CPayout.objects.select_for_update()
            .select_related("intent", "intent__chama")
            .filter(originator_conversation_id=originator_id)
            .first()
        )
        if not payout:
            callback_log.processing_error = "B2C payout not found"
            callback_log.save(update_fields=["processing_error", "updated_at"])
            return {"ResultCode": 0, "ResultDesc": "Accepted"}

        intent = payout.intent
        PaymentWorkflowService._activity(
            intent,
            PaymentActivityEvent.CALLBACK_RECEIVED,
            actor=None,
            meta={
                "callback": "b2c_result",
                "originator_conversation_id": originator_id,
                "result_code": parsed["result_code"],
            },
        )

        if payout.processed_at and payout.status == MpesaB2CStatus.SUCCESS:
            return {"ResultCode": 0, "ResultDesc": "Accepted"}

        payout.conversation_id = parsed["conversation_id"] or payout.conversation_id
        payout.result_code = parsed["result_code"]
        payout.result_desc = parsed["result_desc"]
        payout.transaction_id = parsed["transaction_id"]
        payout.receipt_number = parsed["receipt_number"]
        payout.raw_result = parsed["raw_result"]

        if parsed["result_code"] == "0":
            reference = parsed["transaction_id"] or payout.receipt_number or originator_id
            actor = None
            sent_by = (intent.metadata or {}).get("sent_by")
            if sent_by:
                actor = Membership.objects.filter(
                    chama=intent.chama,
                    user_id=sent_by,
                    is_active=True,
                    is_approved=True,
                    status=MemberStatus.ACTIVE,
                ).select_related("user").first()
                actor = actor.user if actor else None

            try:
                PaymentWorkflowService._post_intent_success(
                    intent=intent,
                    external_reference=reference,
                    actor=actor,
                )
                payout.status = MpesaB2CStatus.SUCCESS
                payout.processed_at = timezone.now()
            except PaymentWorkflowError as exc:
                payout.status = MpesaB2CStatus.FAILED
                payout.processed_at = timezone.now()
                PaymentWorkflowService._mark_failed(intent, reason=str(exc))
        else:
            payout.status = MpesaB2CStatus.FAILED
            payout.processed_at = timezone.now()
            PaymentWorkflowService._mark_failed(
                intent,
                reason=parsed["result_desc"] or "B2C payout failed",
            )
            if intent.intent_type == PaymentIntentType.LOAN_DISBURSEMENT:
                Loan.objects.filter(
                    id=intent.reference_id,
                    status=LoanStatus.DISBURSING,
                ).update(status=LoanStatus.APPROVED, updated_at=timezone.now())

        payout.save(
            update_fields=[
                "conversation_id",
                "result_code",
                "result_desc",
                "transaction_id",
                "receipt_number",
                "raw_result",
                "status",
                "processed_at",
                "updated_at",
            ]
        )

        if payout.status == MpesaB2CStatus.SUCCESS:
            try:
                from apps.notifications.models import (
                    NotificationCategory,
                    NotificationPriority,
                    NotificationTarget,
                    NotificationType,
                )
                from apps.notifications.services import NotificationService

                leadership = Membership.objects.select_related("user").filter(
                    chama=intent.chama,
                    is_active=True,
                    is_approved=True,
                    status=MemberStatus.ACTIVE,
                    role__in=[MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER],
                )
                recipient_phone = PaymentWorkflowService._mask_phone(intent.phone)
                payout_label = (
                    "loan disbursement"
                    if intent.intent_type == PaymentIntentType.LOAN_DISBURSEMENT
                    else "payout"
                )
                for membership in leadership:
                    NotificationService.send_notification(
                        user=membership.user,
                        chama=intent.chama,
                        channels=["in_app", "push"],
                        message=(
                            f"{payout_label.title()} completed for KES {intent.amount}. "
                            f"Recipient: {recipient_phone}. Ref: {reference}."
                        ),
                        subject="Payout completed",
                        notification_type=NotificationType.PAYMENT_CONFIRMATION,
                        priority=NotificationPriority.HIGH,
                        idempotency_key=(
                            f"payment:b2c:leadership-success:{intent.id}:{membership.user_id}"
                        ),
                    )

                NotificationService.publish_event(
                    chama=intent.chama,
                    event_key=f"payment:b2c:group-success:{intent.id}",
                    event_type=NotificationType.PAYMENT_CONFIRMATION,
                    target=NotificationTarget.CHAMA,
                    channels=["in_app"],
                    subject="Payout completed",
                    message=(
                        f"A chama payout of KES {intent.amount} has completed successfully."
                    ),
                    category=NotificationCategory.PAYMENTS,
                    priority=NotificationPriority.NORMAL,
                    payload={
                        "payment_id": str(intent.id),
                        "chama_id": str(intent.chama_id),
                    },
                    enforce_once_daily=False,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed payout completion notifications for intent=%s",
                    intent.id,
                )

        return {"ResultCode": 0, "ResultDesc": "Accepted"}

    @staticmethod
    @transaction.atomic
    def process_b2c_timeout(payload: dict, *, source_ip: str | None, headers: dict):
        callback_log = CallbackLog.objects.create(
            callback_type=CallbackKind.B2C_TIMEOUT,
            source_ip=source_ip,
            payload=payload,
            headers=headers,
        )

        originator_id = str(
            payload.get("OriginatorConversationID")
            or payload.get("originator_conversation_id")
            or ""
        ).strip()
        if not originator_id:
            callback_log.processing_error = "Missing originator conversation id"
            callback_log.save(update_fields=["processing_error", "updated_at"])
            return {"ResultCode": 0, "ResultDesc": "Accepted"}

        payout = (
            MpesaB2CPayout.objects.select_for_update()
            .select_related("intent", "intent__chama")
            .filter(originator_conversation_id=originator_id)
            .first()
        )
        if not payout:
            callback_log.processing_error = "B2C payout not found"
            callback_log.save(update_fields=["processing_error", "updated_at"])
            return {"ResultCode": 0, "ResultDesc": "Accepted"}

        if payout.status == MpesaB2CStatus.SUCCESS:
            return {"ResultCode": 0, "ResultDesc": "Accepted"}

        payout.status = MpesaB2CStatus.TIMEOUT
        payout.raw_result = payload
        payout.processed_at = timezone.now()
        payout.save(update_fields=["status", "raw_result", "processed_at", "updated_at"])

        intent = payout.intent
        PaymentWorkflowService._mark_failed(intent, reason="B2C timeout")
        if intent.intent_type == PaymentIntentType.LOAN_DISBURSEMENT:
            Loan.objects.filter(
                id=intent.reference_id,
                status=LoanStatus.DISBURSING,
            ).update(status=LoanStatus.APPROVED, updated_at=timezone.now())

        return {"ResultCode": 0, "ResultDesc": "Accepted"}

    @staticmethod
    def admin_transactions(
        *,
        chama_id,
        status: str | None = None,
        intent_type: str | None = None,
        purpose: str | None = None,
        phone: str | None = None,
        receipt: str | None = None,
        search: str | None = None,
        member_id=None,
        loan_id=None,
        from_date=None,
        to_date=None,
    ):
        queryset = PaymentIntent.objects.filter(chama_id=chama_id).order_by("-created_at")

        if status:
            queryset = queryset.filter(status=status)
        if intent_type:
            queryset = queryset.filter(intent_type=intent_type)
        if purpose:
            queryset = queryset.filter(purpose=purpose)
        if phone:
            queryset = queryset.filter(phone__icontains=phone[-4:])
        if member_id:
            queryset = queryset.filter(
                Q(created_by_id=member_id) | Q(metadata__member_id=str(member_id))
            )
        if loan_id:
            queryset = queryset.filter(
                Q(reference_type="LOAN", reference_id=loan_id)
                | Q(metadata__loan_id=str(loan_id))
            )
        if from_date:
            queryset = queryset.filter(created_at__date__gte=from_date)
        if to_date:
            queryset = queryset.filter(created_at__date__lte=to_date)
        if receipt:
            queryset = queryset.filter(
                Q(metadata__external_reference__icontains=receipt)
                | Q(stk_transactions__mpesa_receipt_number__icontains=receipt)
                | Q(c2b_transactions__trans_id__icontains=receipt)
                | Q(b2c_payouts__transaction_id__icontains=receipt)
            )
        if search:
            queryset = queryset.filter(
                Q(idempotency_key__icontains=search)
                | Q(metadata__external_reference__icontains=search)
                | Q(stk_transactions__checkout_request_id__icontains=search)
                | Q(b2c_payouts__originator_conversation_id__icontains=search)
            )

        return queryset.distinct()

    @staticmethod
    def get_payment_status(*, intent_id, actor) -> PaymentIntent:
        intent = get_object_or_404(
            PaymentIntent.objects.select_related("chama", "user", "created_by"),
            id=intent_id,
        )
        membership = get_membership(actor, intent.chama_id)
        effective_role = (
            get_effective_role(actor, intent.chama_id, membership) if membership else None
        )
        owns_intent = actor.is_superuser or intent.user_id == actor.id or intent.created_by_id == actor.id
        if not owns_intent and effective_role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
            MembershipRole.AUDITOR,
        }:
            raise PaymentWorkflowError("Not allowed to view this payment status.")
        return intent

    @staticmethod
    def reconcile_payment_intent(*, intent_id=None, chama_id=None, actor=None) -> dict:
        if intent_id:
            intent = get_object_or_404(PaymentIntent, id=intent_id)
            run = PaymentWorkflowService.run_reconciliation(chama_id=intent.chama_id, actor=actor)
            return {
                "run_id": str(run.id),
                "status": run.status,
                "payment_intent_id": str(intent.id),
                "payment_status": intent.status,
                "anomalies": run.anomalies,
            }
        run = PaymentWorkflowService.run_reconciliation(chama_id=chama_id, actor=actor)
        return {
            "run_id": str(run.id),
            "status": run.status,
            "totals": run.totals,
            "anomalies": run.anomalies,
        }

    @staticmethod
    def run_reconciliation(*, chama_id=None, actor=None) -> PaymentReconciliationRun:
        intents = PaymentIntent.objects.all()
        if chama_id:
            intents = intents.filter(chama_id=chama_id)

        success_intents = intents.filter(status=PaymentIntentStatus.SUCCESS)
        missing_ledger = []
        for intent in success_intents:
            metadata = intent.metadata or {}
            ledger_id = metadata.get("ledger_entry_id")
            if not ledger_id:
                missing_ledger.append(
                    {
                        "intent_id": str(intent.id),
                        "intent_type": intent.intent_type,
                        "amount": str(intent.amount),
                    }
                )
                continue
            if not LedgerEntry.objects.filter(id=ledger_id, chama=intent.chama).exists():
                missing_ledger.append(
                    {
                        "intent_id": str(intent.id),
                        "ledger_entry_id": str(ledger_id),
                        "reason": "ledger_missing",
                    }
                )

        orphan_ledger = list(
            LedgerEntry.objects.filter(idempotency_key__startswith="payment:")
            .exclude(
                id__in=[
                    (item.metadata or {}).get("ledger_entry_id")
                    for item in success_intents
                    if (item.metadata or {}).get("ledger_entry_id")
                ]
            )
            .values("id", "chama_id", "idempotency_key")[:200]
        )

        totals = {
            "intents_total": intents.count(),
            "intents_success": success_intents.count(),
            "intents_pending": intents.filter(status=PaymentIntentStatus.PENDING).count(),
            "c2b_total": MpesaC2BTransaction.objects.filter(intent__in=intents).count(),
            "stk_total": MpesaSTKTransaction.objects.filter(intent__in=intents).count(),
            "b2c_total": MpesaB2CPayout.objects.filter(intent__in=intents).count(),
        }
        anomalies = {
            "missing_ledger_for_success_intents": missing_ledger,
            "ledger_without_payment_intent": orphan_ledger,
        }

        status = ReconciliationRunStatus.SUCCESS
        if missing_ledger or orphan_ledger:
            status = ReconciliationRunStatus.PARTIAL

        run = PaymentReconciliationRun.objects.create(
            chama_id=chama_id,
            status=status,
            totals=totals,
            anomalies=anomalies,
            created_by=actor,
            updated_by=actor,
        )
        return run

    @staticmethod
    @transaction.atomic
    def expire_pending_stk(*, now=None) -> int:
        now = now or timezone.now()
        intents = PaymentIntent.objects.select_for_update().filter(
            status=PaymentIntentStatus.PENDING,
            intent_type__in=[PaymentIntentType.DEPOSIT, PaymentIntentType.LOAN_REPAYMENT],
            expires_at__isnull=False,
            expires_at__lt=now,
        )
        count = 0
        for intent in intents:
            latest_stk = intent.stk_transactions.order_by("-created_at").first()
            if latest_stk and latest_stk.status == PaymentIntentStatus.SUCCESS:
                continue
            intent.status = PaymentIntentStatus.EXPIRED
            intent.updated_at = now
            intent.save(update_fields=["status", "updated_at"])
            if latest_stk and latest_stk.status in {
                PaymentIntentStatus.INITIATED,
                PaymentIntentStatus.PENDING,
            }:
                latest_stk.status = PaymentIntentStatus.EXPIRED
                latest_stk.processed_at = now
                latest_stk.save(update_fields=["status", "processed_at", "updated_at"])

            PaymentWorkflowService._activity(
                intent,
                PaymentActivityEvent.FAILED,
                actor=None,
                meta={"reason": "expired"},
            )
            count += 1
        return count

    @staticmethod
    def escalate_pending_disbursements(*, now=None) -> int:
        now = now or timezone.now()
        threshold = now - timedelta(hours=PaymentWorkflowService.DISBURSEMENT_ESCALATION_HOURS)
        intents = PaymentIntent.objects.filter(
            intent_type=PaymentIntentType.LOAN_DISBURSEMENT,
            status__in=[PaymentIntentStatus.INITIATED, PaymentIntentStatus.PENDING],
            created_at__lt=threshold,
        ).select_related("chama")

        notified = 0
        try:
            from apps.notifications.models import NotificationType
        except Exception:  # noqa: BLE001
            return 0

        for intent in intents:
            admin_memberships = Membership.objects.select_related("user").filter(
                chama=intent.chama,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
                role__in=[MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER],
            )
            for membership in admin_memberships:
                PaymentWorkflowService._notify(
                    user=membership.user,
                    chama=intent.chama,
                    message=(
                        "Loan disbursement pending for over 24 hours. "
                        f"Intent {intent.id}."
                    ),
                    subject="Pending loan disbursement",
                    notification_type=NotificationType.LOAN_UPDATE,
                    idempotency_key=(
                        f"payment:disbursement:escalation:{intent.id}:{membership.user_id}:{timezone.localdate()}"
                    ),
                )
                notified += 1
        return notified
