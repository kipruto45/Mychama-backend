from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from django.utils import timezone

from apps.chama.models import Chama
from apps.finance.models import Contribution, ContributionFrequency, ContributionType, Penalty
from apps.finance.serializers import ContributionSerializer
from apps.payments.unified_models import PaymentIntent, PaymentPurpose, PaymentStatus
from apps.payments.unified_serializers import (
    PaymentIntentResponseSerializer,
    PaymentReceiptSerializer,
)


ZERO = Decimal("0.00")
UPCOMING_WINDOW_DAYS = 7
PENDING_PAYMENT_STATES = {
    PaymentStatus.INITIATED,
    PaymentStatus.PENDING,
    PaymentStatus.PENDING_AUTHENTICATION,
    PaymentStatus.PENDING_VERIFICATION,
}

PAYABLE_PENALTY_STATES = {"unpaid", "partial"}


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value in (None, ""):
        return ZERO
    return Decimal(str(value))


def _date_to_iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _safe_month_date(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, monthrange(year, month)[1]))


def _add_months(value: date, months: int, day: int) -> date:
    month_index = (value.month - 1) + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return _safe_month_date(year, month, day)


def _first_due_date(anchor: date, frequency: str, due_day: int) -> date:
    normalized = str(frequency or ContributionFrequency.MONTHLY).lower()

    if normalized == ContributionFrequency.WEEKLY:
        anchor_weekday = ((max(due_day, 1) - 1) % 7)
        candidate = anchor + timedelta(days=(anchor_weekday - anchor.weekday()) % 7)
        if candidate < anchor:
            candidate += timedelta(days=7)
        return candidate

    month_step = 1
    if normalized == ContributionFrequency.QUARTERLY:
        month_step = 3
    elif normalized in {ContributionFrequency.ANNUALLY, "annual"}:
        month_step = 12

    candidate = _safe_month_date(anchor.year, anchor.month, due_day)
    while candidate < anchor:
        candidate = _add_months(candidate, month_step, due_day)
    return candidate


def _recurrence_window(*, today: date, anchor: date, frequency: str, due_day: int) -> tuple[date, date, date]:
    normalized = str(frequency or ContributionFrequency.MONTHLY).lower()
    first_due = _first_due_date(anchor, normalized, due_day)

    if normalized == ContributionFrequency.WEEKLY:
        if today <= first_due:
            last_due = first_due - timedelta(days=7)
            prior_due = last_due - timedelta(days=7)
            next_due = first_due
            return prior_due, last_due, next_due

        delta_days = (today - first_due).days
        steps = delta_days // 7
        candidate = first_due + timedelta(days=steps * 7)
        if candidate < today:
            last_due = candidate
            next_due = candidate + timedelta(days=7)
        else:
            last_due = candidate - timedelta(days=7)
            next_due = candidate
        prior_due = last_due - timedelta(days=7)
        return prior_due, last_due, next_due

    month_step = 1
    if normalized == ContributionFrequency.QUARTERLY:
        month_step = 3
    elif normalized in {ContributionFrequency.ANNUALLY, "annual"}:
        month_step = 12

    if today <= first_due:
        last_due = _add_months(first_due, -month_step, due_day)
        prior_due = _add_months(last_due, -month_step, due_day)
        return prior_due, last_due, first_due

    months_since_first = (today.year - first_due.year) * 12 + (today.month - first_due.month)
    steps = max(months_since_first // month_step, 0)
    candidate = _add_months(first_due, steps * month_step, due_day)

    if candidate >= today:
        next_due = candidate
        last_due = _add_months(candidate, -month_step, due_day)
    else:
        last_due = candidate
        next_due = _add_months(candidate, month_step, due_day)

    prior_due = _add_months(last_due, -month_step, due_day)
    return prior_due, last_due, next_due


def _sum_contributions_between(
    rows: list[Contribution],
    *,
    start: date | None,
    end: date | None,
) -> Decimal:
    total = ZERO
    for row in rows:
        if start and row.date_paid <= start:
            continue
        if end and row.date_paid > end:
            continue
        total += _to_decimal(row.net_amount)
    return total


def _serialize_member_penalty(penalty: Penalty) -> dict[str, Any]:
    return {
        "id": str(penalty.id),
        "category": "penalty",
        "amount": str(_to_decimal(penalty.amount)),
        "status": str(penalty.status).lower(),
        "due_date": penalty.due_date.isoformat(),
        "issued_reason": penalty.reason,
        "reason": penalty.reason,
        "outstanding_amount": str(_to_decimal(penalty.outstanding_amount)),
        "resolved_at": penalty.resolved_at.isoformat() if penalty.resolved_at else None,
        "created_at": penalty.created_at.isoformat() if penalty.created_at else None,
    }


def _build_schedule_rows(
    *,
    contribution_type: ContributionType,
    rows: list[Contribution],
    anchor: date,
    due_day: int,
    grace_period_days: int,
    today: date,
) -> list[dict[str, Any]]:
    prior_due, last_due, next_due = _recurrence_window(
        today=today,
        anchor=anchor,
        frequency=contribution_type.frequency,
        due_day=due_day,
    )

    normalized = str(contribution_type.frequency or ContributionFrequency.MONTHLY).lower()
    if normalized == ContributionFrequency.WEEKLY:
        step_back = lambda base, count=1: base - timedelta(days=7 * count)
        step_forward = lambda base, count=1: base + timedelta(days=7 * count)
    else:
        month_step = 1
        if normalized == ContributionFrequency.QUARTERLY:
            month_step = 3
        elif normalized in {ContributionFrequency.ANNUALLY, "annual"}:
            month_step = 12
        step_back = lambda base, count=1: _add_months(base, -(month_step * count), due_day)
        step_forward = lambda base, count=1: _add_months(base, month_step * count, due_day)

    cycle_due_dates = [
        step_back(last_due, 2),
        step_back(last_due, 1),
        last_due,
        next_due,
        step_forward(next_due, 1),
        step_forward(next_due, 2),
    ]

    schedule_rows: list[dict[str, Any]] = []
    for due in cycle_due_dates:
        prev_due = step_back(due, 1)
        paid_amount = _sum_contributions_between(rows, start=prev_due, end=due)
        required = _to_decimal(contribution_type.default_amount)
        remaining = max(required - paid_amount, ZERO)
        if due > today:
            status = "paid" if remaining <= ZERO else "upcoming"
        elif remaining <= ZERO:
            status = "paid"
        elif today > due + timedelta(days=grace_period_days):
            status = "overdue"
        else:
            status = "due"

        schedule_rows.append(
            {
                "id": f"{contribution_type.id}:{due.isoformat()}",
                "contribution_type_id": str(contribution_type.id),
                "contribution_type_name": contribution_type.name,
                "frequency": contribution_type.frequency,
                "cycle_label": due.strftime("%b %Y"),
                "due_date": due.isoformat(),
                "expected_amount": str(required),
                "paid_amount": str(paid_amount),
                "remaining_amount": str(remaining),
                "status": status,
            }
        )

    return schedule_rows


def _build_obligation(
    *,
    chama: Chama,
    contribution_type: ContributionType,
    rows: list[Contribution],
    anchor: date,
    due_day: int,
    grace_period_days: int,
    today: date,
) -> dict[str, Any]:
    prior_due, last_due, next_due = _recurrence_window(
        today=today,
        anchor=anchor,
        frequency=contribution_type.frequency,
        due_day=due_day,
    )

    required_amount = _to_decimal(contribution_type.default_amount)
    last_cycle_paid = _sum_contributions_between(rows, start=prior_due, end=last_due)
    current_cycle_paid = _sum_contributions_between(rows, start=last_due, end=next_due)

    if last_cycle_paid < required_amount and today > last_due + timedelta(days=grace_period_days):
        state = "overdue"
        due_date = last_due
        paid_amount = last_cycle_paid
        cycle_start = prior_due
    else:
        due_date = next_due
        paid_amount = current_cycle_paid
        cycle_start = last_due
        days_until_due = (due_date - today).days
        if paid_amount >= required_amount:
            state = "fully_paid"
        elif paid_amount > ZERO:
            state = "partially_paid"
        elif days_until_due < 0:
            state = "due"
        elif days_until_due <= UPCOMING_WINDOW_DAYS:
            state = "upcoming"
        else:
            state = "not_due_yet"

    remaining_amount = max(required_amount - paid_amount, ZERO)
    total_paid_all_time = sum((_to_decimal(row.net_amount) for row in rows), ZERO)
    latest_contribution = rows[0] if rows else None

    return {
        "contribution_type_id": str(contribution_type.id),
        "contribution_type_name": contribution_type.name,
        "frequency": contribution_type.frequency,
        "required_amount": str(required_amount),
        "paid_amount": str(paid_amount),
        "remaining_amount": str(remaining_amount),
        "total_paid": str(total_paid_all_time),
        "due_date": _date_to_iso(due_date),
        "cycle_start_date": _date_to_iso(cycle_start),
        "next_due_date": _date_to_iso(next_due),
        "state": state,
        "latest_payment_date": latest_contribution.date_paid.isoformat() if latest_contribution else None,
        "latest_receipt_code": latest_contribution.receipt_code if latest_contribution else None,
    }


def build_member_contribution_workspace(*, chama: Chama, member) -> dict[str, Any]:
    today = timezone.localdate()
    contribution_setting = getattr(chama, "contribution_setting", None)
    due_day = getattr(contribution_setting, "due_day", 1)
    grace_period_days = getattr(contribution_setting, "grace_period_days", 0)
    currency = getattr(getattr(chama, "finance_setting", None), "currency", "KES")
    anchor_date = (
        getattr(getattr(chama, "contribution_setting", None), "created_at", None)
        or chama.created_at
    ).date()

    contribution_types = list(
        ContributionType.objects.filter(chama=chama, is_active=True).order_by("name")
    )
    contributions = list(
        Contribution.objects.select_related("member", "recorded_by", "contribution_type")
        .filter(chama=chama, member=member)
        .order_by("-date_paid", "-created_at")
    )
    contributions_by_type: dict[str, list[Contribution]] = {}
    for row in contributions:
        contributions_by_type.setdefault(str(row.contribution_type_id), []).append(row)

    obligations = [
        _build_obligation(
            chama=chama,
            contribution_type=contribution_type,
            rows=contributions_by_type.get(str(contribution_type.id), []),
            anchor=anchor_date,
            due_day=due_day,
            grace_period_days=grace_period_days,
            today=today,
        )
        for contribution_type in contribution_types
    ]

    schedule_rows: list[dict[str, Any]] = []
    for contribution_type in contribution_types:
        schedule_rows.extend(
            _build_schedule_rows(
                contribution_type=contribution_type,
                rows=contributions_by_type.get(str(contribution_type.id), []),
                anchor=anchor_date,
                due_day=due_day,
                grace_period_days=grace_period_days,
                today=today,
            )
        )
    schedule_rows.sort(key=lambda item: item["due_date"])

    breakdown = [
        {
            "contribution_type_id": obligation["contribution_type_id"],
            "contribution_type_name": obligation["contribution_type_name"],
            "paid_total": obligation["total_paid"],
            "current_cycle_due": obligation["required_amount"],
            "current_cycle_paid": obligation["paid_amount"],
            "outstanding_amount": obligation["remaining_amount"],
            "state": obligation["state"],
        }
        for obligation in obligations
    ]

    penalties_queryset = list(
        Penalty.objects.filter(chama=chama, member=member).order_by("-due_date", "-created_at")
    )
    payable_penalties = [
        penalty for penalty in penalties_queryset if str(penalty.status).lower() in PAYABLE_PENALTY_STATES
    ]
    penalties_summary = {
        "count": len(penalties_queryset),
        "outstanding_total": str(
            sum((_to_decimal(penalty.outstanding_amount) for penalty in payable_penalties), ZERO)
        ),
        "items": [_serialize_member_penalty(penalty) for penalty in penalties_queryset[:5]],
    }

    recent_contributions = ContributionSerializer(contributions[:5], many=True).data
    upcoming_preview = sorted(
        (
            {
                "contribution_type_id": obligation["contribution_type_id"],
                "contribution_type_name": obligation["contribution_type_name"],
                "due_date": obligation["due_date"],
                "required_amount": obligation["required_amount"],
                "remaining_amount": obligation["remaining_amount"],
                "state": obligation["state"],
            }
            for obligation in obligations
            if obligation["due_date"]
        ),
        key=lambda item: item["due_date"],
    )[:4]

    total_contributed = sum((_to_decimal(row.net_amount) for row in contributions), ZERO)
    current_cycle_required = sum((_to_decimal(row["required_amount"]) for row in obligations), ZERO)
    current_cycle_paid = sum((_to_decimal(row["paid_amount"]) for row in obligations), ZERO)
    current_cycle_remaining = sum((_to_decimal(row["remaining_amount"]) for row in obligations), ZERO)

    upcoming_due_dates = [date.fromisoformat(item["due_date"]) for item in upcoming_preview if item["due_date"]]
    pending_payment = (
        PaymentIntent.objects.filter(
            chama=chama,
            user=member,
            purpose__in=[PaymentPurpose.CONTRIBUTION, PaymentPurpose.FINE, PaymentPurpose.SPECIAL_CONTRIBUTION],
            status__in=PENDING_PAYMENT_STATES,
        )
        .order_by("-created_at")
        .first()
    )

    return {
        "summary": {
            "currency": currency,
            "total_contributed": str(total_contributed),
            "current_cycle_amount": str(current_cycle_required),
            "remaining_balance": str(current_cycle_remaining),
            "next_due_date": _date_to_iso(min(upcoming_due_dates)) if upcoming_due_dates else None,
            "required_amount": str(current_cycle_required),
            "paid_amount": str(current_cycle_paid),
            "remaining_amount": str(current_cycle_remaining),
        },
        "obligations": obligations,
        "recent_contributions": recent_contributions,
        "upcoming_preview": upcoming_preview,
        "breakdown": breakdown,
        "schedule": schedule_rows,
        "penalties": penalties_summary,
        "pending_payment": PaymentIntentResponseSerializer(pending_payment).data if pending_payment else None,
        "rules": {
            "due_day": due_day,
            "grace_period_days": grace_period_days,
            "frequency": getattr(contribution_setting, "contribution_frequency", None),
            "default_amount": str(getattr(contribution_setting, "contribution_amount", ZERO)),
        },
    }


def build_member_contribution_detail(*, contribution: Contribution) -> dict[str, Any]:
    payment_intent = (
        PaymentIntent.objects.filter(contribution=contribution)
        .select_related("receipt")
        .order_by("-created_at")
        .first()
    )
    receipt = getattr(payment_intent, "receipt", None) if payment_intent else None

    return {
        "contribution": ContributionSerializer(contribution).data,
        "payment_intent": PaymentIntentResponseSerializer(payment_intent).data if payment_intent else None,
        "receipt": PaymentReceiptSerializer(receipt).data if receipt else None,
        "note": contribution.contribution_type.name if contribution.contribution_type_id else "",
        "cycle_month": contribution.date_paid.strftime("%B %Y"),
        "status": "success",
    }


def build_member_penalties(*, chama: Chama, member) -> list[dict[str, Any]]:
    penalties = Penalty.objects.filter(chama=chama, member=member).order_by("-due_date", "-created_at")
    return [_serialize_member_penalty(penalty) for penalty in penalties]
