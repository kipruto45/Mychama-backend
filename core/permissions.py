from rest_framework import permissions

from apps.security.rbac import (
    resolve_chama_id,
    user_has_any_chama_role,
    user_has_chama_permission,
)


class IsAuthenticatedAndActive(permissions.BasePermission):
    """
    Custom permission to only allow authenticated and active users.
    """

    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated and request.user.is_active


class IsChamaMember(permissions.BasePermission):
    """
    Custom permission to only allow approved members of a chama to access its resources.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        chama_id = resolve_chama_id(request=request, view=view)
        return user_has_chama_permission(
            user=request.user,
            permission_code="can_view_chama",
            chama_id=chama_id,
        )

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False
        chama_id = resolve_chama_id(request=request, view=view, obj=obj)
        return user_has_chama_permission(
            user=request.user,
            permission_code="can_view_chama",
            chama_id=chama_id,
        )


class IsAuthenticatedAndHasPermission(permissions.BasePermission):
    permission_code: str | None = None
    message = "You do not have permission to perform this action."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated or not request.user.is_active:
            return False
        if not self.permission_code:
            return False
        chama_id = resolve_chama_id(request=request, view=view)
        return user_has_chama_permission(
            user=request.user,
            permission_code=self.permission_code,
            chama_id=chama_id,
        )

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated or not request.user.is_active:
            return False
        if not self.permission_code:
            return False
        chama_id = resolve_chama_id(request=request, view=view, obj=obj)
        return user_has_chama_permission(
            user=request.user,
            permission_code=self.permission_code,
            chama_id=chama_id,
        )


class IsChamaAdmin(permissions.BasePermission):
    """
    Custom permission to only allow chama admins to perform certain actions.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        chama_id = resolve_chama_id(request=request, view=view)
        return user_has_any_chama_role(
            user=request.user,
            chama_id=chama_id,
            allowed_roles={"super_admin", "admin", "chairperson"},
        )

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False
        chama_id = resolve_chama_id(request=request, view=view, obj=obj)
        return user_has_any_chama_role(
            user=request.user,
            chama_id=chama_id,
            allowed_roles={"super_admin", "admin", "chairperson"},
        )


class IsTreasurerOrAdmin(permissions.BasePermission):
    """
    Custom permission to only allow treasurers or admins to perform financial actions.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        chama_id = resolve_chama_id(request=request, view=view)
        return user_has_chama_permission(
            user=request.user,
            permission_code="can_manage_finance",
            chama_id=chama_id,
        )

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False
        chama_id = resolve_chama_id(request=request, view=view, obj=obj)
        return user_has_chama_permission(
            user=request.user,
            permission_code="can_manage_finance",
            chama_id=chama_id,
        )


class IsAuditorOrHigher(permissions.BasePermission):
    """
    Custom permission to allow auditors and higher roles to view reports.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        chama_id = resolve_chama_id(request=request, view=view)
        return user_has_chama_permission(
            user=request.user,
            permission_code="can_view_reports",
            chama_id=chama_id,
        )

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False
        chama_id = resolve_chama_id(request=request, view=view, obj=obj)
        return user_has_chama_permission(
            user=request.user,
            permission_code="can_view_reports",
            chama_id=chama_id,
        )


class IsSuperAdmin(permissions.BasePermission):
    """
    Custom permission to only allow super admins.
    """

    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated and request.user.is_superuser

    def has_object_permission(self, request, view, obj):
        return request.user and request.user.is_authenticated and request.user.is_superuser


class IsTreasurer(permissions.BasePermission):
    message = "Only treasurer-level users can perform this action."

    def has_permission(self, request, view):
        chama_id = resolve_chama_id(request=request, view=view)
        return user_has_any_chama_role(
            user=request.user,
            chama_id=chama_id,
            allowed_roles={"super_admin", "admin", "chairperson", "treasurer"},
        )

    def has_object_permission(self, request, view, obj):
        chama_id = resolve_chama_id(request=request, view=view, obj=obj)
        return user_has_any_chama_role(
            user=request.user,
            chama_id=chama_id,
            allowed_roles={"super_admin", "admin", "chairperson", "treasurer"},
        )
