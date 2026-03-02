from __future__ import annotations

from rest_framework import permissions

from apps.chama.models import MembershipRole, MemberStatus
from apps.chama.permissions import get_membership
from apps.chama.services import get_effective_role


class IsSessionOwner(permissions.BasePermission):
    message = "You can only access your own device sessions."

    def has_object_permission(self, request, view, obj):
        return bool(obj.user_id == request.user.id)


class IsSecurityAuditReader(permissions.BasePermission):
    message = "Only admin or auditor can view security audit logs."

    def has_permission(self, request, view):
        if request.user.is_superuser:
            return True
        chama_id = view.get_scoped_chama_id(required=False)
        if not chama_id:
            memberships = request.user.memberships.filter(
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
                exited_at__isnull=True,
            )
            return any(
                get_effective_role(request.user, item.chama_id, item)
                in [MembershipRole.CHAMA_ADMIN, MembershipRole.AUDITOR]
                for item in memberships
            )

        membership = get_membership(request.user, chama_id)
        if not membership:
            return False
        effective_role = get_effective_role(request.user, chama_id, membership)
        return effective_role in [MembershipRole.CHAMA_ADMIN, MembershipRole.AUDITOR]
