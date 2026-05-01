"""
Dashboard and Smart Insights Service

Provides real backend summary endpoints, smart cards, and admin action center.
"""

import logging

from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama, Membership, MemberStatus

logger = logging.getLogger(__name__)


class DashboardService:
    """Service for dashboard data and smart insights."""

    @staticmethod
    def get_user_dashboard(user: User) -> dict:
        """
        Get dashboard data for a user across all chamas.
        """
        # Get user's active chamas
        memberships = Membership.objects.filter(
            user=user,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
        ).select_related('chama')

        chamas_data = []
        total_savings = 0
        total_contributions = 0
        pending_contributions = 0

        for membership in memberships:
            chama_data = DashboardService.get_chama_summary(
                membership.chama,
                user,
            )
            chamas_data.append(chama_data)
            
            total_savings += chama_data.get('balance', 0)
            total_contributions += chama_data.get('total_contributions', 0)
            pending_contributions += chama_data.get('pending_contributions', 0)

        # Get upcoming meetings
        from apps.meetings.models import Meeting
        upcoming_meetings = Meeting.objects.filter(
            chama__in=[m.chama for m in memberships],
            start_time__gte=timezone.now(),
            status='scheduled',
        ).order_by('start_time')[:5]

        # Get recent notifications
        from apps.notifications.models import Notification
        recent_notifications = Notification.objects.filter(
            user=user,
            is_read=False,
        ).order_by('-created_at')[:10]

        return {
            'total_chamas': len(chamas_data),
            'total_savings': total_savings,
            'total_contributions': total_contributions,
            'pending_contributions': pending_contributions,
            'chamas': chamas_data,
            'upcoming_meetings': [
                {
                    'id': str(meeting.id),
                    'chama_name': meeting.chama.name,
                    'title': meeting.title,
                    'start_time': meeting.start_time.isoformat(),
                    'location': meeting.location,
                }
                for meeting in upcoming_meetings
            ],
            'recent_notifications': [
                {
                    'id': str(notif.id),
                    'title': notif.title,
                    'message': notif.message,
                    'type': notif.notification_type,
                    'created_at': notif.created_at.isoformat(),
                }
                for notif in recent_notifications
            ],
        }

    @staticmethod
    def get_chama_summary(chama: Chama, user: User = None) -> dict:
        """
        Get summary data for a chama.
        """
        from apps.finance.models import Account, Contribution, Loan
        from apps.meetings.models import Attendance, Meeting

        # Get balance
        account = Account.objects.filter(chama=chama, account_type='main').first()
        balance = account.balance if account else 0

        # Get contribution stats
        contributions = Contribution.objects.filter(
            membership__chama=chama,
        ).aggregate(
            total=Sum('amount'),
            count=Count('id'),
            pending=Count('id', filter=Q(status='pending')),
            overdue=Count('id', filter=Q(status='overdue')),
        )

        # Get loan stats
        loans = Loan.objects.filter(
            membership__chama=chama,
        ).aggregate(
            total_borrowed=Sum('principal_amount'),
            total_repaid=Sum('amount_repaid'),
            active_count=Count('id', filter=Q(status='active')),
            overdue_count=Count('id', filter=Q(status='overdue')),
        )

        # Get member stats
        members = Membership.objects.filter(chama=chama).aggregate(
            total=Count('id'),
            active=Count('id', filter=Q(status=MemberStatus.ACTIVE)),
            suspended=Count('id', filter=Q(status=MemberStatus.SUSPENDED)),
        )

        # Get meeting stats
        meetings = Meeting.objects.filter(chama=chama).aggregate(
            total=Count('id'),
            upcoming=Count('id', filter=Q(start_time__gte=timezone.now())),
            completed=Count('id', filter=Q(status='completed')),
        )

        # Get attendance rate
        attendance_rate = Attendance.objects.filter(
            membership__chama=chama,
        ).aggregate(
            total=Count('id'),
            present=Count('id', filter=Q(status='present')),
        )

        # Calculate compliance rate
        compliance_rate = 0
        if contributions['count'] > 0:
            paid = contributions['count'] - contributions['pending'] - contributions['overdue']
            compliance_rate = (paid / contributions['count']) * 100

        # Get user-specific data if user provided
        user_data = {}
        if user:
            try:
                membership = Membership.objects.get(chama=chama, user=user)
                user_contributions = Contribution.objects.filter(
                    membership=membership,
                ).aggregate(
                    total=Sum('amount'),
                    pending=Count('id', filter=Q(status='pending')),
                )
                
                user_loans = Loan.objects.filter(
                    membership=membership,
                ).aggregate(
                    total_borrowed=Sum('principal_amount'),
                    total_repaid=Sum('amount_repaid'),
                    active_count=Count('id', filter=Q(status='active')),
                )

                user_data = {
                    'role': membership.role,
                    'my_contributions': user_contributions['total'] or 0,
                    'my_pending': user_contributions['pending'] or 0,
                    'my_loans_borrowed': user_loans['total_borrowed'] or 0,
                    'my_loans_repaid': user_loans['total_repaid'] or 0,
                    'my_active_loans': user_loans['active_count'] or 0,
                }
            except Membership.DoesNotExist:
                pass

        return {
            'chama_id': str(chama.id),
            'chama_name': chama.name,
            'balance': balance,
            'total_contributions': contributions['total'] or 0,
            'contribution_count': contributions['count'] or 0,
            'pending_contributions': contributions['pending'] or 0,
            'overdue_contributions': contributions['overdue'] or 0,
            'compliance_rate': compliance_rate,
            'total_borrowed': loans['total_borrowed'] or 0,
            'total_repaid': loans['total_repaid'] or 0,
            'active_loans': loans['active_count'] or 0,
            'overdue_loans': loans['overdue_count'] or 0,
            'total_members': members['total'] or 0,
            'active_members': members['active'] or 0,
            'suspended_members': members['suspended'] or 0,
            'total_meetings': meetings['total'] or 0,
            'upcoming_meetings': meetings['upcoming'] or 0,
            'completed_meetings': meetings['completed'] or 0,
            'attendance_rate': (
                (attendance_rate['present'] / attendance_rate['total'] * 100)
                if attendance_rate['total'] > 0 else 0
            ),
            **user_data,
        }

    @staticmethod
    def get_smart_insights(chama: Chama) -> list[dict]:
        """
        Get smart insights and recommendations for a chama.
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
            membership__chama=chama,
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

        # Check member engagement
        from apps.meetings.models import Attendance
        low_attendance = Attendance.objects.filter(
            membership__chama=chama,
            status='absent',
        ).count()

        if low_attendance > 5:
            insights.append({
                'type': 'info',
                'title': 'Low Meeting Attendance',
                'message': f'{low_attendance} members have missed meetings',
                'action': 'Send reminders to members',
                'priority': 'medium',
            })

        # Check upcoming meetings
        from apps.meetings.models import Meeting
        upcoming_meetings = Meeting.objects.filter(
            chama=chama,
            start_time__gte=timezone.now(),
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
    def get_admin_action_center(chama: Chama) -> dict:
        """
        Get admin action center data with pending items.
        """
        from apps.chama.models import Invite, JoinRequest
        from apps.finance.models import Contribution, Loan
        from apps.meetings.models import Meeting

        # Pending join requests
        pending_requests = JoinRequest.objects.filter(
            chama=chama,
            status='pending',
        ).count()

        # Pending invites
        pending_invites = Invite.objects.filter(
            chama=chama,
            status='pending',
        ).count()

        # Pending contributions
        pending_contributions = Contribution.objects.filter(
            membership__chama=chama,
            status='pending',
        ).count()

        # Pending loan approvals
        pending_loans = Loan.objects.filter(
            membership__chama=chama,
            status='pending_approval',
        ).count()

        # Upcoming meetings
        upcoming_meetings = Meeting.objects.filter(
            chama=chama,
            start_time__gte=timezone.now(),
            status='scheduled',
        ).count()

        return {
            'pending_requests': pending_requests,
            'pending_invites': pending_invites,
            'pending_contributions': pending_contributions,
            'pending_loans': pending_loans,
            'upcoming_meetings': upcoming_meetings,
            'total_pending': (
                pending_requests +
                pending_invites +
                pending_contributions +
                pending_loans
            ),
        }

    @staticmethod
    def get_chama_health_score(chama: Chama) -> dict:
        """
        Calculate chama health score based on various metrics.
        """
        score = 100
        factors = []

        # Check contribution compliance
        from apps.finance.models import Contribution
        total_contributions = Contribution.objects.filter(
            membership__chama=chama,
        ).count()

        if total_contributions > 0:
            paid_contributions = Contribution.objects.filter(
                membership__chama=chama,
                status='paid',
            ).count()
            
            compliance_rate = (paid_contributions / total_contributions) * 100
            
            if compliance_rate < 80:
                score -= 20
                factors.append('Low contribution compliance')
            elif compliance_rate < 90:
                score -= 10
                factors.append('Moderate contribution compliance')

        # Check loan defaults
        from apps.finance.models import Loan
        total_loans = Loan.objects.filter(
            membership__chama=chama,
        ).count()

        if total_loans > 0:
            overdue_loans = Loan.objects.filter(
                membership__chama=chama,
                status='overdue',
            ).count()
            
            default_rate = (overdue_loans / total_loans) * 100
            
            if default_rate > 10:
                score -= 20
                factors.append('High loan default rate')
            elif default_rate > 5:
                score -= 10
                factors.append('Moderate loan default rate')

        # Check member engagement
        from apps.meetings.models import Attendance
        total_attendance = Attendance.objects.filter(
            membership__chama=chama,
        ).count()

        if total_attendance > 0:
            present_attendance = Attendance.objects.filter(
                membership__chama=chama,
                status='present',
            ).count()
            
            attendance_rate = (present_attendance / total_attendance) * 100
            
            if attendance_rate < 70:
                score -= 15
                factors.append('Low meeting attendance')
            elif attendance_rate < 85:
                score -= 5
                factors.append('Moderate meeting attendance')

        # Check balance
        from apps.finance.models import Account
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
