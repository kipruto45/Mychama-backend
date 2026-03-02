from django.utils import timezone

from apps.chama.models import MemberStatus, Membership, MembershipRole, RoleDelegation

ADMIN_EQUIVALENT_ROLES = {
    MembershipRole.SUPERADMIN,
    MembershipRole.CHAMA_ADMIN,
    
    MembershipRole.ADMIN,
}

ROLE_CANONICAL_MAP = {
    MembershipRole.SUPERADMIN: MembershipRole.CHAMA_ADMIN,
    
    MembershipRole.ADMIN: MembershipRole.CHAMA_ADMIN,
}


def canonicalize_role(role: str | None) -> str | None:
    if role is None:
        return None
    return ROLE_CANONICAL_MAP.get(role, role)


def is_member_suspended(chama_id, user_id) -> bool:
    return Membership.objects.filter(
        chama_id=chama_id,
        user_id=user_id,
        is_approved=True,
        status=MemberStatus.SUSPENDED,
        exited_at__isnull=True,
    ).exists()


def suspend_membership_in_chama(chama_id, user_id, *, actor=None, reason: str = ""):
    membership = Membership.objects.filter(
        chama_id=chama_id,
        user_id=user_id,
        is_approved=True,
        exited_at__isnull=True,
    ).first()
    if not membership:
        return None

    membership.status = MemberStatus.SUSPENDED
    membership.suspension_reason = reason
    membership.is_active = False
    membership.exited_at = None
    if actor:
        membership.updated_by = actor
    membership.save(
        update_fields=[
            "status",
            "suspension_reason",
            "is_active",
            "exited_at",
            "updated_by",
            "updated_at",
        ]
    )
    return membership


def lift_membership_suspension_in_chama(chama_id, user_id, *, actor=None):
    membership = Membership.objects.filter(
        chama_id=chama_id,
        user_id=user_id,
        is_approved=True,
        exited_at__isnull=True,
    ).first()
    if not membership:
        return None

    membership.status = MemberStatus.ACTIVE
    membership.suspension_reason = ""
    membership.is_active = True
    if actor:
        membership.updated_by = actor
    membership.save(
        update_fields=[
            "status",
            "suspension_reason",
            "is_active",
            "updated_by",
            "updated_at",
        ]
    )
    return membership


class MembershipService:
    @staticmethod
    def soft_exit_membership(membership: Membership):
        membership.status = MemberStatus.EXITED
        membership.is_active = False
        membership.exited_at = timezone.now()
        membership.save(update_fields=["status", "is_active", "exited_at", "updated_at"])
        return membership


def get_effective_role(user, chama_id, membership: Membership | None = None) -> str | None:
    if not user or not user.is_authenticated:
        return None

    current_membership = membership or Membership.objects.filter(
        user=user,
        chama_id=chama_id,
        is_approved=True,
        exited_at__isnull=True,
    ).first()
    if (
        not current_membership
        or not current_membership.is_active
        or current_membership.status != MemberStatus.ACTIVE
    ):
        return None

    now = timezone.now()
    delegation = (
        RoleDelegation.objects.filter(
            chama_id=chama_id,
            delegatee=user,
            is_active=True,
            starts_at__lte=now,
            ends_at__gte=now,
        )
        .order_by("-created_at")
        .first()
    )
    if delegation:
        return canonicalize_role(delegation.role)
    return canonicalize_role(current_membership.role)
