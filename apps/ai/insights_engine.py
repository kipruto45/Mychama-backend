"""
Smart Insights Engine for Digital Chama

Generates AI-powered insights:
- Contribution trends
- Payment predictions
- Fund projections
- Member behavior analysis
- Loan risk analysis
"""

from datetime import timedelta
from decimal import Decimal
from typing import Optional

from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone

from apps.ai.models import AIInsight
from apps.chama.models import Chama
from apps.finance.models import LedgerEntry, Loan


class InsightsEngine:
    """
    Smart insights generation engine.
    """
    
    @classmethod
    def generate_all_insights(cls, chama_id: int) -> list[AIInsight]:
        """
        Generate all insights for a chama.
        """
        chama = Chama.objects.get(id=chama_id)
        insights = []
        
        insight_generators = [
            cls.generate_contribution_trend,
            cls.generate_payment_prediction,
            cls.generate_fund_projection,
            cls.generate_member_behavior_insights,
            cls.generate_loan_risk_insights,
        ]
        
        for generator in insight_generators:
            try:
                insight = generator(chama)
                if insight:
                    insights.append(insight)
            except Exception as e:
                print(f"Error generating insight: {e}")
        
        return insights
    
    @classmethod
    def generate_contribution_trend(cls, chama: Chama) -> AIInsight:
        """
        Analyze contribution trends over time.
        """
        now = timezone.now()
        months = 6
        
        # Get monthly contributions
        monthly_data = []
        for i in range(months, 0, -1):
            month_start = (now - timedelta(days=30 * i)).replace(day=1)
            month_end = (now - timedelta(days=30 * (i - 1))).replace(day=1)
            
            total = LedgerEntry.objects.filter(
                chama=chama,
                entry_type=LedgerEntry.ENTRY_CONTRIBUTION,
                status=LedgerEntry.STATUS_SUCCESS,
                created_at__gte=month_start,
                created_at__lt=month_end,
            ).aggregate(Sum("amount"))["amount__sum"] or Decimal("0")
            
            count = LedgerEntry.objects.filter(
                chama=chama,
                entry_type=LedgerEntry.ENTRY_CONTRIBUTION,
                status=LedgerEntry.STATUS_SUCCESS,
                created_at__gte=month_start,
                created_at__lt=month_end,
            ).count()
            
            monthly_data.append({
                "month": month_start.strftime("%b %Y"),
                "total": float(total),
                "count": count,
            })
        
        # Calculate trend
        if len(monthly_data) >= 2:
            recent = monthly_data[-1]["total"]
            previous = monthly_data[-2]["total"]
            if previous > 0:
                change_pct = ((recent - previous) / previous) * 100
                trend = "up" if change_pct > 0 else "down"
            else:
                change_pct = 0
                trend = "stable"
        else:
            change_pct = 0
            trend = "stable"
        
        # Generate description
        if trend == "up":
            description = (
                f"Contributions are trending UP by {abs(change_pct):.1f}%. "
                f"Total of KES {recent:,.0f} collected this month."
            )
        elif trend == "down":
            description = (
                f"Contributions are trending DOWN by {abs(change_pct):.1f}%. "
                f"Consider reminding members about their obligations."
            )
        else:
            description = "Contribution levels have remained stable."
        
        # Recommendations
        recommendations = []
        if trend == "down":
            recommendations.append({
                "type": "action",
                "text": "Send contribution reminder notifications",
            })
            recommendations.append({
                "type": "info",
                "text": "Review if minimum contribution amount needs adjustment",
            })
        
        # Create or update insight
        insight, _ = AIInsight.objects.update_or_create(
            chama=chama,
            insight_type="contribution_trend",
            defaults={
                "title": "Contribution Trend Analysis",
                "description": description,
                "chart_data": {
                    "type": "line",
                    "data": monthly_data,
                    "trend": trend,
                    "change_percent": float(change_pct),
                },
                "recommendations": recommendations,
            },
        )
        
        return insight
    
    @classmethod
    def generate_payment_prediction(cls, chama: Chama) -> AIInsight:
        """
        Predict upcoming payment obligations.
        """
        now = timezone.now()
        
        # Get active loans
        active_loans = Loan.objects.filter(
            chama=chama,
            status__in=[Loan.STATUS_ACTIVE, Loan.STATUS_APPROVED],
        )
        
        if not active_loans.exists():
            return None
        
        # Calculate monthly payment obligations
        total_monthly = sum(
            loan.monthly_repayment for loan in active_loans
        )
        
        # Count upcoming repayments (next 7 days)
        week_later = now + timedelta(days=7)
        upcoming = Loan.objects.filter(
            chama=chama,
            status=Loan.STATUS_ACTIVE,
            next_repayment_date__lte=week_later,
        ).count()
        
        # Predict likelihood of full payment
        # Based on historical payment success rate
        recent_repayments = Loan.objects.filter(
            chama=chama,
            status=Loan.STATUS_ACTIVE,
        ).aggregate(
            total=Count("id"),
            completed=Count("id", filter=Q(loanrepayment__status="completed")),
        )
        
        if recent_repayments["total"] > 0:
            success_rate = (
                recent_repayments["completed"] / recent_repayments["total"]
            ) * 100
        else:
            success_rate = 100
        
        description = (
            f"Monthly payment obligation: KES {total_monthly:,.0f}. "
            f"{upcoming} loan repayment(s) due in next 7 days. "
            f"Historical payment success rate: {success_rate:.0f}%"
        )
        
        recommendations = []
        if success_rate < 80:
            recommendations.append({
                "type": "warning",
                "text": "Payment success rate is below 80% - consider payment reminders",
            })
        
        insight, _ = AIInsight.objects.update_or_create(
            chama=chama,
            insight_type="payment_prediction",
            defaults={
                "title": "Payment Obligations Forecast",
                "description": description,
                "chart_data": {
                    "monthly_obligation": float(total_monthly),
                    "upcoming_count": upcoming,
                    "success_rate": float(success_rate),
                },
                "recommendations": recommendations,
            },
        )
        
        return insight
    
    @classmethod
    def generate_fund_projection(cls, chama: Chama) -> AIInsight:
        """
        Project chama fund balance for next N months.
        """
        months_to_project = 6
        now = timezone.now()
        
        # Get current balance
        current_balance = LedgerEntry.objects.filter(
            chama=chama,
            status=LedgerEntry.STATUS_SUCCESS,
        ).aggregate(Sum("amount"))["amount__sum"] or Decimal("0")
        
        # Withdrawals
        total_withdrawals = LedgerEntry.objects.filter(
            chama=chama,
            entry_type=LedgerEntry.ENTRY_WITHDRAWAL,
            status=LedgerEntry.STATUS_SUCCESS,
        ).aggregate(Sum("amount"))["amount__sum"] or Decimal("0")
        
        net_balance = current_balance - total_withdrawals
        
        # Average monthly contribution
        six_months_ago = now - timedelta(days=180)
        monthly_avg = LedgerEntry.objects.filter(
            chama=chama,
            entry_type=LedgerEntry.ENTRY_CONTRIBUTION,
            status=LedgerEntry.STATUS_SUCCESS,
            created_at__gte=six_months_ago,
        ).aggregate(Avg("amount"))["amount__avg"] or Decimal("0")
        
        # Monthly loan disbursement average
        monthly_loan_avg = Loan.objects.filter(
            chama=chama,
            status__in=[Loan.STATUS_ACTIVE, Loan.STATUS_APPROVED],
            created_at__gte=six_months_ago,
        ).aggregate(Avg("amount"))["amount__avg"] or Decimal("0")
        
        # Project forward
        projection = []
        running_balance = net_balance
        for i in range(1, months_to_project + 1):
            projected_date = (now + timedelta(days=30 * i)).strftime("%b %Y")
            
            # Simple projection: balance + expected contributions - expected loans
            # This is a simplified model
            running_balance = running_balance + monthly_avg - monthly_loan_avg
            
            projection.append({
                "month": projected_date,
                "projected_balance": float(running_balance),
            })
        
        # Determine trend
        if running_balance > net_balance * Decimal("1.5"):
            trend = "growing"
        elif running_balance < 0:
            trend = "depleting"
        else:
            trend = "stable"
        
        description = (
            f"Current net balance: KES {net_balance:,.0f}. "
            f"Monthly average contributions: KES {monthly_avg:,.0f}. "
            f"Monthly average loans: KES {monthly_loan_avg:,.0f}. "
            f"Projected balance in {months_to_project} months: KES {running_balance:,.0f}"
        )
        
        recommendations = []
        if trend == "depleting":
            recommendations.append({
                "type": "warning",
                "text": "Fund may be depleted soon - consider increasing contribution requirements",
            })
        
        insight, _ = AIInsight.objects.update_or_create(
            chama=chama,
            insight_type="fund_projection",
            defaults={
                "title": "Chama Fund Projection",
                "description": description,
                "chart_data": {
                    "current_balance": float(net_balance),
                    "monthly_avg_contribution": float(monthly_avg),
                    "monthly_avg_loan": float(monthly_loan_avg),
                    "projection": projection,
                    "trend": trend,
                },
                "recommendations": recommendations,
            },
        )
        
        return insight
    
    @classmethod
    def generate_member_behavior_insights(cls, chama: Chama) -> AIInsight:
        """
        Analyze member behavior patterns.
        """
        from apps.chama.models import Membership
        
        now = timezone.now()
        six_months_ago = now - timedelta(days=180)
        
        # Get all members
        members = Membership.objects.filter(
            chama=chama,
            status=Membership.STATUS_ACTIVE,
        )
        
        # Categorize members
        consistent = []
        inconsistent = []
        new_members = []
        
        for member in members:
            # Check contribution consistency
            contributions = LedgerEntry.objects.filter(
                owner=member.user,
                chama=chama,
                entry_type=LedgerEntry.ENTRY_CONTRIBUTION,
                status=LedgerEntry.STATUS_SUCCESS,
                created_at__gte=six_months_ago,
            ).count()
            
            if member.join_date and (now.date() - member.join_date).days < 90:
                new_members.append(member.user_id)
            elif contributions >= 5:
                consistent.append(member.user_id)
            else:
                inconsistent.append(member.user_id)
        
        total_members = members.count()
        
        # Generate description
        description = (
            f"Total active members: {total_members}. "
            f"Consistent contributors: {len(consistent)} ({len(consistent)/total_members*100:.0f}%). "
            f"Needs attention: {len(inconsistent)} ({len(inconsistent)/total_members*100:.0f}%). "
            f"New members (last 90 days): {len(new_members)}."
        )
        
        recommendations = []
        if len(inconsistent) > total_members * 0.3:
            recommendations.append({
                "type": "action",
                "text": f"Follow up with {len(inconsistent)} members with inconsistent contributions",
            })
        
        insight, _ = AIInsight.objects.update_or_create(
            chama=chama,
            insight_type="member_behavior",
            defaults={
                "title": "Member Behavior Analysis",
                "description": description,
                "chart_data": {
                    "total_members": total_members,
                    "consistent": len(consistent),
                    "inconsistent": len(inconsistent),
                    "new_members": len(new_members),
                },
                "recommendations": recommendations,
            },
        )
        
        return insight
    
    @classmethod
    def generate_loan_risk_insights(cls, chama: Chama) -> AIInsight:
        """
        Analyze loan portfolio risk.
        """
        from apps.ai.models import RiskProfile, RiskLevel
        
        # Get all risk profiles
        risk_profiles = RiskProfile.objects.filter(chama=chama)
        
        if not risk_profiles.exists():
            return None
        
        risk_counts = {
            RiskLevel.LOW: 0,
            RiskLevel.MEDIUM: 0,
            RiskLevel.HIGH: 0,
        }
        
        avg_score = 0
        for profile in risk_profiles:
            risk_counts[profile.risk_level] += 1
            avg_score += profile.risk_score
        
        avg_score /= risk_profiles.count()
        
        # Get loan stats
        active_loans = Loan.objects.filter(
            chama=chama,
            status=Loan.STATUS_ACTIVE,
        )
        
        total_disbursed = sum(loan.amount for loan in active_loans)
        total_expected_repayment = sum(
            loan.total_repayment for loan in active_loans
        )
        
        # Calculate portfolio health
        if total_expected_repayment > 0:
            health_score = (
                (total_expected_repayment - total_disbursed) / total_expected_repayment
            ) * 100
        else:
            health_score = 100
        
        description = (
            f"Active loans: {active_loans.count()}. "
            f"Total disbursed: KES {total_disbursed:,.0f}. "
            f"Expected returns: KES {total_expected_repayment:,.0f}. "
            f"Average risk score: {avg_score:.0f}/100. "
            f"Portfolio health: {health_score:.0f}%"
        )
        
        recommendations = []
        if health_score < 50:
            recommendations.append({
                "type": "critical",
                "text": "Portfolio health is concerning - review lending criteria",
            })
        
        if risk_counts[RiskLevel.HIGH] > risk_profiles.count() * 0.3:
            recommendations.append({
                "type": "warning",
                "text": "High proportion of high-risk members - tighten eligibility",
            })
        
        insight, _ = AIInsight.objects.update_or_create(
            chama=chama,
            insight_type="loan_risk",
            defaults={
                "title": "Loan Portfolio Risk Analysis",
                "description": description,
                "chart_data": {
                    "total_loans": active_loans.count(),
                    "total_disbursed": float(total_disbursed),
                    "expected_return": float(total_expected_repayment),
                    "health_score": float(health_score),
                    "risk_distribution": {
                        "low": risk_counts[RiskLevel.LOW],
                        "medium": risk_counts[RiskLevel.MEDIUM],
                        "high": risk_counts[RiskLevel.HIGH],
                    },
                },
                "recommendations": recommendations,
            },
        )
        
        return insight
