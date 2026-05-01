"""
Membership, Roles, and Permissions Algorithms

Implements:
- Effective role computation (delegation-aware)
- Permissions checking
- Loan eligibility scoring
- Approval routing
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum

from django.utils import timezone


class MembershipStatus(Enum):
    ACTIVE = "active"
    PENDING = "pending"
    SUSPENDED = "suspended"
    EXITED = "exited"


class LoanStatus(Enum):
    PENDING = "pending"
    FLAGGED = "flagged"
    APPROVED = "approved"
    REJECTED = "rejected"
    DISBURSED = "disbursed"
    REPAID = "repaid"
    DEFAULTED = "defaulted"


class ContributionStatus(Enum):
    PENDING = "pending"
    PAID = "paid"
    LATE = "late"
    DEFAULTED = "defaulted"


# Role definitions
class MembershipRole(Enum):
    ADMIN = "admin"
    TREASURER = "treasurer"
    SECRETARY = "secretary"
    MEMBER = "member"


@dataclass
class Delegation:
    """Represents a role delegation from one member to another"""
    id: str
    delegator_id: str
    delegate_id: str
    role: str
    starts_at: datetime
    ends_at: datetime | None = None
    is_active: bool = True


@dataclass
class Membership:
    """Membership info"""
    id: str
    user_id: str
    chama_id: str
    role: str
    status: str
    joined_at: datetime
    is_active: bool = True


@dataclass
class LoanApplication:
    """Loan application data for eligibility check"""
    member_id: str
    chama_id: str
    amount: Decimal
    purpose: str
    term_months: int
    existing_loans: list[dict] = field(default_factory=list)
    contribution_history: list[dict] = field(default_factory=list)


@dataclass
class LoanEligibilityResult:
    """Result of loan eligibility check"""
    eligible: bool
    max_loan_amount: Decimal
    risk_score: int  # 0-100
    reasons: list[str] = field(default_factory=list)
    recommended_terms: int | None = None
    interest_rate_recommendation: Decimal | None = None


@dataclass 
class ComplianceScore:
    """Contribution compliance score"""
    member_id: str
    chama_id: str
    on_time_percentage: Decimal
    streak: int  # consecutive on-time cycles
    total_expected: int
    total_paid: int
    last_payment_date: date | None = None


# ============================================================================
# EFFECTIVE ROLE ALGORITHM
# ============================================================================

def compute_effective_role(
    membership: Membership,
    active_delegations: list[Delegation],
) -> tuple[str, bool]:
    """
    Compute effective role for a user considering delegations.
    
    Returns: (effective_role, is_delegated)
    
    Rules:
    1. If membership is not ACTIVE, return (MEMBER, False) with blocked access
    2. Check for active delegations that grant elevated privileges
    3. If delegation exists and is valid, return delegated role
    4. Otherwise return membership role
    """
    # Rule 1: Block inactive memberships
    if membership.status != MembershipStatus.ACTIVE.value:
        return (MembershipRole.MEMBER.value, False)
    
    if not membership.is_active:
        return (MembershipRole.MEMBER.value, False)
    
    # Rule 2: Check for active delegations
    now = timezone.now()
    for delegation in active_delegations:
        if not delegation.is_active:
            continue
        if delegation.delegate_id != membership.user_id:
            continue
        # Check date range
        if delegation.starts_at > now:
            continue
        if delegation.ends_at and delegation.ends_at < now:
            continue
        # Valid delegation found
        return (delegation.role, True)
    
    # Rule 3: Return membership role
    return (membership.role, False)


def is_access_allowed(
    membership: Membership,
    required_role: str,
    active_delegations: list[Delegation] | None = None,
) -> bool:
    """
    Check if membership has access for a given role requirement.
    
    This is the server-side enforcement of permissions.
    The app may hide UI elements, but backend must enforce.
    """
    if active_delegations is None:
        active_delegations = []
    
    effective_role, _ = compute_effective_role(membership, active_delegations)
    
    # Role hierarchy
    role_hierarchy = {
        MembershipRole.ADMIN.value: 5,
        MembershipRole.TREASURER.value: 3,
        MembershipRole.SECRETARY.value: 2,
        MembershipRole.MEMBER.value: 1,
    }
    
    member_level = role_hierarchy.get(membership.role, 0)
    required_level = role_hierarchy.get(required_role, 0)
    
    return member_level >= required_level


# ============================================================================
# LOAN ELIGIBILITY ALGORITHM
# ============================================================================

def calculate_loan_eligibility(
    membership: Membership,
    application: LoanApplication,
    chama_config: dict,
    compliance: ComplianceScore | None = None,
) -> LoanEligibilityResult:
    """
    Calculate loan eligibility based on multiple factors.
    
    Inputs:
    - Membership status and history
    - Contribution consistency (% paid on time)
    - Membership age (days active)
    - Current outstanding loan balance
    - Past delinquency count
    - Savings-to-loan ratio
    
    Outputs:
    - eligible: true/false
    - max_loan_amount: maximum recommended loan
    - risk_score: 0-100 (higher = riskier)
    - reasons: list of reasons for decision
    """
    reasons: list[str] = []
    risk_score = 0
    
    # Factor 1: Membership status
    if membership.status != MembershipStatus.ACTIVE.value:
        return LoanEligibilityResult(
            eligible=False,
            max_loan_amount=Decimal("0"),
            risk_score=100,
            reasons=["Membership is not active"],
        )
    
    # Factor 2: Membership age
    membership_age_days = (timezone.now() - membership.joined_at).days
    if membership_age_days < 30:
        risk_score += 30
        reasons.append(f"Membership too new ({membership_age_days} days)")
    elif membership_age_days < 90:
        risk_score += 15
        reasons.append(f"Recent membership ({membership_age_days} days)")
    
    # Factor 3: Contribution compliance
    if compliance:
        if compliance.on_time_percentage < 50:
            risk_score += 40
            reasons.append(f"Poor payment history ({compliance.on_time_percentage}% on-time)")
        elif compliance.on_time_percentage < 80:
            risk_score += 20
            reasons.append(f"Below average payment history ({compliance.on_time_percentage}%)")
        
        if compliance.streak < 2:
            risk_score += 15
            reasons.append("No payment streak")
    
    # Factor 4: Existing loans
    total_outstanding = Decimal("0")
    delinquency_count = 0
    for loan in application.existing_loans:
        outstanding = Decimal(str(loan.get("outstanding", 0)))
        total_outstanding += outstanding
        if loan.get("status") in ["overdue", "defaulted"]:
            delinquency_count += 1
            risk_score += 25
    
    if delinquency_count > 0:
        reasons.append(f"{delinquency_count} prior delinquencies")
    
    # Factor 5: Amount requested vs outstanding
    max_allowed = chama_config.get("max_loan_amount", Decimal("100000"))
    savings_requirement = Decimal(str(chama_config.get("savings_requirement", 1000)))
    
    if total_outstanding > 0:
        risk_score += 15
        reasons.append(f"Existing loan balance: {total_outstanding}")
    
    # Check requested amount against limits
    if application.amount > max_allowed:
        reasons.append(f"Amount exceeds chama limit of {max_allowed}")
    
    # Factor 6: Savings to loan ratio (if applicable)
    if savings_requirement > 0:
        savings_ratio = savings_requirement / max(application.amount, Decimal("1"))
        if savings_ratio < Decimal("0.1"):
            risk_score += 20
            reasons.append("Insufficient savings for loan amount")
    
    # Determine eligibility
    eligible = risk_score < 50
    
    # Calculate max loan amount based on contributions and risk
    base_max = chama_config.get("max_loan_amount", Decimal("100000"))
    if compliance:
        # Max is 3x average contribution for members with good history
        contribution_multiplier = chama_config.get("contribution_multiplier", 3)
        avg_contribution = Decimal(str(compliance.total_paid / max(1, compliance.total_expected)))
        calculated_max = avg_contribution * Decimal(str(contribution_multiplier))
        max_loan_amount = min(base_max, calculated_max)
    else:
        max_loan_amount = base_max * Decimal("0.5")  # Lower if no compliance data
    
    # Adjust for risk
    if risk_score > 30:
        max_loan_amount = max_loan_amount * Decimal("0.7")
        eligible = False
        reasons.append("Risk score too high for approval")
    
    return LoanEligibilityResult(
        eligible=eligible,
        max_loan_amount=max_loan_amount.quantize(Decimal("1")),
        risk_score=min(100, risk_score),
        reasons=reasons,
    )


# ============================================================================
# LOAN APPROVAL ROUTING ALGORITHM
# ============================================================================

def route_loan_for_approval(
    eligibility_result: LoanEligibilityResult,
    application_amount: Decimal,
    chama_config: dict,
) -> str:
    """
    Determine where a loan application should be routed.
    
    Returns: status to set on the loan
    
    Flow:
    - If eligible → PENDING_TREASURER_REVIEW
    - If risky (risk_score > 30) → FLAGGED_TREASURER_REVIEW
    - Treasurer can RECOMMEND_APPROVE or RECOMMEND_REJECT
    - Admin does final APPROVED / REJECTED
    - Only Admin can DISBURSE
    """
    large_loan_threshold = Decimal(str(chama_config.get("large_loan_threshold", 50000)))
    
    if eligibility_result.risk_score > 30:
        return LoanStatus.FLAGGED.value
    
    if application_amount >= large_loan_threshold:
        # Large loans need extra approval
        return LoanStatus.PENDING.value  # Will route to both treasurer and admin
    
    return LoanStatus.PENDING.value


def can_treasurer_approve(amount: Decimal, chama_config: dict) -> bool:
    """Check if treasurer can approve this loan amount"""
    treasurer_limit = Decimal(str(chama_config.get("treasurer_approval_limit", 25000)))
    return amount <= treasurer_limit


def can_disburse(approver_role: str) -> bool:
    """Check if role can disburse loans"""
    return approver_role in [
        MembershipRole.ADMIN.value,
    ]


# ============================================================================
# CONTRIBUTION COMPLIANCE ALGORITHM
# ============================================================================

def calculate_compliance(
    member_id: str,
    chama_id: str,
    expected_contributions: list[dict],
    actual_contributions: list[dict],
) -> ComplianceScore:
    """
    Calculate compliance score for a member.
    
    Returns percentage of on-time payments and streak info.
    """
    if not expected_contributions:
        return ComplianceScore(
            member_id=member_id,
            chama_id=chama_id,
            on_time_percentage=Decimal("0"),
            streak=0,
            total_expected=0,
            total_paid=0,
        )
    
    total_expected = len(expected_contributions)
    on_time_paid = 0
    streak = 0
    max_streak = 0
    last_was_on_time = True
    last_payment_date = None
    
    # Create lookup for actual payments
    actual_lookup = {c["due_date"]: c for c in actual_contributions}
    
    for expected in expected_contributions:
        due_date = expected.get("due_date")
        actual = actual_lookup.get(due_date)
        
        if actual and actual.get("status") == ContributionStatus.PAID.value:
            on_time_paid += 1
            if last_was_on_time:
                streak += 1
            else:
                streak = 1
            last_was_on_time = True
            
            paid_date = actual.get("paid_date")
            if paid_date and paid_date <= due_date:
                pass  # On time
            else:
                last_was_on_time = False
            
            if last_payment_date is None or paid_date > last_payment_date:
                last_payment_date = paid_date
        else:
            last_was_on_time = False
            streak = 0
        
        max_streak = max(max_streak, streak)
    
    percentage = (Decimal(str(on_time_paid)) / Decimal(str(total_expected))) * 100 if total_expected > 0 else Decimal("0")
    
    return ComplianceScore(
        member_id=member_id,
        chama_id=chama_id,
        on_time_percentage=percentage.quantize(Decimal("1")),
        streak=max_streak,
        total_expected=total_expected,
        total_paid=on_time_paid,
        last_payment_date=last_payment_date,
    )


# ============================================================================
# PENALTY ALGORITHM
# ============================================================================

@dataclass
class PenaltyRule:
    """Penalty configuration"""
    grace_period_days: int
    penalty_type: str  # "fixed" or "percentage"
    penalty_value: Decimal
    compounding: bool = False


def calculate_penalty(
    overdue_amount: Decimal,
    days_overdue: int,
    rule: PenaltyRule,
) -> Decimal:
    """
    Calculate penalty for late contribution.
    
    Supports:
    - Fixed fee after grace period
    - Percentage of overdue amount
    - Compounding penalties
    """
    if days_overdue <= rule.grace_period_days:
        return Decimal("0")
    
    effective_days = days_overdue - rule.grace_period_days
    
    if rule.penalty_type == "fixed":
        return rule.penalty_value
    elif rule.penalty_type == "percentage":
        if rule.compounding:
            # Compound daily (simplified)
            rate = rule.penalty_value / 100
            penalty = overdue_amount * (Decimal(str(effective_days)) * rate)
        else:
            penalty = overdue_amount * (rule.penalty_value / 100)
        return penalty.quantize(Decimal("1"))
    
    return Decimal("0")


# ============================================================================
# ANOMALY DETECTION (Lightweight)
# ============================================================================

@dataclass
class AnomalyAlert:
    """Anomaly detection result"""
    alert_type: str
    severity: str  # "low", "medium", "high"
    description: str
    recommended_action: str


def detect_withdrawal_anomaly(
    amount: Decimal,
    member_avg_withdrawal: Decimal,
    chama_avg_withdrawal: Decimal,
    recent_withdrawal_count: int,
    threshold_multiplier: float = 3.0,
) -> AnomalyAlert | None:
    """Detect unusual withdrawal patterns"""
    if recent_withdrawal_count > 5:
        return AnomalyAlert(
            alert_type="high_frequency",
            severity="high",
            description=f"{recent_withdrawal_count} withdrawals in short period",
            recommended_action="Review for potential fraud",
        )
    
    if amount > member_avg_withdrawal * Decimal(str(threshold_multiplier)):
        return AnomalyAlert(
            alert_type="large_amount",
            severity="medium",
            description=f"Amount {amount} is {threshold_multiplier}x member average",
            recommended_action="Verify with member",
        )
    
    if amount > chama_avg_withdrawal * Decimal(str(threshold_multiplier * 2)):
        return AnomalyAlert(
            alert_type="unusual_for_chama",
            severity="high",
            description="Amount unusually large for this chama",
            recommended_action="Require additional approval",
        )
    
    return None


def detect_role_change_anomaly(
    changed_by_role: str,
    target_role: str,
    time_since_last_change: timedelta | None,
) -> AnomalyAlert | None:
    """Detect suspicious role changes"""
    if time_since_last_change and time_since_last_change < timedelta(days=7):
        return AnomalyAlert(
            alert_type="frequent_role_change",
            severity="medium",
            description="Multiple role changes in last 7 days",
            recommended_action="Log for audit review",
        )
    
    # Self-approval or peer approval is suspicious
    if changed_by_role == target_role:
        return AnomalyAlert(
            alert_type="self_approval",
            severity="high",
            description="Member changed their own role",
            recommended_action="Require admin review",
        )
    
    return None
