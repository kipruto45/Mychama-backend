from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db.models import F, Sum
from django.utils import timezone

from apps.chama.models import Membership
from apps.finance.models import (
    ChamaFinancialSnapshot,
    Contribution,
    DailyAggregate,
    InstallmentSchedule,
    InstallmentStatus,
    LedgerEntry,
    LedgerEntryType,
    LedgerStatus,
    Loan,
    LoanStatus,
    Penalty,
    PenaltyStatus,
)

ACTIVE_LOAN_STATUSES = (
    LoanStatus.REQUESTED,
    LoanStatus.REVIEW,
    LoanStatus.APPROVED,
    LoanStatus.DISBURSING,
    LoanStatus.DISBURSED,
    LoanStatus.ACTIVE,
    LoanStatus.DEFAULTED,
)

REPAYMENT_ENTRY_TYPES = {
    LedgerEntryType.LOAN_REPAYMENT,
    "repayment",
}


def _decimal_sum(queryset, field_name: str) -> Decimal:
    return queryset.aggregate(total=Sum(field_name))["total"] or Decimal("0.00")


def _snapshot_defaults() -> dict:
    return {
        "summary_date": timezone.localdate(),
        "cash_in_total": Decimal("0.00"),
        "cash_out_total": Decimal("0.00"),
        "contributions_total": Decimal("0.00"),
        "withdrawals_total": Decimal("0.00"),
        "loan_disbursements_total": Decimal("0.00"),
        "loan_repayments_total": Decimal("0.00"),
        "penalties_total": Decimal("0.00"),
        "fees_total": Decimal("0.00"),
        "adjustments_total": Decimal("0.00"),
        "outstanding_loans_total": Decimal("0.00"),
        "active_loan_count": 0,
        "overdue_loan_count": 0,
        "unpaid_penalties_total": Decimal("0.00"),
        "unpaid_penalties_count": 0,
    }


def _update_daily_aggregate(chama_id, snapshot: ChamaFinancialSnapshot):
    today = timezone.localdate()
    daily_aggregate, _ = DailyAggregate.objects.get_or_create(
        chama_id=chama_id,
        date=today,
    )

    todays_ledger = LedgerEntry.objects.filter(
        chama_id=chama_id,
        status=LedgerStatus.SUCCESS,
        created_at__date=today,
    )
    todays_contributions = Contribution.objects.filter(
        chama_id=chama_id,
        date_paid=today,
    )
    active_member_count = Membership.objects.filter(
        chama_id=chama_id,
        is_active=True,
        is_approved=True,
    ).count()
    paid_member_count = (
        Contribution.objects.filter(
            chama_id=chama_id,
            date_paid__gte=today - timedelta(days=30),
            refunded_amount__lt=F("amount"),
        )
        .values("member_id")
        .distinct()
        .count()
    )

    total_contributions = (
        todays_contributions.aggregate(
            total=Sum(F("amount") - F("refunded_amount"))
        )["total"]
        or Decimal("0.00")
    )
    total_withdrawals = _decimal_sum(
        todays_ledger.filter(entry_type=LedgerEntryType.WITHDRAWAL),
        "amount",
    )
    total_disbursed_loans = _decimal_sum(
        todays_ledger.filter(entry_type=LedgerEntryType.LOAN_DISBURSEMENT),
        "amount",
    )
    total_loan_repayments = _decimal_sum(
        todays_ledger.filter(entry_type__in=REPAYMENT_ENTRY_TYPES),
        "amount",
    )
    total_fines = _decimal_sum(
        todays_ledger.filter(entry_type=LedgerEntryType.PENALTY),
        "amount",
    )

    net_cashflow = (
        total_contributions
        + total_loan_repayments
        + total_fines
        - total_withdrawals
        - total_disbursed_loans
    )

    DailyAggregate.objects.filter(pk=daily_aggregate.pk).update(
        total_contributions=total_contributions,
        total_withdrawals=total_withdrawals,
        total_disbursed_loans=total_disbursed_loans,
        total_loan_repayments=total_loan_repayments,
        total_fines=total_fines,
        contribution_count=todays_contributions.count(),
        withdrawal_count=todays_ledger.filter(
            entry_type=LedgerEntryType.WITHDRAWAL
        ).count(),
        active_member_count=active_member_count,
        active_loan_count=snapshot.active_loan_count,
        overdue_loan_count=snapshot.overdue_loan_count,
        unpaid_member_count=max(active_member_count - paid_member_count, 0),
        net_cashflow=net_cashflow,
        updated_at=timezone.now(),
    )
    cache.set(
        f"daily_aggregate:{chama_id}:{today}",
        {
            "total_contributions": float(total_contributions),
            "total_withdrawals": float(total_withdrawals),
            "net_cashflow": float(net_cashflow),
            "active_member_count": active_member_count,
            "active_loan_count": snapshot.active_loan_count,
            "unpaid_member_count": max(active_member_count - paid_member_count, 0),
        },
        300,
    )


def _ensure_snapshot_row(chama_id):
    snapshot, _ = ChamaFinancialSnapshot.objects.get_or_create(
        chama_id=chama_id,
        defaults=_snapshot_defaults(),
    )
    today = timezone.localdate()
    if snapshot.summary_date != today:
        ChamaFinancialSnapshot.objects.filter(pk=snapshot.pk).update(
            summary_date=today,
            updated_at=timezone.now(),
        )
        snapshot.summary_date = today
    return snapshot


def _cash_flow_updates_for_entry(entry: LedgerEntry) -> dict:
    amount = entry.amount
    updates = {}
    meta = entry.meta if isinstance(entry.meta, dict) else {}

    if entry.entry_type == LedgerEntryType.CONTRIBUTION:
        updates["cash_in_total"] = F("cash_in_total") + amount
        updates["contributions_total"] = F("contributions_total") + amount
    elif entry.entry_type == LedgerEntryType.WALLET_TOPUP:
        updates["cash_in_total"] = F("cash_in_total") + amount
    elif entry.entry_type in REPAYMENT_ENTRY_TYPES:
        updates["cash_in_total"] = F("cash_in_total") + amount
        updates["loan_repayments_total"] = F("loan_repayments_total") + amount
    elif entry.entry_type == LedgerEntryType.WITHDRAWAL:
        updates["cash_out_total"] = F("cash_out_total") + amount
        updates["withdrawals_total"] = F("withdrawals_total") + amount
    elif entry.entry_type == LedgerEntryType.LOAN_DISBURSEMENT:
        updates["cash_out_total"] = F("cash_out_total") + amount
        updates["loan_disbursements_total"] = F("loan_disbursements_total") + amount
    elif entry.entry_type == LedgerEntryType.PENALTY:
        updates["penalties_total"] = F("penalties_total") + amount
        if entry.direction == "credit":
            updates["cash_in_total"] = F("cash_in_total") + amount
    elif entry.entry_type == LedgerEntryType.FEE:
        updates["fees_total"] = F("fees_total") + amount
        if entry.direction == "credit":
            updates["cash_in_total"] = F("cash_in_total") + amount
        elif entry.direction == "debit":
            updates["cash_out_total"] = F("cash_out_total") + amount
    elif entry.entry_type == LedgerEntryType.ADJUSTMENT:
        updates["adjustments_total"] = F("adjustments_total") + amount
        if meta.get("adjustment_reason") == "contribution_refund":
            updates["cash_out_total"] = F("cash_out_total") + amount
            updates["contributions_total"] = F("contributions_total") - amount

    return updates


def refresh_snapshot_derived_metrics(chama_id):
    snapshot = _ensure_snapshot_row(chama_id)

    outstanding_loans_total = _decimal_sum(
        InstallmentSchedule.objects.filter(
            loan__chama_id=chama_id,
            loan__status__in=ACTIVE_LOAN_STATUSES,
        ).exclude(status=InstallmentStatus.PAID),
        "expected_amount",
    )
    active_loan_count = Loan.objects.filter(
        chama_id=chama_id,
        status__in=ACTIVE_LOAN_STATUSES,
    ).count()
    overdue_loan_count = (
        InstallmentSchedule.objects.filter(
            loan__chama_id=chama_id,
            loan__status__in=ACTIVE_LOAN_STATUSES,
            status=InstallmentStatus.OVERDUE,
        )
        .values("loan_id")
        .distinct()
        .count()
    )
    unpaid_penalties_qs = Penalty.objects.filter(
        chama_id=chama_id,
        status__in=[PenaltyStatus.UNPAID, PenaltyStatus.PARTIAL],
    )
    unpaid_penalties_total = (
        unpaid_penalties_qs.aggregate(total=Sum(F("amount") - F("amount_paid")))["total"]
        or Decimal("0.00")
    )

    ChamaFinancialSnapshot.objects.filter(pk=snapshot.pk).update(
        summary_date=timezone.localdate(),
        outstanding_loans_total=outstanding_loans_total,
        active_loan_count=active_loan_count,
        overdue_loan_count=overdue_loan_count,
        unpaid_penalties_total=unpaid_penalties_total,
        unpaid_penalties_count=unpaid_penalties_qs.count(),
        updated_at=timezone.now(),
    )
    snapshot.refresh_from_db()
    _update_daily_aggregate(chama_id, snapshot)


def apply_ledger_entry_to_snapshot(entry: LedgerEntry):
    if entry.status != LedgerStatus.SUCCESS:
        return

    snapshot = _ensure_snapshot_row(entry.chama_id)
    updates = _cash_flow_updates_for_entry(entry)
    if updates:
        updates["summary_date"] = timezone.localdate()
        updates["updated_at"] = timezone.now()
        ChamaFinancialSnapshot.objects.filter(pk=snapshot.pk).update(**updates)

    refresh_snapshot_derived_metrics(entry.chama_id)


def rebuild_chama_financial_snapshot(chama_id):
    snapshot = _ensure_snapshot_row(chama_id)
    successful_entries = LedgerEntry.objects.filter(
        chama_id=chama_id,
        status=LedgerStatus.SUCCESS,
    )

    contribution_entries = successful_entries.filter(
        entry_type=LedgerEntryType.CONTRIBUTION
    )
    withdrawal_entries = successful_entries.filter(
        entry_type=LedgerEntryType.WITHDRAWAL
    )
    loan_disbursement_entries = successful_entries.filter(
        entry_type=LedgerEntryType.LOAN_DISBURSEMENT
    )
    loan_repayment_entries = successful_entries.filter(
        entry_type__in=REPAYMENT_ENTRY_TYPES
    )
    penalty_entries = successful_entries.filter(entry_type=LedgerEntryType.PENALTY)
    fee_entries = successful_entries.filter(entry_type=LedgerEntryType.FEE)
    adjustment_entries = successful_entries.filter(entry_type=LedgerEntryType.ADJUSTMENT)
    contribution_refund_entries = adjustment_entries.filter(
        meta__adjustment_reason="contribution_refund"
    )

    cash_in_total = (
        _decimal_sum(contribution_entries, "amount")
        + _decimal_sum(
            successful_entries.filter(entry_type=LedgerEntryType.WALLET_TOPUP),
            "amount",
        )
        + _decimal_sum(loan_repayment_entries, "amount")
        + _decimal_sum(fee_entries.filter(direction="credit"), "amount")
        + _decimal_sum(penalty_entries.filter(direction="credit"), "amount")
    )
    cash_out_total = (
        _decimal_sum(withdrawal_entries, "amount")
        + _decimal_sum(loan_disbursement_entries, "amount")
        + _decimal_sum(fee_entries.filter(direction="debit"), "amount")
        + _decimal_sum(contribution_refund_entries, "amount")
    )

    ChamaFinancialSnapshot.objects.filter(pk=snapshot.pk).update(
        summary_date=timezone.localdate(),
        cash_in_total=cash_in_total,
        cash_out_total=cash_out_total,
        contributions_total=(
            _decimal_sum(contribution_entries, "amount")
            - _decimal_sum(contribution_refund_entries, "amount")
        ),
        withdrawals_total=_decimal_sum(withdrawal_entries, "amount"),
        loan_disbursements_total=_decimal_sum(loan_disbursement_entries, "amount"),
        loan_repayments_total=_decimal_sum(loan_repayment_entries, "amount"),
        penalties_total=_decimal_sum(penalty_entries, "amount"),
        fees_total=_decimal_sum(fee_entries, "amount"),
        adjustments_total=_decimal_sum(adjustment_entries, "amount"),
        updated_at=timezone.now(),
    )
    refresh_snapshot_derived_metrics(chama_id)
    return ChamaFinancialSnapshot.objects.get(pk=snapshot.pk)


def get_chama_financial_snapshot(chama):
    snapshot = ChamaFinancialSnapshot.objects.filter(chama=chama).first()
    if snapshot is None:
        return rebuild_chama_financial_snapshot(chama.id)
    if snapshot.summary_date != timezone.localdate():
        ChamaFinancialSnapshot.objects.filter(pk=snapshot.pk).update(
            summary_date=timezone.localdate(),
            updated_at=timezone.now(),
        )
        snapshot.summary_date = timezone.localdate()
    return snapshot
