"""
Smart Scoring and Analytics Service

Manages scoring formulas, analytics, and insight generation.
"""

import logging

from django.db import models

from apps.accounts.models import User
from apps.chama.models import Chama, Membership

logger = logging.getLogger(__name__)


class ScoringService:
    """Service for managing smart scoring and analytics."""

    @staticmethod
    def calculate_member_reliability_score(
        chama: Chama,
        user: User,
    ) -> dict:
        """
        Calculate reliability score for a member.
        Returns score details.
        """
        from django.db.models import Count

        from apps.finance.models import Contribution, Loan
        from apps.meetings.models import Attendance

        # Get contribution history
        contributions = Contribution.objects.filter(
            membership__chama=chama,
            membership__user=user,
        ).aggregate(
            total=Count('id'),
            paid=Count('id', filter=models.Q(status='paid')),
            overdue=Count('id', filter=models.Q(status='overdue')),
        )

        # Get loan history
        loans = Loan.objects.filter(
            chama=chama,
            user=user,
        ).aggregate(
            total=Count('id'),
            repaid=Count('id', filter=models.Q(status='repaid')),
            overdue=Count('id', filter=models.Q(status='overdue')),
        )

        # Get attendance history
        attendance = Attendance.objects.filter(
            meeting__chama=chama,
            user=user,
        ).aggregate(
            total=Count('id'),
            present=Count('id', filter=models.Q(status='present')),
        )

        # Calculate scores
        contribution_score = 0
        if contributions['total'] > 0:
            contribution_score = (contributions['paid'] / contributions['total']) * 100

        loan_score = 0
        if loans['total'] > 0:
            loan_score = (loans['repaid'] / loans['total']) * 100

        attendance_score = 0
        if attendance['total'] > 0:
            attendance_score = (attendance['present'] / attendance['total']) * 100

        # Calculate overall score (weighted average)
        overall_score = (
            contribution_score * 0.4 +
            loan_score * 0.3 +
            attendance_score * 0.3
        )

        return {
            'overall_score': round(overall_score, 2),
            'contribution_score': round(contribution_score, 2),
            'loan_score': round(loan_score, 2),
            'attendance_score': round(attendance_score, 2),
            'details': {
                'total_contributions': contributions['total'],
                'paid_contributions': contributions['paid'],
                'overdue_contributions': contributions['overdue'],
                'total_loans': loans['total'],
                'repaid_loans': loans['repaid'],
                'overdue_loans': loans['overdue'],
                'total_meetings': attendance['total'],
                'attended_meetings': attendance['present'],
            },
        }

    @staticmethod
    def calculate_chama_health_score(chama: Chama) -> dict:
        """
        Calculate health score for a chama.
        Returns score details.
        """
        from django.db.models import Count

        from apps.chama.models import Membership
        from apps.finance.models import Account, Contribution, Loan

        # Get account balance
        account = Account.objects.filter(chama=chama, account_type='main').first()
        balance = account.balance if account else 0

        # Get contribution compliance
        contributions = Contribution.objects.filter(
            membership__chama=chama,
        ).aggregate(
            total=Count('id'),
            paid=Count('id', filter=models.Q(status='paid')),
            overdue=Count('id', filter=models.Q(status='overdue')),
        )

        # Get loan health
        loans = Loan.objects.filter(chama=chama).aggregate(
            total=Count('id'),
            active=Count('id', filter=models.Q(status='active')),
            overdue=Count('id', filter=models.Q(status='overdue')),
            repaid=Count('id', filter=models.Q(status='repaid')),
        )

        # Get member engagement
        members = Membership.objects.filter(chama=chama, status='active')
        member_count = members.count()

        # Calculate scores
        balance_score = min(100, (balance / 10000) * 100) if balance > 0 else 0

        compliance_score = 0
        if contributions['total'] > 0:
            compliance_score = (contributions['paid'] / contributions['total']) * 100

        loan_health_score = 0
        if loans['total'] > 0:
            loan_health_score = (loans['repaid'] / loans['total']) * 100

        # Calculate overall score
        overall_score = (
            balance_score * 0.3 +
            compliance_score * 0.4 +
            loan_health_score * 0.3
        )

        return {
            'overall_score': round(overall_score, 2),
            'balance_score': round(balance_score, 2),
            'compliance_score': round(compliance_score, 2),
            'loan_health_score': round(loan_health_score, 2),
            'details': {
                'balance': balance,
                'member_count': member_count,
                'total_contributions': contributions['total'],
                'paid_contributions': contributions['paid'],
                'overdue_contributions': contributions['overdue'],
                'total_loans': loans['total'],
                'active_loans': loans['active'],
                'overdue_loans': loans['overdue'],
                'repaid_loans': loans['repaid'],
            },
        }

    @staticmethod
    def calculate_loan_risk_score(
        chama: Chama,
        user: User,
        loan_amount: float,
    ) -> dict:
        """
        Calculate risk score for a loan application.
        Returns risk assessment.
        """
        from django.db.models import Count, Sum

        from apps.finance.models import Contribution, Loan

        # Get member's contribution history
        contributions = Contribution.objects.filter(
            membership__chama=chama,
            membership__user=user,
        ).aggregate(
            total=Sum('amount'),
            paid=Sum('amount_paid'),
            count=Count('id'),
        )

        # Get member's loan history
        loans = Loan.objects.filter(
            chama=chama,
            user=user,
        ).aggregate(
            total=Count('id'),
            repaid=Count('id', filter=models.Q(status='repaid')),
            overdue=Count('id', filter=models.Q(status='overdue')),
        )

        # Calculate risk factors
        contribution_ratio = 0
        if contributions['total'] and contributions['total'] > 0:
            contribution_ratio = (contributions['paid'] or 0) / contributions['total']

        loan_repayment_ratio = 0
        if loans['total'] and loans['total'] > 0:
            loan_repayment_ratio = (loans['repaid'] or 0) / loans['total']

        # Calculate risk score (0-100, lower is better)
        risk_score = 50  # Base score

        # Adjust based on contribution history
        if contribution_ratio >= 0.9:
            risk_score -= 20
        elif contribution_ratio >= 0.7:
            risk_score -= 10
        elif contribution_ratio < 0.5:
            risk_score += 20

        # Adjust based on loan history
        if loans['overdue'] and loans['overdue'] > 0:
            risk_score += 30
        elif loan_repayment_ratio >= 0.9:
            risk_score -= 15
        elif loan_repayment_ratio < 0.5:
            risk_score += 15

        # Adjust based on loan amount
        if contributions['total'] and contributions['total'] > 0:
            amount_ratio = loan_amount / contributions['total']
            if amount_ratio > 3:
                risk_score += 25
            elif amount_ratio > 2:
                risk_score += 15

        # Clamp score
        risk_score = max(0, min(100, risk_score))

        # Determine risk level
        if risk_score < 30:
            risk_level = 'low'
        elif risk_score < 60:
            risk_level = 'medium'
        else:
            risk_level = 'high'

        return {
            'risk_score': risk_score,
            'risk_level': risk_level,
            'factors': {
                'contribution_ratio': contribution_ratio,
                'loan_repayment_ratio': loan_repayment_ratio,
                'overdue_loans': loans['overdue'] or 0,
                'amount_ratio': loan_amount / (contributions['total'] or 1),
            },
        }

    @staticmethod
    def get_engagement_score(chama: Chama, user: User) -> dict:
        """
        Calculate engagement score for a member.
        """

        from apps.finance.models import Contribution
        from apps.governance.models import Vote
        from apps.meetings.models import Attendance, Meeting

        # Get contribution activity
        contributions = Contribution.objects.filter(
            membership__chama=chama,
            membership__user=user,
        ).count()

        # Get attendance
        attendance = Attendance.objects.filter(
            meeting__chama=chama,
            user=user,
            status='present',
        ).count()

        # Get total meetings
        total_meetings = Meeting.objects.filter(chama=chama).count()

        # Get voting participation
        votes = Vote.objects.filter(
            motion__chama=chama,
            user=user,
        ).count()

        # Calculate engagement score
        engagement_score = 0

        # Contribution activity (0-40 points)
        if contributions > 0:
            engagement_score += min(40, contributions * 5)

        # Attendance (0-40 points)
        if total_meetings > 0:
            attendance_rate = attendance / total_meetings
            engagement_score += attendance_rate * 40

        # Voting participation (0-20 points)
        if votes > 0:
            engagement_score += min(20, votes * 10)

        return {
            'engagement_score': round(engagement_score, 2),
            'details': {
                'contributions': contributions,
                'attendance': attendance,
                'total_meetings': total_meetings,
                'votes': votes,
            },
        }

    @staticmethod
    def get_smart_insights(chama: Chama) -> list[dict]:
        """
        Generate smart insights for a chama.
        """
        insights = []

        # Get chama health
        health = ScoringService.calculate_chama_health_score(chama)

        if health['overall_score'] < 50:
            insights.append({
                'type': 'warning',
                'title': 'Low Chama Health Score',
                'message': f'Your chama health score is {health["overall_score"]:.1f}. Consider improving contribution compliance.',
                'priority': 'high',
            })

        # Check for overdue contributions
        from apps.finance.models import Contribution
        overdue = Contribution.objects.filter(
            membership__chama=chama,
            status='overdue',
        ).count()

        if overdue > 0:
            insights.append({
                'type': 'alert',
                'title': 'Overdue Contributions',
                'message': f'{overdue} contributions are overdue. Follow up with members.',
                'priority': 'high',
            })

        # Check for overdue loans
        from apps.finance.models import Loan
        overdue_loans = Loan.objects.filter(
            chama=chama,
            status='overdue',
        ).count()

        if overdue_loans > 0:
            insights.append({
                'type': 'alert',
                'title': 'Overdue Loans',
                'message': f'{overdue_loans} loans are overdue. Review repayment status.',
                'priority': 'high',
            })

        return insights

    @staticmethod
    def get_member_rankings(chama: Chama) -> list[dict]:
        """
        Get member rankings based on reliability scores.
        """
        members = Membership.objects.filter(
            chama=chama,
            status='active',
        ).select_related('user')

        rankings = []
        for member in members:
            score = ScoringService.calculate_member_reliability_score(
                chama=chama,
                user=member.user,
            )
            rankings.append({
                'user_id': str(member.user.id),
                'user_name': member.user.full_name,
                'overall_score': score['overall_score'],
                'contribution_score': score['contribution_score'],
                'loan_score': score['loan_score'],
                'attendance_score': score['attendance_score'],
            })

        # Sort by overall score
        rankings.sort(key=lambda x: x['overall_score'], reverse=True)

        # Add rank
        for i, ranking in enumerate(rankings):
            ranking['rank'] = i + 1

        return rankings
