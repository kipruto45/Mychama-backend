"""
Admin and Moderation Service

Manages admin actions, moderation tools, and platform administration.
"""

import logging

from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama, Membership

logger = logging.getLogger(__name__)


class AdminService:
    """Service for managing admin and moderation."""

    @staticmethod
    def get_pending_approvals(chama: Chama = None) -> dict:
        """
        Get all pending approvals for a chama or platform.
        """
        from apps.chama.models import Invite, JoinRequest
        from apps.finance.models import Expense, Loan, Withdrawal

        result = {
            'join_requests': [],
            'invites': [],
            'loans': [],
            'expenses': [],
            'withdrawals': [],
        }

        # Get pending join requests
        join_requests = JoinRequest.objects.filter(status='pending')
        if chama:
            join_requests = join_requests.filter(chama=chama)

        result['join_requests'] = [
            {
                'id': str(req.id),
                'user_name': req.user.full_name,
                'chama_name': req.chama.name,
                'created_at': req.created_at.isoformat(),
            }
            for req in join_requests[:20]
        ]

        # Get pending invites
        invites = Invite.objects.filter(status='pending')
        if chama:
            invites = invites.filter(chama=chama)

        result['invites'] = [
            {
                'id': str(invite.id),
                'email': invite.email,
                'phone': invite.phone,
                'chama_name': invite.chama.name,
                'created_at': invite.created_at.isoformat(),
            }
            for invite in invites[:20]
        ]

        # Get pending loans
        loans = Loan.objects.filter(status='pending')
        if chama:
            loans = loans.filter(chama=chama)

        result['loans'] = [
            {
                'id': str(loan.id),
                'user_name': loan.user.full_name,
                'amount': loan.principal_amount,
                'chama_name': loan.chama.name,
                'created_at': loan.created_at.isoformat(),
            }
            for loan in loans[:20]
        ]

        # Get pending expenses
        expenses = Expense.objects.filter(status='pending')
        if chama:
            expenses = expenses.filter(chama=chama)

        result['expenses'] = [
            {
                'id': str(expense.id),
                'user_name': expense.user.full_name,
                'amount': expense.amount,
                'category': expense.category,
                'chama_name': expense.chama.name,
                'created_at': expense.created_at.isoformat(),
            }
            for expense in expenses[:20]
        ]

        # Get pending withdrawals
        withdrawals = Withdrawal.objects.filter(status='pending')
        if chama:
            withdrawals = withdrawals.filter(chama=chama)

        result['withdrawals'] = [
            {
                'id': str(withdrawal.id),
                'user_name': withdrawal.user.full_name,
                'amount': withdrawal.amount,
                'chama_name': withdrawal.chama.name,
                'created_at': withdrawal.created_at.isoformat(),
            }
            for withdrawal in withdrawals[:20]
        ]

        return result

    @staticmethod
    def get_admin_dashboard(chama: Chama = None) -> dict:
        """
        Get admin dashboard data.
        """
        from django.db.models import Count, Sum

        from apps.finance.models import Account, Contribution, Loan
        from apps.meetings.models import Meeting

        # Get member count
        members = Membership.objects.filter(status='active')
        if chama:
            members = members.filter(chama=chama)

        member_count = members.count()

        # Get account balance
        accounts = Account.objects.filter(account_type='main')
        if chama:
            accounts = accounts.filter(chama=chama)

        total_balance = accounts.aggregate(
            total=Sum('balance')
        )['total'] or 0

        # Get contribution stats
        contributions = Contribution.objects.all()
        if chama:
            contributions = contributions.filter(membership__chama=chama)

        contribution_stats = contributions.aggregate(
            total=Sum('amount'),
            paid=Sum('amount_paid'),
            pending=Count('id', filter=models.Q(status='pending')),
            overdue=Count('id', filter=models.Q(status='overdue')),
        )

        # Get loan stats
        loans = Loan.objects.all()
        if chama:
            loans = loans.filter(chama=chama)

        loan_stats = loans.aggregate(
            total_borrowed=Sum('principal_amount'),
            total_repaid=Sum('amount_repaid'),
            active=Count('id', filter=models.Q(status='active')),
            overdue=Count('id', filter=models.Q(status='overdue')),
        )

        # Get upcoming meetings
        upcoming_meetings = Meeting.objects.filter(
            start_time__gt=timezone.now(),
            status='scheduled',
        )
        if chama:
            upcoming_meetings = upcoming_meetings.filter(chama=chama)

        return {
            'member_count': member_count,
            'total_balance': total_balance,
            'contributions': {
                'total': contribution_stats['total'] or 0,
                'paid': contribution_stats['paid'] or 0,
                'pending': contribution_stats['pending'] or 0,
                'overdue': contribution_stats['overdue'] or 0,
            },
            'loans': {
                'total_borrowed': loan_stats['total_borrowed'] or 0,
                'total_repaid': loan_stats['total_repaid'] or 0,
                'active': loan_stats['active'] or 0,
                'overdue': loan_stats['overdue'] or 0,
            },
            'upcoming_meetings': upcoming_meetings.count(),
        }

    @staticmethod
    def get_system_health() -> dict:
        """
        Get system health status.
        """
        from django.core.cache import cache
        from django.db import connection

        health = {
            'database': 'healthy',
            'cache': 'healthy',
            'celery': 'healthy',
            'timestamp': timezone.now().isoformat(),
        }

        # Check database
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except Exception as e:
            health['database'] = f'unhealthy: {str(e)}'

        # Check cache
        try:
            cache.set('health_check', 'ok', 10)
            cache.get('health_check')
        except Exception as e:
            health['cache'] = f'unhealthy: {str(e)}'

        # Check Celery
        try:
            from celery import current_app
            inspect = current_app.control.inspect()
            stats = inspect.stats()
            if not stats:
                health['celery'] = 'unhealthy: no workers available'
        except Exception as e:
            health['celery'] = f'unhealthy: {str(e)}'

        return health

    @staticmethod
    def get_platform_stats() -> dict:
        """
        Get platform-wide statistics.
        """
        from django.db.models import Sum

        from apps.accounts.models import User
        from apps.chama.models import Chama
        from apps.finance.models import Account, Contribution, Loan

        # Get user stats
        total_users = User.objects.count()
        active_users = User.objects.filter(is_active=True).count()

        # Get chama stats
        total_chamas = Chama.objects.count()
        active_chamas = Chama.objects.filter(status='active').count()

        # Get member stats
        total_members = Membership.objects.filter(status='active').count()

        # Get financial stats
        total_balance = Account.objects.filter(
            account_type='main',
        ).aggregate(total=Sum('balance'))['total'] or 0

        total_contributions = Contribution.objects.aggregate(
            total=Sum('amount'),
            paid=Sum('amount_paid'),
        )

        total_loans = Loan.objects.aggregate(
            total_borrowed=Sum('principal_amount'),
            total_repaid=Sum('amount_repaid'),
        )

        return {
            'users': {
                'total': total_users,
                'active': active_users,
            },
            'chamas': {
                'total': total_chamas,
                'active': active_chamas,
            },
            'members': {
                'total': total_members,
            },
            'finance': {
                'total_balance': total_balance,
                'total_contributions': total_contributions['total'] or 0,
                'total_contributions_paid': total_contributions['paid'] or 0,
                'total_loans_borrowed': total_loans['total_borrowed'] or 0,
                'total_loans_repaid': total_loans['total_repaid'] or 0,
            },
        }

    @staticmethod
    @transaction.atomic
    def moderate_content(
        content_type: str,
        content_id: str,
        action: str,
        moderator: User,
        reason: str = '',
    ) -> tuple[bool, str]:
        """
        Moderate content (e.g., announcements, comments).
        Returns (success, message).
        """
        from apps.audit.audit_service import AuditService

        # Log moderation action
        AuditService.log_activity(
            user=moderator,
            action=f'content_{action}',
            entity_type=content_type,
            entity_id=content_id,
            metadata={'reason': reason},
        )

        logger.info(
            f"Content moderated: {content_type}:{content_id} - {action} by {moderator.full_name}"
        )

        return True, f"Content {action} successfully"

    @staticmethod
    def get_flagged_content(chama: Chama = None) -> list[dict]:
        """
        Get flagged content for review.
        """
        # This would integrate with a content flagging system
        # For now, return empty list
        return []

    @staticmethod
    def get_user_management(
        search: str = None,
        status: str = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """
        Get user management data.
        """
        from apps.accounts.models import User

        queryset = User.objects.all()

        if search:
            queryset = queryset.filter(
                models.Q(full_name__icontains=search) |
                models.Q(email__icontains=search) |
                models.Q(phone__icontains=search)
            )

        if status:
            if status == 'active':
                queryset = queryset.filter(is_active=True)
            elif status == 'inactive':
                queryset = queryset.filter(is_active=False)

        queryset = queryset.order_by('-date_joined')

        # Paginate
        total = queryset.count()
        start = (page - 1) * page_size
        end = start + page_size
        users = queryset[start:end]

        return {
            'results': [
                {
                    'id': str(user.id),
                    'full_name': user.full_name,
                    'email': user.email,
                    'phone': user.phone,
                    'is_active': user.is_active,
                    'is_staff': user.is_staff,
                    'date_joined': user.date_joined.isoformat(),
                    'last_login': user.last_login.isoformat() if user.last_login else None,
                }
                for user in users
            ],
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total': total,
                'total_pages': (total + page_size - 1) // page_size,
            },
        }

    @staticmethod
    @transaction.atomic
    def manage_user(
        user_id: str,
        action: str,
        admin: User,
        reason: str = '',
    ) -> tuple[bool, str]:
        """
        Manage user account (activate, deactivate, etc.).
        Returns (success, message).
        """
        from apps.accounts.models import User
        from apps.audit.audit_service import AuditService

        try:
            user = User.objects.get(id=user_id)

            if action == 'activate':
                user.is_active = True
            elif action == 'deactivate':
                user.is_active = False
            elif action == 'make_staff':
                user.is_staff = True
            elif action == 'remove_staff':
                user.is_staff = False
            else:
                return False, f"Unknown action: {action}"

            user.save()

            # Log action
            AuditService.log_activity(
                user=admin,
                action=f'user_{action}',
                entity_type='user',
                entity_id=user_id,
                metadata={'reason': reason},
            )

            logger.info(
                f"User {action}: {user.full_name} by {admin.full_name}"
            )

            return True, f"User {action} successfully"

        except User.DoesNotExist:
            return False, "User not found"
