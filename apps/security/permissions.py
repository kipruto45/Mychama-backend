from __future__ import annotations

from rest_framework import permissions

from apps.security.rbac import (
    get_active_memberships,
    resolve_chama_id,
    user_has_any_chama_role,
)


class IsSessionOwner(permissions.BasePermission):
    message = "You can only access your own device sessions."

    def has_object_permission(self, request, view, obj):
        return bool(obj.user_id == request.user.id)


class IsSecurityAuditReader(permissions.BasePermission):
    message = "Only admin or auditor can view security audit logs."

    def has_permission(self, request, view):
        if request.user.is_superuser:
            return True
        chama_id = resolve_chama_id(request=request, view=view)
        if not chama_id:
            return any(
                user_has_any_chama_role(
                    user=request.user,
                    chama_id=str(item.chama_id),
                    allowed_roles={"super_admin", "admin", "chairperson", "auditor"},
                    membership=item,
                )
                for item in get_active_memberships(request.user)
            )
        return user_has_any_chama_role(
            user=request.user,
            chama_id=chama_id,
            allowed_roles={"super_admin", "admin", "chairperson", "auditor"},
        )
