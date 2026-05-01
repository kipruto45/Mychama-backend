from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable

from django.db.models import Exists, OuterRef, Prefetch, Q
from django.utils import timezone

from apps.accounts.models import User
from apps.finance.models import (
    Account,
    JournalEntry,
    LedgerDirection,
    LedgerEntry,
    LedgerEntryType,
)
from apps.payments.unified_models import PaymentIntent, PaymentReceipt, PaymentStatus


def _mask_phone(phone: str | None) -> str:
    raw = str(phone or "").strip()
    if len(raw) < 7:
        return raw
    return f"{raw[:5]}****{raw[-3:]}"


def _safe_decimal(value: Any) -> str:
    try:
        return str(Decimal(str(value)).quantize(Decimal("0.01")))
    except Exception:  # noqa: BLE001
        return "0.00"


ENTRY_TYPE_CATEGORY: dict[str, str] = {
    LedgerEntryType.CONTRIBUTION: "inflow",
    LedgerEntryType.WALLET_TOPUP: "inflow",
    LedgerEntryType.LOAN_REPAYMENT: "inflow",
    LedgerEntryType.PENALTY: "inflow",
    LedgerEntryType.PAYOUT: "outflow",
    LedgerEntryType.LOAN_DISBURSEMENT: "outflow",
    LedgerEntryType.WITHDRAWAL: "outflow",
    LedgerEntryType.EXPENSE: "outflow",
    LedgerEntryType.WALLET_TRANSFER: "internal",
    LedgerEntryType.FEE: "system",
    LedgerEntryType.ADJUSTMENT: "system",
}

ENTRY_TYPE_LABEL: dict[str, str] = {
    LedgerEntryType.CONTRIBUTION: "Member Contribution",
    LedgerEntryType.WALLET_TOPUP: "Wallet Top-up",
    LedgerEntryType.WALLET_TRANSFER: "Wallet Transfer",
    LedgerEntryType.PAYOUT: "Payout",
    LedgerEntryType.LOAN_DISBURSEMENT: "Loan Disbursement",
    LedgerEntryType.LOAN_REPAYMENT: "Loan Repayment",
    LedgerEntryType.WITHDRAWAL: "Withdrawal",
    LedgerEntryType.EXPENSE: "Operational Expense",
    LedgerEntryType.FEE: "Fee",
    LedgerEntryType.PENALTY: "Penalty",
    LedgerEntryType.ADJUSTMENT: "Adjustment",
}


def _normalize_payment_status(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"success", "reconciled", "refunded", "partially_refunded"}:
        return "success"
    if normalized in {"failed", "cancelled", "expired"}:
        return "failed"
    if normalized in {"initiated", "pending", "pending_authentication", "pending_verification"}:
        return "pending"
    return normalized or "unknown"


def _normalize_ledger_status(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"success", "completed"}:
        return "success"
    if normalized in {"failed"}:
        return "failed"
    if normalized in {"reversed"}:
        return "reversed"
    if normalized in {"pending"}:
        return "pending"
    return normalized or "success"


def _infer_method_from_accounts(accounts: Iterable[Account]) -> str:
    codes = {str(acc.code or "").upper() for acc in accounts if acc}
    if "MPESA_CLEARING" in codes:
        return "mpesa"
    if "BANK_MAIN" in codes:
        return "bank_transfer"
    if "CARD_CLEARING" in codes:
        return "card"
    if "WALLET" in codes:
        return "wallet"
    if "CASH" in codes:
        return "cash"
    return "internal"


def _entry_type_direction(entry_type: str, ledger_direction: str | None) -> str:
    category = ENTRY_TYPE_CATEGORY.get(entry_type, "internal")
    if category == "inflow":
        return "inflow"
    if category == "outflow":
        return "outflow"
    if category == "system":
        if str(ledger_direction or "").lower() == LedgerDirection.DEBIT:
            return "outflow"
        if str(ledger_direction or "").lower() == LedgerDirection.CREDIT:
            return "inflow"
        return "system"
    return "internal"


def _encode_cursor(event_at: datetime | None) -> str | None:
    if not event_at:
        return None
    payload = {"t": event_at.replace(microsecond=0).isoformat()}
    return base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")


def _decode_cursor(raw_cursor: str | None) -> datetime | None:
    if not raw_cursor:
        return None
    try:
        decoded = base64.urlsafe_b64decode(raw_cursor.encode("utf-8")).decode("utf-8")
        payload = json.loads(decoded)
        ts = payload.get("t")
        if not ts:
            return None
        parsed = datetime.fromisoformat(ts)
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed)
        return parsed
    except Exception:  # noqa: BLE001
        return None


@dataclass(frozen=True)
class TransactionsFeedFilters:
    chama_id: str
    category: str | None = None
    entry_type: str | None = None
    method: str | None = None
    status: str | None = None
    search: str | None = None
    from_date: date | None = None
    to_date: date | None = None
    cursor: str | None = None
    limit: int = 50

    @property
    def cursor_dt(self) -> datetime | None:
        return _decode_cursor(self.cursor)


def _serialize_member(user: User | None) -> dict[str, Any] | None:
    if not user:
        return None
    return {
        "id": str(user.id),
        "name": getattr(user, "full_name", "") or "",
        "phone": _mask_phone(getattr(user, "phone", None)),
    }


def _serialize_payment_intent(intent: PaymentIntent, *, receipt_map: dict[str, PaymentReceipt]) -> dict[str, Any]:
    metadata = intent.metadata or {}
    status = _normalize_payment_status(intent.status)
    method = str(intent.payment_method or "").strip().lower() or "unknown"
    title = str(intent.description or "").strip() or "Payment"
    entry_type = str(metadata.get("entry_type") or "").strip().lower()
    if not entry_type:
        entry_type = str(intent.purpose or "").strip().lower() or "payment"

    category = "inflow"
    if str(intent.intent_type or "").upper() in {"WITHDRAWAL", "LOAN_DISBURSEMENT"}:
        category = "outflow"
    elif str(intent.intent_type or "").upper() in {"DEPOSIT", "LOAN_REPAYMENT"}:
        category = "inflow"
    elif method == "wallet" and str(metadata.get("wallet_flow_kind") or "").lower() == "withdrawal":
        category = "outflow"

    receipt = receipt_map.get(str(intent.id))
    receipt_intent_id = str(intent.id) if receipt else None
    provider_reference = (
        str(getattr(intent, "mpesa_receipt_number", "") or "").strip()
        or str(metadata.get("external_reference") or "").strip()
        or str(intent.reference or "").strip()
    )

    return {
        "ref": f"payment_{intent.id}",
        "source": "payment_intent",
        "source_id": str(intent.id),
        "event_at": intent.created_at.replace(microsecond=0).isoformat(),
        "category": category,
        "direction": "inflow" if category == "inflow" else ("outflow" if category == "outflow" else category),
        "type": entry_type,
        "title": title,
        "amount": _safe_decimal(intent.amount),
        "currency": str(intent.currency or "KES"),
        "status": status,
        "method": method,
        "provider": str(intent.provider or "").strip() or None,
        "reference": provider_reference or None,
        "member": _serialize_member(getattr(intent, "user", None) or getattr(intent, "created_by", None)),
        "receipt_available": bool(receipt_intent_id),
        "receipt_intent_id": receipt_intent_id,
    }


def _serialize_journal_entry(
    journal: JournalEntry,
    *,
    lines: list[LedgerEntry],
    member: User | None = None,
) -> dict[str, Any]:
    entry_type = None
    amount = None
    currency = None
    accounts: list[Account] = []
    for line in lines:
        if not entry_type and line.entry_type:
            entry_type = str(line.entry_type)
        if amount is None and line.amount is not None:
            amount = line.amount
        if currency is None and line.currency:
            currency = str(line.currency)
        if line.account_id and line.account:
            accounts.append(line.account)

    resolved_entry_type = entry_type or "adjustment"
    category = ENTRY_TYPE_CATEGORY.get(resolved_entry_type, "system")
    method = _infer_method_from_accounts(accounts)
    event_at = (journal.posted_at or journal.created_at or timezone.now()).replace(microsecond=0)

    payment_intent_id = None
    raw_key = str(journal.idempotency_key or "")
    if raw_key.startswith("payment-") and ":" in raw_key:
        candidate = raw_key.split(":")[-1]
        try:
            payment_intent_id = str(uuid.UUID(candidate))
        except ValueError:
            payment_intent_id = None

    return {
        "ref": f"journal_{journal.id}",
        "source": "journal_entry",
        "source_id": str(journal.id),
        "event_at": event_at.isoformat(),
        "category": category,
        "direction": category if category in {"inflow", "outflow"} else ("system" if category == "system" else "internal"),
        "type": str(resolved_entry_type).lower(),
        "title": ENTRY_TYPE_LABEL.get(resolved_entry_type, "Transaction"),
        "description": str(journal.description or "").strip() or None,
        "amount": _safe_decimal(amount or Decimal("0.00")),
        "currency": currency or "KES",
        "status": "reversed" if journal.is_reversal else "success",
        "method": method,
        "reference": str(journal.reference or "").strip() or None,
        "member": _serialize_member(member),
        "receipt_available": bool(payment_intent_id),
        "receipt_intent_id": payment_intent_id,
    }


def _serialize_ledger_entry(entry: LedgerEntry, *, member: User | None = None) -> dict[str, Any]:
    entry_type = str(entry.entry_type or "").strip() or "adjustment"
    category = ENTRY_TYPE_CATEGORY.get(entry_type, "internal")
    method = str(entry.provider or "").strip().lower() or "internal"
    direction = _entry_type_direction(entry_type, entry.direction)
    related_intent_id = str(entry.related_payment_id) if entry.related_payment_id else None
    return {
        "ref": f"ledger_{entry.id}",
        "source": "ledger_entry",
        "source_id": str(entry.id),
        "event_at": entry.created_at.replace(microsecond=0).isoformat(),
        "category": category,
        "direction": direction,
        "type": str(entry_type).lower(),
        "title": ENTRY_TYPE_LABEL.get(entry_type, "Ledger Entry"),
        "description": str(entry.narration or "").strip() or None,
        "amount": _safe_decimal(entry.amount),
        "currency": str(entry.currency or "KES"),
        "status": _normalize_ledger_status(entry.status),
        "method": method,
        "provider": str(entry.provider or "").strip() or None,
        "reference": str(entry.provider_reference or "").strip() or None,
        "member": _serialize_member(member),
        "receipt_available": bool(related_intent_id),
        "receipt_intent_id": related_intent_id,
    }


def list_all_transactions(*, filters: TransactionsFeedFilters) -> dict[str, Any]:
    """
    Chama-wide, role-gated transactions feed.

    Returns unified items built from:
    - JournalEntry (double-entry postings) grouped as one item per business event
    - Standalone LedgerEntry (no journal_entry)
    - PaymentIntent (only when not yet posted to finance/ledger)
    """
    limit = max(1, min(int(filters.limit or 50), 200))
    cursor_dt = filters.cursor_dt

    journal_qs = JournalEntry.objects.filter(chama_id=filters.chama_id).order_by("-posted_at", "-id")
    if cursor_dt:
        journal_qs = journal_qs.filter(posted_at__lt=cursor_dt)
    if filters.from_date:
        journal_qs = journal_qs.filter(posted_at__date__gte=filters.from_date)
    if filters.to_date:
        journal_qs = journal_qs.filter(posted_at__date__lte=filters.to_date)
    if filters.search:
        search = filters.search.strip()
        journal_qs = journal_qs.filter(
            Q(reference__icontains=search)
            | Q(description__icontains=search)
            | Q(idempotency_key__icontains=search)
        )
    if filters.entry_type:
        journal_qs = journal_qs.filter(lines__entry_type=str(filters.entry_type).lower()).distinct()

    journals = list(
        journal_qs.prefetch_related(
            Prefetch(
                "lines",
                queryset=LedgerEntry.objects.select_related("account").order_by("direction"),
            )
        )[: (limit * 2)]
    )

    ledger_qs = (
        LedgerEntry.objects.select_related("wallet", "related_payment", "related_loan")
        .filter(chama_id=filters.chama_id, journal_entry__isnull=True)
        .order_by("-created_at", "-id")
    )
    if cursor_dt:
        ledger_qs = ledger_qs.filter(created_at__lt=cursor_dt)
    if filters.from_date:
        ledger_qs = ledger_qs.filter(created_at__date__gte=filters.from_date)
    if filters.to_date:
        ledger_qs = ledger_qs.filter(created_at__date__lte=filters.to_date)
    if filters.search:
        search = filters.search.strip()
        ledger_qs = ledger_qs.filter(
            Q(narration__icontains=search)
            | Q(provider_reference__icontains=search)
            | Q(idempotency_key__icontains=search)
        )
    if filters.entry_type:
        ledger_qs = ledger_qs.filter(entry_type=str(filters.entry_type).lower())
    if filters.method:
        ledger_qs = ledger_qs.filter(provider__iexact=str(filters.method).strip().lower())
    if filters.status:
        ledger_qs = ledger_qs.filter(status__iexact=str(filters.status).strip().lower())

    # De-duplicate wallet transfers (one item per transfer): keep only the debit leg.
    ledger_qs = ledger_qs.exclude(
        entry_type=LedgerEntryType.WALLET_TRANSFER,
        direction=LedgerDirection.CREDIT,
    )

    ledgers = list(ledger_qs[: (limit * 2)])

    intent_qs = PaymentIntent.objects.filter(chama_id=filters.chama_id).order_by(
        "-created_at", "-id"
    )
    if cursor_dt:
        intent_qs = intent_qs.filter(created_at__lt=cursor_dt)
    if filters.from_date:
        intent_qs = intent_qs.filter(created_at__date__gte=filters.from_date)
    if filters.to_date:
        intent_qs = intent_qs.filter(created_at__date__lte=filters.to_date)
    if filters.search:
        search = filters.search.strip()
        intent_qs = intent_qs.filter(
            Q(reference__icontains=search)
            | Q(description__icontains=search)
            | Q(idempotency_key__icontains=search)
            | Q(mpesa_receipt_number__icontains=search)
            | Q(metadata__external_reference__icontains=search)
        )
    if filters.method:
        intent_qs = intent_qs.filter(payment_method__iexact=str(filters.method).strip().lower())

    if filters.status:
        normalized = str(filters.status).strip().lower()
        if normalized == "pending":
            intent_qs = intent_qs.filter(
                status__in=[
                    PaymentStatus.INITIATED,
                    PaymentStatus.PENDING,
                    PaymentStatus.PENDING_AUTHENTICATION,
                    PaymentStatus.PENDING_VERIFICATION,
                ]
            )
        elif normalized == "failed":
            intent_qs = intent_qs.filter(
                status__in=[
                    PaymentStatus.FAILED,
                    PaymentStatus.CANCELLED,
                    PaymentStatus.EXPIRED,
                ]
            )
        elif normalized == "success":
            intent_qs = intent_qs.filter(
                status__in=[
                    PaymentStatus.SUCCESS,
                    PaymentStatus.RECONCILED,
                    PaymentStatus.REFUNDED,
                    PaymentStatus.PARTIALLY_REFUNDED,
                ]
            )

    intent_qs = intent_qs.annotate(
        has_related_ledger=Exists(
            LedgerEntry.objects.filter(related_payment_id=OuterRef("pk"))
        )
    )

    intents = list(intent_qs.select_related("user", "created_by")[: (limit * 2)])
    receipt_map = {
        str(receipt.payment_intent_id): receipt
        for receipt in PaymentReceipt.objects.filter(
            payment_intent_id__in=[intent.id for intent in intents]
        )
    }

    # Bulk-resolve members referenced by journal metadata and wallet owners.
    member_ids: set[str] = set()
    for journal in journals:
        meta = journal.metadata or {}
        candidate = str(meta.get("member_id") or "").strip()
        if candidate:
            member_ids.add(candidate)

    for entry in ledgers:
        wallet = getattr(entry, "wallet", None)
        if wallet and str(getattr(wallet, "owner_type", "")).upper() == "USER":
            member_ids.add(str(getattr(wallet, "owner_id", "")).strip())

    for intent in intents:
        if getattr(intent, "user_id", None):
            member_ids.add(str(intent.user_id))

    member_uuid_values: list[uuid.UUID] = []
    for raw in member_ids:
        try:
            member_uuid_values.append(uuid.UUID(str(raw)))
        except ValueError:
            continue

    users_by_id: dict[str, User] = {
        str(user.id): user for user in User.objects.filter(id__in=member_uuid_values)
    }

    items: list[dict[str, Any]] = []
    for journal in journals:
        line_items = list(getattr(journal, "lines", []).all())
        meta_member_id = str((journal.metadata or {}).get("member_id") or "").strip()
        items.append(
            _serialize_journal_entry(
                journal,
                lines=line_items,
                member=users_by_id.get(meta_member_id) if meta_member_id else None,
            )
        )

    for ledger in ledgers:
        wallet = getattr(ledger, "wallet", None)
        owner_id = str(getattr(wallet, "owner_id", "")).strip() if wallet else ""
        items.append(_serialize_ledger_entry(ledger, member=users_by_id.get(owner_id) if owner_id else None))

    for intent in intents:
        # De-duplicate completed intents that already have a ledger or finance posting.
        normalized_status = _normalize_payment_status(intent.status)
        ledger_entry_id = str((intent.metadata or {}).get("ledger_entry_id") or "").strip()
        if normalized_status == "success" and (ledger_entry_id or getattr(intent, "has_related_ledger", False)):
            continue
        items.append(_serialize_payment_intent(intent, receipt_map=receipt_map))

    # Apply remaining filters that are derived.
    if filters.category:
        items = [item for item in items if item.get("category") == filters.category]
    if filters.status:
        normalized = str(filters.status).strip().lower()
        items = [item for item in items if item.get("status") == normalized]
    if filters.entry_type:
        normalized = str(filters.entry_type).strip().lower()
        items = [item for item in items if item.get("type") == normalized]

    def _sort_key(item: dict[str, Any]):
        return (item.get("event_at") or "", item.get("ref") or "")

    items.sort(key=_sort_key, reverse=True)
    sliced = items[:limit]

    next_cursor = None
    if len(sliced) == limit:
        last = sliced[-1]
        try:
            last_dt = datetime.fromisoformat(last["event_at"])
            if timezone.is_naive(last_dt):
                last_dt = timezone.make_aware(last_dt)
            next_cursor = _encode_cursor(last_dt)
        except Exception:  # noqa: BLE001
            next_cursor = None

    return {
        "items": sliced,
        "pagination": {
            "limit": limit,
            "next_cursor": next_cursor,
        },
    }


def get_transaction_detail(*, chama_id: str, transaction_ref: str) -> dict[str, Any]:
    if not transaction_ref:
        raise ValueError("transaction_ref is required")

    if transaction_ref.startswith("payment_"):
        intent_id = transaction_ref.replace("payment_", "", 1)
        intent = (
            PaymentIntent.objects.select_related("user", "created_by")
            .filter(id=intent_id, chama_id=chama_id)
            .first()
        )
        if not intent:
            raise PaymentIntent.DoesNotExist
        receipt = PaymentReceipt.objects.filter(payment_intent=intent).first()
        payload = _serialize_payment_intent(intent, receipt_map={str(intent.id): receipt} if receipt else {})
        payload["receipt_number"] = receipt.receipt_number if receipt else None
        payload["reference_number"] = receipt.reference_number if receipt else None
        payload["raw_metadata"] = intent.metadata or {}
        return {"transaction": payload}

    if transaction_ref.startswith("journal_"):
        journal_id = transaction_ref.replace("journal_", "", 1)
        try:
            journal_uuid = uuid.UUID(str(journal_id))
        except ValueError as exc:
            raise JournalEntry.DoesNotExist from exc

        journal = (
            JournalEntry.objects.filter(id=journal_uuid, chama_id=chama_id)
            .prefetch_related(
                Prefetch(
                    "lines",
                    queryset=LedgerEntry.objects.select_related("account", "wallet", "related_payment", "related_loan").order_by("direction"),
                )
            )
            .first()
        )
        if not journal:
            raise JournalEntry.DoesNotExist
        lines = list(getattr(journal, "lines", []).all())
        meta_member_id = str((journal.metadata or {}).get("member_id") or "").strip()
        member = None
        if meta_member_id:
            member = User.objects.filter(id=meta_member_id).first()
        payload = _serialize_journal_entry(journal, lines=lines, member=member)
        payload["lines"] = [
            {
                "id": str(line.id),
                "entry_type": str(line.entry_type),
                "direction": str(line.direction),
                "amount": _safe_decimal(line.amount),
                "debit": _safe_decimal(getattr(line, "debit", None) or Decimal("0.00")),
                "credit": _safe_decimal(getattr(line, "credit", None) or Decimal("0.00")),
                "status": _normalize_ledger_status(line.status),
                "provider": str(line.provider or "").strip() or None,
                "provider_reference": str(line.provider_reference or "").strip() or None,
                "account": {
                    "id": str(line.account_id) if line.account_id else None,
                    "code": str(line.account.code) if line.account_id and line.account else None,
                    "name": str(line.account.name) if line.account_id and line.account else None,
                },
                "related_payment_intent_id": str(line.related_payment_id) if line.related_payment_id else None,
                "related_loan_id": str(line.related_loan_id) if line.related_loan_id else None,
            }
            for line in lines
        ]
        payload["raw_metadata"] = journal.metadata or {}
        return {"transaction": payload}

    if transaction_ref.startswith("ledger_"):
        ledger_id = transaction_ref.replace("ledger_", "", 1)
        entry = (
            LedgerEntry.objects.select_related("wallet", "related_payment", "related_loan")
            .filter(id=ledger_id, chama_id=chama_id)
            .first()
        )
        if not entry:
            raise LedgerEntry.DoesNotExist
        member = None
        wallet = getattr(entry, "wallet", None)
        if wallet and str(getattr(wallet, "owner_type", "")).upper() == "USER":
            owner_id = str(getattr(wallet, "owner_id", "")).strip()
            if owner_id:
                member = User.objects.filter(id=owner_id).first()
        payload = _serialize_ledger_entry(entry, member=member)
        payload["raw_metadata"] = entry.meta or {}
        payload["related_payment_intent_id"] = str(entry.related_payment_id) if entry.related_payment_id else None
        payload["related_loan_id"] = str(entry.related_loan_id) if entry.related_loan_id else None
        return {"transaction": payload}

    raise ValueError("Unsupported transaction reference.")
