"""
Membership and RBAC Service

Manages membership records, roles, permissions, and access control.
"""

import logging

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama, Membership

logger = logging.getLogger(__name__)


class MembershipService:
    """Service for managing membership and RBAC."""

    # Role hierarchy
    ROLE_HIERARCHY = {
        'creator': 100,
        'admin': 80,
        'treasurer': 60,
        'secretary': 40,
        'member': 20,
    }

    # Role permissions
    ROLE_PERMISSIONS = {
        'creator': [
            'manage_chama', 'manage_members', 'manage_finance',
            'manage_meetings', 'manage_loans', 'view_reports',
            'send_announcements', 'manage_settings',
        ],
        'admin': [
            'manage_members', 'manage_finance', 'manage_meetings',
            'manage_loans', 'view_reports', 'send_announcements',
        ],
        'treasurer': [
            'manage_finance', 'view_reports', 'manage_loans',
        ],
        'secretary': [
            'manage_meetings', 'send_announcements', 'view_reports',
        ],
        'member': [
            'view_reports', 'make_contributions', 'request_loans',
        ],
    }

    @staticmethod
    def get_memberships(user: User) -> list[dict]:
        """
        Get all memberships for a user.
        """
        memberships = Membership.objects.filter(
            user=user,
            status='active',
        ).select_related('chama')

        return [
            {
                'id': str(membership.id),
                'chama_id': str(membership.chama.id),
                'chama_name': membership.chama.name,
                'role': membership.role,
                'status': membership.status,
                'is_active': membership.is_active,
                'joined_at': membership.joined_at.isoformat() if membership.joined_at else None,
            }
            for membership in memberships
        ]

    @staticmethod
    def get_chama_members(
        chama: Chama,
        status: str = None,
        role: str = None,
    ) -> list[dict]:
        """
        Get members of a chama.
        """
        queryset = Membership.objects.filter(chama=chama).select_related('user')

        if status:
            queryset = queryset.filter(status=status)

        if role:
            queryset = queryset.filter(role=role)

        memberships = queryset.order_by('-joined_at')

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
    @transaction.atomic
    def assign_role(
        chama: Chama,
        user: User,
        role: str,
        assigned_by: User,
    ) -> tuple[bool, str]:
        """
        Assign a role to a member.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker

        # Check if assigner has permission
        if not PermissionChecker.has_permission(
            assigned_by,
            Permission.CAN_ASSIGN_ROLES,
            str(chama.id),
        ):
            return False, "Permission denied"

        # Validate role
        if role not in MembershipService.ROLE_HIERARCHY:
            return False, f"Invalid role: {role}"

        try:
            membership = Membership.objects.get(chama=chama, user=user)

            # Cannot assign role higher than your own
            assigner_membership = Membership.objects.get(chama=chama, user=assigned_by)
            if MembershipService.ROLE_HIERARCHY.get(role, 0) >= MembershipService.ROLE_HIERARCHY.get(assigner_membership.role, 0):
                return False, "Cannot assign role equal to or higher than your own"

            membership.role = role
            membership.save(update_fields=['role', 'updated_at'])

            logger.info(f"Role {role} assigned to user {user.full_name} in chama {chama.name}")

            return True, f"Role {role} assigned"

        except Membership.DoesNotExist:
            return False, "Membership not found"

    @staticmethod
    def has_permission(user: User, permission: str, chama_id: str) -> bool:
        """
        Check if user has a specific permission in a chama.
        """
        try:
            membership = Membership.objects.get(
                user=user,
                chama_id=chama_id,
                status='active',
            )

            role_permissions = MembershipService.ROLE_PERMISSIONS.get(membership.role, [])
            return permission in role_permissions

        except Membership.DoesNotExist:
            return False

    @staticmethod
    def get_user_role(user: User, chama_id: str) -> str | None:
        """
        Get user's role in a chama.
        """
        try:
            membership = Membership.objects.get(
                user=user,
                chama_id=chama_id,
                status='active',
            )
            return membership.role
        except Membership.DoesNotExist:
            return None

    @staticmethod
    @transaction.atomic
    def suspend_member(
        chama: Chama,
        user: User,
        suspended_by: User,
        reason: str,
    ) -> tuple[bool, str]:
        """
        Suspend a member.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker

        # Check permission
        if not PermissionChecker.has_permission(
            suspended_by,
            Permission.CAN_REMOVE_MEMBERS,
            str(chama.id),
        ):
            return False, "Permission denied"

        try:
            membership = Membership.objects.get(chama=chama, user=user)

            membership.status = 'suspended'
            membership.suspension_reason = reason
            membership.suspended_by = suspended_by
            membership.suspended_at = timezone.now()
            membership.save(update_fields=[
                'status',
                'suspension_reason',
                'suspended_by',
                'suspended_at',
                'updated_at',
            ])

            logger.info(f"Member {user.full_name} suspended in chama {chama.name}")

            return True, "Member suspended"

        except Membership.DoesNotExist:
            return False, "Membership not found"

    @staticmethod
    @transaction.atomic
    def remove_member(
        chama: Chama,
        user: User,
        removed_by: User,
        reason: str,
    ) -> tuple[bool, str]:
        """
        Remove a member from chama.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker

        # Check permission
        if not PermissionChecker.has_permission(
            removed_by,
            Permission.CAN_REMOVE_MEMBERS,
            str(chama.id),
        ):
            return False, "Permission denied"

        try:
            membership = Membership.objects.get(chama=chama, user=user)

            membership.status = 'removed'
            membership.removal_reason = reason
            membership.removed_by = removed_by
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

            logger.info(f"Member {user.full_name} removed from chama {chama.name}")

            return True, "Member removed"

        except Membership.DoesNotExist:
            return False, "Membership not found"

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
            )

            loans = Loan.objects.filter(
                chama=chama,
                user=user,
            ).aggregate(
                total_borrowed=Sum('principal_amount'),
                total_repaid=Sum('amount_repaid'),
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
                    'total_borrowed': loans['total_borrowed'] or 0,
                    'total_repaid': loans['total_repaid'] or 0,
                },
            }

        except Membership.DoesNotExist:
            return None

    @staticmethod
    def get_role_permissions(role: str) -> list[str]:
        """
        Get permissions for a role.
        """
        return MembershipService.ROLE_PERMISSIONS.get(role, [])

    @staticmethod
    def get_available_roles() -> list[dict]:
        """
        Get available roles.
        """
        return [
            {
                'id': 'creator',
                'name': 'Creator',
                'description': 'Full control over chama',
                'permissions': MembershipService.ROLE_PERMISSIONS['creator'],
            },
            {
                'id': 'admin',
                'name': 'Admin',
                'description': 'Administrative privileges',
                'permissions': MembershipService.ROLE_PERMISSIONS['admin'],
            },
            {
                'id': 'treasurer',
                'name': 'Treasurer',
                'description': 'Financial management',
                'permissions': MembershipService.ROLE_PERMISSIONS['treasurer'],
            },
            {
                'id': 'secretary',
                'name': 'Secretary',
                'description': 'Meeting and communication management',
                'permissions': MembershipService.ROLE_PERMISSIONS['secretary'],
            },
            {
                'id': 'member',
                'name': 'Member',
                'description': 'Basic member access',
                'permissions': MembershipService.ROLE_PERMISSIONS['member'],
            },
        ]
