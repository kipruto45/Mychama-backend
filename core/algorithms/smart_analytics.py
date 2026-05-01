"""
Smart Analytics Engine for MyChama

Provides intelligent analytics, forecasting, and decision support:
- Cashflow forecasting
- Contribution trend analysis
- Expense pattern detection
- Member engagement analytics
- Loan risk assessment
- Monthly AI summaries
- Anomaly detection
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from django.utils import timezone

logger = logging.getLogger(__name__)


@dataclass
class CashflowForecast:
    """Cashflow prediction for upcoming periods"""
    chama_id: str
    forecast_period: str  # "monthly", "quarterly"
    predicted_income: Decimal
    predicted_expenses: Decimal
    predicted_balance: Decimal
    confidence_level: Decimal  # 0-100%
    factors: dict = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=timezone.now)


@dataclass
class ContributionTrend:
    """Contribution trend analysis"""
    chama_id: str
    period: str
    total_contributions: Decimal
    average_per_member: Decimal
    completion_rate: Decimal
    trend_direction: str  # "up", "down", "stable"
    trend_percentage: Decimal
    at_risk_members: int
    generated_at: datetime = field(default_factory=timezone.now)


@dataclass
class ExpensePattern:
    """Expense pattern detection"""
    chama_id: str
    category: str
    total_amount: Decimal
    percentage_of_total: Decimal
    trend: str  # "increasing", "decreasing", "stable"
    anomaly_detected: bool = False
    anomaly_reason: str | None = None
    generated_at: datetime = field(default_factory=timezone.now)


@dataclass
class MemberEngagement:
    """Member engagement analytics"""
    chama_id: str
    total_members: int
    active_members: int
    engagement_score: Decimal  # 0-100
    top_contributors: list[dict] = field(default_factory=list)
    inactive_members: list[dict] = field(default_factory=list)
    new_members_this_month: int = 0
    generated_at: datetime = field(default_factory=timezone.now)


@dataclass
class LoanRiskAssessment:
    """Loan portfolio risk assessment"""
    chama_id: str
    total_loans: int
    total_outstanding: Decimal
    overdue_loans: int
    overdue_amount: Decimal
    default_risk_score: int  # 0-100
    high_risk_loans: list[dict] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=timezone.now)


@dataclass
class MonthlyAISummary:
    """AI-generated monthly summary"""
    chama_id: str
    month: str
    summary_text: str
    key_metrics: dict = field(default_factory=dict)
    highlights: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=timezone.now)


@dataclass
class AnomalyAlert:
    """Anomaly detection alert"""
    alert_type: str
    severity: str  # "low", "medium", "high", "critical"
    description: str
    affected_entity: str
    entity_type: str
    detected_value: Decimal
    expected_range: tuple[Decimal, Decimal]
    metadata: dict = field(default_factory=dict)
    detected_at: datetime = field(default_factory=timezone.now)


# ============================================================================
# CASHFLOW FORECASTING
# ============================================================================

def forecast_cashflow(
    chama_id: str,
    historical_contributions: list[dict],
    historical_expenses: list[dict],
    active_loans: list[dict],
    forecast_months: int = 3,
) -> CashflowForecast:
    """
    Predict future cashflow based on historical patterns.
    
    Uses:
    - Average monthly contributions
    - Seasonal patterns
    - Scheduled loan repayments
    - Recurring expenses
    """
    # Calculate average monthly contributions
    if historical_contributions:
        monthly_totals = {}
        for contrib in historical_contributions:
            month_key = contrib.get("month", "")
            amount = Decimal(str(contrib.get("amount", 0)))
            monthly_totals[month_key] = monthly_totals.get(month_key, Decimal("0")) + amount
        
        if monthly_totals:
            avg_monthly_income = sum(monthly_totals.values()) / len(monthly_totals)
        else:
            avg_monthly_income = Decimal("0")
    else:
        avg_monthly_income = Decimal("0")
    
    # Calculate average monthly expenses
    if historical_expenses:
        monthly_expenses = {}
        for expense in historical_expenses:
            month_key = expense.get("month", "")
            amount = Decimal(str(expense.get("amount", 0)))
            monthly_expenses[month_key] = monthly_expenses.get(month_key, Decimal("0")) + amount
        
        if monthly_expenses:
            avg_monthly_expenses = sum(monthly_expenses.values()) / len(monthly_expenses)
        else:
            avg_monthly_expenses = Decimal("0")
    else:
        avg_monthly_expenses = Decimal("0")
    
    # Calculate expected loan repayments
    monthly_loan_repayments = Decimal("0")
    for loan in active_loans:
        monthly_payment = Decimal(str(loan.get("monthly_payment", 0)))
        monthly_loan_repayments += monthly_payment
    
    # Forecast
    predicted_income = avg_monthly_income * forecast_months
    predicted_expenses = (avg_monthly_expenses + monthly_loan_repayments) * forecast_months
    predicted_balance = predicted_income - predicted_expenses
    
    # Confidence level based on data quality
    data_points = len(historical_contributions) + len(historical_expenses)
    confidence = min(95, 50 + (data_points * 2))
    
    # Recommendations
    recommendations = []
    if predicted_balance < 0:
        recommendations.append("Projected negative balance - review expenses and loan exposure")
    if avg_monthly_expenses > avg_monthly_income * Decimal("0.8"):
        recommendations.append("Expenses are high relative to income - consider cost control")
    
    return CashflowForecast(
        chama_id=chama_id,
        forecast_period="monthly",
        predicted_income=predicted_income,
        predicted_expenses=predicted_expenses,
        predicted_balance=predicted_balance,
        confidence_level=Decimal(str(confidence)),
        factors={
            "avg_monthly_income": str(avg_monthly_income),
            "avg_monthly_expenses": str(avg_monthly_expenses),
            "monthly_loan_repayments": str(monthly_loan_repayments),
            "data_points": data_points,
        },
        recommendations=recommendations,
    )


# ============================================================================
# CONTRIBUTION TREND ANALYSIS
# ============================================================================

def analyze_contribution_trends(
    chama_id: str,
    contributions: list[dict],
    members: list[dict],
    periods: int = 6,
) -> ContributionTrend:
    """
    Analyze contribution patterns and trends.
    
    Detects:
    - Overall trend direction
    - Completion rates
    - At-risk members
    """
    # Group by month
    monthly_data = {}
    for contrib in contributions:
        month_key = contrib.get("month", "")
        if month_key not in monthly_data:
            monthly_data[month_key] = {
                "total": Decimal("0"),
                "count": 0,
                "completed": 0,
            }
        monthly_data[month_key]["total"] += Decimal(str(contrib.get("amount", 0)))
        monthly_data[month_key]["count"] += 1
        if contrib.get("status") == "paid":
            monthly_data[month_key]["completed"] += 1
    
    # Calculate trend
    sorted_months = sorted(monthly_data.keys())
    if len(sorted_months) >= 2:
        recent_total = monthly_data[sorted_months[-1]]["total"]
        previous_total = monthly_data[sorted_months[-2]]["total"]
        
        if previous_total > 0:
            trend_pct = ((recent_total - previous_total) / previous_total * 100).quantize(Decimal("0.01"))
            if trend_pct > 5:
                direction = "up"
            elif trend_pct < -5:
                direction = "down"
            else:
                direction = "stable"
        else:
            trend_pct = Decimal("0")
            direction = "stable"
    else:
        trend_pct = Decimal("0")
        direction = "stable"
    
    # Calculate completion rate
    total_expected = sum(data["count"] for data in monthly_data.values())
    total_completed = sum(data["completed"] for data in monthly_data.values())
    completion_rate = (Decimal(str(total_completed)) / Decimal(str(total_expected)) * 100).quantize(Decimal("0.01")) if total_expected > 0 else Decimal("0")
    
    # Identify at-risk members
    at_risk_count = 0
    for member in members:
        member_contribs = [c for c in contributions if c.get("member_id") == member.get("id")]
        if member_contribs:
            member_completed = sum(1 for c in member_contribs if c.get("status") == "paid")
            member_rate = member_completed / len(member_contribs) if member_contribs else 0
            if member_rate < 0.7:
                at_risk_count += 1
    
    # Average per member
    total_amount = sum(data["total"] for data in monthly_data.values())
    avg_per_member = total_amount / len(members) if members else Decimal("0")
    
    return ContributionTrend(
        chama_id=chama_id,
        period=f"last_{periods}_months",
        total_contributions=total_amount,
        average_per_member=avg_per_member,
        completion_rate=completion_rate,
        trend_direction=direction,
        trend_percentage=trend_pct,
        at_risk_members=at_risk_count,
    )


# ============================================================================
# EXPENSE PATTERN DETECTION
# ============================================================================

def detect_expense_patterns(
    chama_id: str,
    expenses: list[dict],
    historical_avg: dict[str, Decimal],
) -> list[ExpensePattern]:
    """
    Detect expense patterns and anomalies.
    
    Identifies:
    - Category-wise spending
    - Unusual spikes
    - Budget overruns
    """
    patterns = []
    
    # Group by category
    category_totals = {}
    total_expenses = Decimal("0")
    
    for expense in expenses:
        category = expense.get("category", "uncategorized")
        amount = Decimal(str(expense.get("amount", 0)))
        category_totals[category] = category_totals.get(category, Decimal("0")) + amount
        total_expenses += amount
    
    # Analyze each category
    for category, amount in category_totals.items():
        percentage = (amount / total_expenses * 100).quantize(Decimal("0.01")) if total_expenses > 0 else Decimal("0")
        
        # Check for anomalies
        anomaly = False
        anomaly_reason = None
        avg = historical_avg.get(category, Decimal("0"))
        
        if avg > 0:
            deviation = ((amount - avg) / avg * 100).quantize(Decimal("0.01"))
            if deviation > 50:
                anomaly = True
                anomaly_reason = f"Spending {deviation}% above average"
            elif deviation < -30:
                anomaly = True
                anomaly_reason = f"Spending {abs(deviation)}% below average"
        
        # Determine trend
        if avg > 0:
            if amount > avg * Decimal("1.1"):
                trend = "increasing"
            elif amount < avg * Decimal("0.9"):
                trend = "decreasing"
            else:
                trend = "stable"
        else:
            trend = "stable"
        
        patterns.append(ExpensePattern(
            chama_id=chama_id,
            category=category,
            total_amount=amount,
            percentage_of_total=percentage,
            trend=trend,
            anomaly_detected=anomaly,
            anomaly_reason=anomaly_reason,
        ))
    
    return patterns


# ============================================================================
# MEMBER ENGAGEMENT ANALYTICS
# ============================================================================

def analyze_member_engagement(
    chama_id: str,
    members: list[dict],
    contributions: list[dict],
    meetings: list[dict],
    votes: list[dict],
) -> MemberEngagement:
    """
    Analyze member engagement levels.
    
    Factors:
    - Contribution consistency
    - Meeting attendance
    - Voting participation
    - Activity recency
    """
    total_members = len(members)
    active_members = 0
    engagement_scores = []
    top_contributors = []
    inactive_members = []
    
    for member in members:
        member_id = member.get("id")
        
        # Contribution score
        member_contribs = [c for c in contributions if c.get("member_id") == member_id]
        contrib_score = 0
        if member_contribs:
            paid_count = sum(1 for c in member_contribs if c.get("status") == "paid")
            contrib_score = (paid_count / len(member_contribs)) * 40
        
        # Attendance score
        member_attendance = [m for m in meetings if member_id in m.get("attendees", [])]
        attendance_score = min(30, len(member_attendance) * 5)
        
        # Voting score
        member_votes = [v for v in votes if v.get("voter_id") == member_id]
        vote_score = min(20, len(member_votes) * 5)
        
        # Activity recency
        last_activity = member.get("last_activity_date")
        recency_score = 10
        if last_activity:
            days_since = (timezone.now().date() - last_activity).days
            if days_since > 90:
                recency_score = 0
            elif days_since > 30:
                recency_score = 5
        
        total_score = contrib_score + attendance_score + vote_score + recency_score
        engagement_scores.append({
            "member_id": member_id,
            "score": total_score,
            "contributions": contrib_score,
            "attendance": attendance_score,
            "voting": vote_score,
        })
        
        if total_score >= 50:
            active_members += 1
        
        if total_score >= 70:
            top_contributors.append({
                "member_id": member_id,
                "name": member.get("name", ""),
                "score": total_score,
            })
        
        if total_score < 30:
            inactive_members.append({
                "member_id": member_id,
                "name": member.get("name", ""),
                "score": total_score,
                "last_activity": last_activity,
            })
    
    # Calculate overall engagement
    avg_score = sum(s["score"] for s in engagement_scores) / len(engagement_scores) if engagement_scores else 0
    
    # Sort top contributors
    top_contributors.sort(key=lambda x: x["score"], reverse=True)
    top_contributors = top_contributors[:5]
    
    return MemberEngagement(
        chama_id=chama_id,
        total_members=total_members,
        active_members=active_members,
        engagement_score=Decimal(str(avg_score)).quantize(Decimal("0.01")),
        top_contributors=top_contributors,
        inactive_members=inactive_members,
    )


# ============================================================================
# LOAN RISK ASSESSMENT
# ============================================================================

def assess_loan_portfolio_risk(
    chama_id: str,
    loans: list[dict],
    chama_balance: Decimal,
) -> LoanRiskAssessment:
    """
    Assess overall loan portfolio risk.
    
    Analyzes:
    - Overdue loans
    - Concentration risk
    - Default probability
    - Recovery prospects
    """
    total_loans = len(loans)
    total_outstanding = Decimal("0")
    overdue_loans = 0
    overdue_amount = Decimal("0")
    high_risk_loans = []
    
    for loan in loans:
        outstanding = Decimal(str(loan.get("outstanding_balance", 0)))
        total_outstanding += outstanding
        
        status = loan.get("status", "")
        if status in ["overdue", "defaulted"]:
            overdue_loans += 1
            overdue_amount += outstanding
            
            # Calculate risk factors
            days_overdue = loan.get("days_overdue", 0)
            amount = Decimal(str(loan.get("amount", 0)))
            
            risk_factors = []
            if days_overdue > 90:
                risk_factors.append("Long overdue")
            if amount > chama_balance * Decimal("0.2"):
                risk_factors.append("Large amount relative to balance")
            if loan.get("guarantor_count", 0) == 0:
                risk_factors.append("No guarantors")
            
            if risk_factors:
                high_risk_loans.append({
                    "loan_id": loan.get("id"),
                    "member_id": loan.get("member_id"),
                    "amount": str(amount),
                    "outstanding": str(outstanding),
                    "days_overdue": days_overdue,
                    "risk_factors": risk_factors,
                })
    
    # Calculate default risk score
    if total_loans > 0:
        overdue_ratio = overdue_loans / total_loans
        amount_ratio = overdue_amount / total_outstanding if total_outstanding > 0 else 0
        default_risk_score = int(overdue_ratio * 50 + amount_ratio * 50)
    else:
        default_risk_score = 0
    
    # Recommendations
    recommendations = []
    if overdue_loans > 0:
        recommendations.append(f"Follow up on {overdue_loans} overdue loan(s)")
    if overdue_amount > chama_balance * Decimal("0.3"):
        recommendations.append("High overdue exposure - consider stricter lending policies")
    if len(high_risk_loans) > 0:
        recommendations.append("Review high-risk loans for recovery actions")
    
    return LoanRiskAssessment(
        chama_id=chama_id,
        total_loans=total_loans,
        total_outstanding=total_outstanding,
        overdue_loans=overdue_loans,
        overdue_amount=overdue_amount,
        default_risk_score=default_risk_score,
        high_risk_loans=high_risk_loans,
        recommendations=recommendations,
    )


# ============================================================================
# MONTHLY AI SUMMARY GENERATION
# ============================================================================

def generate_monthly_ai_summary(
    chama_id: str,
    month: str,
    contribution_trend: ContributionTrend,
    expense_patterns: list[ExpensePattern],
    loan_risk: LoanRiskAssessment,
    member_engagement: MemberEngagement,
    cashflow_forecast: CashflowForecast,
) -> MonthlyAISummary:
    """
    Generate plain-language monthly summary.
    
    Example outputs:
    - "Your chama collected KES 42,000 this month"
    - "Expenses increased by 18% from last month"
    - "2 loans are overdue"
    - "Savings are healthy, but liquidity is getting tighter"
    """
    highlights = []
    concerns = []
    recommendations = []
    
    # Contribution highlights
    highlights.append(f"Total contributions: KES {contribution_trend.total_contributions:,.2f}")
    if contribution_trend.trend_direction == "up":
        highlights.append(f"Contributions increased by {contribution_trend.trend_percentage}%")
    elif contribution_trend.trend_direction == "down":
        concerns.append(f"Contributions decreased by {abs(contribution_trend.trend_percentage)}%")
    
    if contribution_trend.at_risk_members > 0:
        concerns.append(f"{contribution_trend.at_risk_members} member(s) at risk of missing contributions")
        recommendations.append("Send reminders to at-risk members")
    
    # Expense highlights
    total_expenses = sum(p.total_amount for p in expense_patterns)
    highlights.append(f"Total expenses: KES {total_expenses:,.2f}")
    
    for pattern in expense_patterns:
        if pattern.anomaly_detected:
            concerns.append(f"{pattern.category}: {pattern.anomaly_reason}")
    
    # Loan highlights
    if loan_risk.overdue_loans > 0:
        concerns.append(f"{loan_risk.overdue_loans} loan(s) are overdue (KES {loan_risk.overdue_amount:,.2f})")
        recommendations.append("Follow up on overdue loans")
    
    # Engagement highlights
    highlights.append(f"Active members: {member_engagement.active_members}/{member_engagement.total_members}")
    if member_engagement.engagement_score < 50:
        concerns.append("Member engagement is low")
        recommendations.append("Consider member engagement initiatives")
    
    # Cashflow outlook
    if cashflow_forecast.predicted_balance < 0:
        concerns.append("Projected negative balance in coming months")
        recommendations.append("Review expenses and loan exposure")
    else:
        highlights.append(f"Projected balance: KES {cashflow_forecast.predicted_balance:,.2f}")
    
    # Generate summary text
    summary_parts = []
    summary_parts.append(f"Monthly Summary for {month}:")
    summary_parts.append(f"Your chama collected KES {contribution_trend.total_contributions:,.2f} this month.")
    
    if contribution_trend.trend_direction != "stable":
        summary_parts.append(f"Contributions are trending {contribution_trend.trend_direction}.")
    
    if loan_risk.overdue_loans > 0:
        summary_parts.append(f"{loan_risk.overdue_loans} loan(s) are overdue.")
    
    if member_engagement.engagement_score >= 70:
        summary_parts.append("Member engagement is strong.")
    elif member_engagement.engagement_score < 50:
        summary_parts.append("Member engagement needs improvement.")
    
    summary_text = " ".join(summary_parts)
    
    # Key metrics
    key_metrics = {
        "total_contributions": str(contribution_trend.total_contributions),
        "total_expenses": str(total_expenses),
        "completion_rate": str(contribution_trend.completion_rate),
        "active_members": member_engagement.active_members,
        "overdue_loans": loan_risk.overdue_loans,
        "engagement_score": str(member_engagement.engagement_score),
    }
    
    return MonthlyAISummary(
        chama_id=chama_id,
        month=month,
        summary_text=summary_text,
        key_metrics=key_metrics,
        highlights=highlights,
        concerns=concerns,
        recommendations=recommendations,
    )


# ============================================================================
# ANOMALY DETECTION
# ============================================================================

def detect_anomalies(
    chama_id: str,
    transactions: list[dict],
    historical_baselines: dict,
) -> list[AnomalyAlert]:
    """
    Detect unusual activity and anomalies.
    
    Detects:
    - Unusual transaction amounts
    - Unusual transaction timing
    - Duplicate payments
    - Suspicious patterns
    """
    alerts = []
    
    for txn in transactions:
        amount = Decimal(str(txn.get("amount", 0)))
        txn_type = txn.get("type", "")
        
        # Check against historical baseline
        baseline = historical_baselines.get(txn_type, {})
        avg = Decimal(str(baseline.get("average", 0)))
        std_dev = Decimal(str(baseline.get("std_dev", 0)))
        
        if avg > 0 and std_dev > 0:
            # Check if amount is unusual (more than 2 standard deviations)
            z_score = abs((amount - avg) / std_dev)
            if z_score > 2:
                alerts.append(AnomalyAlert(
                    alert_type="unusual_amount",
                    severity="high" if z_score > 3 else "medium",
                    description=f"Unusual {txn_type} amount: KES {amount:,.2f}",
                    affected_entity=txn.get("id", ""),
                    entity_type="transaction",
                    detected_value=amount,
                    expected_range=(avg - std_dev * 2, avg + std_dev * 2),
                ))
        
        # Check for duplicate payments
        similar_txns = [
            t for t in transactions
            if t.get("id") != txn.get("id")
            and t.get("member_id") == txn.get("member_id")
            and t.get("amount") == amount
            and abs((t.get("date") - txn.get("date")).days) < 7
        ]
        if similar_txns:
            alerts.append(AnomalyAlert(
                alert_type="potential_duplicate",
                severity="medium",
                description="Potential duplicate payment detected",
                affected_entity=txn.get("id", ""),
                entity_type="transaction",
                detected_value=amount,
                expected_range=(Decimal("0"), amount),
            ))
    
    return alerts
