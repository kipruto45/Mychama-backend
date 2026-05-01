from __future__ import annotations

from collections.abc import Iterable

from apps.chama.models import Membership, MembershipRole, MemberStatus
from apps.chama.services import get_effective_role, is_member_suspended

ROLE_SCOPE_GLOBAL = "global"
ROLE_SCOPE_CHAMA = "chama"

PERMISSION_SCOPE_GLOBAL = "global"
PERMISSION_SCOPE_CHAMA = "chama"

RBAC_PERMISSION_DEFINITIONS: dict[str, dict[str, str]] = {
    "can_view_chama": {
        "name": "View chama",
        "description": "View chama information and membership-scoped records.",
        "scope": PERMISSION_SCOPE_CHAMA,
    },
    "can_edit_chama": {
        "name": "Edit chama",
        "description": "Edit chama profile, settings, and governance configuration.",
        "scope": PERMISSION_SCOPE_CHAMA,
    },
    "can_delete_chama": {
        "name": "Delete chama",
        "description": "Delete or archive a chama.",
        "scope": PERMISSION_SCOPE_CHAMA,
    },
    "can_invite_members": {
        "name": "Invite members",
        "description": "Invite new members into a chama.",
        "scope": PERMISSION_SCOPE_CHAMA,
    },
    "can_remove_members": {
        "name": "Remove members",
        "description": "Suspend, remove, or reject chama members.",
        "scope": PERMISSION_SCOPE_CHAMA,
    },
    "can_assign_roles": {
        "name": "Assign roles",
        "description": "Assign and revoke chama role assignments.",
        "scope": PERMISSION_SCOPE_CHAMA,
    },
    "can_view_finance": {
        "name": "View finance",
        "description": "View finance summaries, ledgers, and balances.",
        "scope": PERMISSION_SCOPE_CHAMA,
    },
    "can_manage_finance": {
        "name": "Manage finance",
        "description": "Create, update, and reconcile financial records.",
        "scope": PERMISSION_SCOPE_CHAMA,
    },
    "can_record_payments": {
        "name": "Record payments",
        "description": "Initiate, confirm, or reconcile contribution and payment records.",
        "scope": PERMISSION_SCOPE_CHAMA,
    },
    "can_view_meetings": {
        "name": "View meetings",
        "description": "View meetings, agendas, minutes, and attendance.",
        "scope": PERMISSION_SCOPE_CHAMA,
    },
    "can_manage_meetings": {
        "name": "Manage meetings",
        "description": "Create and manage meetings, agendas, and minutes.",
        "scope": PERMISSION_SCOPE_CHAMA,
    },
    "can_view_reports": {
        "name": "View reports",
        "description": "View operational, finance, and audit reports.",
        "scope": PERMISSION_SCOPE_CHAMA,
    },
    "can_manage_notifications": {
        "name": "Manage notifications",
        "description": "Trigger or manage notifications and communication workflows.",
        "scope": PERMISSION_SCOPE_CHAMA,
    },
    "can_use_ai": {
        "name": "Use AI",
        "description": "Use AI-assisted features within the chama context.",
        "scope": PERMISSION_SCOPE_CHAMA,
    },
    "can_export_data": {
        "name": "Export data",
        "description": "Export audit, finance, and operational data.",
        "scope": PERMISSION_SCOPE_CHAMA,
    },
}

RBAC_ROLE_DEFINITIONS: dict[str, dict[str, str | int]] = {
    "super_admin": {
        "name": "Super Admin",
        "description": "Global platform super administrator.",
        "scope": ROLE_SCOPE_GLOBAL,
        "membership_role_key": MembershipRole.SUPERADMIN,
        "sort_order": 10,
    },
    "admin": {
        "name": "Admin",
        "description": "Administrative role with broad governance powers.",
        "scope": ROLE_SCOPE_CHAMA,
        "membership_role_key": MembershipRole.ADMIN,
        "sort_order": 20,
    },
    "chairperson": {
        "name": "Chairperson",
        "description": "Primary chama administrator and governance lead.",
        "scope": ROLE_SCOPE_CHAMA,
        "membership_role_key": MembershipRole.CHAMA_ADMIN,
        "sort_order": 30,
    },
    "treasurer": {
        "name": "Treasurer",
        "description": "Finance owner for collection, reconciliation, and balances.",
        "scope": ROLE_SCOPE_CHAMA,
        "membership_role_key": MembershipRole.TREASURER,
        "sort_order": 40,
    },
    "secretary": {
        "name": "Secretary",
        "description": "Meeting and membership workflow owner.",
        "scope": ROLE_SCOPE_CHAMA,
        "membership_role_key": MembershipRole.SECRETARY,
        "sort_order": 50,
    },
    "auditor": {
        "name": "Auditor",
        "description": "Read-focused oversight role for reports and controls.",
        "scope": ROLE_SCOPE_CHAMA,
        "membership_role_key": MembershipRole.AUDITOR,
        "sort_order": 60,
    },
    "member": {
        "name": "Member",
        "description": "Standard chama member with basic visibility and participation.",
        "scope": ROLE_SCOPE_CHAMA,
        "membership_role_key": MembershipRole.MEMBER,
        "sort_order": 70,
    },
}

RBAC_ROLE_PERMISSION_MATRIX: dict[str, set[str]] = {
    "super_admin": set(RBAC_PERMISSION_DEFINITIONS.keys()),
    "admin": {
        "can_view_chama",
        "can_edit_chama",
        "can_delete_chama",
        "can_invite_members",
        "can_remove_members",
        "can_assign_roles",
        "can_view_finance",
        "can_manage_finance",
        "can_record_payments",
        "can_view_meetings",
        "can_manage_meetings",
        "can_view_reports",
        "can_manage_notifications",
        "can_use_ai",
        "can_export_data",
    },
    "chairperson": {
        "can_view_chama",
        "can_edit_chama",
        "can_delete_chama",
        "can_invite_members",
        "can_remove_members",
        "can_assign_roles",
        "can_view_finance",
        "can_manage_finance",
        "can_record_payments",
        "can_view_meetings",
        "can_manage_meetings",
        "can_view_reports",
        "can_manage_notifications",
        "can_use_ai",
        "can_export_data",
    },
    "treasurer": {
        "can_view_chama",
        "can_view_finance",
        "can_manage_finance",
        "can_record_payments",
        "can_view_meetings",
        "can_view_reports",
        "can_use_ai",
        "can_export_data",
    },
    "secretary": {
        "can_view_chama",
        "can_invite_members",
        "can_view_meetings",
        "can_manage_meetings",
        "can_manage_notifications",
        "can_use_ai",
    },
    "auditor": {
        "can_view_chama",
        "can_view_finance",
        "can_view_meetings",
        "can_view_reports",
        "can_export_data",
    },
    "member": {
        "can_view_chama",
        "can_view_meetings",
        "can_use_ai",
    },
}

MEMBERSHIP_ROLE_TO_RBAC_ROLE = {
    MembershipRole.SUPERADMIN: "super_admin",
    MembershipRole.ADMIN: "admin",
    MembershipRole.CHAMA_ADMIN: "chairperson",
    MembershipRole.TREASURER: "treasurer",
    MembershipRole.SECRETARY: "secretary",
    MembershipRole.AUDITOR: "auditor",
    MembershipRole.MEMBER: "member",
}


def get_membership_role_code(membership_role: str | None) -> str | None:
    if membership_role is None:
        return None
    return MEMBERSHIP_ROLE_TO_RBAC_ROLE.get(membership_role)


def get_role_permission_codes(role_code: str | None) -> set[str]:
    if not role_code:
        return set()
    return set(RBAC_ROLE_PERMISSION_MATRIX.get(role_code, set()))


def resolve_chama_id(*, request=None, view=None, obj=None) -> str | None:
    candidates: list[object] = []

    if obj is not None:
        candidates.extend(
            [
                getattr(obj, "chama_id", None),
                getattr(getattr(obj, "chama", None), "id", None),
                getattr(getattr(obj, "membership", None), "chama_id", None),
                getattr(getattr(obj, "member", None), "chama_id", None),
                getattr(getattr(obj, "user_role", None), "chama_id", None),
            ]
        )
        if getattr(getattr(obj, "_meta", None), "model_name", "") == "chama":
            candidates.append(getattr(obj, "id", None))

    if view is not None:
        getter = getattr(view, "get_scoped_chama_id", None)
        if callable(getter):
            try:
                candidates.append(getter(required=False))
            except TypeError:
                candidates.append(getter())
        kwargs = getattr(view, "kwargs", {}) or {}
        if "chama_id" in kwargs:
            candidates.append(kwargs.get("chama_id"))

    if request is not None:
        params = getattr(request, "query_params", None) or {}
        data = getattr(request, "data", None) or {}
        headers = getattr(request, "headers", None) or {}
        candidates.extend(
            [
                params.get("chama_id"),
                data.get("chama_id"),
                headers.get("X-CHAMA-ID"),
            ]
        )

    for value in candidates:
        if value not in (None, ""):
            return str(value)
    return None


def get_active_membership(user, chama_id: str | None) -> Membership | None:
    if not user or not user.is_authenticated or not user.is_active or not chama_id:
        return None
    if is_member_suspended(chama_id, user.id):
        return None
    return (
        Membership.objects.select_related("chama", "user")
        .filter(
            user=user,
            chama_id=chama_id,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        )
        .first()
    )


def get_active_memberships(user) -> list[Membership]:
    if not user or not user.is_authenticated or not user.is_active:
        return []
    return list(
        Membership.objects.select_related("chama")
        .filter(
            user=user,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        )
        .order_by("chama__name")
    )


def get_effective_role_code(
    user,
    chama_id: str | None,
    membership: Membership | None = None,
) -> str | None:
    if not chama_id:
        return None
    resolved_membership = membership or get_active_membership(user, chama_id)
    if not resolved_membership:
        return None
    effective_role = get_effective_role(user, chama_id, resolved_membership)
    return get_membership_role_code(effective_role or resolved_membership.role)


def user_has_chama_permission(
    *,
    user,
    permission_code: str,
    chama_id: str | None,
    membership: Membership | None = None,
) -> bool:
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    if permission_code not in RBAC_PERMISSION_DEFINITIONS:
        return False
    role_code = get_effective_role_code(user, chama_id, membership)
    return permission_code in get_role_permission_codes(role_code)


def user_has_any_chama_role(
    *,
    user,
    chama_id: str | None,
    allowed_roles: Iterable[str],
    membership: Membership | None = None,
) -> bool:
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    role_code = get_effective_role_code(user, chama_id, membership)
    return role_code in set(allowed_roles)


def build_role_catalog() -> list[dict]:
    items: list[dict] = []
    for code, definition in sorted(
        RBAC_ROLE_DEFINITIONS.items(),
        key=lambda item: int(item[1]["sort_order"]),
    ):
        items.append(
            {
                "code": code,
                "name": definition["name"],
                "description": definition["description"],
                "scope": definition["scope"],
                "membership_role_key": definition["membership_role_key"],
                "permissions": [
                    {
                        "code": permission_code,
                        **RBAC_PERMISSION_DEFINITIONS[permission_code],
                    }
                    for permission_code in sorted(get_role_permission_codes(code))
                ],
            }
        )
    return items


def build_user_access_snapshot(*, user, chama_id: str | None = None) -> dict:
    memberships = get_active_memberships(user)
    if chama_id:
        membership = next(
            (item for item in memberships if str(item.chama_id) == str(chama_id)),
            None,
        )
        role_code = get_effective_role_code(user, chama_id, membership)
        return {
            "chama_id": str(chama_id),
            "membership_id": str(membership.id) if membership else None,
            "role": role_code,
            "permissions": sorted(get_role_permission_codes(role_code)),
            "is_member": membership is not None,
        }

    return {
        "memberships": [
            {
                "membership_id": str(item.id),
                "chama_id": str(item.chama_id),
                "chama_name": item.chama.name,
                "role": get_effective_role_code(user, str(item.chama_id), item),
                "permissions": sorted(
                    get_role_permission_codes(
                        get_effective_role_code(user, str(item.chama_id), item)
                    )
                ),
            }
            for item in memberships
        ]
    }
