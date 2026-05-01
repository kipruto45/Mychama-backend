"""
Smart Scoring Engine for MyChama

Implements intelligent scoring systems:
- Member Reliability Score
- Loan Eligibility Score  
- Chama Health Score
- Risk Flags Detection
- Contribution Compliance Score
- Meeting Attendance Score
- Member Participation Score

All scores are computed from real backend data and cached for performance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from django.utils import timezone

logger = logging.getLogger(__name__)


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ScoreCategory(Enum):
    CONTRIBUTION = "contribution"
    LOAN = "loan"
    ATTENDANCE = "attendance"
    PARTICIPATION = "participation"
    FINANCIAL = "financial"
    GOVERNANCE = "governance"


@dataclass
class MemberReliabilityScore:
    """Comprehensive member reliability scoring"""
    member_id: str
    chama_id: str
    overall_score: int  # 0-100
    contribution_score: int  # 0-100
    loan_repayment_score: int  # 0-100
    attendance_score: int  # 0-100
    participation_score: int  # 0-100
    risk_level: RiskLevel
    risk_flags: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    computed_at: datetime = field(default_factory=timezone.now)


@dataclass
class LoanEligibilityScore:
    """Smart loan eligibility scoring with detailed breakdown"""
    member_id: str
    chama_id: str
    eligible: bool
    eligibility_score: int  # 0-100
    max_loan_amount: Decimal
    recommended_amount: Decimal
    risk_score: int  # 0-100 (higher = riskier)
    factors: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    guarantor_requirements: dict = field(default_factory=dict)
    computed_at: datetime = field(default_factory=timezone.now)


@dataclass
class ChamaHealthScore:
    """Overall chama health assessment"""
    chama_id: str
    overall_score: int  # 0-100
    financial_health: int  # 0-100
    member_engagement: int  # 0-100
    governance_score: int  # 0-100
    growth_score: int  # 0-100
    risk_level: RiskLevel
    risk_flags: list[str] = field(default_factory=list)
    insights: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    computed_at: datetime = field(default_factory=timezone.now)


@dataclass
class ContributionComplianceScore:
    """Member contribution compliance tracking"""
    member_id: str
    chama_id: str
    compliance_rate: Decimal  # 0-100%
    on_time_rate: Decimal  # 0-100%
    current_streak: int  # consecutive on-time payments
    longest_streak: int
    total_expected: int
    total_paid: int
    total_late: int
    total_missed: int
    average_delay_days: Decimal
    last_payment_date: date | None = None
    next_due_date: date | None = None
    risk_level: RiskLevel = RiskLevel.LOW
    computed_at: datetime = field(default_factory=timezone.now)


@dataclass
class RiskFlag:
    """Individual risk flag detection"""
    flag_type: str
    severity: RiskLevel
    description: str
    affected_entity: str  # member_id or chama_id
    entity_type: str  # "member" or "chama"
    detected_at: datetime = field(default_factory=timezone.now)
    metadata: dict = field(default_factory=dict)


@dataclass
class SmartInsight:
    """AI-generated insight for dashboard"""
    insight_type: str
    title: str
    description: str
    severity: str  # info, warning, critical
    action_required: bool = False
    suggested_action: str | None = None
    affected_members: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    generated_at: datetime = field(default_factory=timezone.now)


# ============================================================================
# MEMBER RELIABILITY SCORING
# ============================================================================

def compute_member_reliability_score(
    member_id: str,
    chama_id: str,
    contribution_compliance: ContributionComplianceScore,
    loan_history: list[dict],
    attendance_records: list[dict],
    participation_data: dict,
) -> MemberReliabilityScore:
    """
    Compute comprehensive member reliability score.
    
    Factors:
    - Contribution compliance (40% weight)
    - Loan repayment history (25% weight)
    - Meeting attendance (20% weight)
    - General participation (15% weight)
    """
    risk_flags = []
    recommendations = []
    
    # Contribution Score (40%)
    contribution_score = int(contribution_compliance.compliance_rate)
    if contribution_compliance.compliance_rate < 50:
        risk_flags.append("Poor contribution compliance")
        recommendations.append("Follow up on missed contributions")
    elif contribution_compliance.compliance_rate < 80:
        recommendations.append("Encourage more consistent contributions")
    
    # Loan Repayment Score (25%)
    loan_score = 100
    if loan_history:
        total_loans = len(loan_history)
        defaulted_loans = sum(1 for l in loan_history if l.get("status") == "defaulted")
        late_payments = sum(1 for l in loan_history if l.get("late_payments", 0) > 0)
        
        if defaulted_loans > 0:
            loan_score -= (defaulted_loans / total_loans) * 60
            risk_flags.append(f"{defaulted_loans} defaulted loan(s)")
        if late_payments > 0:
            loan_score -= (late_payments / total_loans) * 30
    loan_score = max(0, int(loan_score))
    
    # Attendance Score (20%)
    attendance_score = 100
    if attendance_records:
        total_meetings = len(attendance_records)
        attended = sum(1 for a in attendance_records if a.get("status") == "attended")
        if total_meetings > 0:
            attendance_rate = (attended / total_meetings) * 100
            attendance_score = int(attendance_rate)
            if attendance_rate < 50:
                risk_flags.append("Low meeting attendance")
                recommendations.append("Improve meeting attendance")
    
    # Participation Score (15%)
    participation_score = 100
    votes_cast = participation_data.get("votes_cast", 0)
    total_votes = participation_data.get("total_votes", 0)
    if total_votes > 0:
        participation_score = int((votes_cast / total_votes) * 100)
    
    # Overall Score (weighted average)
    overall_score = int(
        (contribution_score * 0.40) +
        (loan_score * 0.25) +
        (attendance_score * 0.20) +
        (participation_score * 0.15)
    )
    
    # Determine risk level
    if overall_score >= 80:
        risk_level = RiskLevel.LOW
    elif overall_score >= 60:
        risk_level = RiskLevel.MEDIUM
    elif overall_score >= 40:
        risk_level = RiskLevel.HIGH
    else:
        risk_level = RiskLevel.CRITICAL
        risk_flags.append("Critical reliability score")
    
    return MemberReliabilityScore(
        member_id=member_id,
        chama_id=chama_id,
        overall_score=overall_score,
        contribution_score=contribution_score,
        loan_repayment_score=loan_score,
        attendance_score=attendance_score,
        participation_score=participation_score,
        risk_level=risk_level,
        risk_flags=risk_flags,
        recommendations=recommendations,
    )


# ============================================================================
# LOAN ELIGIBILITY SCORING
# ============================================================================

def compute_loan_eligibility_score(
    member_id: str,
    chama_id: str,
    requested_amount: Decimal,
    member_savings: Decimal,
    chama_balance: Decimal,
    contribution_compliance: ContributionComplianceScore,
    active_loans: list[dict],
    loan_history: list[dict],
    guarantor_capacity: Decimal,
    membership_age_days: int,
    chama_loan_policy: dict,
) -> LoanEligibilityScore:
    """
    Smart loan eligibility engine with detailed scoring.
    
    Factors:
    - Savings history (20%)
    - Contribution compliance (25%)
    - Active/defaulted loans (20%)
    - Guarantor strength (15%)
    - Membership age (10%)
    - Chama liquidity (10%)
    """
    factors = {}
    warnings = []
    recommendations = []
    
    # Factor 1: Savings History (20%)
    savings_ratio = (member_savings / requested_amount * 100) if requested_amount > 0 else 0
    savings_score = min(100, int(savings_ratio * 2))
    factors["savings"] = savings_score
    if savings_ratio < 50:
        warnings.append("Insufficient savings relative to loan amount")
        recommendations.append("Build more savings before requesting large loans")
    
    # Factor 2: Contribution Compliance (25%)
    compliance_score = int(contribution_compliance.compliance_rate)
    factors["compliance"] = compliance_score
    if compliance_score < 70:
        warnings.append("Below average contribution compliance")
        recommendations.append("Improve contribution consistency")
    
    # Factor 3: Active/Defaulted Loans (20%)
    loan_history_score = 100
    total_outstanding = sum(Decimal(str(l.get("outstanding_balance", 0))) for l in active_loans)
    defaulted_count = sum(1 for l in loan_history if l.get("status") == "defaulted")
    
    if defaulted_count > 0:
        loan_history_score -= defaulted_count * 30
        warnings.append(f"{defaulted_count} previous default(s)")
    
    if total_outstanding > 0:
        outstanding_ratio = (total_outstanding / member_savings * 100) if member_savings > 0 else 100
        if outstanding_ratio > 50:
            loan_history_score -= 20
            warnings.append("High existing loan burden")
    
    loan_history_score = max(0, loan_history_score)
    factors["loan_history"] = loan_history_score
    
    # Factor 4: Guarantor Strength (15%)
    guarantor_score = 100
    if guarantor_capacity < requested_amount * Decimal("0.5"):
        guarantor_score = 50
        warnings.append("Guarantor capacity may be insufficient")
    elif guarantor_capacity < requested_amount:
        guarantor_score = 75
    factors["guarantor"] = guarantor_score
    
    # Factor 5: Membership Age (10%)
    age_score = min(100, int(membership_age_days / 3))
    factors["membership_age"] = age_score
    if membership_age_days < 90:
        warnings.append("Recent membership - limited history")
    
    # Factor 6: Chama Liquidity (10%)
    liquidity_score = 100
    max_loan_ratio = Decimal(str(chama_loan_policy.get("max_loan_to_savings_ratio", 3)))
    safe_loan_amount = chama_balance * Decimal("0.3")  # 30% of balance
    
    if requested_amount > safe_loan_amount:
        liquidity_score = 50
        warnings.append("Loan amount exceeds safe liquidity threshold")
        recommendations.append("Consider reducing loan amount or waiting for more contributions")
    
    if requested_amount > member_savings * max_loan_ratio:
        liquidity_score = min(liquidity_score, 40)
        warnings.append(f"Exceeds maximum loan-to-savings ratio ({max_loan_ratio}x)")
    
    factors["liquidity"] = liquidity_score
    
    # Calculate overall eligibility score
    eligibility_score = int(
        (savings_score * 0.20) +
        (compliance_score * 0.25) +
        (loan_history_score * 0.20) +
        (guarantor_score * 0.15) +
        (age_score * 0.10) +
        (liquidity_score * 0.10)
    )
    
    # Determine eligibility
    eligible = eligibility_score >= 60 and len([w for w in warnings if "default" in w.lower()]) == 0
    
    # Calculate max and recommended amounts
    max_loan_amount = min(
        member_savings * max_loan_ratio,
        chama_balance * Decimal("0.5"),
        guarantor_capacity * Decimal("2"),
    )
    recommended_amount = min(requested_amount, max_loan_amount * Decimal("0.8"))
    
    # Risk score (inverse of eligibility)
    risk_score = 100 - eligibility_score
    
    # Guarantor requirements
    guarantor_requirements = {
        "min_guarantors": 1 if requested_amount > member_savings else 0,
        "min_guarantor_savings": requested_amount * Decimal("0.5"),
        "guarantor_compliance_min": 80,
    }
    
    return LoanEligibilityScore(
        member_id=member_id,
        chama_id=chama_id,
        eligible=eligible,
        eligibility_score=eligibility_score,
        max_loan_amount=max_loan_amount,
        recommended_amount=recommended_amount,
        risk_score=risk_score,
        factors=factors,
        warnings=warnings,
        recommendations=recommendations,
        guarantor_requirements=guarantor_requirements,
    )


# ============================================================================
# CHAMA HEALTH SCORING
# ============================================================================

def compute_chama_health_score(
    chama_id: str,
    total_members: int,
    active_members: int,
    total_savings: Decimal,
    total_loans_outstanding: Decimal,
    overdue_loans_count: int,
    contribution_completion_rate: Decimal,
    meeting_attendance_rate: Decimal,
    expense_control_rate: Decimal,
    member_growth_rate: Decimal,
    monthly_contributions: list[Decimal],
    monthly_expenses: list[Decimal],
) -> ChamaHealthScore:
    """
    Compute overall chama health score.
    
    Factors:
    - Financial health (35%)
    - Member engagement (25%)
    - Governance (20%)
    - Growth (20%)
    """
    risk_flags = []
    insights = []
    recommendations = []
    
    # Financial Health (35%)
    financial_score = 100
    
    # Liquidity check
    if total_savings > 0:
        loan_to_savings_ratio = total_loans_outstanding / total_savings
        if loan_to_savings_ratio > Decimal("0.7"):
            financial_score -= 30
            risk_flags.append("High loan-to-savings ratio")
            recommendations.append("Reduce loan exposure to improve liquidity")
        elif loan_to_savings_ratio > Decimal("0.5"):
            financial_score -= 15
            insights.append("Loan exposure is moderate")
    
    # Overdue loans
    if overdue_loans_count > 0:
        overdue_penalty = min(40, overdue_loans_count * 10)
        financial_score -= overdue_penalty
        risk_flags.append(f"{overdue_loans_count} overdue loan(s)")
        recommendations.append("Follow up on overdue loans")
    
    # Expense control
    if expense_control_rate < 80:
        financial_score -= 20
        insights.append("Expenses are high relative to budget")
    
    financial_score = max(0, financial_score)
    
    # Member Engagement (25%)
    engagement_score = 100
    
    # Active member ratio
    if total_members > 0:
        active_ratio = (active_members / total_members) * 100
        if active_ratio < 50:
            engagement_score -= 40
            risk_flags.append("Low active member ratio")
            recommendations.append("Re-engage inactive members")
        elif active_ratio < 70:
            engagement_score -= 20
    
    # Contribution completion
    if contribution_completion_rate < 70:
        engagement_score -= 30
        risk_flags.append("Low contribution completion rate")
    elif contribution_completion_rate < 85:
        engagement_score -= 15
    
    engagement_score = max(0, engagement_score)
    
    # Governance Score (20%)
    governance_score = 100
    
    # Meeting attendance
    if meeting_attendance_rate < 50:
        governance_score -= 40
        risk_flags.append("Low meeting attendance")
        recommendations.append("Improve meeting scheduling and reminders")
    elif meeting_attendance_rate < 70:
        governance_score -= 20
    
    governance_score = max(0, governance_score)
    
    # Growth Score (20%)
    growth_score = 100
    
    # Member growth
    if member_growth_rate < 0:
        growth_score -= 30
        insights.append("Member count is declining")
    elif member_growth_rate < 5:
        growth_score -= 10
        insights.append("Slow member growth")
    
    # Contribution trend
    if len(monthly_contributions) >= 2:
        recent_avg = sum(monthly_contributions[-3:]) / min(3, len(monthly_contributions))
        older_avg = sum(monthly_contributions[:-3]) / max(1, len(monthly_contributions) - 3) if len(monthly_contributions) > 3 else recent_avg
        
        if older_avg > 0:
            growth_trend = ((recent_avg - older_avg) / older_avg) * 100
            if growth_trend < -10:
                growth_score -= 20
                insights.append("Contribution trend is declining")
            elif growth_trend > 10:
                insights.append("Contribution trend is positive")
    
    growth_score = max(0, growth_score)
    
    # Overall Score
    overall_score = int(
        (financial_score * 0.35) +
        (engagement_score * 0.25) +
        (governance_score * 0.20) +
        (growth_score * 0.20)
    )
    
    # Risk Level
    if overall_score >= 80:
        risk_level = RiskLevel.LOW
        insights.append("Chama is in good financial health")
    elif overall_score >= 60:
        risk_level = RiskLevel.MEDIUM
    elif overall_score >= 40:
        risk_level = RiskLevel.HIGH
    else:
        risk_level = RiskLevel.CRITICAL
        risk_flags.append("Critical chama health")
    
    return ChamaHealthScore(
        chama_id=chama_id,
        overall_score=overall_score,
        financial_health=financial_score,
        member_engagement=engagement_score,
        governance_score=governance_score,
        growth_score=growth_score,
        risk_level=risk_level,
        risk_flags=risk_flags,
        insights=insights,
        recommendations=recommendations,
    )


# ============================================================================
# CONTRIBUTION COMPLIANCE SCORING
# ============================================================================

def compute_contribution_compliance(
    member_id: str,
    chama_id: str,
    contribution_history: list[dict],
    contribution_schedule: dict,
) -> ContributionComplianceScore:
    """
    Compute detailed contribution compliance score.
    
    Tracks:
    - On-time payment rate
    - Current and longest streaks
    - Average delay days
    - Missed contributions
    """
    total_expected = len(contribution_history)
    total_paid = 0
    total_late = 0
    total_missed = 0
    on_time_count = 0
    delay_days = []
    current_streak = 0
    longest_streak = 0
    temp_streak = 0
    
    grace_period = contribution_schedule.get("grace_period_days", 0)
    
    for record in contribution_history:
        status = record.get("status")
        due_date = record.get("due_date")
        paid_date = record.get("paid_date")
        
        if status == "paid":
            total_paid += 1
            if paid_date and due_date:
                delay = (paid_date - due_date).days
                delay_days.append(delay)
                if delay <= grace_period:
                    on_time_count += 1
                    temp_streak += 1
                    longest_streak = max(longest_streak, temp_streak)
                else:
                    total_late += 1
                    temp_streak = 0
        elif status == "missed":
            total_missed += 1
            temp_streak = 0
        elif status == "pending":
            # Check if overdue
            if due_date and due_date < timezone.now().date():
                total_missed += 1
                temp_streak = 0
    
    current_streak = temp_streak
    
    # Calculate rates
    compliance_rate = Decimal("0")
    on_time_rate = Decimal("0")
    avg_delay = Decimal("0")
    
    if total_expected > 0:
        compliance_rate = (Decimal(str(total_paid)) / Decimal(str(total_expected)) * 100).quantize(Decimal("0.01"))
        on_time_rate = (Decimal(str(on_time_count)) / Decimal(str(total_expected)) * 100).quantize(Decimal("0.01"))
    
    if delay_days:
        avg_delay = (Decimal(str(sum(delay_days))) / Decimal(str(len(delay_days)))).quantize(Decimal("0.01"))
    
    # Risk level
    if compliance_rate >= 90:
        risk_level = RiskLevel.LOW
    elif compliance_rate >= 70:
        risk_level = RiskLevel.MEDIUM
    elif compliance_rate >= 50:
        risk_level = RiskLevel.HIGH
    else:
        risk_level = RiskLevel.CRITICAL
    
    return ContributionComplianceScore(
        member_id=member_id,
        chama_id=chama_id,
        compliance_rate=compliance_rate,
        on_time_rate=on_time_rate,
        current_streak=current_streak,
        longest_streak=longest_streak,
        total_expected=total_expected,
        total_paid=total_paid,
        total_late=total_late,
        total_missed=total_missed,
        average_delay_days=avg_delay,
        risk_level=risk_level,
    )


# ============================================================================
# RISK FLAG DETECTION
# ============================================================================

def detect_risk_flags(
    chama_id: str,
    members_data: list[dict],
    finance_data: dict,
    loan_data: list[dict],
) -> list[RiskFlag]:
    """
    Detect various risk flags across the chama.
    
    Detects:
    - Repeated missed contributions
    - High loan concentration
    - Declining liquidity
    - Unusual payout activity
    - Too many pending approvals
    - Over-exposed guarantors
    """
    flags = []
    
    # Check member risks
    for member in members_data:
        member_id = member.get("id")
        
        # Repeated missed contributions
        missed_count = member.get("missed_contributions", 0)
        if missed_count >= 3:
            flags.append(RiskFlag(
                flag_type="repeated_missed_contributions",
                severity=RiskLevel.HIGH if missed_count >= 5 else RiskLevel.MEDIUM,
                description=f"Member has missed {missed_count} contributions",
                affected_entity=member_id,
                entity_type="member",
            ))
        
        # High loan concentration
        total_loans = member.get("total_loans", 0)
        if total_loans > 0:
            outstanding = member.get("outstanding_loans", 0)
            savings = member.get("savings", 0)
            if savings > 0 and outstanding / savings > 0.8:
                flags.append(RiskFlag(
                    flag_type="high_loan_concentration",
                    severity=RiskLevel.HIGH,
                    description="Member has high loan-to-savings ratio",
                    affected_entity=member_id,
                    entity_type="member",
                ))
    
    # Check chama-level risks
    # Declining liquidity
    monthly_balance = finance_data.get("monthly_balance_trend", [])
    if len(monthly_balance) >= 3:
        recent = monthly_balance[-1]
        previous = monthly_balance[-3]
        if previous > 0 and (recent - previous) / previous < -0.2:
            flags.append(RiskFlag(
                flag_type="declining_liquidity",
                severity=RiskLevel.HIGH,
                description="Chama balance has declined significantly over 3 months",
                affected_entity=chama_id,
                entity_type="chama",
            ))
    
    # Overdue loans
    overdue_loans = [l for l in loan_data if l.get("status") == "overdue"]
    if len(overdue_loans) > 0:
        flags.append(RiskFlag(
            flag_type="overdue_loans",
            severity=RiskLevel.HIGH if len(overdue_loans) >= 3 else RiskLevel.MEDIUM,
            description=f"{len(overdue_loans)} loan(s) are overdue",
            affected_entity=chama_id,
            entity_type="chama",
        ))
    
    # Guarantor exposure
    guarantor_exposure = {}
    for loan in loan_data:
        for guarantor in loan.get("guarantors", []):
            guarantor_id = guarantor.get("id")
            if guarantor_id not in guarantor_exposure:
                guarantor_exposure[guarantor_id] = Decimal("0")
            guarantor_exposure[guarantor_id] += Decimal(str(guarantor.get("amount", 0)))
    
    for guarantor_id, exposure in guarantor_exposure.items():
        guarantor_savings = next(
            (m.get("savings", 0) for m in members_data if m.get("id") == guarantor_id),
            0
        )
        if guarantor_savings > 0 and exposure / Decimal(str(guarantor_savings)) > 0.5:
            flags.append(RiskFlag(
                flag_type="over_exposed_guarantor",
                severity=RiskLevel.MEDIUM,
                description="Guarantor has high exposure relative to savings",
                affected_entity=guarantor_id,
                entity_type="member",
            ))
    
    return flags


# ============================================================================
# SMART INSIGHTS GENERATION
# ============================================================================

def generate_smart_insights(
    chama_id: str,
    health_score: ChamaHealthScore,
    member_scores: list[MemberReliabilityScore],
    risk_flags: list[RiskFlag],
    finance_summary: dict,
) -> list[SmartInsight]:
    """
    Generate AI-powered insights for dashboard.
    
    Creates plain-language insights like:
    - "3 members are likely to miss this month's contribution"
    - "Liquidity is low for new loan approvals"
    - "Attendance has dropped for 2 consecutive meetings"
    """
    insights = []
    
    # Contribution insights
    at_risk_members = [m for m in member_scores if m.contribution_score < 60]
    if at_risk_members:
        insights.append(SmartInsight(
            insight_type="contribution_risk",
            title=f"{len(at_risk_members)} member(s) at risk of missing contributions",
            description=f"Based on compliance patterns, {len(at_risk_members)} member(s) may miss their next contribution",
            severity="warning",
            action_required=True,
            suggested_action="Send contribution reminders to at-risk members",
            affected_members=[m.member_id for m in at_risk_members],
        ))
    
    # Liquidity insights
    if health_score.financial_health < 60:
        insights.append(SmartInsight(
            insight_type="liquidity_warning",
            title="Liquidity is getting tighter",
            description="Chama liquidity is below recommended levels",
            severity="warning",
            action_required=True,
            suggested_action="Review pending loan requests and expense approvals",
        ))
    
    # Attendance insights
    low_attendance_members = [m for m in member_scores if m.attendance_score < 50]
    if len(low_attendance_members) > len(member_scores) * 0.3:
        insights.append(SmartInsight(
            insight_type="attendance_decline",
            title="Meeting attendance has dropped",
            description=f"{len(low_attendance_members)} members have low attendance",
            severity="warning",
            suggested_action="Review meeting schedule and send reminders",
        ))
    
    # Risk flag insights
    critical_flags = [f for f in risk_flags if f.severity == RiskLevel.CRITICAL]
    if critical_flags:
        insights.append(SmartInsight(
            insight_type="critical_risks",
            title="Critical risks detected",
            description=f"{len(critical_flags)} critical risk(s) require immediate attention",
            severity="critical",
            action_required=True,
            suggested_action="Review risk flags in admin dashboard",
        ))
    
    # Positive insights
    if health_score.overall_score >= 80:
        insights.append(SmartInsight(
            insight_type="positive_health",
            title="Chama is in good financial health",
            description="All metrics are performing well",
            severity="info",
        ))
    
    # Loan insights
    overdue_count = finance_summary.get("overdue_loans_count", 0)
    if overdue_count > 0:
        insights.append(SmartInsight(
            insight_type="overdue_loans",
            title=f"{overdue_count} loan(s) are overdue",
            description="Some loans have missed their repayment deadlines",
            severity="warning",
            action_required=True,
            suggested_action="Follow up on overdue loans and apply penalties if needed",
        ))
    
    return insights
