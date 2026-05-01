"""
Member Management Service

Manages member operations, financial profiles, and activity tracking.
"""

import logging

from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama, Membership, MemberStatus

logger = logging.getLogger(__name__)


class MemberManagementService:
    """Service for managing chama members."""

    @staticmethod
    def get_member_detail(
        chama: Chama,
        user: User,
    ) -> dict | None:
        """
        Get detailed member information including financial profile.
        """
        try:
            membership = Membership.objects.get(
                chama=chama,
                user=user,
            )
        except Membership.DoesNotExist:
            return None

        # Get financial summary
        from django.db.models import Count, Sum

        from apps.finance.models import Contribution, Loan

        contributions = Contribution.objects.filter(
            membership=membership,
        ).aggregate(
            total=Sum('amount'),
            count=Count('id'),
        )

        loans = Loan.objects.filter(
            membership=membership,
        ).aggregate(
            total_borrowed=Sum('principal_amount'),
            total_repaid=Sum('amount_repaid'),
            active_count=Count('id', filter=models.Q(status='active')),
        )

        # Get attendance summary
        from apps.meetings.models import Attendance
        attendance = Attendance.objects.filter(
            membership=membership,
        ).aggregate(
            total=Count('id'),
            present=Count('id', filter=models.Q(status='present')),
        )

        return {
            'user_id': str(user.id),
            'user_name': user.full_name,
            'user_phone': user.phone,
            'user_email': user.email,
            'role': membership.role,
            'status': membership.status,
            'is_active': membership.is_active,
            'is_approved': membership.is_approved,
            'joined_at': membership.joined_at.isoformat() if membership.joined_at else None,
            'financial_summary': {
                'total_contributions': contributions['total'] or 0,
                'contribution_count': contributions['count'] or 0,
                'total_borrowed': loans['total_borrowed'] or 0,
                'total_repaid': loans['total_repaid'] or 0,
                'active_loans': loans['active_count'] or 0,
            },
            'attendance_summary': {
                'total_meetings': attendance['total'] or 0,
                'meetings_present': attendance['present'] or 0,
                'attendance_rate': (
                    (attendance['present'] / attendance['total'] * 100)
                    if attendance['total'] > 0 else 0
                ),
            },
        }

    @staticmethod
    def get_chama_members(
        chama: Chama,
        status: str = None,
        role: str = None,
        search: str = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """
        Get list of members with filtering and pagination.
        Returns (members, total_count).
        """
        queryset = Membership.objects.filter(chama=chama).select_related('user')

        if status:
            queryset = queryset.filter(status=status)

        if role:
            queryset = queryset.filter(role=role)

        if search:
            queryset = queryset.filter(
                models.Q(user__full_name__icontains=search) |
                models.Q(user__phone__icontains=search) |
                models.Q(user__email__icontains=search)
            )

        total_count = queryset.count()
        members = queryset.order_by('-joined_at')[offset:offset + limit]

        return [
            {
                'user_id': str(member.user.id),
                'user_name': member.user.full_name,
                'user_phone': member.user.phone,
                'user_email': member.user.email,
                'role': member.role,
                'status': member.status,
                'is_active': member.is_active,
                'is_approved': member.is_approved,
                'joined_at': member.joined_at.isoformat() if member.joined_at else None,
            }
            for member in members
        ], total_count

    @staticmethod
    @transaction.atomic
    def update_member_role(
        chama: Chama,
        user: User,
        new_role: str,
        updater: User,
    ) -> tuple[bool, str]:
        """
        Update member's role.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker

        # Check if updater has permission
        if not PermissionChecker.has_permission(
            updater,
            Permission.CAN_ASSIGN_ROLES,
            str(chama.id),
        ):
            return False, "Permission denied"

        try:
            membership = Membership.objects.get(
                chama=chama,
                user=user,
            )

            old_role = membership.role
            membership.role = new_role
            membership.save(update_fields=['role', 'updated_at'])

            logger.info(
                f"Member role updated for user {user.id} in chama {chama.id} "
                f"from {old_role} to {new_role} by {updater.id}"
            )

            # TODO: Send notification
            # from apps.notifications.services import NotificationService
            # NotificationService.send_role_change_notification(user, chama, old_role, new_role)

            return True, "Role updated successfully"

        except Membership.DoesNotExist:
            return False, "Member not found"

    @staticmethod
    @transaction.atomic
    def suspend_member(
        chama: Chama,
        user: User,
        reason: str,
        suspender: User,
    ) -> tuple[bool, str]:
        """
        Suspend a member.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker

        # Check if suspender has permission
        if not PermissionChecker.has_permission(
            suspender,
            Permission.CAN_SUSPEND_MEMBERS,
            str(chama.id),
        ):
            return False, "Permission denied"

        try:
            membership = Membership.objects.get(
                chama=chama,
                user=user,
            )

            if membership.status == MemberStatus.SUSPENDED:
                return False, "Member is already suspended"

            membership.status = MemberStatus.SUSPENDED
            membership.suspension_reason = reason
            membership.suspended_by = suspender
            membership.suspended_at = timezone.now()
            membership.save(update_fields=[
                'status',
                'suspension_reason',
                'suspended_by',
                'suspended_at',
                'updated_at',
            ])

            logger.info(
                f"Member {user.id} suspended in chama {chama.id} "
                f"by {suspender.id}: {reason}"
            )

            # TODO: Send notification
            # from apps.notifications.services import NotificationService
            # NotificationService.send_member_suspended_notification(user, chama, reason)

            return True, "Member suspended successfully"

        except Membership.DoesNotExist:
            return False, "Member not found"

    @staticmethod
    @transaction.atomic
    def unsuspend_member(
        chama: Chama,
        user: User,
        unsuspender: User,
    ) -> tuple[bool, str]:
        """
        Unsuspend a member.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker

        # Check if unsuspender has permission
        if not PermissionChecker.has_permission(
            unsuspender,
            Permission.CAN_SUSPEND_MEMBERS,
            str(chama.id),
        ):
            return False, "Permission denied"

        try:
            membership = Membership.objects.get(
                chama=chama,
                user=user,
            )

            if membership.status != MemberStatus.SUSPENDED:
                return False, "Member is not suspended"

            membership.status = MemberStatus.ACTIVE
            membership.suspension_reason = ''
            membership.suspended_by = None
            membership.suspended_at = None
            membership.save(update_fields=[
                'status',
                'suspension_reason',
                'suspended_by',
                'suspended_at',
                'updated_at',
            ])

            logger.info(
                f"Member {user.id} unsuspended in chama {chama.id} "
                f"by {unsuspender.id}"
            )

            # TODO: Send notification
            # from apps.notifications.services import NotificationService
            # NotificationService.send_member_unsuspended_notification(user, chama)

            return True, "Member unsuspended successfully"

        except Membership.DoesNotExist:
            return False, "Member not found"

    @staticmethod
    @transaction.atomic
    def remove_member(
        chama: Chama,
        user: User,
        reason: str,
        remover: User,
    ) -> tuple[bool, str]:
        """
        Remove a member from chama.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker

        # Check if remover has permission
        if not PermissionChecker.has_permission(
            remover,
            Permission.CAN_REMOVE_MEMBERS,
            str(chama.id),
        ):
            return False, "Permission denied"

        # Cannot remove yourself
        if user == remover:
            return False, "Cannot remove yourself"

        try:
            membership = Membership.objects.get(
                chama=chama,
                user=user,
            )

            # Check if member has outstanding balance
            from apps.finance.models import Contribution
            outstanding = Contribution.objects.filter(
                membership=membership,
                status='pending',
            ).aggregate(total=models.Sum('amount'))['total'] or 0

            if outstanding > 0:
                return False, f"Member has outstanding balance of {outstanding}"

            membership.status = MemberStatus.REMOVED
            membership.removal_reason = reason
            membership.removed_by = remover
            membership.removed_at = timezone.now()
            membership.is_active = False
            membership.save(update_fields=[
                'status',
                'removal_reason',
                'removed_by',
                'removed_at',
                'is_active',
                'updated_at',
            ])

            logger.info(
                f"Member {user.id} removed from chama {chama.id} "
                f"by {remover.id}: {reason}"
            )

            # TODO: Send notification
            # from apps.notifications.services import NotificationService
            # NotificationService.send_member_removed_notification(user, chama, reason)

            return True, "Member removed successfully"

        except Membership.DoesNotExist:
            return False, "Member not found"

    @staticmethod
    def get_member_activity_timeline(
        chama: Chama,
        user: User,
        limit: int = 50,
    ) -> list[dict]:
        """
        Get activity timeline for a member.
        """
        try:
            membership = Membership.objects.get(
                chama=chama,
                user=user,
            )
        except Membership.DoesNotExist:
            return []

        activities = []

        # Get contributions
        from apps.finance.models import Contribution
        contributions = Contribution.objects.filter(
            membership=membership,
        ).order_by('-created_at')[:limit]

        for contrib in contributions:
            activities.append({
                'type': 'contribution',
                'description': f"Contribution of {contrib.amount}",
                'timestamp': contrib.created_at.isoformat(),
                'metadata': {
                    'amount': contrib.amount,
                    'status': contrib.status,
                },
            })

        # Get loans
        from apps.finance.models import Loan
        loans = Loan.objects.filter(
            membership=membership,
        ).order_by('-created_at')[:limit]

        for loan in loans:
            activities.append({
                'type': 'loan',
                'description': f"Loan of {loan.principal_amount}",
                'timestamp': loan.created_at.isoformat(),
                'metadata': {
                    'amount': loan.principal_amount,
                    'status': loan.status,
                },
            })

        # Get attendance
        from apps.meetings.models import Attendance
        attendance = Attendance.objects.filter(
            membership=membership,
        ).order_by('-created_at')[:limit]

        for att in attendance:
            activities.append({
                'type': 'attendance',
                'description': f"Attendance: {att.status}",
                'timestamp': att.created_at.isoformat(),
                'metadata': {
                    'status': att.status,
                },
            })

        # Sort by timestamp
        activities.sort(key=lambda x: x['timestamp'], reverse=True)

        return activities[:limit]
