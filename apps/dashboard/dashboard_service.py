"""
Dashboard and Smart Insights Service

Manages dashboard data, smart cards, and insights.
"""

import logging

from django.db import models
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama, Membership

logger = logging.getLogger(__name__)


class DashboardService:
    """Service for managing dashboard and insights."""

    @staticmethod
    def get_user_dashboard(user: User) -> dict:
        """
        Get dashboard data for a user.
        """
        from django.db.models import Sum

        from apps.finance.models import Account, Contribution, Loan
        from apps.meetings.models import Meeting

        # Get user's chamas
        memberships = Membership.objects.filter(
            user=user,
            status='active',
        ).select_related('chama')

        chamas_data = []
        total_balance = 0
        total_contributions = 0
        total_loans = 0

        for membership in memberships:
            chama = membership.chama

            # Get account balance
            account = Account.objects.filter(chama=chama, account_type='main').first()
            balance = account.balance if account else 0
            total_balance += balance

            # Get contributions
            contributions = Contribution.objects.filter(
                membership__chama=chama,
                membership__user=user,
            ).aggregate(
                total=Sum('amount'),
                paid=Sum('amount_paid'),
            )
            total_contributions += contributions['paid'] or 0

            # Get loans
            loans = Loan.objects.filter(
                chama=chama,
                user=user,
            ).aggregate(
                total_borrowed=Sum('principal_amount'),
                total_repaid=Sum('amount_repaid'),
            )
            total_loans += (loans['total_borrowed'] or 0) - (loans['total_repaid'] or 0)

            chamas_data.append({
                'id': str(chama.id),
                'name': chama.name,
                'balance': balance,
                'my_contributions': contributions['paid'] or 0,
                'my_loans': (loans['total_borrowed'] or 0) - (loans['total_repaid'] or 0),
                'role': membership.role,
            })

        # Get upcoming meetings
        upcoming_meetings = Meeting.objects.filter(
            chama__in=[m.chama for m in memberships],
            start_time__gt=timezone.now(),
            status='scheduled',
        ).order_by('start_time')[:5]

        # Get pending contributions
        pending_contributions = Contribution.objects.filter(
            membership__user=user,
            status='pending',
        ).select_related('membership__chama')[:5]

        return {
            'total_balance': total_balance,
            'total_contributions': total_contributions,
            'total_loans': total_loans,
            'chamas': chamas_data,
            'upcoming_meetings': [
                {
                    'id': str(meeting.id),
                    'chama_name': meeting.chama.name,
                    'title': meeting.title,
                    'start_time': meeting.start_time.isoformat(),
                }
                for meeting in upcoming_meetings
            ],
            'pending_contributions': [
                {
                    'id': str(contrib.id),
                    'chama_name': contrib.membership.chama.name,
                    'amount': contrib.amount,
                    'due_date': contrib.due_date.isoformat(),
                }
                for contrib in pending_contributions
            ],
        }

    @staticmethod
    def get_chama_dashboard(chama: Chama) -> dict:
        """
        Get dashboard data for a chama.
        """
        from django.db.models import Count, Sum

        from apps.chama.models import Membership
        from apps.finance.models import Account, Contribution, Loan
        from apps.meetings.models import Meeting

        # Get account balance
        account = Account.objects.filter(chama=chama, account_type='main').first()
        balance = account.balance if account else 0

        # Get contribution stats
        contributions = Contribution.objects.filter(
            membership__chama=chama,
        ).aggregate(
            total=Sum('amount'),
            paid=Sum('amount_paid'),
            pending=Count('id', filter=models.Q(status='pending')),
            overdue=Count('id', filter=models.Q(status='overdue')),
        )

        # Get loan stats
        loans = Loan.objects.filter(chama=chama).aggregate(
            total_borrowed=Sum('principal_amount'),
            total_repaid=Sum('amount_repaid'),
            active=Count('id', filter=models.Q(status='active')),
            overdue=Count('id', filter=models.Q(status='overdue')),
        )

        # Get member count
        member_count = Membership.objects.filter(chama=chama, status='active').count()

        # Get upcoming meetings
        upcoming_meetings = Meeting.objects.filter(
            chama=chama,
            start_time__gt=timezone.now(),
            status='scheduled',
        ).order_by('start_time')[:5]

        return {
            'balance': balance,
            'member_count': member_count,
            'contributions': {
                'total': contributions['total'] or 0,
                'paid': contributions['paid'] or 0,
                'pending': contributions['pending'] or 0,
                'overdue': contributions['overdue'] or 0,
            },
            'loans': {
                'total_borrowed': loans['total_borrowed'] or 0,
                'total_repaid': loans['total_repaid'] or 0,
                'active': loans['active'] or 0,
                'overdue': loans['overdue'] or 0,
                'outstanding': (loans['total_borrowed'] or 0) - (loans['total_repaid'] or 0),
            },
            'upcoming_meetings': [
                {
                    'id': str(meeting.id),
                    'title': meeting.title,
                    'start_time': meeting.start_time.isoformat(),
                }
                for meeting in upcoming_meetings
            ],
        }

    @staticmethod
    def get_smart_insights(chama: Chama) -> list[dict]:
        """
        Get smart insights for a chama.
        """
        insights = []

        # Check contribution compliance
        from apps.finance.models import Contribution
        overdue_contributions = Contribution.objects.filter(
            membership__chama=chama,
            status='overdue',
        ).count()

        if overdue_contributions > 0:
            insights.append({
                'type': 'warning',
                'title': 'Overdue Contributions',
                'message': f'{overdue_contributions} contributions are overdue',
                'action': 'Review and follow up with members',
                'priority': 'high',
            })

        # Check loan defaults
        from apps.finance.models import Loan
        overdue_loans = Loan.objects.filter(
            chama=chama,
            status='overdue',
        ).count()

        if overdue_loans > 0:
            insights.append({
                'type': 'alert',
                'title': 'Overdue Loans',
                'message': f'{overdue_loans} loans are overdue',
                'action': 'Review loan repayment status',
                'priority': 'high',
            })

        # Check low balance
        from apps.finance.models import Account
        account = Account.objects.filter(chama=chama, account_type='main').first()
        if account and account.balance < 1000:
            insights.append({
                'type': 'info',
                'title': 'Low Balance',
                'message': 'Chama balance is below KES 1,000',
                'action': 'Consider increasing contributions',
                'priority': 'medium',
            })

        # Check upcoming meetings
        from apps.meetings.models import Meeting
        upcoming_meetings = Meeting.objects.filter(
            chama=chama,
            start_time__gt=timezone.now(),
            start_time__lte=timezone.now() + timezone.timedelta(days=7),
        ).count()

        if upcoming_meetings > 0:
            insights.append({
                'type': 'info',
                'title': 'Upcoming Meetings',
                'message': f'{upcoming_meetings} meetings scheduled this week',
                'action': 'Review meeting agenda',
                'priority': 'low',
            })

        return insights

    @staticmethod
    def get_quick_actions(user: User, chama: Chama = None) -> list[dict]:
        """
        Get quick actions for a user.
        """
        actions = []

        if chama:
            # Check if user can make contributions
            from apps.finance.models import Contribution
            pending_contributions = Contribution.objects.filter(
                membership__chama=chama,
                membership__user=user,
                status='pending',
            ).exists()

            if pending_contributions:
                actions.append({
                    'id': 'make_contribution',
                    'title': 'Make Contribution',
                    'description': 'Pay your pending contribution',
                    'icon': 'payments',
                    'route': '/contribution',
                })

            # Check if user can request loans
            from apps.chama.permissions import Permission, PermissionChecker
            if PermissionChecker.has_permission(user, Permission.CAN_REQUEST_LOANS, str(chama.id)):
                actions.append({
                    'id': 'request_loan',
                    'title': 'Request Loan',
                    'description': 'Apply for a loan',
                    'icon': 'account_balance',
                    'route': '/loan/apply',
                })

        # General actions
        actions.extend([
            {
                'id': 'view_dashboard',
                'title': 'View Dashboard',
                'description': 'See your financial summary',
                'icon': 'dashboard',
                'route': '/dashboard',
            },
            {
                'id': 'view_meetings',
                'title': 'View Meetings',
                'description': 'Check upcoming meetings',
                'icon': 'event',
                'route': '/meetings',
            },
        ])

        return actions

    @staticmethod
    def get_health_score(chama: Chama) -> dict:
        """
        Calculate chama health score.
        """
        from django.db.models import Count

        from apps.finance.models import Account, Contribution, Loan

        score = 100
        factors = []

        # Check contribution compliance
        contributions = Contribution.objects.filter(
            membership__chama=chama,
        ).aggregate(
            total=Count('id'),
            paid=Count('id', filter=models.Q(status='paid')),
        )

        if contributions['total'] > 0:
            compliance_rate = (contributions['paid'] / contributions['total']) * 100
            if compliance_rate < 80:
                score -= 20
                factors.append('Low contribution compliance')
            elif compliance_rate < 90:
                score -= 10
                factors.append('Moderate contribution compliance')

        # Check loan defaults
        loans = Loan.objects.filter(chama=chama).aggregate(
            total=Count('id'),
            overdue=Count('id', filter=models.Q(status='overdue')),
        )

        if loans['total'] > 0:
            default_rate = (loans['overdue'] / loans['total']) * 100
            if default_rate > 10:
                score -= 20
                factors.append('High loan default rate')
            elif default_rate > 5:
                score -= 10
                factors.append('Moderate loan default rate')

        # Check balance
        account = Account.objects.filter(chama=chama, account_type='main').first()
        if account and account.balance < 0:
            score -= 25
            factors.append('Negative balance')

        return {
            'score': max(0, score),
            'rating': (
                'excellent' if score >= 90 else
                'good' if score >= 75 else
                'fair' if score >= 60 else
                'poor' if score >= 40 else
                'critical'
            ),
            'factors': factors,
        }
