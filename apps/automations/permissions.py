from __future__ import annotations

from rest_framework import permissions

from apps.chama.models import MembershipRole, MemberStatus
from apps.chama.permissions import get_membership
from apps.chama.services import get_effective_role

READ_ROLES = {
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.TREASURER,
    MembershipRole.SECRETARY,
    MembershipRole.AUDITOR,
}
WRITE_ROLES = {
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.TREASURER,
    MembershipRole.SECRETARY,
}


def get_automation_membership(user, chama_id):
    return get_membership(user, chama_id)


class IsAutomationReader(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_superuser:
            return True
        chama_id = view.get_scoped_chama_id()
        if not chama_id:
            memberships = request.user.memberships.filter(
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
                exited_at__isnull=True,
            )
            return any(
                get_effective_role(request.user, item.chama_id, item) in READ_ROLES
                for item in memberships
            )
        membership = get_membership(request.user, chama_id)
        if not membership:
            return False
        effective_role = get_effective_role(request.user, chama_id, membership)
        return effective_role in READ_ROLES


class IsAutomationManager(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_superuser:
            return True
        chama_id = view.get_scoped_chama_id()
        if not chama_id:
            memberships = request.user.memberships.filter(
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
                exited_at__isnull=True,
            )
            return any(
                get_effective_role(request.user, item.chama_id, item) in WRITE_ROLES
                for item in memberships
            )
        membership = get_membership(request.user, chama_id)
        if not membership:
            return False
        effective_role = get_effective_role(request.user, chama_id, membership)
        return effective_role in WRITE_ROLES
