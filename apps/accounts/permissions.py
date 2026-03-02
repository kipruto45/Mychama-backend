from rest_framework import permissions


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
        if not bool(request.user and request.user.is_authenticated):
            return False

        from apps.chama.models import MemberStatus, Membership, MembershipRole

        # Get chama from URL params
        chama_id = view.kwargs.get('chama_id') or request.query_params.get('chama_id')
        if not chama_id:
            # Try to get from request data
            chama_id = request.data.get('chama_id')

        if not chama_id:
            return False

        try:
            membership = Membership.objects.get(
                user=request.user,
                chama_id=chama_id,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
                exited_at__isnull=True,
            )
            return membership.role in [
                MembershipRole.CHAMA_ADMIN,
                MembershipRole.ADMIN,
                MembershipRole.SUPERADMIN,
                MembershipRole.TREASURER,
            ]
        except Membership.DoesNotExist:
            return False


class IsChamaAdminOnly(permissions.BasePermission):
    """
    Allows access only to admin-equivalent chama roles.
    """
    message = "You must be a chama admin to access this resource."

    def has_permission(self, request, view):
        if not bool(request.user and request.user.is_authenticated):
            return False

        from apps.chama.models import MemberStatus, Membership, MembershipRole

        chama_id = view.kwargs.get('chama_id') or request.query_params.get('chama_id')
        if not chama_id:
            chama_id = request.data.get('chama_id')

        if not chama_id:
            return False

        try:
            membership = Membership.objects.get(
                user=request.user,
                chama_id=chama_id,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
                exited_at__isnull=True,
            )
            return membership.role in [
                MembershipRole.CHAMA_ADMIN,
                MembershipRole.ADMIN,
                MembershipRole.SUPERADMIN,
            ]
        except Membership.DoesNotExist:
            return False
