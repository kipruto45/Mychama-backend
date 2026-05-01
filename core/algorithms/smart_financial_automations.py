"""
Smart Financial Intelligence Automations

Production-grade automations for:
- NPL (Non-Performing Loan) auto-tagging (30/60/90 days overdue)
- Overpayment detection and auto-crediting
- Variance alerts for unexpected transactions
- Reconciliation auto-runner
- Cash flow forecasting
- Investment opportunity alerts
- Chama credit score auto-updater
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from django.conf import settings
from django.db.models import Count, Q, Sum
from django.utils import timezone

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from apps.chama.models import Chama
    from apps.finance.models import Loan, Payment


@dataclass
class NPLBucket:
    """Non-Performing Loan bucket classification."""
    label: str
    min_days: int
    max_days: int | None


NPL_BUCKETS = [
    NPLBucket("current", 0, 0),
    NPLBucket("dpd_1_30", 1, 30),
    NPLBucket("dpd_31_60", 31, 60),
    NPLBucket("dpd_61_90", 61, 90),
    NPLBucket("dpd_90_plus", 91, None),
]


def tag_loan_delinquency(loan: "Loan") -> str:
    """Tag a loan with its current delinquency status."""
    if loan.status in ("paid", "written_off", "discharged"):
        return "current"
    
    if not loan.next_due_date:
        return "current"
    
    today = timezone.now().date()
    days_overdue = (today - loan.next_due_date).days
    
    if days_overdue <= 0:
        return "current"
    
    for bucket in NPL_BUCKETS:
        if bucket.max_days is None and days_overdue >= bucket.min_days:
            return bucket.label
        if bucket.max_days is not None and bucket.min_days <= days_overdue <= bucket.max_days:
            return bucket.label
    
    return "dpd_90_plus"


def get_npl_loans(chama: "Chama", days_threshold: int = 30) -> list[dict]:
    """Get all NPL loans above threshold for a chama."""
    from apps.finance.models import Loan, LoanStatus
    
    today = timezone.now().date()
    threshold_date = today - timedelta(days=days_threshold)
    
    npl_loans = Loan.objects.filter(
        chama=chama,
        status=LoanStatus.ACTIVE,
    ).select_related("borrower").annotate(
        total_paid=Sum("repayments__amount"),
    )
    
    results = []
    for loan in npl_loans:
        if loan.next_due_date and loan.next_due_date <= threshold_date:
            days_overdue = (today - loan.next_due_date).days
            results.append({
                "loan_id": str(loan.id),
                "borrower_id": str(loan.borrower_id),
                "borrower_name": loan.borrower.full_name if loan.borrower else "Unknown",
                "principal": loan.principal_amount,
                "outstanding": loan.outstanding_balance,
                "days_overdue": days_overdue,
                "delinquency_bucket": tag_loan_delinquency(loan),
                "next_due_date": loan.next_due_date,
                "status": loan.status,
            })
    
    return sorted(results, key=lambda x: x["days_overdue"], reverse=True)


def compute_par_ratio(chama: "Chama", days_threshold: int = 30) -> Decimal:
    """Compute Portfolio at Risk ratio for a chama."""
    from apps.finance.models import Loan, LoanStatus
    
    total_outstanding = Decimal("0.00")
    at_risk = Decimal("0.00")
    
    today = timezone.now().date()
    threshold_date = today - timedelta(days=days_threshold)
    
    active_loans = Loan.objects.filter(
        chama=chama,
        status=LoanStatus.ACTIVE,
    )
    
    for loan in active_loans:
        outstanding = loan.outstanding_balance or Decimal("0.00")
        total_outstanding += outstanding
        
        if loan.next_due_date and loan.next_due_date <= threshold_date:
            at_risk += outstanding
    
    if total_outstanding <= Decimal("0.00"):
        return Decimal("0.00")
    
    return (at_risk / total_outstanding) * Decimal("100")


@dataclass
class OverpaymentResult:
    """Result of overpayment detection."""
    is_overpayment: bool
    excess_amount: Decimal
    credit_to_next_cycle: bool
    message: str


def detect_overpayment(
    payment_amount: Decimal,
    expected_amount: Decimal,
    credit_to_next_cycle: bool = True,
) -> OverpaymentResult:
    """Detect overpayment and optionally credit excess to next cycle."""
    excess = payment_amount - expected_amount
    
    if excess <= Decimal("0.00"):
        return OverpaymentResult(
            is_overpayment=False,
            excess_amount=Decimal("0.00"),
            credit_to_next_cycle=False,
            message="Payment is exactly on or below expected amount.",
        )
    
    credit_amount = excess if credit_to_next_cycle else Decimal("0.00")
    
    return OverpaymentResult(
        is_overpayment=True,
        excess_amount=excess,
        credit_to_next_cycle=credit_to_next_cycle,
        message=f"Overpayment of {excess} detected. {'Credit to next cycle.' if credit_to_next_cycle else 'Refund required.'}",
    )


@dataclass
class VarianceAlert:
    """Transaction variance alert."""
    chama_id: str
    transaction_type: str
    expected_amount: Decimal
    actual_amount: Decimal
    variance: Decimal
    variance_percent: float
    is_alert: bool
    severity: str


def check_variance_alert(
    *,
    expected_amount: Decimal,
    actual_amount: Decimal,
    variance_threshold_percent: float = 20.0,
) -> VarianceAlert:
    """Check if transaction amount deviates significantly from expected."""
    variance = actual_amount - expected_amount
    variance_pct = float("0.00")
    
    if expected_amount > Decimal("0.00"):
        variance_pct = abs(float(variance) / float(expected_amount)) * 100
    
    is_alert = variance_pct >= variance_threshold_percent
    
    if variance_pct >= 50:
        severity = "HIGH"
    elif variance_pct >= 30:
        severity = "MEDIUM"
    elif variance_pct >= 20:
        severity = "LOW"
    else:
        severity = "NONE"
    
    return VarianceAlert(
        chama_id="",
        transaction_type="",
        expected_amount=expected_amount,
        actual_amount=actual_amount,
        variance=abs(variance),
        variance_percent=variance_pct,
        is_alert=is_alert,
        severity=severity,
    )


@dataclass
class CashFlowForecast:
    """Cash flow forecast result."""
    date: date
    expected_inflow: Decimal
    expected_outflow: Decimal
    net_flow: Decimal
    running_balance: Decimal
    confidence: float


def generate_cash_flow_forecast(
    chama: "Chama",
    days_ahead: int = 7,
) -> list[CashFlowForecast]:
    """Generate weekly cash flow forecast for a chama."""
    from apps.finance.models import ContributionSchedule, Loan, LoanStatus, Payout
    
    forecasts = []
    today = timezone.now().date()
    current_balance = Decimal("0.00")
    
    try:
        from apps.finance.services import FinanceService
        current_balance = FinanceService.get_wallet_balance(chama) or Decimal("0.00")
    except Exception:
        pass
    
    for day_offset in range(days_ahead):
        forecast_date = today + timedelta(days=day_offset)
        
        expected_contributions = ContributionSchedule.objects.filter(
            chama=chama,
            due_date=forecast_date,
            is_active=True,
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        
        expected_payouts = Payout.objects.filter(
            chama=chama,
            scheduled_date=forecast_date,
            status__in=["pending", "approved"],
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        
        expected_loan_disbursements = Loan.objects.filter(
            chama=chama,
            disbursed_at__date=forecast_date,
            status=LoanStatus.ACTIVE,
        ).aggregate(total=Sum("principal_amount"))["total"] or Decimal("0.00")
        
        inflow = expected_contributions
        outflow = expected_payouts + expected_loan_disbursements
        net_flow = inflow - outflow
        running_balance = current_balance + net_flow
        
        forecasts.append(CashFlowForecast(
            date=forecast_date,
            expected_inflow=inflow,
            expected_outflow=outflow,
            net_flow=net_flow,
            running_balance=running_balance,
            confidence=0.85,
        ))
        
        current_balance = running_balance
    
    return forecasts


@dataclass
class InvestmentOpportunity:
    """Investment opportunity alert."""
    chama_id: str
    current_balance: Decimal
    threshold: Decimal
    excess_balance: Decimal
    suggested_investment: str
    alert_level: str


def check_investment_opportunity(
    chama: "Chama",
    balance_threshold: Decimal | None = None,
) -> InvestmentOpportunity | None:
    """Check if chama pool balance exceeds threshold for investment."""
    threshold = balance_threshold or getattr(
        settings, "INVESTMENT_BALANCE_THRESHOLD", Decimal("50000.00")
    )
    
    current_balance = Decimal("0.00")
    try:
        from apps.finance.services import FinanceService
        current_balance = FinanceService.get_wallet_balance(chama) or Decimal("0.00")
    except Exception:
        pass
    
    if current_balance <= threshold:
        return None
    
    excess = current_balance - threshold
    
    if excess >= threshold:
        alert_level = "HIGH"
        suggestion = "Consider term deposit or money market fund"
    elif excess >= threshold * Decimal("0.5"):
        alert_level = "MEDIUM"
        suggestion = "Build reserve before investing"
    else:
        alert_level = "LOW"
        suggestion = "Maintain as operational reserve"
    
    return InvestmentOpportunity(
        chama_id=str(chama.id),
        current_balance=current_balance,
        threshold=threshold,
        excess_balance=excess,
        suggested_investment=suggestion,
        alert_level=alert_level,
    )


@dataclass
class ChamaCreditScore:
    """Chama credit score result."""
    chama_id: str
    score: int
    rating: str
    collection_rate: Decimal
    loan_repayment_rate: Decimal
    meeting_attendance: Decimal
    governance_score: Decimal
    factors: dict


def calculate_chama_credit_score(chama: "Chama") -> ChamaCreditScore:
    """Calculate chama credit score based on financial health."""
    from apps.finance.models import Loan, LoanStatus
    from apps.meetings.models import Meeting
    
    collection_rate = Decimal("100.00")
    loan_repayment_rate = Decimal("100.00")
    meeting_attendance = Decimal("100.00")
    governance_score = Decimal("100.00")
    factors = {}
    
    recent_contributions = chama.contributions.filter(
        created_at__gte=timezone.now() - timedelta(days=90)
    ).count()
    expected_contributions = chama.memberships.filter(
        is_active=True,
    ).count() * 3
    
    if expected_contributions > 0:
        collection_rate = Decimal(str(
            min(100, (recent_contributions / expected_contributions) * 100)
        ))
        factors["collection_rate"] = float(collection_rate)
    
    active_loans = Loan.objects.filter(
        chama=chama,
        status=LoanStatus.ACTIVE,
    )
    npl_count = 0
    total_count = active_loans.count()
    
    if total_count > 0:
        today = timezone.now().date()
        threshold_date = today - timedelta(days=30)
        npl_count = active_loans.filter(
            next_due_date__lte=threshold_date,
        ).count()
        loan_repayment_rate = Decimal(str(
            max(0, 100 - (npl_count / total_count) * 100)
        ))
        factors["loan_repayment_rate"] = float(loan_repayment_rate)
    
    meetings = Meeting.objects.filter(
        chama=chama,
        date__gte=timezone.now() - timedelta(days=180),
    )
    total_meetings = meetings.count()
    if total_meetings > 0:
        total_attendance = sum(
            m.attendance_records.filter(
                status="present"
            ).count()
            for m in meetings
        )
        expected_attendance = total_meetings * chama.memberships.filter(
            is_active=True,
        ).count()
        if expected_attendance > 0:
            meeting_attendance = Decimal(str(
                min(100, (total_attendance / expected_attendance) * 100)
            ))
            factors["meeting_attendance"] = float(meeting_attendance)
    
    par_ratio = float(compute_par_ratio(chama))
    par_score = max(0, 100 - par_ratio * 100)
    
    overall_score = int((
        collection_rate * Decimal("0.30") +
        loan_repayment_rate * Decimal("0.30") +
        meeting_attendance * Decimal("0.20") +
        Decimal(str(par_score)) * Decimal("0.20")
    ))
    
    if overall_score >= 90:
        rating = "AAA"
    elif overall_score >= 80:
        rating = "AA"
    elif overall_score >= 70:
        rating = "A"
    elif overall_score >= 60:
        rating = "BB"
    elif overall_score >= 50:
        rating = "B"
    else:
        rating = "C"
    
    return ChamaCreditScore(
        chama_id=str(chama.id),
        score=overall_score,
        rating=rating,
        collection_rate=collection_rate,
        loan_repayment_rate=loan_repayment_rate,
        meeting_attendance=meeting_attendance,
        governance_score=governance_score,
        factors=factors,
    )


@dataclass
class ReconciliationResult:
    """Reconciliation result."""
    matched_count: int
    unmatched_count: int
    total_expected: Decimal
    total_received: Decimal
    variance: Decimal
    matched_transactions: list


def run_daily_reconciliation(chama: "Chama") -> ReconciliationResult:
    """Run daily reconciliation for a chama."""
    from apps.finance.models import ContributionSchedule, Payment, PaymentStatus
    
    today = timezone.now().date()
    
    expected = ContributionSchedule.objects.filter(
        chama=chama,
        due_date=today,
        is_active=True,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    
    received = Payment.objects.filter(
        chama=chama,
        payment_date__date=today,
        status=PaymentStatus.COMPLETED,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    
    expected_payments = ContributionSchedule.objects.filter(
        chama=chama,
        due_date=today,
        is_active=True,
    ).values_list("member_id", "amount")
    
    received_payments = Payment.objects.filter(
        chama=chama,
        payment_date__date=today,
        status=PaymentStatus.COMPLETED,
    ).values_list("member_id", "amount")
    
    expected_map = {str(mid): amt for mid, amt in expected_payments}
    received_map = {str(mid): amt for mid, amt in received_payments}
    
    matched = set(expected_map.keys()) & set(received_map.keys())
    unmatched_expected = set(expected_map.keys()) - matched
    unmatched_received = set(received_map.keys()) - matched
    
    matched_total = Decimal("0.00")
    for mid in matched:
        matched_total += expected_map[mid]
    
    variance = received - expected
    
    return ReconciliationResult(
        matched_count=len(matched),
        unmatched_count=len(unmatched_expected) + len(unmatched_received),
        total_expected=expected,
        total_received=received,
        variance=variance,
        matched_transactions=list(matched),
    )