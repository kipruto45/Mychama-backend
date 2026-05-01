"""
Member Experience Automations

Production-grade automations for:
- Payout countdown notifier
- Contribution streak tracker
- Onboarding checklist tracker
- Birthday/milestone notifier
- Exit settlement calculator
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.chama.models import Chama, Membership


@dataclass
class PayoutCountdown:
    """Payout countdown result."""
    member_id: str
    member_name: str
    payout_position: int
    payout_date: date | None
    days_until_payout: int | None
    payout_amount: Decimal
    is_next_in_line: bool
    notify: bool
    notification_message: str | None


def get_payout_countdown(
    membership: "Membership",
    days_ahead: int = 7,
) -> PayoutCountdown | None:
    """Get payout countdown for a member."""
    from apps.chama.models import MembershipStatus
    from apps.finance.models import Payout, PayoutStatus
    
    if membership.status != MembershipStatus.ACTIVE:
        return None
    
    today = timezone.now().date()
    future_date = today + timedelta(days=days_ahead)
    
    next_payout = Payout.objects.filter(
        chama=membership.chama,
        status__in=[PayoutStatus.PENDING, PayoutStatus.APPROVED, PayoutStatus.DISBURSING],
        scheduled_date__gte=today,
        scheduled_date__lte=future_date,
    ).order_by("scheduled_date").first()
    
    if not next_payout:
        return PayoutCountdown(
            member_id=str(membership.id),
            member_name=membership.user.full_name if membership.user else "Unknown",
            payout_position=0,
            payout_date=None,
            days_until_payout=None,
            payout_amount=Decimal("0.00"),
            is_next_in_line=False,
            notify=False,
            notification_message=None,
        )
    
    payout_rotation = membership.chama.chama.payout_rotation_order or "fifo"
    if payout_rotation == "random":
        position = 1
    else:
        member_count = membership.chama.memberships.filter(
            status=MembershipStatus.ACTIVE,
        ).count()
        position = min(position, member_count)
    
    days_until = (next_payout.scheduled_date - today).days
    
    is_next = position == 1
    
    if is_next and days_until <= days_ahead:
        message = f"Your payout is in {days_until} days! Amount: KES {next_payout.amount:,.2f}"
    elif is_next:
        message = None
    else:
        message = f"Payout #{position} in rotation. Expected in {days_until} days."
    
    return PayoutCountdown(
        member_id=str(membership.id),
        member_name=membership.user.full_name if membership.user else "Unknown",
        payout_position=position,
        payout_date=next_payout.scheduled_date,
        days_until_payout=days_until,
        payout_amount=next_payout.amount,
        is_next_in_line=is_next,
        notify=is_next and days_until <= days_ahead,
        notification_message=message,
    )


@dataclass
class ContributionStreak:
    """Contribution streak result."""
    member_id: str
    member_name: str
    current_streak: int
    longest_streak: int
    streak_months: int
    on_time_count: int
    late_count: int
    total_contributions: int
    consistency_score: float
    milestone_reached: str | None


def get_contribution_streak(
    membership: "Membership",
    months_lookback: int = 12,
) -> ContributionStreak:
    """Get contribution streak for a member."""
    from apps.chama.models import MembershipStatus
    from apps.finance.models import Payment, PaymentStatus
    
    today = timezone.now().date()
    lookback_date = today - timedelta(days=months_lookback * 30)
    
    contributions = Payment.objects.filter(
        chama=membership.chama,
        member=membership.user,
        payment_date__gte=lookback_date,
        status=PaymentStatus.COMPLETED,
    ).order_by("payment_date")
    
    total = contributions.count()
    on_time = 0
    late = 0
    
    for contrib in contributions:
        from apps.finance.models import ContributionSchedule
        expected = ContributionSchedule.objects.filter(
            chama=membership.chama,
            member=membership.user,
            due_date__lte=contrib.payment_date.date(),
            is_active=True,
        ).first()
        
        if expected and contrib.payment_date.date() <= expected.due_date:
            on_time += 1
        else:
            late += 1
    
    streak = 0
    longest = 0
    current = 0
    
    dates = sorted(set(
        contrib.payment_date.date().replace(day=1)
        for contrib in contributions
    ))
    
    for i, dt in enumerate(dates):
        if i == 0:
            current = 1
        elif (dt.year == dates[i-1].year and dt.month == dates[i-1].month + 1) or \
             (dt.year == dates[i-1].year + 1 and dt.month == 1 and dates[i-1].month == 12):
            current += 1
        else:
            current = 1
        
        longest = max(longest, current)
    
    streak = current
    
    consistency = 100.0
    if total > 0:
        consistency = (on_time / total) * 100
    
    milestone = None
    if streak >= 12:
        milestone = "12-month streak champion!"
    elif streak >= 6:
        milestone = "6-month streak!"
    elif streak >= 3:
        milestone = "3-month streak!"
    elif streak >= 1:
        milestone = "1-month streak!"
    
    return ContributionStreak(
        member_id=str(membership.id),
        member_name=membership.user.full_name if membership.user else "Unknown",
        current_streak=streak,
        longest_streak=longest,
        streak_months=streak,
        on_time_count=on_time,
        late_count=late,
        total_contributions=total,
        consistency_score=consistency,
        milestone_reached=milestone,
    )


@dataclass
class OnboardingStep:
    """Onboarding step status."""
    step_key: str
    step_label: str
    is_completed: bool
    completed_at: date | None
    is_blocked: bool
    blocked_reason: str | None


@dataclass
class OnboardingProgress:
    """Onboarding progress result."""
    member_id: str
    member_name: str
    steps: list[OnboardingStep]
    completed_count: int
    total_steps: int
    progress_percent: float
    is_complete: bool
    nudge_message: str | None


ONBOARDING_STEPS = [
    ("phone_verified", "Verify Phone Number", True, None),
    ("kyc_submitted", "Submit KYC Documents", True, "phone_verified"),
    ("chama_joined", "Join a Chama", True, "kyc_submitted"),
    ("profile_completed", "Complete Profile", True, "chama_joined"),
    ("first_contribution", "Make First Contribution", True, "profile_completed"),
]


def get_onboarding_progress(
    membership: "Membership",
) -> OnboardingProgress:
    """Get onboarding progress for a member."""
    steps = []
    completed_keys = set()
    blocked_reason = None
    
    for step_key, step_label, required, depends_on in ONBOARDING_STEPS:
        is_blocked = depends_on is not None and depends_on not in completed_keys
        
        is_completed = False
        completed_at = None
        
        if step_key == "phone_verified":
            is_completed = bool(membership.user.phone)
        elif step_key == "kyc_submitted":
            from apps.accounts.models import MemberKYC, MemberKYCStatus
            kyc = MemberKYC.objects.filter(
                user=membership.user,
                chama=membership.chama,
                status__in=[MemberKYCStatus.PENDING, MemberKYCStatus.APPROVED],
            ).first()
            if kyc:
                is_completed = True
                completed_at = kyc.created_at.date() if kyc.created_at else None
        elif step_key == "chama_joined":
            is_completed = membership.is_active and membership.is_approved
            if is_completed:
                completed_at = membership.joined_at.date() if membership.joined_at else None
        elif step_key == "profile_completed":
            is_completed = all([
                membership.user.full_name,
                membership.user.phone,
            ])
        elif step_key == "first_contribution":
            from apps.finance.models import Payment, PaymentStatus
            first = Payment.objects.filter(
                chama=membership.chama,
                member=membership.user,
                status=PaymentStatus.COMPLETED,
            ).first()
            if first:
                is_completed = True
                completed_at = first.payment_date.date() if first.payment_date else None
        
        if is_completed:
            completed_keys.add(step_key)
            completed_at = completed_at or timezone.now().date()
        
        steps.append(OnboardingStep(
            step_key=step_key,
            step_label=step_label,
            is_completed=is_completed,
            completed_at=completed_at,
            is_blocked=is_blocked,
            blocked_reason="Complete previous step first" if is_blocked else None,
        ))
    
    completed_count = sum(1 for s in steps if s.is_completed)
    total_steps = len(steps)
    progress_percent = (completed_count / total_steps) * 100 if total_steps > 0 else 0.0
    
    next_step = next((s for s in steps if not s.is_completed and not s.is_blocked), None)
    nudge_message = None
    if next_step:
        nudge_message = f"Complete '{next_step.step_label}' to continue your setup."
    
    return OnboardingProgress(
        member_id=str(membership.id),
        member_name=membership.user.full_name if membership.user else "Unknown",
        steps=steps,
        completed_count=completed_count,
        total_steps=total_steps,
        progress_percent=progress_percent,
        is_complete=completed_count == total_steps,
        nudge_message=nudge_message,
    )


@dataclass
class Milestone:
    """Member milestone."""
    milestone_type: str
    milestone_label: str
    milestone_date: date
    days_since: int


@dataclass
class MemberMilestones:
    """Member milestones result."""
    member_id: str
    member_name: str
    milestones: list[Milestone]
    upcoming_birthday: bool
    anniversary_soon: bool


def get_member_milestones(
    user: "User",
    days_ahead: int = 7,
) -> MemberMilestones:
    """Get upcoming milestones for a member."""
    from apps.chama.models import Membership
    
    milestones = []
    today = timezone.now().date()
    
    if user.date_of_birth:
        birthday_this_year = user.date_of_birth.replace(year=today.year)
        if birthday_this_year < today:
            birthday_this_year = birthday_this_year.replace(year=today.year + 1)
        
        if 0 <= (birthday_this_year - today).days <= days_ahead:
            milestones.append(Milestone(
                milestone_type="birthday",
                milestone_label=f"Birthday on {birthday_this_year.strftime('%B %d')}",
                milestone_date=birthday_this_year,
                days_since=0,
            ))
    
    memberships = Membership.objects.filter(
        user=user,
        is_active=True,
    ).select_related("chama")
    
    for membership in memberships:
        if membership.joined_at:
            join_anniversary = membership.joined_at.date().replace(year=today.year)
            if join_anniversary < today:
                join_anniversary = join_anniversary.replace(year=today.year + 1)
            
            days_until = (join_anniversary - today).days
            
            if 0 <= days_until <= days_ahead:
                years = today.year - membership.joined_at.date().year
                milestones.append(Milestone(
                    milestone_type="anniversary",
                    milestone_label=f"{years} year anniversary with {membership.chama.name}",
                    milestone_date=join_anniversary,
                    days_since=days_until,
                ))
    
    return MemberMilestones(
        member_id=str(user.id),
        member_name=user.full_name if user.full_name else "Unknown",
        milestones=sorted(milestones, key=lambda m: m.days_since),
        upcoming_birthday=any(m.milestone_type == "birthday" for m in milestones),
        anniversary_soon=any(m.milestone_type == "anniversary" for m in milestones),
    )


@dataclass
class ExitSettlement:
    """Exit settlement result."""
    member_id: str
    member_name: str
    chama_id: str
    chama_name: str
    total_dues: Decimal
    total_receivable: Decimal
    net_settlement: Decimal
    breakdown: dict


def calculate_exit_settlement(membership: "Membership") -> ExitSettlement:
    """Calculate exit settlement for a member."""
    from apps.chama.models import MembershipStatus
    from apps.finance.models import Loan, LoanStatus, Payment, PaymentStatus, Wallet
    
    if membership.status != MembershipStatus.ACTIVE:
        raise ValueError("Member must be active to calculate exit settlement.")
    
    total_dues = Decimal("0.00")
    total_receivable = Decimal("0.00")
    breakdown = {}
    
    pending_loans = Loan.objects.filter(
        chama=membership.chama,
        borrower=membership.user,
        status__in=[LoanStatus.PENDING, LoanStatus.ACTIVE, LoanStatus.OVERDUE],
    )
    for loan in pending_loans:
        outstanding = loan.outstanding_balance or Decimal("0.00")
        total_dues += outstanding
        breakdown[f"loan_{loan.id}"] = {
            "description": f"Outstanding loan balance",
            "amount": outstanding,
        }
    
    contributions = Payment.objects.filter(
        chama=membership.chama,
        member=membership.user,
        status=PaymentStatus.COMPLETED,
    ).order_by("-payment_date")[:1]
    
    if contributions.exists():
        last_contribution = contributions.first()
        total_receivable += last_contribution.amount
        breakdown["last_contribution"] = {
            "description": "Last contribution amount (for payout calculation)",
            "amount": last_contribution.amount,
        }
    
    wallet_balance = Decimal("0.00")
    try:
        wallet = Wallet.objects.get(
            user=membership.user,
            chama=membership.chama,
        )
        wallet_balance = wallet.balance or Decimal("0.00")
    except Wallet.DoesNotExist:
        pass
    
    if wallet_balance > Decimal("0.00"):
        total_receivable += wallet_balance
        breakdown["wallet_balance"] = {
            "description": "Wallet balance refund",
            "amount": wallet_balance,
        }
    elif wallet_balance < Decimal("0.00"):
        total_dues += abs(wallet_balance)
        breakdown["wallet_balance"] = {
            "description": "Wallet balance owed",
            "amount": abs(wallet_balance),
        }
    
    net_settlement = total_receivable - total_dues
    
    return ExitSettlement(
        member_id=str(membership.user_id),
        member_name=membership.user.full_name if membership.user else "Unknown",
        chama_id=str(membership.chama_id),
        chama_name=membership.chama.name,
        total_dues=total_dues,
        total_receivable=total_receivable,
        net_settlement=net_settlement,
        breakdown=breakdown,
    )