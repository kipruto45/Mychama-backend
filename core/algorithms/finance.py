from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal


def _money(value: Decimal | str | int | float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _add_months(base: date, offset: int) -> date:
    month_index = (base.month - 1) + offset
    year = base.year + (month_index // 12)
    month = (month_index % 12) + 1
    last_day = calendar.monthrange(year, month)[1]
    day = min(base.day, last_day)
    return date(year, month, day)


@dataclass(frozen=True)
class AmortizationInstallment:
    period: int
    due_date: date
    principal: Decimal
    interest: Decimal
    total: Decimal
    balance_after: Decimal


@dataclass(frozen=True)
class DelinquencyBucket:
    label: str
    min_days: int
    max_days: int | None


DELINQUENCY_BUCKETS = [
    DelinquencyBucket("current", 0, 0),
    DelinquencyBucket("dpd_1_30", 1, 30),
    DelinquencyBucket("dpd_31_60", 31, 60),
    DelinquencyBucket("dpd_61_90", 61, 90),
    DelinquencyBucket("dpd_90_plus", 91, None),
]


def classify_delinquency(days_past_due: int) -> str:
    dpd = max(0, int(days_past_due))
    for bucket in DELINQUENCY_BUCKETS:
        if bucket.max_days is None and dpd >= bucket.min_days:
            return bucket.label
        if bucket.max_days is not None and bucket.min_days <= dpd <= bucket.max_days:
            return bucket.label
    return "unknown"


def allocate_repayment(
    *,
    amount: Decimal,
    principal_due: Decimal,
    interest_due: Decimal,
    penalty_due: Decimal,
    strategy: str = "penalty_interest_principal",
) -> dict[str, Decimal]:
    remaining = _money(amount)
    principal = _money(principal_due)
    interest = _money(interest_due)
    penalty = _money(penalty_due)

    if remaining <= Decimal("0.00"):
        return {
            "penalty_paid": Decimal("0.00"),
            "interest_paid": Decimal("0.00"),
            "principal_paid": Decimal("0.00"),
            "unallocated": Decimal("0.00"),
        }

    order = (
        ["penalty", "interest", "principal"]
        if strategy == "penalty_interest_principal"
        else ["interest", "principal", "penalty"]
    )
    due_map = {
        "penalty": penalty,
        "interest": interest,
        "principal": principal,
    }
    paid_map = {
        "penalty": Decimal("0.00"),
        "interest": Decimal("0.00"),
        "principal": Decimal("0.00"),
    }

    for key in order:
        if remaining <= Decimal("0.00"):
            break
        take = min(remaining, due_map[key])
        paid_map[key] = _money(take)
        remaining = _money(remaining - take)

    return {
        "penalty_paid": paid_map["penalty"],
        "interest_paid": paid_map["interest"],
        "principal_paid": paid_map["principal"],
        "unallocated": _money(remaining),
    }


def generate_flat_amortization(
    *,
    principal: Decimal,
    annual_interest_rate_percent: Decimal,
    months: int,
    first_due_date: date,
) -> list[AmortizationInstallment]:
    n = max(1, int(months))
    principal_value = _money(principal)
    monthly_rate = (
        Decimal(str(annual_interest_rate_percent)) / Decimal("100") / Decimal("12")
    )
    total_interest = _money(principal_value * monthly_rate * Decimal(n))
    per_interest = _money(total_interest / Decimal(n))
    per_principal = _money(principal_value / Decimal(n))

    schedule: list[AmortizationInstallment] = []
    remaining = principal_value
    for idx in range(1, n + 1):
        interest = per_interest
        principal_component = per_principal
        if idx == n:
            principal_component = _money(remaining)
        total = _money(principal_component + interest)
        remaining = _money(max(Decimal("0.00"), remaining - principal_component))
        schedule.append(
            AmortizationInstallment(
                period=idx,
                due_date=_add_months(first_due_date, idx - 1),
                principal=principal_component,
                interest=interest,
                total=total,
                balance_after=remaining,
            )
        )
    return schedule


def generate_reducing_balance_amortization(
    *,
    principal: Decimal,
    annual_interest_rate_percent: Decimal,
    months: int,
    first_due_date: date,
) -> list[AmortizationInstallment]:
    n = max(1, int(months))
    principal_value = _money(principal)
    monthly_rate = (
        Decimal(str(annual_interest_rate_percent)) / Decimal("100") / Decimal("12")
    )
    if monthly_rate <= Decimal("0"):
        payment = _money(principal_value / Decimal(n))
    else:
        factor = (Decimal("1") + monthly_rate) ** n
        payment = _money(
            principal_value * (monthly_rate * factor) / (factor - Decimal("1"))
        )

    schedule: list[AmortizationInstallment] = []
    remaining = principal_value
    for idx in range(1, n + 1):
        interest = _money(remaining * monthly_rate)
        principal_component = _money(payment - interest)
        if idx == n:
            principal_component = _money(remaining)
            payment = _money(principal_component + interest)
        remaining = _money(max(Decimal("0.00"), remaining - principal_component))
        schedule.append(
            AmortizationInstallment(
                period=idx,
                due_date=_add_months(first_due_date, idx - 1),
                principal=principal_component,
                interest=interest,
                total=payment,
                balance_after=remaining,
            )
        )
    return schedule


def compute_par_ratio(
    *,
    loans: list[dict[str, Decimal | int | str]],
    days_threshold: int = 30,
) -> Decimal:
    threshold = max(1, int(days_threshold))
    total_outstanding = Decimal("0.00")
    at_risk = Decimal("0.00")
    for loan in loans:
        outstanding = _money(loan.get("outstanding", Decimal("0.00")))
        dpd = int(loan.get("days_past_due", 0))
        total_outstanding += outstanding
        if dpd >= threshold:
            at_risk += outstanding
    if total_outstanding <= Decimal("0.00"):
        return Decimal("0.00")
    return _money((at_risk / total_outstanding) * Decimal("100"))
