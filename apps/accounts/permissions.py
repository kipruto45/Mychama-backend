from rest_framework import permissions

from apps.security.rbac import resolve_chama_id, user_has_any_chama_role


class IsSelf(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return request.user.is_authenticated and obj == request.user


class IsStaffOrSelf(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        if not request.user.is_authenticated:
            return False
        return request.user.is_staff or obj == request.user


class IsActiveMember(permissions.BasePermission):
    """
    Allows access only to active authenticated users who are members of a chama.
    """
    message = "You must be an active member to access this resource."

    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            request.user.is_active
        )


class IsChamaAdmin(permissions.BasePermission):
    """
    Allows access only to elevated chama roles.
    """
    message = "You must be a chama admin to access this resource."

    def has_permission(self, request, view):
        chama_id = resolve_chama_id(request=request, view=view)
        return user_has_any_chama_role(
            user=request.user,
            chama_id=chama_id,
            allowed_roles={"super_admin", "admin", "chairperson"},
        )


class IsChamaAdminOnly(permissions.BasePermission):
    """
    Allows access only to admin-equivalent chama roles.
    """
    message = "You must be a chama admin to access this resource."

    def has_permission(self, request, view):
        chama_id = resolve_chama_id(request=request, view=view)
        return user_has_any_chama_role(
            user=request.user,
            chama_id=chama_id,
            allowed_roles={"super_admin", "admin", "chairperson"},
        )
