"""
Member Management Service

Manages member details, admin actions, and financial profiles.
"""

import logging

from django.db import models
from django.db.models import Count

from apps.accounts.models import User
from apps.chama.models import Chama, Membership

logger = logging.getLogger(__name__)


class MemberService:
    """Service for managing members."""

    @staticmethod
    def get_member_list(chama: Chama, filters: dict = None) -> list[dict]:
        """
        Get member list with optional filters.
        """
        queryset = Membership.objects.filter(chama=chama).select_related('user')

        if filters:
            if 'status' in filters:
                queryset = queryset.filter(status=filters['status'])
            if 'role' in filters:
                queryset = queryset.filter(role=filters['role'])
            if 'search' in filters:
                search = filters['search']
                queryset = queryset.filter(
                    models.Q(user__full_name__icontains=search) |
                    models.Q(user__phone__icontains=search) |
                    models.Q(user__email__icontains=search)
                )

        memberships = queryset.order_by('role', 'user__full_name')

        return [
            {
                'id': str(membership.id),
                'user_id': str(membership.user.id),
                'user_name': membership.user.full_name,
                'user_phone': membership.user.phone,
                'user_email': membership.user.email,
                'role': membership.role,
                'status': membership.status,
                'is_active': membership.is_active,
                'joined_at': membership.joined_at.isoformat() if membership.joined_at else None,
            }
            for membership in memberships
        ]

    @staticmethod
    def get_member_detail(chama: Chama, user: User) -> dict | None:
        """
        Get detailed member information.
        """
        try:
            membership = Membership.objects.get(chama=chama, user=user)

            # Get financial summary
            from django.db.models import Sum

            from apps.finance.models import Contribution, Loan

            contributions = Contribution.objects.filter(
                membership=membership,
            ).aggregate(
                total=Sum('amount'),
                paid=Sum('amount_paid'),
                pending=Count('id', filter=models.Q(status='pending')),
            )

            loans = Loan.objects.filter(
                chama=chama,
                user=user,
            ).aggregate(
                total_borrowed=Sum('principal_amount'),
                total_repaid=Sum('amount_repaid'),
                active_count=Count('id', filter=models.Q(status='active')),
            )

            # Get attendance summary
            from apps.meetings.models import Attendance
            attendance = Attendance.objects.filter(
                meeting__chama=chama,
                user=user,
            ).aggregate(
                total=Count('id'),
                present=Count('id', filter=models.Q(status='present')),
            )

            return {
                'id': str(membership.id),
                'user_id': str(user.id),
                'user_name': user.full_name,
                'user_phone': user.phone,
                'user_email': user.email,
                'role': membership.role,
                'status': membership.status,
                'is_active': membership.is_active,
                'joined_at': membership.joined_at.isoformat() if membership.joined_at else None,
                'financial_summary': {
                    'total_contributions': contributions['total'] or 0,
                    'paid_contributions': contributions['paid'] or 0,
                    'pending_contributions': contributions['pending'] or 0,
                    'total_borrowed': loans['total_borrowed'] or 0,
                    'total_repaid': loans['total_repaid'] or 0,
                    'active_loans': loans['active_count'] or 0,
                },
                'attendance_summary': {
                    'total_meetings': attendance['total'] or 0,
                    'attended': attendance['present'] or 0,
                    'attendance_rate': (
                        (attendance['present'] / attendance['total'] * 100)
                        if attendance['total'] > 0 else 0
                    ),
                },
            }

        except Membership.DoesNotExist:
            return None

    @staticmethod
    def get_member_activity(chama: Chama, user: User, limit: int = 50) -> list[dict]:
        """
        Get member activity timeline.
        """
        activities = []

        # Get contributions
        from apps.finance.models import Contribution
        contributions = Contribution.objects.filter(
            membership__chama=chama,
            membership__user=user,
        ).order_by('-created_at')[:limit]

        for contrib in contributions:
            activities.append({
                'type': 'contribution',
                'description': f"Contribution of {contrib.amount}",
                'amount': contrib.amount,
                'status': contrib.status,
                'timestamp': contrib.created_at.isoformat(),
            })

        # Get loans
        from apps.finance.models import Loan
        loans = Loan.objects.filter(
            chama=chama,
            user=user,
        ).order_by('-created_at')[:limit]

        for loan in loans:
            activities.append({
                'type': 'loan',
                'description': f"Loan of {loan.principal_amount}",
                'amount': loan.principal_amount,
                'status': loan.status,
                'timestamp': loan.created_at.isoformat(),
            })

        # Get attendance
        from apps.meetings.models import Attendance
        attendance = Attendance.objects.filter(
            meeting__chama=chama,
            user=user,
        ).order_by('-created_at')[:limit]

        for att in attendance:
            activities.append({
                'type': 'attendance',
                'description': f"Meeting attendance: {att.status}",
                'status': att.status,
                'timestamp': att.created_at.isoformat(),
            })

        # Sort by timestamp
        activities.sort(key=lambda x: x['timestamp'], reverse=True)

        return activities[:limit]

    @staticmethod
    def get_participation_score(chama: Chama, user: User) -> dict:
        """
        Calculate participation score for a member.
        """
        from django.db.models import Count

        from apps.finance.models import Contribution
        from apps.meetings.models import Attendance

        # Contribution score (0-40)
        contributions = Contribution.objects.filter(
            membership__chama=chama,
            membership__user=user,
        ).aggregate(
            total=Count('id'),
            paid=Count('id', filter=models.Q(status='paid')),
        )

        contribution_score = 0
        if contributions['total'] > 0:
            contribution_score = (contributions['paid'] / contributions['total']) * 40

        # Attendance score (0-40)
        attendance = Attendance.objects.filter(
            meeting__chama=chama,
            user=user,
        ).aggregate(
            total=Count('id'),
            present=Count('id', filter=models.Q(status='present')),
        )

        attendance_score = 0
        if attendance['total'] > 0:
            attendance_score = (attendance['present'] / attendance['total']) * 40

        # Activity score (0-20)
        from apps.governance.models import Vote
        votes = Vote.objects.filter(
            motion__chama=chama,
            user=user,
        ).count()

        activity_score = min(20, votes * 5)

        total_score = contribution_score + attendance_score + activity_score

        return {
            'total_score': round(total_score, 2),
            'contribution_score': round(contribution_score, 2),
            'attendance_score': round(attendance_score, 2),
            'activity_score': round(activity_score, 2),
            'rating': (
                'excellent' if total_score >= 90 else
                'good' if total_score >= 75 else
                'fair' if total_score >= 60 else
                'poor'
            ),
        }

    @staticmethod
    def get_reliability_score(chama: Chama, user: User) -> dict:
        """
        Calculate reliability score for a member.
        """
        from django.db.models import Count

        from apps.finance.models import Contribution, Loan

        # Contribution reliability (0-50)
        contributions = Contribution.objects.filter(
            membership__chama=chama,
            membership__user=user,
        ).aggregate(
            total=Count('id'),
            paid_on_time=Count('id', filter=models.Q(status='paid', paid_at__lte=models.F('due_date'))),
        )

        contribution_reliability = 0
        if contributions['total'] > 0:
            contribution_reliability = (contributions['paid_on_time'] / contributions['total']) * 50

        # Loan reliability (0-50)
        loans = Loan.objects.filter(
            chama=chama,
            user=user,
        ).aggregate(
            total=Count('id'),
            repaid_on_time=Count('id', filter=models.Q(status='repaid', repaid_at__lte=models.F('due_date'))),
        )

        loan_reliability = 0
        if loans['total'] > 0:
            loan_reliability = (loans['repaid_on_time'] / loans['total']) * 50

        total_score = contribution_reliability + loan_reliability

        return {
            'total_score': round(total_score, 2),
            'contribution_reliability': round(contribution_reliability, 2),
            'loan_reliability': round(loan_reliability, 2),
            'rating': (
                'excellent' if total_score >= 90 else
                'good' if total_score >= 75 else
                'fair' if total_score >= 60 else
                'poor'
            ),
        }

    @staticmethod
    def search_members(chama: Chama, query: str, limit: int = 20) -> list[dict]:
        """
        Search members by name, phone, or email.
        """
        memberships = Membership.objects.filter(
            chama=chama,
            status='active',
        ).filter(
            models.Q(user__full_name__icontains=query) |
            models.Q(user__phone__icontains=query) |
            models.Q(user__email__icontains=query)
        ).select_related('user')[:limit]

        return [
            {
                'id': str(membership.id),
                'user_id': str(membership.user.id),
                'user_name': membership.user.full_name,
                'user_phone': membership.user.phone,
                'role': membership.role,
            }
            for membership in memberships
        ]
