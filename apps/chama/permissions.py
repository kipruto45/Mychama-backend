from rest_framework import permissions

from apps.chama.models import Membership, MembershipRole, MemberStatus
from apps.chama.services import get_effective_role, is_member_suspended


def get_membership(user, chama_id):
    if not user or not user.is_authenticated or not user.is_active:
        return None
    if is_member_suspended(chama_id, user.id):
        return None
    return Membership.objects.filter(
        user=user,
        chama_id=chama_id,
        is_active=True,
        is_approved=True,
        status=MemberStatus.ACTIVE,
        exited_at__isnull=True,
    ).first()


class IsApprovedActiveMember(permissions.BasePermission):
    message = "You are not an approved active member of this chama."

    def has_permission(self, request, view):
        chama_id = view.get_scoped_chama_id()
        return bool(get_membership(request.user, chama_id))

    def has_object_permission(self, request, view, obj):
        chama_id = getattr(obj, "chama_id", None) or getattr(obj, "id", None)
        return bool(get_membership(request.user, chama_id))


class IsChamaAdmin(permissions.BasePermission):
    message = "Only chama admins can perform this action."

    def has_permission(self, request, view):
        chama_id = view.get_scoped_chama_id()
        membership = get_membership(request.user, chama_id)
        if not membership:
            return False
        return get_effective_role(request.user, chama_id, membership) == MembershipRole.CHAMA_ADMIN


class IsMembershipApprover(permissions.BasePermission):
    message = "Only chama admin/secretary can perform this action."

    def has_permission(self, request, view):
        if request.user.is_superuser:
            return True
        chama_id = view.get_scoped_chama_id()
        membership = get_membership(request.user, chama_id)
        if not membership:
            return False
        role = get_effective_role(request.user, chama_id, membership)
        return role in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.ADMIN,
            
            MembershipRole.SECRETARY,
        }


class IsTreasurerOrAdmin(permissions.BasePermission):
    message = "Only treasurer or admin can perform this action."

    def has_permission(self, request, view):
        chama_id = view.get_scoped_chama_id()
        membership = get_membership(request.user, chama_id)
        if not membership:
            return False
        effective_role = get_effective_role(request.user, chama_id, membership)
        return effective_role in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
        }
