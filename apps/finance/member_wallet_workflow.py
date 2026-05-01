from __future__ import annotations

import uuid
from datetime import datetime, time
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.chama.models import Chama, MemberStatus, Membership, PaymentProviderConfig
from apps.finance.member_contribution_workflow import (
    build_member_contribution_workspace,
)
from apps.finance.member_loan_workflow import build_member_loan_workspace
from apps.finance.models import (
    Contribution,
    InstallmentSchedule,
    InstallmentStatus,
    LedgerDirection,
    LedgerEntry,
    LedgerEntryType,
    LedgerStatus,
    Loan,
    LoanStatus,
    Penalty,
    PenaltyStatus,
    Wallet,
    WalletOwnerType,
)
from apps.finance.services import FinanceService
from apps.payments.unified_models import (
    BankPaymentDetails,
    PaymentAuditLog,
    PaymentIntent,
    PaymentMethod,
    PaymentPurpose,
    PaymentReceipt,
    PaymentStatus,
    TransactionStatus,
)
from apps.payments.unified_services import PaymentServiceError, UnifiedPaymentService
from core.utils import normalize_kenyan_phone

ZERO = Decimal("0.00")
MIN_DEPOSIT = Decimal("10.00")
MAX_DEPOSIT = Decimal("150000.00")
MIN_WITHDRAWAL = Decimal("100.00")
MAX_WITHDRAWAL = Decimal("50000.00")
DAILY_WITHDRAWAL_LIMIT = Decimal("150000.00")
MIN_REMAINING_WALLET_BALANCE = Decimal("500.00")
MIN_TRANSFER = Decimal("10.00")
MAX_TRANSFER = Decimal("150000.00")
SUCCESS_PAYMENT_STATES = {"success", "reconciled", "partially_refunded", "refunded"}
PENDING_PAYMENT_STATES = {"initiated", "pending", "pending_authentication", "pending_verification"}
FAILED_PAYMENT_STATES = {"failed"}
CANCELLED_PAYMENT_STATES = {"cancelled", "expired"}
LEDGER_VISIBLE_TYPES = {
    LedgerEntryType.LOAN_DISBURSEMENT,
    LedgerEntryType.ADJUSTMENT,
    LedgerEntryType.WALLET_TRANSFER,
    LedgerEntryType.PAYOUT,
}


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value in (None, ""):
        return ZERO
    return Decimal(str(value))


def _decimal_to_str(value: Any) -> str:
    return str(_to_decimal(value).quantize(Decimal("0.01")))


def _pick_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value not in (None, ""):
            coerced = str(value).strip()
            if coerced:
                return coerced
    return None


def _iso_datetime(value) -> str | None:
    return value.isoformat() if value else None


def _payment_direction(intent: PaymentIntent) -> str:
    wallet_flow_kind = _wallet_flow_kind(intent)
    if wallet_flow_kind == "deposit":
        return "inflow"
    if wallet_flow_kind == "withdrawal":
        return "outflow"
    if str(intent.purpose or "").lower() == "loan_disbursement":
        return "inflow"
    return "outflow"


def _wallet_flow_kind(intent: PaymentIntent) -> str | None:
    metadata = intent.metadata or {}
    raw_value = metadata.get("wallet_flow_kind") or metadata.get("wallet_action_kind")
    if not raw_value:
        return None
    normalized = str(raw_value).strip().lower()
    if normalized in {"deposit", "wallet_deposit"}:
        return "deposit"
    if normalized in {"withdrawal", "wallet_withdrawal"}:
        return "withdrawal"
    return None


def _payment_activity_type(intent: PaymentIntent) -> str:
    wallet_flow_kind = _wallet_flow_kind(intent)
    if wallet_flow_kind == "deposit":
        return "wallet_deposit"
    if wallet_flow_kind == "withdrawal":
        return "wallet_withdrawal"
    purpose = str(intent.purpose or "").lower()
    if purpose in {"contribution", "special_contribution"}:
        return "contribution_payment"
    if purpose == "loan_repayment":
        return "loan_repayment"
    if purpose == "fine":
        return "fine_payment"
    if purpose == "loan_disbursement":
        return "loan_disbursement"
    if str(intent.status or "").lower() in PENDING_PAYMENT_STATES:
        return "pending_transaction"
    return "wallet_adjustment"


def _ledger_activity_type(entry: LedgerEntry) -> str:
    entry_type = str(entry.entry_type or "").lower()
    if entry_type == LedgerEntryType.LOAN_DISBURSEMENT:
        return "loan_disbursement"
    if entry_type == LedgerEntryType.WALLET_TRANSFER:
        return "wallet_transfer"
    if entry_type == LedgerEntryType.PAYOUT:
        return "payout"
    if str(entry.status or "").lower() == "pending":
        return "pending_transaction"
    return "wallet_adjustment"


def _activity_type_label(activity_type: str) -> str:
    labels = {
        "wallet_deposit": "Wallet deposit",
        "wallet_withdrawal": "Wallet withdrawal",
        "wallet_transfer": "Wallet transfer",
        "payout": "Payout",
        "contribution_payment": "Contribution payment",
        "loan_repayment": "Loan repayment",
        "loan_disbursement": "Loan disbursement",
        "fine_payment": "Fine payment",
        "wallet_adjustment": "Wallet adjustment",
        "pending_transaction": "Pending transaction",
    }
    return labels.get(activity_type, "Wallet activity")


def _payment_transaction_state(intent: PaymentIntent) -> str:
    status = str(intent.status or "").lower()
    metadata = intent.metadata or {}
    withdrawal_state = str(metadata.get("withdrawal_state") or "").lower()
    if withdrawal_state in {"rejected", "failed"}:
        return "failed"
    if withdrawal_state == "cancelled":
        return "cancelled"
    if status in SUCCESS_PAYMENT_STATES:
        return "success"
    if status in PENDING_PAYMENT_STATES:
        return "pending"
    if status in FAILED_PAYMENT_STATES:
        return "failed"
    if status in CANCELLED_PAYMENT_STATES:
        return "cancelled"
    return "pending"


def _ledger_transaction_state(entry: LedgerEntry) -> str:
    status = str(entry.status or "").lower()
    if status == "reversed":
        return "reversed"
    if status == "pending":
        return "pending"
    if status == "failed":
        return "failed"
    if status == "cancelled":
        return "cancelled"
    return "success"


def _status_label(transaction_state: str) -> str:
    return {
        "success": "Successful",
        "pending": "Pending",
        "failed": "Failed",
        "reversed": "Reversed",
        "cancelled": "Cancelled",
    }.get(transaction_state, "Pending")


def _payment_explanation(intent: PaymentIntent, transaction_state: str) -> str | None:
    metadata = intent.metadata or {}
    wallet_flow_kind = _wallet_flow_kind(intent)
    if wallet_flow_kind == "deposit":
        if transaction_state == "pending":
            return "This transaction is still being processed."
        if transaction_state == "failed":
            return intent.failure_reason or "We couldn’t complete this deposit."
        if transaction_state == "cancelled":
            return "This deposit was cancelled before it was completed."
        return "Your deposit was received successfully."
    if wallet_flow_kind == "withdrawal":
        withdrawal_state = str(metadata.get("withdrawal_state") or "").lower()
        if withdrawal_state == "rejected":
            return (
                str(metadata.get("withdrawal_status_message")).strip()
                or "This withdrawal request was not approved."
            )
        if transaction_state == "pending":
            return (
                str(metadata.get("withdrawal_status_message")).strip()
                or "This transaction is still being processed."
            )
        if transaction_state == "failed":
            return intent.failure_reason or "We couldn’t complete this withdrawal."
        if transaction_state == "cancelled":
            return "This withdrawal request was cancelled."
        if transaction_state == "success":
            return "This withdrawal was completed successfully."
    if transaction_state == "pending":
        return "This payment is still being confirmed."
    if transaction_state == "failed":
        return intent.failure_reason or "This payment could not be completed."
    if transaction_state == "cancelled":
        return "This payment was cancelled before it was completed."
    return None


def _ledger_explanation(entry: LedgerEntry, transaction_state: str) -> str | None:
    if transaction_state == "pending":
        return "This transaction is still being confirmed."
    if transaction_state == "failed":
        return "This transaction did not complete successfully."
    if transaction_state == "reversed":
        return "This transaction has been reversed."
    return None


def _coerce_uuid(value: Any) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def _resolve_payment_links(intent: PaymentIntent) -> dict[str, str | None]:
    metadata = intent.metadata or {}
    contribution_id = _coerce_uuid(
        intent.contribution_id
        or metadata.get("contribution_id")
        or metadata.get("business_record_id")
    )
    loan_id = _coerce_uuid(metadata.get("loan_id") or metadata.get("loanId") or intent.purpose_id)
    installment_id = _coerce_uuid(metadata.get("installment_id") or metadata.get("installmentId"))
    penalty_id = _coerce_uuid(metadata.get("penalty_id") or metadata.get("penaltyId") or intent.purpose_id)
    contribution_type_id = _coerce_uuid(metadata.get("contribution_type_id"))

    return {
        "contribution_id": contribution_id,
        "contribution_type_id": contribution_type_id,
        "contribution_type_name": _pick_string(
            metadata.get("contribution_type_name"),
            metadata.get("type_name"),
            metadata.get("target_label"),
        ),
        "loan_id": loan_id,
        "installment_id": installment_id,
        "penalty_id": penalty_id,
        "target_label": _pick_string(
            metadata.get("target_label"),
            metadata.get("installment_label"),
            metadata.get("reference_label"),
        ),
        "wallet_flow_kind": _wallet_flow_kind(intent),
    }


def _serialize_payment_activity(
    intent: PaymentIntent,
    *,
    receipts_map: dict[str, PaymentReceipt],
) -> dict[str, Any]:
    links = _resolve_payment_links(intent)
    transaction_state = _payment_transaction_state(intent)
    activity_type = _payment_activity_type(intent)
    receipt = receipts_map.get(str(intent.id))
    direction = _payment_direction(intent)
    amount = _to_decimal(intent.amount)
    receipt_reference = ""
    if receipt:
        receipt_reference = (
            getattr(getattr(receipt, "transaction", None), "reference", "")
            or receipt.reference_number
            or receipt.receipt_number
        )

    return {
        "id": f"payment_{intent.id}",
        "transaction_id": f"payment_{intent.id}",
        "source_type": "payment",
        "intent_id": str(intent.id),
        "amount": _decimal_to_str(amount),
        "signed_amount": _decimal_to_str(amount if direction == "inflow" else amount * Decimal("-1")),
        "currency": intent.currency or "KES",
        "direction": direction,
        "type": activity_type,
        "type_label": _activity_type_label(activity_type),
        "purpose_label": links.get("target_label")
        or links.get("contribution_type_name")
        or ("Deposit to wallet" if links.get("wallet_flow_kind") == "deposit" else None)
        or ("Withdraw from wallet" if links.get("wallet_flow_kind") == "withdrawal" else None)
        or _activity_type_label(activity_type),
        "status": transaction_state,
        "status_label": _status_label(transaction_state),
        "reference": receipt_reference or intent.reference or intent.provider_intent_id or str(intent.id),
        "date": _iso_datetime(intent.completed_at or intent.created_at),
        "updated_at": _iso_datetime(intent.updated_at),
        "payment_method": intent.payment_method,
        "receipt_available": bool(receipt),
        "receipt_ready": bool(receipt),
        "refresh_supported": transaction_state == "pending",
        "explanation": _payment_explanation(intent, transaction_state),
        "contribution_id": links["contribution_id"],
        "contribution_type_id": links["contribution_type_id"],
        "contribution_type_name": links["contribution_type_name"],
        "loan_id": links["loan_id"],
        "installment_id": links["installment_id"],
        "penalty_id": links["penalty_id"],
        "target_label": links["target_label"],
        "linked_purpose": str(intent.purpose or "").lower(),
        "wallet_flow_kind": links.get("wallet_flow_kind"),
    }


def _serialize_ledger_activity(entry: LedgerEntry) -> dict[str, Any]:
    activity_type = _ledger_activity_type(entry)
    transaction_state = _ledger_transaction_state(entry)
    direction = "inflow" if str(entry.entry_type or "").lower() == LedgerEntryType.LOAN_DISBURSEMENT else (
        "inflow" if str(entry.direction or "").lower() == "credit" else "outflow"
    )
    amount = _to_decimal(entry.amount)
    loan_id = str(entry.related_loan_id) if entry.related_loan_id else None
    target_label = None
    if entry.related_loan_id:
        target_label = f"Loan {str(entry.related_loan_id).split('-')[0].upper()}"
    meta = entry.meta or {}
    if str(entry.entry_type or "").lower() == LedgerEntryType.WALLET_TRANSFER:
        counterparty_name = _pick_string(meta.get("counterparty_name"), meta.get("counterparty_phone"))
        if counterparty_name:
            target_label = f"{'From' if direction == 'inflow' else 'To'} {counterparty_name}"
    if str(entry.entry_type or "").lower() == LedgerEntryType.PAYOUT:
        target_label = _pick_string(meta.get("target_label"), meta.get("payout_label")) or "Payout"

    return {
        "id": f"ledger_{entry.id}",
        "transaction_id": f"ledger_{entry.id}",
        "source_type": "ledger",
        "ledger_entry_id": str(entry.id),
        "intent_id": None,
        "amount": _decimal_to_str(amount),
        "signed_amount": _decimal_to_str(amount if direction == "inflow" else amount * Decimal("-1")),
        "currency": entry.currency or "KES",
        "direction": direction,
        "type": activity_type,
        "type_label": _activity_type_label(activity_type),
        "purpose_label": target_label or _activity_type_label(activity_type),
        "status": transaction_state,
        "status_label": _status_label(transaction_state),
        "reference": entry.provider_reference or entry.idempotency_key or str(entry.id),
        "date": _iso_datetime(entry.created_at),
        "updated_at": _iso_datetime(entry.updated_at),
        "payment_method": None,
        "receipt_available": False,
        "receipt_ready": False,
        "refresh_supported": False,
        "explanation": _ledger_explanation(entry, transaction_state),
        "contribution_id": None,
        "contribution_type_id": None,
        "contribution_type_name": None,
        "loan_id": loan_id,
        "installment_id": None,
        "penalty_id": None,
        "target_label": target_label,
        "linked_purpose": str(entry.entry_type or "").lower(),
    }


def _wallet_activity_queryset(
    *,
    chama: Chama,
    member,
    limit: int = 150,
    start_date=None,
    end_date=None,
) -> list[dict[str, Any]]:
    intent_qs = PaymentIntent.objects.filter(chama=chama, user=member)
    if start_date:
        start_dt = timezone.make_aware(
            datetime.combine(start_date, time.min)
        )
        intent_qs = intent_qs.filter(created_at__gte=start_dt)
    if end_date:
        end_dt = timezone.make_aware(
            datetime.combine(end_date, time.max).replace(microsecond=0)
        )
        intent_qs = intent_qs.filter(created_at__lte=end_dt)

    payments = list(
        intent_qs.select_related("contribution")
        .order_by("-created_at")[:limit]
    )
    receipts_map = {
        str(receipt.payment_intent_id): receipt
        for receipt in PaymentReceipt.objects.filter(payment_intent_id__in=[payment.id for payment in payments])
    }

    payment_rows = [
        _serialize_payment_activity(payment, receipts_map=receipts_map)
        for payment in payments
    ]

    user_wallet = Wallet.objects.filter(
        owner_type=WalletOwnerType.USER,
        owner_id=member.id,
    ).first()
    ledger_filters = Q(chama=chama, related_loan__member=member, entry_type=LedgerEntryType.LOAN_DISBURSEMENT)
    if user_wallet:
        ledger_filters |= Q(
            chama=chama,
            wallet=user_wallet,
            entry_type__in=LEDGER_VISIBLE_TYPES,
        )
    ledger_qs = (
        LedgerEntry.objects.select_related("related_loan", "wallet")
        .filter(ledger_filters, related_payment__isnull=True)
        .order_by("-created_at")
    )
    if start_date:
        ledger_qs = ledger_qs.filter(created_at__gte=start_dt)
    if end_date:
        ledger_qs = ledger_qs.filter(created_at__lte=end_dt)

    ledger_rows = [_serialize_ledger_activity(entry) for entry in ledger_qs[:limit]]

    combined = payment_rows + ledger_rows
    combined.sort(key=lambda item: item.get("date") or "", reverse=True)
    return combined


def _apply_activity_filter(
    rows: list[dict[str, Any]],
    *,
    filter_key: str = "all",
    search: str | None = None,
) -> list[dict[str, Any]]:
    filtered = rows

    if filter_key == "deposits":
        filtered = [row for row in filtered if row["type"] == "wallet_deposit"]
    elif filter_key == "withdrawals":
        filtered = [row for row in filtered if row["type"] == "wallet_withdrawal"]
    elif filter_key == "transfers":
        filtered = [row for row in filtered if row["type"] == "wallet_transfer"]
    elif filter_key == "contributions":
        filtered = [
            row for row in filtered if row["type"] in {"contribution_payment", "fine_payment"}
        ]
    elif filter_key == "loan_repayments":
        filtered = [row for row in filtered if row["type"] == "loan_repayment"]
    elif filter_key == "pending":
        filtered = [row for row in filtered if row["status"] == "pending"]
    elif filter_key == "failed":
        filtered = [
            row
            for row in filtered
            if row["status"] in {"failed", "cancelled", "reversed"}
        ]

    if search:
        lowered = search.strip().lower()
        filtered = [
            row
            for row in filtered
            if lowered
            in " ".join(
                [
                    str(row.get("purpose_label") or ""),
                    str(row.get("reference") or ""),
                    str(row.get("target_label") or ""),
                    str(row.get("contribution_type_name") or ""),
                ]
            ).lower()
        ]

    return filtered


def _get_member_wallet(member, *, currency: str = "KES", lock_for_update: bool = False) -> Wallet:
    queryset = Wallet.objects
    if lock_for_update:
        queryset = queryset.select_for_update()
    wallet, _ = queryset.get_or_create(
        owner_type=WalletOwnerType.USER,
        owner_id=member.id,
        defaults={
            "available_balance": ZERO,
            "locked_balance": ZERO,
            "currency": currency,
        },
    )
    return wallet


def _successful_withdrawals_today(member) -> Decimal:
    wallet = Wallet.objects.filter(
        owner_type=WalletOwnerType.USER,
        owner_id=member.id,
    ).first()
    if not wallet:
        return ZERO

    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    total = (
        LedgerEntry.objects.filter(
            wallet=wallet,
            entry_type=LedgerEntryType.WITHDRAWAL,
            direction=LedgerDirection.DEBIT,
            status="success",
            created_at__gte=today_start,
        ).aggregate(total=Sum("amount"))["total"]
    )
    return _to_decimal(total)


def _wallet_limits(member, wallet: Wallet) -> dict[str, str]:
    today_withdrawn = _successful_withdrawals_today(member)
    remaining_daily = max(DAILY_WITHDRAWAL_LIMIT - today_withdrawn, ZERO)
    return {
        "min_deposit": _decimal_to_str(MIN_DEPOSIT),
        "max_deposit": _decimal_to_str(MAX_DEPOSIT),
        "min_withdrawal": _decimal_to_str(MIN_WITHDRAWAL),
        "max_withdrawal": _decimal_to_str(MAX_WITHDRAWAL),
        "min_transfer": _decimal_to_str(MIN_TRANSFER),
        "max_transfer": _decimal_to_str(MAX_TRANSFER),
        "daily_withdrawal_limit": _decimal_to_str(DAILY_WITHDRAWAL_LIMIT),
        "today_withdrawn": _decimal_to_str(today_withdrawn),
        "remaining_daily": _decimal_to_str(remaining_daily),
        "withdrawable_balance": _decimal_to_str(wallet.available_balance),
    }


def _active_bank_provider_config(*, chama: Chama) -> PaymentProviderConfig | None:
    return (
        PaymentProviderConfig.objects.filter(
            chama=chama,
            provider_type__iexact="bank",
            is_active=True,
        )
        .order_by("-created_at")
        .first()
    )


def _member_wallet_methods(*, chama: Chama) -> dict[str, list[dict[str, Any]]]:
    bank_config = _active_bank_provider_config(chama=chama)
    bank_enabled = bool(
        bank_config and str(bank_config.bank_name or "").strip() and str(bank_config.bank_account_number or "").strip()
    )
    bank_name = str(bank_config.bank_name).strip() if bank_config else ""
    return {
        "deposit_methods": [
            {
                "key": "mpesa",
                "label": "M-Pesa",
                "description": "Add money to your wallet securely.",
                "enabled": True,
            },
            {
                "key": "paybill",
                "label": "Paybill Instructions",
                "description": "Direct paybill funding is not available right now.",
                "enabled": False,
            },
            {
                "key": "bank",
                "label": "Bank transfer",
                "description": (
                    f"Transfer funds to {bank_name} using the provided reference code."
                    if bank_enabled and bank_name
                    else "Bank transfer funding is not available right now."
                ),
                "enabled": bank_enabled,
            },
        ],
        "withdrawal_methods": [
            {
                "key": "mpesa",
                "label": "M-Pesa",
                "description": "Withdraw to your verified mobile money number.",
                "enabled": True,
            },
            {
                "key": "bank_transfer_placeholder",
                "label": "Bank transfer",
                "description": "Bank transfer withdrawals are not available right now.",
                "enabled": False,
            },
        ],
    }


def build_member_wallet_flow_transaction(
    *,
    chama: Chama,
    member,
    intent: PaymentIntent,
) -> dict[str, Any]:
    return build_member_wallet_transaction_detail(
        chama=chama,
        member=member,
        transaction_ref=f"payment_{intent.id}",
    )


def _build_deposit_workflow_payload(*, chama: Chama, member, intent: PaymentIntent) -> dict[str, Any]:
    transaction = build_member_wallet_flow_transaction(chama=chama, member=member, intent=intent)
    state = "processing"
    normalized_status = str(intent.status or "").lower()
    if normalized_status in SUCCESS_PAYMENT_STATES:
        state = "success"
    elif normalized_status in FAILED_PAYMENT_STATES:
        state = "failed"
    elif normalized_status in CANCELLED_PAYMENT_STATES:
        state = "cancelled"
    elif normalized_status == "initiated":
        state = "initiated"
    elif normalized_status == "pending_verification":
        state = "pending"

    instructions = None
    if str(intent.payment_method or "").lower() == PaymentMethod.BANK:
        details = BankPaymentDetails.objects.filter(payment_intent=intent).first()
        if details:
            instructions = {
                "type": "bank_transfer",
                "bank_name": details.bank_name,
                "account_number": details.account_number,
                "account_name": details.account_name or getattr(chama, "name", "") or "",
                "transfer_reference": details.transfer_reference,
                "member_action_label": "Transfer and wait for confirmation",
            }

    return {
        "intent_id": str(intent.id),
        "state": state,
        "transaction": transaction["transaction"],
        "instructions": instructions,
        "wallet_snapshot": build_member_wallet_workspace(chama=chama, member=member),
    }


def _build_withdrawal_workflow_payload(*, chama: Chama, member, intent: PaymentIntent) -> dict[str, Any]:
    metadata = intent.metadata or {}
    transaction = build_member_wallet_flow_transaction(chama=chama, member=member, intent=intent)
    state = str(metadata.get("withdrawal_state") or "pending_processing").lower()
    if str(intent.status or "").lower() in SUCCESS_PAYMENT_STATES:
        state = "approved_completed"
    elif str(intent.status or "").lower() in FAILED_PAYMENT_STATES:
        state = "failed"
    elif str(intent.status or "").lower() in CANCELLED_PAYMENT_STATES:
        state = "cancelled"

    return {
        "intent_id": str(intent.id),
        "state": state,
        "transaction": transaction["transaction"],
        "wallet_snapshot": build_member_wallet_workspace(chama=chama, member=member),
    }


def create_member_wallet_deposit(
    *,
    chama: Chama,
    member,
    amount: Decimal,
    payment_method: str,
    phone: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    normalized_method = str(payment_method or "").strip().lower()
    if normalized_method not in {PaymentMethod.MPESA, PaymentMethod.BANK}:
        raise PaymentServiceError("This deposit method is not available right now.")
    if amount < MIN_DEPOSIT or amount > MAX_DEPOSIT:
        raise PaymentServiceError("Enter a valid amount to continue.")

    if normalized_method == PaymentMethod.BANK:
        bank_config = _active_bank_provider_config(chama=chama)
        if not bank_config or not str(bank_config.bank_name or "").strip() or not str(bank_config.bank_account_number or "").strip():
            raise PaymentServiceError("Bank transfer details are not configured for this chama.")

        intent = UnifiedPaymentService.create_payment_intent(
            chama=chama,
            user=member,
            amount=amount,
            currency="KES",
            payment_method=PaymentMethod.BANK,
            purpose=PaymentPurpose.OTHER,
            description="Wallet deposit (bank transfer)",
            idempotency_key=idempotency_key,
            bank_name=str(bank_config.bank_name).strip(),
            account_number=str(bank_config.bank_account_number).strip(),
            account_name=str(chama.name or "").strip(),
            metadata={
                "wallet_flow_kind": "deposit",
                "payment_purpose_type": "wallet_deposit",
                "payment_purpose_label": "Deposit to Wallet",
                "target_label": "My wallet",
                "source": "mobile_member_wallet_deposit_bank",
            },
        )
    else:
        intent = UnifiedPaymentService.create_payment_intent(
            chama=chama,
            user=member,
            amount=amount,
            currency="KES",
            payment_method=PaymentMethod.MPESA,
            purpose=PaymentPurpose.OTHER,
            description="Wallet deposit",
            idempotency_key=idempotency_key,
            phone=phone,
            metadata={
                "wallet_flow_kind": "deposit",
                "payment_purpose_type": "wallet_deposit",
                "payment_purpose_label": "Deposit to Wallet",
                "target_label": "My wallet",
                "source": "mobile_member_wallet_deposit",
            },
        )

    return _build_deposit_workflow_payload(chama=chama, member=member, intent=intent)


def get_member_wallet_deposit_detail(*, chama: Chama, member, intent_id: str) -> dict[str, Any]:
    intent = PaymentIntent.objects.filter(id=intent_id, chama=chama, user=member).first()
    if not intent or _wallet_flow_kind(intent) != "deposit":
        raise PaymentIntent.DoesNotExist
    return _build_deposit_workflow_payload(chama=chama, member=member, intent=intent)


def refresh_member_wallet_deposit(*, chama: Chama, member, intent_id: str) -> dict[str, Any]:
    intent = PaymentIntent.objects.filter(id=intent_id, chama=chama, user=member).first()
    if not intent or _wallet_flow_kind(intent) != "deposit":
        raise PaymentIntent.DoesNotExist

    if str(intent.status or "").lower() in PENDING_PAYMENT_STATES:
        intent = UnifiedPaymentService.verify_payment(intent.id)
    return _build_deposit_workflow_payload(chama=chama, member=member, intent=intent)


def create_member_wallet_withdrawal(
    *,
    chama: Chama,
    member,
    amount: Decimal,
    payment_method: str,
    phone: str,
    pin: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    normalized_method = str(payment_method or "").strip().lower()
    if normalized_method != PaymentMethod.MPESA:
        raise PaymentServiceError("This withdrawal method is not available right now.")
    if amount < MIN_WITHDRAWAL or amount > MAX_WITHDRAWAL:
        raise PaymentServiceError("Enter a valid amount to continue.")

    if getattr(settings, "WITHDRAWAL_PIN_REQUIRED", True):
        provided_pin = str(pin or "").strip()
        if not provided_pin:
            raise PaymentServiceError("Enter your withdrawal PIN to continue.")
        from apps.security.pin_service import PinService, PinType

        ok, message = PinService.verify_pin(member, provided_pin, PinType.WITHDRAWAL)
        if not ok:
            raise PaymentServiceError(message or "Incorrect PIN.")

    try:
        normalized_phone = normalize_kenyan_phone(phone)
    except ValueError as exc:
        raise PaymentServiceError(str(exc)) from exc

    member_phone = str(getattr(member, "phone", "") or "").strip()
    if member_phone:
        try:
            member_phone_normalized = normalize_kenyan_phone(member_phone)
        except ValueError:
            member_phone_normalized = member_phone
        if member_phone_normalized != normalized_phone:
            raise PaymentServiceError("Withdrawals are only allowed to your verified M-Pesa number.")

    from apps.accounts.models import MemberKYC, MemberKYCStatus, MemberKYCTier

    kyc_ok = MemberKYC.objects.filter(
        user=member,
        status=MemberKYCStatus.APPROVED,
        kyc_tier__in=[MemberKYCTier.TIER_2, MemberKYCTier.TIER_3],
    ).exists()
    if not kyc_ok:
        raise PaymentServiceError("Complete Tier 2 verification before requesting a wallet withdrawal.")

    with transaction.atomic():
        wallet = _get_member_wallet(member, currency="KES", lock_for_update=True)
        remaining_daily = _to_decimal(_wallet_limits(member, wallet)["remaining_daily"])
        if wallet.available_balance <= ZERO or wallet.available_balance < amount or remaining_daily < amount:
            raise PaymentServiceError("You don’t have enough withdrawable balance.")
        if (_to_decimal(wallet.available_balance) - amount) < MIN_REMAINING_WALLET_BALANCE:
            raise PaymentServiceError(
                f"Keep at least KES {MIN_REMAINING_WALLET_BALANCE:,.0f} in your wallet after a withdrawal."
            )
        if Penalty.objects.filter(
            chama=chama,
            member=member,
            status__in=[PenaltyStatus.UNPAID, PenaltyStatus.PARTIAL],
        ).exists():
            raise PaymentServiceError(
                "Clear outstanding penalties before requesting a wallet withdrawal."
            )
        if Loan.objects.filter(
            chama=chama,
            member=member,
            status__in=[LoanStatus.OVERDUE, LoanStatus.DEFAULTED],
        ).exists() or InstallmentSchedule.objects.filter(
            loan__chama=chama,
            loan__member=member,
            status=InstallmentStatus.OVERDUE,
        ).exists():
            raise PaymentServiceError(
                "You have overdue loan obligations and cannot withdraw right now."
            )

        wallet.available_balance = _to_decimal(wallet.available_balance) - amount
        wallet.locked_balance = _to_decimal(wallet.locked_balance) + amount
        wallet.save(update_fields=["available_balance", "locked_balance", "updated_at"])

        intent = PaymentIntent.objects.create(
            chama=chama,
            user=member,
            amount=amount,
            currency="KES",
            purpose=PaymentPurpose.OTHER,
            description="Wallet withdrawal",
            payment_method=PaymentMethod.MPESA,
            phone=normalized_phone,
            provider="member_wallet_withdrawal",
            provider_intent_id=f"withdrawal_{uuid.uuid4().hex}",
            status=PaymentStatus.PENDING,
            idempotency_key=idempotency_key
            or UnifiedPaymentService.generate_idempotency_key(
                chama.id,
                member.id,
                amount,
                "wallet_withdrawal",
                PaymentMethod.MPESA,
            ),
            reference=f"WDR-{uuid.uuid4().hex[:12].upper()}",
            metadata={
                "wallet_flow_kind": "withdrawal",
                "payment_purpose_type": "wallet_withdrawal",
                "payment_purpose_label": "Withdraw from Wallet",
                "target_label": "M-Pesa withdrawal",
                "withdrawal_state": "pending_processing",
                "withdrawal_status_message": "Your withdrawal request has been submitted.",
                "beneficiary_phone": normalized_phone,
                "source": "mobile_member_wallet_withdrawal",
            },
            created_by=member,
        )
        PaymentAuditLog.objects.create(
            payment_intent=intent,
            actor=member,
            event="withdrawal_requested",
            new_status=intent.status,
            metadata={
                "wallet_flow_kind": "withdrawal",
                "beneficiary_phone": normalized_phone,
            },
        )

    return _build_withdrawal_workflow_payload(chama=chama, member=member, intent=intent)


def get_member_wallet_withdrawal_detail(*, chama: Chama, member, intent_id: str) -> dict[str, Any]:
    intent = PaymentIntent.objects.filter(id=intent_id, chama=chama, user=member).first()
    if not intent or _wallet_flow_kind(intent) != "withdrawal":
        raise PaymentIntent.DoesNotExist
    return _build_withdrawal_workflow_payload(chama=chama, member=member, intent=intent)


def _complete_member_wallet_withdrawal_stub(*, chama: Chama, member, intent: PaymentIntent) -> PaymentIntent:
    if str(intent.status or "").lower() not in PENDING_PAYMENT_STATES:
        return intent

    metadata = intent.metadata or {}
    if str(metadata.get("withdrawal_state") or "").lower() not in {"pending_processing", "submitted"}:
        return intent

    provider_reference = str(metadata.get("payout_reference") or f"B2C-{intent.reference}")[:120]
    return _complete_member_wallet_withdrawal_success(
        chama=chama,
        member=member,
        intent=intent,
        provider_reference=provider_reference,
        raw_response={"stub": True, "payout_reference": provider_reference},
    )


def _complete_member_wallet_withdrawal_success(
    *,
    chama: Chama,
    member,
    intent: PaymentIntent,
    provider_reference: str,
    raw_response: dict[str, Any] | None = None,
) -> PaymentIntent:
    with transaction.atomic():
        intent = PaymentIntent.objects.select_for_update().get(id=intent.id)
        wallet = _get_member_wallet(member, currency=intent.currency or "KES", lock_for_update=True)
        amount = _to_decimal(intent.amount)

        if _to_decimal(wallet.locked_balance) < amount:
            raise PaymentServiceError("Your withdrawal could not be completed right now.")

        now = timezone.now()
        transaction_record = UnifiedPaymentService._upsert_transaction(
            intent=intent,
            provider_reference=str(provider_reference or "")[:120],
            provider_name=intent.provider or "safaricom",
            amount=amount,
            currency=intent.currency or "KES",
            status=TransactionStatus.VERIFIED,
            payer_reference=getattr(member, "phone", "") or "",
            raw_response=raw_response or {},
            verified_by=member,
            verified_at=now,
        )

        if not hasattr(intent, "receipt"):
            PaymentReceipt.objects.create(
                payment_intent=intent,
                transaction=transaction_record,
                amount=amount,
                currency=intent.currency or "KES",
                payment_method=intent.payment_method,
                issued_by=None,
                metadata={
                    "wallet_flow_kind": "withdrawal",
                    "withdrawal_method": intent.payment_method,
                    "payout_reference": str(provider_reference or "")[:120],
                },
            )

        ledger_key = f"wallet-withdrawal:{intent.id}"
        if not LedgerEntry.objects.filter(chama=chama, idempotency_key=ledger_key).exists():
            LedgerEntry.objects.create(
                wallet=wallet,
                chama=chama,
                entry_type=LedgerEntryType.WITHDRAWAL,
                direction=LedgerDirection.DEBIT,
                amount=amount,
                debit=amount,
                credit=ZERO,
                currency=intent.currency or "KES",
                status=LedgerStatus.SUCCESS,
                provider=intent.payment_method,
                provider_reference=str(provider_reference or "")[:120],
                idempotency_key=ledger_key,
                related_payment=intent,
                narration="Member wallet withdrawal completed.",
                meta={"wallet_flow_kind": "withdrawal", "payment_intent_id": str(intent.id)},
                created_by=member,
                updated_by=member,
            )

        wallet.locked_balance = _to_decimal(wallet.locked_balance) - amount
        wallet.save(update_fields=["locked_balance", "updated_at"])

        new_metadata = dict(intent.metadata or {})
        new_metadata.update(
            {
                "withdrawal_state": "approved_completed",
                "withdrawal_status_message": "Your withdrawal was completed successfully.",
                "payout_reference": str(provider_reference or "")[:120],
            }
        )
        intent.metadata = new_metadata
        old_status = intent.status
        intent.status = PaymentStatus.SUCCESS
        intent.completed_at = now
        intent.save(update_fields=["metadata", "status", "completed_at", "updated_at"])

        PaymentAuditLog.objects.create(
            payment_intent=intent,
            actor=member,
            event="wallet_withdrawal_completed",
            previous_status=old_status,
            new_status=intent.status,
            metadata={"payout_reference": str(provider_reference or "")[:120]},
        )

        try:
            from apps.notifications.models import NotificationCategory, NotificationPriority, NotificationType
            from apps.notifications.services import create_notification

            create_notification(
                recipient=member,
                chama=chama,
                notification_type=NotificationType.PAYMENT_CONFIRMATION,
                title="Withdrawal completed",
                message=f"KES {amount:,.2f} was withdrawn to your M-Pesa.",
                priority=NotificationPriority.HIGH,
                category=NotificationCategory.PAYMENTS,
                action_url=f"/member/wallet?ref=payment_{intent.id}",
                metadata={"intent_id": str(intent.id), "amount": str(amount)},
                send_email=False,
                send_sms=False,
            )
        except Exception:  # noqa: BLE001
            pass

        return intent


def complete_member_wallet_withdrawal_from_b2c(
    *,
    intent: PaymentIntent,
    provider_reference: str,
    raw_response: dict[str, Any] | None = None,
) -> PaymentIntent:
    member = intent.user
    chama = intent.chama
    if not member or not chama:
        raise PaymentServiceError("Withdrawal could not be completed.")
    return _complete_member_wallet_withdrawal_success(
        chama=chama,
        member=member,
        intent=intent,
        provider_reference=provider_reference,
        raw_response=raw_response,
    )


def fail_member_wallet_withdrawal_from_b2c(*, intent: PaymentIntent, reason: str) -> PaymentIntent:
    member = intent.user
    chama = intent.chama
    if not member or not chama:
        raise PaymentServiceError("Withdrawal could not be updated.")
    return _release_member_wallet_withdrawal_lock(
        chama=chama,
        member=member,
        intent=intent,
        reason=reason or "We couldn’t complete this withdrawal.",
        failure_code="wallet_withdrawal_failed",
    )


def _release_member_wallet_withdrawal_lock(
    *,
    chama: Chama,
    member,
    intent: PaymentIntent,
    reason: str,
    failure_code: str = "wallet_withdrawal_failed",
) -> PaymentIntent:
    """
    Ensure wallet balances are restored when a withdrawal does not complete.

    Withdrawal requests lock wallet funds upfront (available -> locked). If the payout fails
    (FAILED/CANCELLED), the locked funds must be released back to available.
    """
    with transaction.atomic():
        intent = PaymentIntent.objects.select_for_update().get(id=intent.id)
        metadata = dict(intent.metadata or {})
        if metadata.get("withdrawal_lock_released"):
            return intent

        wallet = _get_member_wallet(member, currency=intent.currency or "KES", lock_for_update=True)
        amount = _to_decimal(intent.amount)

        if amount > ZERO and _to_decimal(wallet.locked_balance) >= amount:
            wallet.locked_balance = _to_decimal(wallet.locked_balance) - amount
            wallet.available_balance = _to_decimal(wallet.available_balance) + amount
            wallet.save(update_fields=["available_balance", "locked_balance", "updated_at"])

        ledger_key = f"wallet-withdrawal-failed:{intent.id}"
        if not LedgerEntry.objects.filter(chama=chama, idempotency_key=ledger_key).exists():
            LedgerEntry.objects.create(
                wallet=wallet,
                chama=chama,
                entry_type=LedgerEntryType.WITHDRAWAL,
                direction=LedgerDirection.DEBIT,
                amount=amount,
                debit=amount,
                credit=ZERO,
                currency=intent.currency or "KES",
                status=LedgerStatus.FAILED,
                provider=intent.payment_method,
                provider_reference=str(metadata.get("payout_reference") or intent.reference or "")[:120],
                idempotency_key=ledger_key,
                related_payment=intent,
                narration="Member wallet withdrawal failed.",
                meta={
                    "wallet_flow_kind": "withdrawal",
                    "payment_intent_id": str(intent.id),
                    "failure_reason": str(reason or "")[:500],
                },
                created_by=member,
                updated_by=member,
            )

        old_status = intent.status
        metadata.update(
            {
                "withdrawal_state": "failed",
                "withdrawal_status_message": reason or "We couldn’t complete this withdrawal.",
                "withdrawal_lock_released": True,
            }
        )
        intent.metadata = metadata
        intent.failure_reason = str(reason or "")[:1000]
        intent.failure_code = str(failure_code or "wallet_withdrawal_failed")[:50]
        intent.status = PaymentStatus.FAILED
        intent.completed_at = timezone.now()
        intent.save(
            update_fields=[
                "metadata",
                "failure_reason",
                "failure_code",
                "status",
                "completed_at",
                "updated_at",
            ]
        )
        PaymentAuditLog.objects.create(
            payment_intent=intent,
            actor=member,
            event="wallet_withdrawal_failed",
            previous_status=old_status,
            new_status=intent.status,
            metadata={"reason": reason},
        )

        try:
            from apps.notifications.models import NotificationCategory, NotificationPriority, NotificationType
            from apps.notifications.services import create_notification

            create_notification(
                recipient=member,
                chama=chama,
                notification_type=NotificationType.PAYMENT_CONFIRMATION,
                title="Withdrawal failed",
                message=reason or "We couldn’t complete this withdrawal. Your funds are available in your wallet.",
                priority=NotificationPriority.HIGH,
                category=NotificationCategory.PAYMENTS,
                action_url=f"/member/wallet?ref=payment_{intent.id}",
                metadata={"intent_id": str(intent.id), "amount": str(amount)},
                send_email=False,
                send_sms=False,
            )
        except Exception:  # noqa: BLE001
            pass

        return intent


def refresh_member_wallet_withdrawal(*, chama: Chama, member, intent_id: str) -> dict[str, Any]:
    intent = PaymentIntent.objects.filter(id=intent_id, chama=chama, user=member).first()
    if not intent or _wallet_flow_kind(intent) != "withdrawal":
        raise PaymentIntent.DoesNotExist
    if getattr(settings, "MPESA_USE_STUB", True) and str(intent.status or "").lower() in PENDING_PAYMENT_STATES:
        try:
            intent = _complete_member_wallet_withdrawal_stub(chama=chama, member=member, intent=intent)
        except PaymentServiceError:
            pass
    normalized_status = str(intent.status or "").lower()
    if normalized_status in FAILED_PAYMENT_STATES | CANCELLED_PAYMENT_STATES:
        intent = _release_member_wallet_withdrawal_lock(
            chama=chama,
            member=member,
            intent=intent,
            reason=intent.failure_reason or "We couldn’t complete this withdrawal.",
            failure_code=intent.failure_code or "wallet_withdrawal_failed",
        )
    return _build_withdrawal_workflow_payload(chama=chama, member=member, intent=intent)


def create_member_wallet_transfer(
    *,
    chama: Chama,
    member,
    recipient_member_id: str,
    amount: Decimal,
    idempotency_key: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    if amount <= ZERO or amount < MIN_TRANSFER or amount > MAX_TRANSFER:
        raise PaymentServiceError("Enter a valid amount to continue.")

    try:
        recipient_uuid = uuid.UUID(str(recipient_member_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise PaymentServiceError("Select a valid member to continue.") from exc

    if str(recipient_uuid) == str(getattr(member, "id", "")):
        raise PaymentServiceError("Choose a different member to continue.")

    operation_key = (str(idempotency_key or "").strip() or f"wallet-transfer:{uuid.uuid4().hex}")[:90]
    debit_key = f"{operation_key}:debit"
    credit_key = f"{operation_key}:credit"

    with transaction.atomic():
        recipient_membership = (
            Membership.objects.select_related("user")
            .select_for_update()
            .filter(
                chama=chama,
                user_id=recipient_uuid,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
            )
            .first()
        )
        if not recipient_membership:
            raise PaymentServiceError("This member is not eligible to receive transfers.")
        recipient = recipient_membership.user

        sender_wallet = _get_member_wallet(member, currency="KES", lock_for_update=True)
        recipient_wallet = _get_member_wallet(recipient, currency="KES", lock_for_update=True)

        existing_debit = LedgerEntry.objects.filter(chama=chama, idempotency_key=debit_key).first()
        if existing_debit:
            return {
                "transaction_ref": f"ledger_{existing_debit.id}",
                "wallet_snapshot": build_member_wallet_workspace(chama=chama, member=member),
            }

        if _to_decimal(sender_wallet.available_balance) < amount:
            raise PaymentServiceError("You do not have enough wallet balance for this transfer.")

        sender_wallet.available_balance = _to_decimal(sender_wallet.available_balance) - amount
        recipient_wallet.available_balance = _to_decimal(recipient_wallet.available_balance) + amount
        sender_wallet.save(update_fields=["available_balance", "updated_at"])
        recipient_wallet.save(update_fields=["available_balance", "updated_at"])

        transfer_ref = f"WTR-{uuid.uuid4().hex[:10].upper()}"
        counterparty_phone = _pick_string(getattr(recipient, "phone", None))

        debit_entry = LedgerEntry.objects.create(
            wallet=sender_wallet,
            chama=chama,
            entry_type=LedgerEntryType.WALLET_TRANSFER,
            direction=LedgerDirection.DEBIT,
            amount=amount,
            debit=amount,
            credit=ZERO,
            currency=sender_wallet.currency or "KES",
            status=LedgerStatus.SUCCESS,
            provider="internal",
            provider_reference=transfer_ref,
            idempotency_key=debit_key,
            narration=f"Wallet transfer to {getattr(recipient, 'full_name', 'member')}.",
            meta={
                "wallet_action_kind": "transfer_to_member",
                "counterparty_id": str(recipient.id),
                "counterparty_name": getattr(recipient, "full_name", "") or "",
                "counterparty_phone": counterparty_phone or "",
                "note": str(note or "")[:300],
            },
            created_by=member,
            updated_by=member,
        )
        LedgerEntry.objects.create(
            wallet=recipient_wallet,
            chama=chama,
            entry_type=LedgerEntryType.WALLET_TRANSFER,
            direction=LedgerDirection.CREDIT,
            amount=amount,
            debit=ZERO,
            credit=amount,
            currency=recipient_wallet.currency or "KES",
            status=LedgerStatus.SUCCESS,
            provider="internal",
            provider_reference=transfer_ref,
            idempotency_key=credit_key,
            narration=f"Wallet transfer from {getattr(member, 'full_name', 'member')}.",
            meta={
                "wallet_action_kind": "transfer_from_member",
                "counterparty_id": str(getattr(member, "id", "")),
                "counterparty_name": getattr(member, "full_name", "") or "",
                "counterparty_phone": _pick_string(getattr(member, "phone", None)) or "",
                "note": str(note or "")[:300],
            },
            created_by=member,
            updated_by=member,
        )

        from core.audit import create_activity_log

        create_activity_log(
            actor=member,
            chama_id=chama.id,
            action="wallet_transfer_created",
            entity_type="LedgerEntry",
            entity_id=debit_entry.id,
            metadata={
                "transfer_reference": transfer_ref,
                "sender_id": str(getattr(member, "id", "")),
                "recipient_id": str(recipient.id),
                "amount": str(amount),
            },
        )

        try:
            from apps.notifications.models import NotificationCategory, NotificationPriority, NotificationType
            from apps.notifications.services import create_notification

            create_notification(
                recipient=recipient,
                chama=chama,
                notification_type=NotificationType.PAYMENT_CONFIRMATION,
                title="Wallet transfer received",
                message=f"KES {amount:,.2f} was added to your wallet.",
                priority=NotificationPriority.HIGH,
                category=NotificationCategory.PAYMENTS,
                action_url=f"/member/wallet?ref=ledger_{debit_entry.id}",
                metadata={"transaction_ref": f"ledger_{debit_entry.id}", "amount": str(amount)},
                send_email=False,
                send_sms=False,
            )
            create_notification(
                recipient=member,
                chama=chama,
                notification_type=NotificationType.PAYMENT_CONFIRMATION,
                title="Wallet transfer sent",
                message=f"KES {amount:,.2f} was sent to {getattr(recipient, 'full_name', 'a member')}.",
                priority=NotificationPriority.NORMAL,
                category=NotificationCategory.PAYMENTS,
                action_url=f"/member/wallet?ref=ledger_{debit_entry.id}",
                metadata={"transaction_ref": f"ledger_{debit_entry.id}", "amount": str(amount)},
                send_email=False,
                send_sms=False,
            )
        except Exception:  # noqa: BLE001
            pass

    return {
        "transaction_ref": f"ledger_{debit_entry.id}",
        "wallet_snapshot": build_member_wallet_workspace(chama=chama, member=member),
    }


def create_member_wallet_contribution(
    *,
    chama: Chama,
    member,
    contribution_type_id: str,
    amount: Decimal,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if amount <= ZERO:
        raise PaymentServiceError("Enter a valid amount to continue.")

    with transaction.atomic():
        wallet = _get_member_wallet(member, currency="KES", lock_for_update=True)
        if _to_decimal(wallet.available_balance) < amount:
            raise PaymentServiceError("You do not have enough wallet balance to contribute.")

        stable_key = (str(idempotency_key or "").strip() or f"wallet-contribution:{uuid.uuid4().hex}")[:100]
        existing_intent = PaymentIntent.objects.filter(chama=chama, user=member, idempotency_key=stable_key).first()
        if existing_intent:
            return {
                "intent_id": str(existing_intent.id),
                "transaction": build_member_wallet_flow_transaction(chama=chama, member=member, intent=existing_intent)["transaction"],
                "wallet_snapshot": build_member_wallet_workspace(chama=chama, member=member),
            }

        intent = UnifiedPaymentService.create_payment_intent(
            chama=chama,
            user=member,
            amount=amount,
            currency="KES",
            payment_method=PaymentMethod.WALLET,
            purpose=PaymentPurpose.CONTRIBUTION,
            description="Contribution from wallet",
            idempotency_key=stable_key,
            contribution_type_id=str(contribution_type_id),
            metadata={
                "wallet_action_kind": "send_to_chama",
                "payment_purpose_type": "contribution",
                "payment_purpose_label": "Contribution",
                "target_label": f"{getattr(chama, 'name', 'Chama')} contribution",
                "source": "mobile_member_wallet_contribution",
            },
        )

        if str(intent.status or "").lower() not in SUCCESS_PAYMENT_STATES:
            raise PaymentServiceError("We couldn’t complete this contribution right now.")

        idempotency = f"wallet-contribution-debit:{intent.id}"
        if not LedgerEntry.objects.filter(chama=chama, idempotency_key=idempotency).exists():
            LedgerEntry.objects.create(
                wallet=wallet,
                chama=chama,
                entry_type=LedgerEntryType.CONTRIBUTION,
                direction=LedgerDirection.DEBIT,
                amount=amount,
                debit=amount,
                credit=ZERO,
                currency=wallet.currency or "KES",
                status=LedgerStatus.SUCCESS,
                provider="internal",
                provider_reference=intent.reference,
                idempotency_key=idempotency,
                related_payment=intent,
                narration="Contribution paid from member wallet.",
                meta={
                    "wallet_action_kind": "send_to_chama",
                    "payment_intent_id": str(intent.id),
                    "contribution_type_id": str(contribution_type_id),
                },
                created_by=member,
                updated_by=member,
            )
            wallet.available_balance = _to_decimal(wallet.available_balance) - amount
            wallet.save(update_fields=["available_balance", "updated_at"])

    return {
        "intent_id": str(intent.id),
        "transaction": build_member_wallet_flow_transaction(chama=chama, member=member, intent=intent)["transaction"],
        "wallet_snapshot": build_member_wallet_workspace(chama=chama, member=member),
    }


def build_member_wallet_activity(
    *,
    chama: Chama,
    member,
    filter_key: str = "all",
    search: str | None = None,
    start_date=None,
    end_date=None,
    limit: int = 50,
) -> dict[str, Any]:
    activity_rows = _wallet_activity_queryset(
        chama=chama,
        member=member,
        limit=max(limit, 50),
        start_date=start_date,
        end_date=end_date,
    )
    filtered = _apply_activity_filter(activity_rows, filter_key=filter_key, search=search)
    return {
        "filters": [
            {"key": "all", "label": "All"},
            {"key": "deposits", "label": "Deposits"},
            {"key": "withdrawals", "label": "Withdrawals"},
            {"key": "transfers", "label": "Transfers"},
            {"key": "contributions", "label": "Contributions"},
            {"key": "loan_repayments", "label": "Loan Repayments"},
            {"key": "pending", "label": "Pending"},
            {"key": "failed", "label": "Failed"},
        ],
        "selected_filter": filter_key,
        "search": search or "",
        "count": len(filtered),
        "items": filtered[:limit],
    }


def build_member_wallet_transaction_detail(*, chama: Chama, member, transaction_ref: str) -> dict[str, Any]:
    if transaction_ref.startswith("payment_"):
        intent_id = transaction_ref.replace("payment_", "", 1)
        intent = (
            PaymentIntent.objects.filter(id=intent_id, chama=chama, user=member)
            .select_related("contribution")
            .first()
        )
        if not intent:
            raise PaymentIntent.DoesNotExist
        receipt = PaymentReceipt.objects.filter(payment_intent=intent).first()
        activity = _serialize_payment_activity(intent, receipts_map={str(intent.id): receipt} if receipt else {})
        contribution_id = activity.get("contribution_id")
        loan_id = activity.get("loan_id")
        activity["linked_contribution_available"] = bool(
            contribution_id and Contribution.objects.filter(id=contribution_id, member=member, chama=chama).exists()
        )
        activity["linked_loan_available"] = bool(
            loan_id and Loan.objects.filter(id=loan_id, member=member, chama=chama).exists()
        )
        activity["receipt_number"] = receipt.receipt_number if receipt else None
        activity["reference_number"] = receipt.reference_number if receipt else None
        return {
            "transaction": activity,
        }

    if transaction_ref.startswith("ledger_"):
        ledger_id = transaction_ref.replace("ledger_", "", 1)
        ledger = (
            LedgerEntry.objects.select_related("related_loan", "wallet")
            .filter(id=ledger_id, chama=chama)
            .filter(
                Q(related_loan__member=member)
                | Q(wallet__owner_type=WalletOwnerType.USER, wallet__owner_id=member.id)
            )
            .first()
        )
        if not ledger:
            raise LedgerEntry.DoesNotExist
        activity = _serialize_ledger_activity(ledger)
        activity["linked_contribution_available"] = False
        activity["linked_loan_available"] = bool(ledger.related_loan_id)
        activity["receipt_number"] = None
        activity["reference_number"] = None
        return {
            "transaction": activity,
        }

    raise ValueError("Unsupported transaction reference.")


def build_member_wallet_workspace(*, chama: Chama, member) -> dict[str, Any]:
    currency = getattr(chama, "currency", "") or "KES"
    wallet_data = FinanceService.compute_wallet_balance(str(chama.id), str(member.id))
    user_wallet = _get_member_wallet(member, currency=currency)
    contribution_workspace = build_member_contribution_workspace(chama=chama, member=member)
    loan_workspace = build_member_loan_workspace(chama=chama, member=member)
    activity_rows = _wallet_activity_queryset(chama=chama, member=member, limit=150)

    successful_rows = [row for row in activity_rows if row["status"] == "success"]
    pending_rows = [row for row in activity_rows if row["status"] == "pending"]
    inflows_total = sum(
        (_to_decimal(row["amount"]) for row in successful_rows if row["direction"] == "inflow"),
        ZERO,
    )
    outflows_total = sum(
        (_to_decimal(row["amount"]) for row in successful_rows if row["direction"] == "outflow"),
        ZERO,
    )
    pending_total = max(_to_decimal(user_wallet.locked_balance), ZERO)
    available_balance = _to_decimal(user_wallet.available_balance)
    if available_balance <= ZERO and pending_total <= ZERO and not any(
        row["type"] in {"wallet_deposit", "wallet_withdrawal"} for row in activity_rows
    ):
        available_balance = _to_decimal(wallet_data.get("wallet_balance"))
    last_updated = next(
        (row.get("updated_at") or row.get("date") for row in activity_rows if row.get("date")),
        _iso_datetime(timezone.now()),
    )

    balance_state = "positive_balance"
    if pending_rows:
        balance_state = "pending_update"
    elif available_balance <= ZERO:
        balance_state = "zero_balance"

    contribution_rows = [
        row for row in successful_rows if row["type"] == "contribution_payment"
    ]
    contribution_total = sum((_to_decimal(row["amount"]) for row in contribution_rows), ZERO)
    wallet_methods = _member_wallet_methods(chama=chama)
    limits = _wallet_limits(member, user_wallet)

    if pending_rows:
        health = {
            "tone": "attention",
            "title": "Some wallet activity is still being confirmed.",
            "message": "Review pending transactions and refresh their status if needed.",
        }
    elif available_balance > ZERO:
        health = {
            "tone": "positive",
            "title": "Your wallet balance at a glance.",
            "message": "Track inflows, outflows, and recent activity from one place.",
        }
    else:
        health = {
            "tone": "neutral",
            "title": "No wallet activity yet.",
            "message": "Start with a contribution or a repayment when you are ready.",
        }

    return {
        "chama_id": str(chama.id),
        "member_id": str(member.id),
        "currency": currency,
        "balance_state": balance_state,
        "available_balance": _decimal_to_str(available_balance),
        "withdrawable_balance": _decimal_to_str(user_wallet.available_balance),
        "pending_balance": _decimal_to_str(pending_total),
        "total_inflows": _decimal_to_str(inflows_total),
        "total_outflows": _decimal_to_str(outflows_total),
        "last_updated": last_updated,
        "limits": limits,
        "methods": wallet_methods,
        "summary_cards": {
            "recent_contribution_payments_count": len(contribution_rows),
            "recent_contribution_payments_total": _decimal_to_str(contribution_total),
            "active_loan_outstanding": str(
                loan_workspace.get("summary", {}).get("active_loan_balance", "0.00")
            ),
            "active_loan_id": (
                loan_workspace.get("active_loan", {}) or {}
            ).get("id"),
            "next_loan_repayment_due": loan_workspace.get("summary", {}).get("next_repayment_due"),
            "next_loan_repayment_amount": str(
                loan_workspace.get("summary", {}).get("next_repayment_amount", "0.00")
            ),
        },
        "linked": {
            "contributions": contribution_workspace.get("summary", {}),
            "loans": loan_workspace.get("summary", {}),
        },
        "recent_activity": activity_rows[:5],
        "pending_activity": pending_rows[:3],
        "financial_health": health,
        "empty_state": {
            "title": "No wallet activity yet.",
            "description": "Start with a contribution to see money movement and receipts here.",
            "action_label": "Make Contribution",
        },
        "recovery": {
            "last_opened_transaction_id": activity_rows[0]["transaction_id"] if activity_rows else None,
            "pending_transaction_reference": pending_rows[0]["transaction_id"] if pending_rows else None,
            "active_chama_id": str(chama.id),
        },
    }
