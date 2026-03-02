"""
Risk Scoring Engine for Digital Chama

Calculates loan risk scores based on:
- Contribution consistency
- Payment history
- Debt ratio
- Withdrawal patterns
"""

from datetime import timedelta
from decimal import Decimal
from typing import Optional

from django.db.models import Avg, Count, Q
from django.utils import timezone

from apps.ai.models import RiskLevel, RiskProfile
from apps.chama.models import Membership
from apps.finance.models import LedgerEntry, Loan, Repayment


class RiskEngine:
    """
    Risk scoring engine using weighted algorithm.
    
    Score Components:
    - Contribution Consistency: 30%
    - Payment History: 30%
    - Debt Ratio: 20%
    - Withdrawal Frequency: 20%
    """
    
    # Weights for each component
    WEIGHTS = {
        "contribution_consistency": 0.30,
        "payment_history": 0.30,
        "debt_ratio": 0.20,
        "withdrawal_frequency": 0.20,
    }
    
    # Risk thresholds
    LOW_RISK_MAX = 70
    MEDIUM_RISK_MAX = 85
    
    @classmethod
    def calculate_risk_profile(
        cls,
        user,
        chama,
        force_refresh: bool = False,
    ) -> RiskProfile:
        """
        Calculate and update risk profile for user in chama.
        """
        # Check if profile exists
        profile, created = RiskProfile.objects.get_or_create(
            user=user,
            chama=chama,
            defaults={"risk_score": 50, "risk_level": RiskLevel.MEDIUM},
        )
        
        # Skip if recently calculated (unless force refresh)
        if not force_refresh and profile.last_calculated:
            hours_since = (timezone.now() - profile.last_calculated).total_seconds() / 3600
            if hours_since < 24:
                return profile
        
        # Calculate component scores
        contribution_score = cls._calculate_contribution_score(user, chama)
        payment_score = cls._calculate_payment_score(user, chama)
        debt_ratio, debt_score = cls._calculate_debt_ratio(user, chama)
        withdrawal_score = cls._calculate_withdrawal_score(user, chama)
        
        # Calculate weighted total
        total_score = (
            contribution_score * cls.WEIGHTS["contribution_consistency"] +
            payment_score * cls.WEIGHTS["payment_history"] +
            debt_score * cls.WEIGHTS["debt_ratio"] +
            withdrawal_score * cls.WEIGHTS["withdrawal_frequency"]
        )
        
        # Determine risk level
        if total_score <= cls.LOW_RISK_MAX:
            risk_level = RiskLevel.LOW
            loan_multiplier = Decimal("3.0")
        elif total_score <= cls.MEDIUM_RISK_MAX:
            risk_level = RiskLevel.MEDIUM
            loan_multiplier = Decimal("2.0")
        else:
            risk_level = RiskLevel.HIGH
            loan_multiplier = Decimal("1.0")
        
        # Update profile
        profile.risk_score = int(total_score)
        profile.risk_level = risk_level
        profile.contribution_consistency_score = contribution_score
        profile.payment_history_score = payment_score
        profile.debt_ratio = debt_ratio
        profile.withdrawal_frequency_score = withdrawal_score
        profile.loan_multiplier = loan_multiplier
        profile.save()
        
        return profile
    
    @classmethod
    def _calculate_contribution_score(
        cls,
        user,
        chama,
        months: int = 6,
    ) -> int:
        """
        Calculate contribution consistency score (0-100).
        Higher score = more consistent contributions.
        """
        from django.utils import timezone
        from datetime import timedelta
        
        cutoff = timezone.now() - timedelta(days=months * 30)
        
        # Get membership
        try:
            membership = Membership.objects.get(user=user, chama=chama)
        except Membership.DoesNotExist:
            return 0
        
        # Get expected contribution dates (monthly)
        expected_contributions = months
        if membership.join_date:
            months_active = (timezone.now().date() - membership.join_date).days // 30
            expected_contributions = min(months_active, months)
        
        # Get actual contributions
        actual_contributions = LedgerEntry.objects.filter(
            owner=user,
            chama=chama,
            entry_type=LedgerEntry.ENTRY_CONTRIBUTION,
            status=LedgerEntry.STATUS_SUCCESS,
            created_at__gte=cutoff,
        ).values("created_at__date").distinct().count()
        
        if expected_contributions == 0:
            return 50  # Default for new members
        
        # Calculate percentage
        percentage = (actual_contributions / expected_contributions) * 100
        return min(int(percentage), 100)
    
    @classmethod
    def _calculate_payment_score(
        cls,
        user,
        chama,
        months: int = 6,
    ) -> int:
        """
        Calculate payment history score (0-100).
        Based on loan repayment punctuality.
        """
        from django.utils import timezone
        from datetime import timedelta
        
        cutoff = timezone.now() - timedelta(days=months * 30)
        
        # Get loans in this chama
        loans = Loan.objects.filter(
            borrower=user,
            chama=chama,
            status__in=[Loan.STATUS_APPROVED, Loan.STATUS_ACTIVE],
        )
        
        if not loans.exists():
            return 75  # No loans = neutral-good score
        
        # Get repayments
        repayments = Repayment.objects.filter(
            loan__in=loans,
            created_at__gte=cutoff,
        )
        
        total_expected = loans.count() * months
        total_paid = repayments.count()
        
        if total_expected == 0:
            return 75
        
        percentage = (total_paid / total_expected) * 100
        return min(int(percentage), 100)
    
    @classmethod
    def _calculate_debt_ratio(
        cls,
        user,
        chama,
    ) -> tuple[Decimal, int]:
        """
        Calculate debt-to-contribution ratio.
        Returns (ratio, score) where ratio is Decimal and score is 0-100.
        """
        from django.db.models import Sum
        
        # Get total contributions
        total_contributions = LedgerEntry.objects.filter(
            owner=user,
            chama=chama,
            entry_type=LedgerEntry.ENTRY_CONTRIBUTION,
            status=LedgerEntry.STATUS_SUCCESS,
        ).aggregate(Sum("amount"))["amount__sum"] or Decimal("0")
        
        if total_contributions == 0:
            return Decimal("0"), 50
        
        # Get total outstanding debt
        outstanding_debt = Loan.objects.filter(
            borrower=user,
            chama=chama,
            status__in=[Loan.STATUS_ACTIVE, Loan.STATUS_APPROVED],
        ).aggregate(Sum("amount"))["amount__sum"] or Decimal("0")
        
        # Calculate ratio
        ratio = outstanding_debt / total_contributions
        
        # Convert to score (lower ratio = higher score)
        # ratio 0 = 100, ratio 1 = 50, ratio > 1 = decreasing
        if ratio <= 0:
            score = 100
        elif ratio >= 1:
            score = max(0, 50 - int((ratio - 1) * 50))
        else:
            score = int(100 - (ratio * 50))
        
        return ratio, max(0, min(score, 100))
    
    @classmethod
    def _calculate_withdrawal_score(
        cls,
        user,
        chama,
        months: int = 6,
    ) -> int:
        """
        Calculate withdrawal frequency score (0-100).
        High withdrawals = lower score (risky behavior).
        """
        from django.utils import timezone
        from datetime import timedelta
        
        cutoff = timezone.now() - timedelta(days=months * 30)
        
        # Get withdrawals
        withdrawals = LedgerEntry.objects.filter(
            owner=user,
            chama=chama,
            entry_type=LedgerEntry.ENTRY_WITHDRAWAL,
            status=LedgerEntry.STATUS_SUCCESS,
            created_at__gte=cutoff,
        ).count()
        
        # Expected: 1 withdrawal per 3 months = 2 per 6 months
        expected = months // 3
        
        if withdrawals <= expected:
            return 100
        
        # Calculate penalty
        excess = withdrawals - expected
        penalty = min(excess * 10, 60)  # Max 60 point penalty
        
        return max(40, 100 - penalty)
    
    @classmethod
    def calculate_loan_eligibility(
        cls,
        user,
        chama,
    ) -> dict:
        """
        Calculate loan eligibility based on risk profile.
        """
        # Get or calculate risk profile
        profile = cls.calculate_risk_profile(user, chama)
        
        # Get chama's contribution settings
        from apps.chama.models import Chama
        try:
            chama_obj = Chama.objects.get(id=chama.id)
            min_contribution = chama_obj.minimum_contribution or Decimal("5000")
            avg_contribution = chama_obj.average_contribution or Decimal("10000")
        except Chama.DoesNotExist:
            min_contribution = Decimal("5000")
            avg_contribution = Decimal("10000")
        
        # Calculate max loan amount
        base_max = avg_contribution * profile.loan_multiplier
        
        # Adjust based on membership age
        try:
            membership = Membership.objects.get(user=user, chama=chama)
            if membership.join_date:
                months_active = (timezone.now().date() - membership.join_date).days // 30
                # Increase limit for long-term members
                if months_active >= 12:
                    base_max *= Decimal("1.5")
                elif months_active >= 6:
                    base_max *= Decimal("1.2")
        except Membership.DoesNotExist:
            pass
        
        # Check eligibility
        eligible = profile.risk_level != RiskLevel.HIGH
        
        # Build risk factors
        risk_factors = []
        if profile.contribution_consistency_score < 60:
            risk_factors.append("Inconsistent contributions")
        if profile.payment_history_score < 60:
            risk_factors.append("Poor loan repayment history")
        if profile.debt_ratio > Decimal("0.5"):
            risk_factors.append("High debt-to-contribution ratio")
        if profile.withdrawal_frequency_score < 60:
            risk_factors.append("Frequent withdrawals")
        
        # Determine ineligibility reason
        ineligibility_reason = ""
        if not eligible:
            if profile.risk_score > 90:
                ineligibility_reason = "Very high risk score"
            elif profile.debt_ratio > Decimal("1.0"):
                ineligibility_reason = "Debt exceeds total contributions"
            else:
                ineligibility_reason = "Risk profile does not meet eligibility requirements"
        
        # Calculate suggested amount (80% of max)
        suggested_amount = base_max * Decimal("0.8")
        
        # Calculate suggested term based on risk
        if profile.risk_level == RiskLevel.LOW:
            suggested_term = 12
            interest_rate = Decimal("10.0")  # Best rate
        elif profile.risk_level == RiskLevel.MEDIUM:
            suggested_term = 6
            interest_rate = Decimal("12.0")
        else:
            suggested_term = 3
            interest_rate = Decimal("15.0")
        
        return {
            "max_loan_amount": min(base_max, chama_obj.max_loan_amount or Decimal("500000")),
            "suggested_amount": suggested_amount,
            "eligible": eligible,
            "ineligibility_reason": ineligibility_reason,
            "risk_factors": risk_factors,
            "suggested_term_months": suggested_term,
            "interest_rate": interest_rate,
            "risk_score": profile.risk_score,
            "risk_level": profile.risk_level,
        }
