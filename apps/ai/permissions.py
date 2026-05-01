from __future__ import annotations

from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied

from apps.chama.models import MembershipRole
from apps.chama.permissions import get_membership
from apps.chama.services import get_effective_role


def get_ai_membership(user, chama_id):
    membership = get_membership(user, chama_id)
    if not membership:
        raise PermissionDenied("You are not an approved active member of this chama.")
    return membership


def assert_role(membership, allowed_roles: set[str], message: str):
    effective_role = get_effective_role(membership.user, membership.chama_id, membership)
    if effective_role not in allowed_roles:
        raise PermissionDenied(message)


class IsAIAuthenticatedMember(permissions.BasePermission):
    message = "You must be an approved active member to use AI endpoints."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        chama_id = view.get_scoped_chama_id()
        if not chama_id:
            return True
        return bool(get_membership(request.user, chama_id))


class IsAIAdminOrTreasurer(permissions.BasePermission):
    message = "Only chama admin or treasurer can access this AI endpoint."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        chama_id = view.get_scoped_chama_id()
        if not chama_id:
            return True
        membership = get_membership(request.user, chama_id)
        if not membership:
            return False
        effective_role = get_effective_role(request.user, chama_id, membership)
        return effective_role in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
            MembershipRole.SECRETARY,
        }
