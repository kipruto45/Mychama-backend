"""
Centralized Permission System

Provides role-based access control (RBAC) with object-level permissions.
All permission checks should go through this module.
"""

import logging
from functools import wraps

from django.http import HttpRequest
from rest_framework import permissions

from apps.chama.models import MembershipRole

logger = logging.getLogger(__name__)


def _extract_chama_id(request, view) -> str | None:
    kwargs = getattr(view, "kwargs", {}) or {}
    return (
        kwargs.get("chama_id")
        or kwargs.get("id")
        or kwargs.get("pk")
        or request.query_params.get("chama_id")
        or request.headers.get("X-CHAMA-ID")
    )


def get_membership(user, chama_id: str):
    """
    Return the user's approved active membership for a chama, if any.
    """
    from apps.chama.models import Membership, MemberStatus

    if not user or not getattr(user, "is_authenticated", False) or not chama_id:
        return None

    return (
        Membership.objects.filter(
            user=user,
            chama_id=chama_id,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
        )
        .select_related("chama", "user")
        .first()
    )


class IsApprovedActiveMember(permissions.BasePermission):
    message = "You must be an approved active member of this chama."

    def has_permission(self, request, view):
        chama_id = _extract_chama_id(request, view)
        return bool(get_membership(request.user, chama_id))


class IsChamaMember(permissions.BasePermission):
    """Allow any user with any membership in the chama (pending, active, etc.)"""
    message = "You must be a member of this chama."

    def has_permission(self, request, view):
        from apps.chama.models import Membership, MemberStatus
        
        chama_id = _extract_chama_id(request, view)
        if not chama_id:
            return False
        
        # Allow if user has any non-exited, non-suspended membership
        membership = (
            Membership.objects.filter(
                user=request.user,
                chama_id=chama_id,
            )
            .exclude(status__in=[MemberStatus.EXITED, MemberStatus.SUSPENDED])
            .first()
        )
        return bool(membership)


class IsChamaAdmin(permissions.BasePermission):
    message = "You must be a chama admin to perform this action."

    def has_permission(self, request, view):
        from apps.chama.services import ADMIN_EQUIVALENT_ROLES, get_effective_role

        chama_id = _extract_chama_id(request, view)
        membership = get_membership(request.user, chama_id)
        if not membership:
            return False
        return get_effective_role(request.user, chama_id, membership) in ADMIN_EQUIVALENT_ROLES


class IsMembershipApprover(permissions.BasePermission):
    message = "You must be allowed to approve membership actions."

    def has_permission(self, request, view):
        from apps.chama.services import get_effective_role

        chama_id = _extract_chama_id(request, view)
        membership = get_membership(request.user, chama_id)
        if not membership:
            return False

        allowed_roles = {
            MembershipRole.SUPERADMIN,
            MembershipRole.ADMIN,
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.SECRETARY,
        }
        return get_effective_role(request.user, chama_id, membership) in allowed_roles


class IsTreasurerOrAdmin(permissions.BasePermission):
    message = "You must be a treasurer or chama admin to perform this action."

    def has_permission(self, request, view):
        from apps.chama.models import MembershipRole
        from apps.chama.services import get_effective_role

        chama_id = _extract_chama_id(request, view)
        membership = get_membership(request.user, chama_id)
        if not membership:
            return False
        allowed_roles = {
            MembershipRole.SUPERADMIN,
            MembershipRole.ADMIN,
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
        }
        return get_effective_role(request.user, chama_id, membership) in allowed_roles


class Permission:
    """Permission constants shared across frontend and backend."""

    CAN_VIEW_CHAMA = "can_view_chama"
    CAN_EDIT_CHAMA = "can_edit_chama"
    CAN_DELETE_CHAMA = "can_delete_chama"
    CAN_MANAGE_CHAMA_SETTINGS = "can_manage_chama_settings"

    CAN_VIEW_MEMBERS = "can_view_members"
    CAN_INVITE_MEMBERS = "can_invite_members"
    CAN_REMOVE_MEMBERS = "can_remove_members"
    CAN_ASSIGN_ROLES = "can_assign_roles"
    CAN_APPROVE_MEMBERS = "can_approve_members"
    CAN_SUSPEND_MEMBERS = "can_suspend_members"

    CAN_VIEW_FINANCE = "can_view_finance"
    CAN_VIEW_FINANCES = CAN_VIEW_FINANCE
    CAN_VIEW_ALL_CONTRIBUTIONS = "can_view_all_contributions"
    CAN_VIEW_OWN_CONTRIBUTIONS = "can_view_own_contributions"
    CAN_RECORD_CONTRIBUTIONS = "can_record_contributions"
    CAN_VIEW_ALL_LOANS = "can_view_all_loans"
    CAN_REQUEST_LOAN = "can_request_loan"
    CAN_REQUEST_LOANS = CAN_REQUEST_LOAN
    CAN_APPROVE_LOAN = "can_approve_loan"
    CAN_DISBURSE_LOAN = "can_disburse_loan"
    CAN_VIEW_FINANCIAL_REPORTS = "can_view_financial_reports"
    CAN_MANAGE_LOAN_PRODUCTS = "can_manage_loan_products"
    CAN_MANAGE_CONTRIBUTION_TYPES = "can_manage_contribution_types"
    CAN_ISSUE_PENALTY = "can_issue_penalty"
    CAN_CLOSE_MONTH = "can_close_month"
    CAN_MAKE_ADJUSTMENTS = "can_make_adjustments"

    CAN_VIEW_MEETINGS = "can_view_meetings"
    CAN_CREATE_MEETINGS = "can_create_meetings"
    CAN_EDIT_MEETINGS = "can_edit_meetings"
    CAN_DELETE_MEETINGS = "can_delete_meetings"
    CAN_RECORD_MINUTES = "can_record_minutes"
    CAN_APPROVE_MINUTES = "can_approve_minutes"
    CAN_RECORD_ATTENDANCE = "can_record_attendance"

    CAN_VIEW_NOTIFICATIONS = "can_view_notifications"
    CAN_MANAGE_NOTIFICATIONS = "can_manage_notifications"
    CAN_SEND_ANNOUNCEMENTS = "can_send_announcements"

    CAN_VIEW_REPORTS = "can_view_reports"
    CAN_EXPORT_DATA = "can_export_data"
    CAN_USE_AI_ASSISTANT = "can_use_ai_assistant"
    CAN_ACCESS_ADMIN_TOOLS = "can_access_admin_tools"

    CAN_VIEW_PROFILE = "can_view_profile"
    CAN_EDIT_PROFILE = "can_edit_profile"
    CAN_VIEW_PAYMENTS = "can_view_payments"
    CAN_MAKE_PAYMENTS = "can_make_payments"


class Role:
    """Canonical role constants backed by MembershipRole."""

    SUPERADMIN = MembershipRole.SUPERADMIN
    ADMIN = MembershipRole.ADMIN
    CHAMA_ADMIN = MembershipRole.CHAMA_ADMIN
    TREASURER = MembershipRole.TREASURER
    SECRETARY = MembershipRole.SECRETARY
    AUDITOR = MembershipRole.AUDITOR
    MEMBER = MembershipRole.MEMBER


# Role hierarchy (higher number = more permissions)
ROLE_HIERARCHY = {
    Role.MEMBER: 10,
    Role.AUDITOR: 20,
    Role.SECRETARY: 30,
    Role.TREASURER: 40,
    Role.CHAMA_ADMIN: 50,
    Role.ADMIN: 60,
    Role.SUPERADMIN: 70,
}

# Role permissions mapping
ROLE_PERMISSIONS = {
    Role.SUPERADMIN: [
        Permission.CAN_VIEW_CHAMA,
        Permission.CAN_EDIT_CHAMA,
        Permission.CAN_DELETE_CHAMA,
        Permission.CAN_MANAGE_CHAMA_SETTINGS,
        Permission.CAN_VIEW_MEMBERS,
        Permission.CAN_INVITE_MEMBERS,
        Permission.CAN_APPROVE_MEMBERS,
        Permission.CAN_REMOVE_MEMBERS,
        Permission.CAN_SUSPEND_MEMBERS,
        Permission.CAN_ASSIGN_ROLES,
        Permission.CAN_VIEW_FINANCE,
        Permission.CAN_RECORD_CONTRIBUTIONS,
        Permission.CAN_VIEW_ALL_CONTRIBUTIONS,
        Permission.CAN_VIEW_OWN_CONTRIBUTIONS,
        Permission.CAN_VIEW_ALL_LOANS,
        Permission.CAN_REQUEST_LOAN,
        Permission.CAN_APPROVE_LOAN,
        Permission.CAN_DISBURSE_LOAN,
        Permission.CAN_VIEW_FINANCIAL_REPORTS,
        Permission.CAN_MANAGE_LOAN_PRODUCTS,
        Permission.CAN_ISSUE_PENALTY,
        Permission.CAN_CLOSE_MONTH,
        Permission.CAN_MAKE_ADJUSTMENTS,
        Permission.CAN_VIEW_MEETINGS,
        Permission.CAN_CREATE_MEETINGS,
        Permission.CAN_EDIT_MEETINGS,
        Permission.CAN_DELETE_MEETINGS,
        Permission.CAN_RECORD_MINUTES,
        Permission.CAN_APPROVE_MINUTES,
        Permission.CAN_RECORD_ATTENDANCE,
        Permission.CAN_VIEW_NOTIFICATIONS,
        Permission.CAN_MANAGE_NOTIFICATIONS,
        Permission.CAN_SEND_ANNOUNCEMENTS,
        Permission.CAN_VIEW_REPORTS,
        Permission.CAN_MANAGE_CHAMA_SETTINGS,
        Permission.CAN_MANAGE_CONTRIBUTION_TYPES,
        Permission.CAN_EXPORT_DATA,
        Permission.CAN_ACCESS_ADMIN_TOOLS,
        Permission.CAN_USE_AI_ASSISTANT,
        Permission.CAN_VIEW_PROFILE,
        Permission.CAN_EDIT_PROFILE,
        Permission.CAN_VIEW_PAYMENTS,
        Permission.CAN_MAKE_PAYMENTS,
    ],
    Role.ADMIN: [
        Permission.CAN_VIEW_CHAMA,
        Permission.CAN_VIEW_MEMBERS,
        Permission.CAN_VIEW_FINANCE,
        Permission.CAN_VIEW_ALL_CONTRIBUTIONS,
        Permission.CAN_VIEW_ALL_LOANS,
        Permission.CAN_VIEW_FINANCIAL_REPORTS,
        Permission.CAN_VIEW_MEETINGS,
        Permission.CAN_VIEW_NOTIFICATIONS,
        Permission.CAN_MANAGE_NOTIFICATIONS,
        Permission.CAN_VIEW_REPORTS,
        Permission.CAN_EXPORT_DATA,
        Permission.CAN_ACCESS_ADMIN_TOOLS,
        Permission.CAN_USE_AI_ASSISTANT,
        Permission.CAN_VIEW_PROFILE,
        Permission.CAN_EDIT_PROFILE,
        Permission.CAN_VIEW_PAYMENTS,
    ],
    Role.CHAMA_ADMIN: [
        Permission.CAN_VIEW_CHAMA,
        Permission.CAN_EDIT_CHAMA,
        Permission.CAN_DELETE_CHAMA,
        Permission.CAN_MANAGE_CHAMA_SETTINGS,
        Permission.CAN_VIEW_MEMBERS,
        Permission.CAN_INVITE_MEMBERS,
        Permission.CAN_APPROVE_MEMBERS,
        Permission.CAN_REMOVE_MEMBERS,
        Permission.CAN_SUSPEND_MEMBERS,
        Permission.CAN_ASSIGN_ROLES,
        Permission.CAN_VIEW_FINANCE,
        Permission.CAN_RECORD_CONTRIBUTIONS,
        Permission.CAN_VIEW_ALL_CONTRIBUTIONS,
        Permission.CAN_VIEW_OWN_CONTRIBUTIONS,
        Permission.CAN_VIEW_ALL_LOANS,
        Permission.CAN_REQUEST_LOAN,
        Permission.CAN_APPROVE_LOAN,
        Permission.CAN_DISBURSE_LOAN,
        Permission.CAN_VIEW_FINANCIAL_REPORTS,
        Permission.CAN_MANAGE_LOAN_PRODUCTS,
        Permission.CAN_ISSUE_PENALTY,
        Permission.CAN_CLOSE_MONTH,
        Permission.CAN_MAKE_ADJUSTMENTS,
        Permission.CAN_VIEW_MEETINGS,
        Permission.CAN_CREATE_MEETINGS,
        Permission.CAN_EDIT_MEETINGS,
        Permission.CAN_DELETE_MEETINGS,
        Permission.CAN_RECORD_MINUTES,
        Permission.CAN_APPROVE_MINUTES,
        Permission.CAN_RECORD_ATTENDANCE,
        Permission.CAN_VIEW_NOTIFICATIONS,
        Permission.CAN_MANAGE_NOTIFICATIONS,
        Permission.CAN_SEND_ANNOUNCEMENTS,
        Permission.CAN_VIEW_REPORTS,
        Permission.CAN_MANAGE_CONTRIBUTION_TYPES,
        Permission.CAN_EXPORT_DATA,
        Permission.CAN_USE_AI_ASSISTANT,
        Permission.CAN_VIEW_PROFILE,
        Permission.CAN_EDIT_PROFILE,
        Permission.CAN_VIEW_PAYMENTS,
        Permission.CAN_MAKE_PAYMENTS,
    ],
    Role.TREASURER: [
        Permission.CAN_VIEW_CHAMA,
        Permission.CAN_VIEW_MEMBERS,
        Permission.CAN_VIEW_FINANCE,
        Permission.CAN_RECORD_CONTRIBUTIONS,
        Permission.CAN_VIEW_ALL_CONTRIBUTIONS,
        Permission.CAN_VIEW_OWN_CONTRIBUTIONS,
        Permission.CAN_VIEW_ALL_LOANS,
        Permission.CAN_REQUEST_LOAN,
        Permission.CAN_APPROVE_LOAN,
        Permission.CAN_VIEW_FINANCIAL_REPORTS,
        Permission.CAN_MANAGE_LOAN_PRODUCTS,
        Permission.CAN_ISSUE_PENALTY,
        Permission.CAN_CLOSE_MONTH,
        Permission.CAN_MAKE_ADJUSTMENTS,
        Permission.CAN_VIEW_MEETINGS,
        Permission.CAN_RECORD_ATTENDANCE,
        Permission.CAN_VIEW_NOTIFICATIONS,
        Permission.CAN_MANAGE_NOTIFICATIONS,
        Permission.CAN_VIEW_REPORTS,
        Permission.CAN_EXPORT_DATA,
        Permission.CAN_USE_AI_ASSISTANT,
        Permission.CAN_VIEW_PROFILE,
        Permission.CAN_EDIT_PROFILE,
        Permission.CAN_VIEW_PAYMENTS,
        Permission.CAN_MAKE_PAYMENTS,
    ],
    Role.SECRETARY: [
        Permission.CAN_VIEW_CHAMA,
        Permission.CAN_VIEW_MEMBERS,
        Permission.CAN_VIEW_ALL_CONTRIBUTIONS,
        Permission.CAN_VIEW_OWN_CONTRIBUTIONS,
        Permission.CAN_REQUEST_LOAN,
        Permission.CAN_VIEW_MEETINGS,
        Permission.CAN_CREATE_MEETINGS,
        Permission.CAN_EDIT_MEETINGS,
        Permission.CAN_DELETE_MEETINGS,
        Permission.CAN_RECORD_MINUTES,
        Permission.CAN_APPROVE_MINUTES,
        Permission.CAN_RECORD_ATTENDANCE,
        Permission.CAN_VIEW_NOTIFICATIONS,
        Permission.CAN_MANAGE_NOTIFICATIONS,
        Permission.CAN_SEND_ANNOUNCEMENTS,
        Permission.CAN_VIEW_REPORTS,
        Permission.CAN_USE_AI_ASSISTANT,
        Permission.CAN_VIEW_PROFILE,
        Permission.CAN_EDIT_PROFILE,
        Permission.CAN_MAKE_PAYMENTS,
    ],
    Role.AUDITOR: [
        Permission.CAN_VIEW_CHAMA,
        Permission.CAN_VIEW_MEMBERS,
        Permission.CAN_VIEW_FINANCE,
        Permission.CAN_VIEW_ALL_CONTRIBUTIONS,
        Permission.CAN_VIEW_OWN_CONTRIBUTIONS,
        Permission.CAN_VIEW_ALL_LOANS,
        Permission.CAN_VIEW_FINANCIAL_REPORTS,
        Permission.CAN_VIEW_MEETINGS,
        Permission.CAN_VIEW_NOTIFICATIONS,
        Permission.CAN_VIEW_REPORTS,
        Permission.CAN_EXPORT_DATA,
        Permission.CAN_USE_AI_ASSISTANT,
        Permission.CAN_VIEW_PROFILE,
        Permission.CAN_EDIT_PROFILE,
        Permission.CAN_VIEW_PAYMENTS,
    ],
    Role.MEMBER: [
        Permission.CAN_VIEW_CHAMA,
        Permission.CAN_VIEW_MEMBERS,
        Permission.CAN_VIEW_OWN_CONTRIBUTIONS,
        Permission.CAN_REQUEST_LOAN,
        Permission.CAN_VIEW_MEETINGS,
        Permission.CAN_VIEW_NOTIFICATIONS,
        Permission.CAN_VIEW_REPORTS,
        Permission.CAN_USE_AI_ASSISTANT,
        Permission.CAN_VIEW_PROFILE,
        Permission.CAN_EDIT_PROFILE,
        Permission.CAN_VIEW_PAYMENTS,
        Permission.CAN_MAKE_PAYMENTS,
    ],
}


class PermissionChecker:
    """Centralized permission checking."""

    @staticmethod
    def get_user_role_in_chama(user, chama_id: str) -> str | None:
        """
        Get user's role in a specific chama.
        Returns None if user is not a member.
        """
        from apps.chama.models import Membership, MemberStatus

        try:
            membership = Membership.objects.get(
                user=user,
                chama_id=chama_id,
                status=MemberStatus.ACTIVE,
                is_active=True,
                is_approved=True,
            )
            return membership.role
        except Membership.DoesNotExist:
            return None

    @staticmethod
    def has_permission(
        user,
        permission: str,
        chama_id: str | None = None,
    ) -> bool:
        """
        Check if user has a specific permission.
        If chama_id is provided, checks chama-specific permission.
        Otherwise, checks global permission.
        """
        # Superusers have all permissions
        if user.is_superuser:
            return True

        # Check global role
        if hasattr(user, 'global_role') and user.global_role:
            role_permissions = ROLE_PERMISSIONS.get(user.global_role, [])
            if permission in role_permissions:
                return True

        # Check chama-specific role
        if chama_id:
            role = PermissionChecker.get_user_role_in_chama(user, chama_id)
            if role:
                role_permissions = ROLE_PERMISSIONS.get(role, [])
                return permission in role_permissions

        return False

    @staticmethod
    def has_any_permission(
        user,
        permissions: list[str],
        chama_id: str | None = None,
    ) -> bool:
        """Check if user has any of the specified permissions."""
        return any(
            PermissionChecker.has_permission(user, perm, chama_id)
            for perm in permissions
        )

    @staticmethod
    def has_all_permissions(
        user,
        permissions: list[str],
        chama_id: str | None = None,
    ) -> bool:
        """Check if user has all of the specified permissions."""
        return all(
            PermissionChecker.has_permission(user, perm, chama_id)
            for perm in permissions
        )

    @staticmethod
    def is_role_at_least(
        user,
        min_role: str,
        chama_id: str | None = None,
    ) -> bool:
        """
        Check if user's role is at least the specified minimum role.
        """
        if user.is_superuser:
            return True

        # Check global role
        if hasattr(user, 'global_role') and user.global_role:
            user_level = ROLE_HIERARCHY.get(user.global_role, 0)
            min_level = ROLE_HIERARCHY.get(min_role, 0)
            if user_level >= min_level:
                return True

        # Check chama-specific role
        if chama_id:
            role = PermissionChecker.get_user_role_in_chama(user, chama_id)
            if role:
                user_level = ROLE_HIERARCHY.get(role, 0)
                min_level = ROLE_HIERARCHY.get(min_role, 0)
                return user_level >= min_level

        return False

    @staticmethod
    def can_access_chama(user, chama_id: str) -> bool:
        """Check if user can access a specific chama."""
        from apps.chama.models import Membership, MemberStatus

        return Membership.objects.filter(
            user=user,
            chama_id=chama_id,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
        ).exists()

    @staticmethod
    def get_accessible_chamas(user) -> list[str]:
        """Get list of chama IDs user can access."""
        from apps.chama.models import Membership, MemberStatus

        if user.is_superuser:
            from apps.chama.models import Chama
            return list(Chama.objects.values_list('id', flat=True))

        return list(
            Membership.objects.filter(
                user=user,
                status=MemberStatus.ACTIVE,
                is_active=True,
                is_approved=True,
            ).values_list('chama_id', flat=True)
        )


# ============================================================================
# DECORATORS
# ============================================================================

def require_permission(permission: str, chama_id_param: str = 'chama_id'):
    """
    Decorator to require a specific permission for a view.
    
    Usage:
        @require_permission(Permission.CAN_VIEW_MEMBERS)
        def my_view(request, chama_id):
            ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request: HttpRequest, *args, **kwargs):
            # Get chama_id from kwargs or request
            chama_id = kwargs.get(chama_id_param) or request.GET.get(chama_id_param)
            
            if not chama_id:
                from rest_framework import status
                from rest_framework.response import Response
                return Response(
                    {'error': 'Chama ID is required'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if not PermissionChecker.has_permission(
                request.user,
                permission,
                chama_id,
            ):
                from rest_framework import status
                from rest_framework.response import Response
                return Response(
                    {'error': 'Permission denied'},
                    status=status.HTTP_403_FORBIDDEN,
                )

            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def require_role(min_role: str, chama_id_param: str = 'chama_id'):
    """
    Decorator to require a minimum role for a view.
    
    Usage:
        @require_role(Role.ADMIN)
        def my_view(request, chama_id):
            ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request: HttpRequest, *args, **kwargs):
            chama_id = kwargs.get(chama_id_param) or request.GET.get(chama_id_param)
            
            if not chama_id:
                from rest_framework import status
                from rest_framework.response import Response
                return Response(
                    {'error': 'Chama ID is required'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if not PermissionChecker.is_role_at_least(
                request.user,
                min_role,
                chama_id,
            ):
                from rest_framework import status
                from rest_framework.response import Response
                return Response(
                    {'error': 'Insufficient permissions'},
                    status=status.HTTP_403_FORBIDDEN,
                )

            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def require_chama_access(chama_id_param: str = 'chama_id'):
    """
    Decorator to require access to a specific chama.
    
    Usage:
        @require_chama_access()
        def my_view(request, chama_id):
            ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request: HttpRequest, *args, **kwargs):
            chama_id = kwargs.get(chama_id_param) or request.GET.get(chama_id_param)
            
            if not chama_id:
                from rest_framework import status
                from rest_framework.response import Response
                return Response(
                    {'error': 'Chama ID is required'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if not PermissionChecker.can_access_chama(request.user, chama_id):
                from rest_framework import status
                from rest_framework.response import Response
                return Response(
                    {'error': 'Access denied'},
                    status=status.HTTP_403_FORBIDDEN,
                )

            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator
