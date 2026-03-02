from rest_framework import permissions

from apps.chama.models import MemberStatus, MembershipRole


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

        chama_id = view.kwargs.get('chama_id') or getattr(view, 'chama_id', None)
        if chama_id:
            return request.user.memberships.filter(
                chama_id=chama_id,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
            ).exists()
        return False

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False

        # Check if user is approved member of the object's chama
        if hasattr(obj, 'chama'):
            return request.user.memberships.filter(
                chama=obj.chama,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
            ).exists()
        return False


class IsChamaAdmin(permissions.BasePermission):
    """
    Custom permission to only allow chama admins to perform certain actions.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        chama_id = view.kwargs.get('chama_id')
        if chama_id:
            return request.user.memberships.filter(
                chama_id=chama_id,
                role__in=[
                    MembershipRole.CHAMA_ADMIN,
                    MembershipRole.ADMIN,
                    
                    MembershipRole.TREASURER,
                ],
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
            ).exists()
        return False

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False

        # Check if user is admin of the object's chama
        if hasattr(obj, 'chama'):
            return request.user.memberships.filter(
                chama=obj.chama,
                role__in=[
                    MembershipRole.CHAMA_ADMIN,
                    MembershipRole.ADMIN,
                    
                    MembershipRole.TREASURER,
                ],
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
            ).exists()
        return False


class IsTreasurerOrAdmin(permissions.BasePermission):
    """
    Custom permission to only allow treasurers or admins to perform financial actions.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        chama_id = view.kwargs.get('chama_id')
        if chama_id:
            return request.user.memberships.filter(
                chama_id=chama_id,
                role__in=[
                    MembershipRole.CHAMA_ADMIN,
                    MembershipRole.ADMIN,
                    
                    MembershipRole.TREASURER,
                ],
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
            ).exists()
        return False

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False

        if hasattr(obj, 'chama'):
            return request.user.memberships.filter(
                chama=obj.chama,
                role__in=[
                    MembershipRole.CHAMA_ADMIN,
                    MembershipRole.ADMIN,
                    
                    MembershipRole.TREASURER,
                ],
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
            ).exists()
        return False


class IsAuditorOrHigher(permissions.BasePermission):
    """
    Custom permission to allow auditors and higher roles to view reports.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        chama_id = view.kwargs.get('chama_id')
        if chama_id:
            return request.user.memberships.filter(
                chama_id=chama_id,
                role__in=[
                    MembershipRole.CHAMA_ADMIN,
                    MembershipRole.ADMIN,
                    
                    MembershipRole.TREASURER,
                    MembershipRole.AUDITOR,
                ],
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
            ).exists()
        return False

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False

        if hasattr(obj, 'chama'):
            return request.user.memberships.filter(
                chama=obj.chama,
                role__in=[
                    MembershipRole.CHAMA_ADMIN,
                    MembershipRole.ADMIN,
                    
                    MembershipRole.TREASURER,
                    MembershipRole.AUDITOR,
                ],
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
            ).exists()
        return False


class IsSuperAdmin(permissions.BasePermission):
    """
    Custom permission to only allow super admins.
    """
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated and request.user.is_superuser

    def has_object_permission(self, request, view, obj):
        return request.user and request.user.is_authenticated and request.user.is_superuser
