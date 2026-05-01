from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus
from apps.deeplinks.deeplinks_service import DeepLinksService
from apps.notifications.models import NotificationType
from apps.notifications.services import NotificationService
from core.audit import create_audit_log

User = get_user_model()

ROLE_CONFLICTS = {
    MembershipRole.TREASURER: {
        MembershipRole.SECRETARY,
        MembershipRole.AUDITOR,
    },
    MembershipRole.SECRETARY: {
        MembershipRole.TREASURER,
        MembershipRole.AUDITOR,
    },
    MembershipRole.AUDITOR: {
        MembershipRole.CHAMA_ADMIN,
        MembershipRole.ADMIN,
        MembershipRole.SUPERADMIN,
        MembershipRole.TREASURER,
        MembershipRole.SECRETARY,
    },
    MembershipRole.CHAMA_ADMIN: {MembershipRole.AUDITOR},
}

MANDATORY_COMMITTEE_ROLES = (
    MembershipRole.TREASURER,
    MembershipRole.SECRETARY,
)

HANDOVER_CHECKLIST = [
    "Review pending approvals, payments, and member issues.",
    "Confirm access to finance, governance, and messaging tools.",
    "Share outstanding actions and supporting records with the incoming role holder.",
]


def _deep_link_payload(*, route: str, params: dict | None = None, chama_id=None) -> dict:
    return {
        "deep_link": DeepLinksService.generate_deep_link(
            route=route,
            params=params or {},
            chama_id=str(chama_id) if chama_id else None,
        ),
        "universal_link": DeepLinksService.generate_universal_link(
            route=route,
            params=params or {},
            chama_id=str(chama_id) if chama_id else None,
        ),
    }


def send_user_notification(
    *,
    user,
    chama,
    message: str,
    subject: str,
    channels: list[str],
    notification_type: str = NotificationType.SYSTEM,
    idempotency_key: str,
    actor=None,
    route: str = "chama/detail",
    route_params: dict | None = None,
    metadata: dict | None = None,
):
    link_payload = _deep_link_payload(
        route=route,
        params=route_params or {},
        chama_id=getattr(chama, "id", None),
    )
    return NotificationService.send_notification(
        user=user,
        chama=chama,
        message=message,
        subject=subject,
        channels=channels,
        notification_type=notification_type,
        action_url=link_payload["universal_link"],
        metadata={**(metadata or {}), **link_payload},
        idempotency_key=idempotency_key,
        actor=actor,
    )


def notify_system_admins(
    *,
    chama,
    subject: str,
    message: str,
    idempotency_key_prefix: str,
    actor=None,
    route: str = "chama/detail",
    route_params: dict | None = None,
):
    admins = User.objects.filter(
        is_active=True,
    ).filter(Q(is_staff=True) | Q(is_superuser=True))
    sent = 0
    for admin in admins:
        send_user_notification(
            user=admin,
            chama=chama,
            message=message,
            subject=subject,
            channels=["in_app", "email"],
            notification_type=NotificationType.SYSTEM,
            idempotency_key=f"{idempotency_key_prefix}:{admin.id}",
            actor=actor,
            route=route,
            route_params=route_params,
            metadata={"audience": "system_admin"},
        )
        sent += 1
    return sent


def active_role_holders(*, chama: Chama, role: str):
    return Membership.objects.select_related("user").filter(
        chama=chama,
        role=role,
        is_active=True,
        is_approved=True,
        status=MemberStatus.ACTIVE,
        exited_at__isnull=True,
    )


def ensure_no_dual_role_conflict(*, chama: Chama, user, new_role: str) -> None:
    conflict_roles = ROLE_CONFLICTS.get(new_role, set())
    if not conflict_roles:
        return

    conflicting_membership = Membership.objects.filter(
        chama=chama,
        user=user,
        role__in=conflict_roles,
        is_active=True,
        is_approved=True,
        status=MemberStatus.ACTIVE,
        exited_at__isnull=True,
    ).first()
    if conflicting_membership:
        raise ValueError(
            f"{user.get_full_name()} already holds conflicting role "
            f"{conflicting_membership.role} in this chama."
        )


def notify_role_vacancies(*, chama: Chama, actor=None) -> list[str]:
    vacancies: list[str] = []
    chairpersons = active_role_holders(chama=chama, role=MembershipRole.CHAMA_ADMIN)
    if not chairpersons.exists():
        return vacancies

    for role in MANDATORY_COMMITTEE_ROLES:
        if active_role_holders(chama=chama, role=role).exists():
            continue
        vacancies.append(role)
        for chairperson in chairpersons:
            send_user_notification(
                user=chairperson.user,
                chama=chama,
                message=(
                    f"{role.replace('_', ' ').title()} role is currently vacant in {chama.name}. "
                    "Assign a replacement to keep finance and governance workflows moving."
                ),
                subject=f"{role.title()} role vacant",
                channels=["in_app", "sms"],
                notification_type=NotificationType.SYSTEM,
                idempotency_key=f"role-vacancy:{chama.id}:{role}:{chairperson.user_id}:{timezone.localdate().isoformat()}",
                actor=actor,
                route="chama/detail",
                route_params={"chama_id": str(chama.id)},
                metadata={"vacant_role": role},
            )
    return vacancies


def apply_membership_role_change(*, chama: Chama, member_user, new_role: str, actor=None):
    membership = Membership.objects.select_related("user", "chama").filter(
        chama=chama,
        user=member_user,
        is_active=True,
        is_approved=True,
        status=MemberStatus.ACTIVE,
        exited_at__isnull=True,
    ).first()
    if not membership:
        raise ValueError("Role changes require an active approved membership.")

    ensure_no_dual_role_conflict(chama=chama, user=member_user, new_role=new_role)

    previous_role = membership.role
    if previous_role == new_role:
        return membership, previous_role, []

    outgoing_memberships = []
    if new_role in MANDATORY_COMMITTEE_ROLES:
        outgoing_memberships = list(
            active_role_holders(chama=chama, role=new_role).exclude(user=member_user)
        )
        for outgoing in outgoing_memberships:
            outgoing.role = MembershipRole.MEMBER
            outgoing.updated_by = actor
            outgoing.save(update_fields=["role", "updated_by", "updated_at"])

    membership.role = new_role
    membership.updated_by = actor
    membership.save(update_fields=["role", "updated_by", "updated_at"])
    return membership, previous_role, outgoing_memberships


def notify_role_change(
    *,
    chama: Chama,
    membership: Membership,
    old_role: str,
    new_role: str,
    outgoing_memberships: list[Membership] | None = None,
    actor=None,
    reason: str = "",
):
    outgoing_memberships = outgoing_memberships or []
    incoming_message = (
        f"Your role in {chama.name} is now {new_role.replace('_', ' ').title()}. "
        "Your permissions were updated immediately."
    )
    if reason:
        incoming_message += f" Reason: {reason}"
    incoming_message += " Responsibilities: review the dashboard, open tasks, and pending approvals."
    send_user_notification(
        user=membership.user,
        chama=chama,
        message=incoming_message,
        subject="Role updated",
        channels=["in_app", "sms"],
        notification_type=NotificationType.SYSTEM,
        idempotency_key=f"role-change:incoming:{chama.id}:{membership.user_id}:{new_role}:{timezone.localdate().isoformat()}",
        actor=actor,
        route="member/detail",
        route_params={
            "chama_id": str(chama.id),
            "user_id": str(membership.user_id),
        },
        metadata={"old_role": old_role, "new_role": new_role},
    )

    for outgoing in outgoing_memberships:
        checklist = " ".join(HANDOVER_CHECKLIST)
        send_user_notification(
            user=outgoing.user,
            chama=chama,
            message=(
                f"You are handing over the {new_role.replace('_', ' ').title()} role in {chama.name}. "
                f"Complete the handover checklist within 7 days: {checklist}"
            ),
            subject="Role handover started",
            channels=["in_app", "sms"],
            notification_type=NotificationType.SYSTEM,
            idempotency_key=f"role-change:outgoing:{chama.id}:{outgoing.user_id}:{new_role}:{timezone.localdate().isoformat()}",
            actor=actor,
            route="member/detail",
            route_params={
                "chama_id": str(chama.id),
                "user_id": str(outgoing.user_id),
            },
            metadata={
                "handover_role": new_role,
                "handover_deadline": (
                    timezone.now() + timedelta(days=7)
                ).isoformat(),
            },
        )

    for member in Membership.objects.select_related("user").filter(
        chama=chama,
        is_active=True,
        is_approved=True,
        status=MemberStatus.ACTIVE,
        exited_at__isnull=True,
    ):
        if member.user_id == membership.user_id:
            continue
        send_user_notification(
            user=member.user,
            chama=chama,
            message=(
                f"{membership.user.get_full_name()} is now serving as "
                f"{new_role.replace('_', ' ').title()} in {chama.name}."
            ),
            subject="Leadership update",
            channels=["in_app"],
            notification_type=NotificationType.SYSTEM,
            idempotency_key=f"role-change:group:{chama.id}:{member.user_id}:{membership.user_id}:{new_role}:{timezone.localdate().isoformat()}",
            actor=actor,
            route="chama/detail",
            route_params={"chama_id": str(chama.id)},
            metadata={"old_role": old_role, "new_role": new_role},
        )

    create_audit_log(
        actor=actor,
        chama_id=chama.id,
        action="role_handover_started",
        entity_type="Membership",
        entity_id=membership.id,
        metadata={
            "old_role": old_role,
            "new_role": new_role,
            "outgoing_user_ids": [str(item.user_id) for item in outgoing_memberships],
            "handover_deadline": (timezone.now() + timedelta(days=7)).isoformat(),
        },
    )

    return notify_role_vacancies(chama=chama, actor=actor)


def unlock_member_access_after_kyc(*, kyc_record, actor=None):
    memberships = Membership.objects.filter(
        user=kyc_record.user,
        chama=kyc_record.chama,
        is_active=True,
        is_approved=True,
        status=MemberStatus.ACTIVE,
        exited_at__isnull=True,
    )
    for membership in memberships:
        membership.can_request_loan = True
        membership.can_withdraw_savings = True
        membership.can_vote = True
        membership.restriction_reason = ""
        membership.restrictions_applied_at = timezone.now()
        membership.restrictions_applied_by = actor
        membership.updated_by = actor
        membership.save(
            update_fields=[
                "can_request_loan",
                "can_withdraw_savings",
                "can_vote",
                "restriction_reason",
                "restrictions_applied_at",
                "restrictions_applied_by",
                "updated_by",
                "updated_at",
            ]
        )

    user = kyc_record.user
    if not user.phone_verified:
        user.phone_verified = True
        user.phone_verified_at = timezone.now()
        user.save(update_fields=["phone_verified", "phone_verified_at"])


def notify_kyc_result(*, kyc_record, actor=None):
    chama_name = kyc_record.chama.name if getattr(kyc_record, "chama", None) else "MyChama"
    route = "member/detail" if getattr(kyc_record, "chama_id", None) else "profile/kyc"
    route_params = (
        {"chama_id": str(kyc_record.chama_id), "user_id": str(kyc_record.user_id)}
        if getattr(kyc_record, "chama_id", None)
        else {"user_id": str(kyc_record.user_id)}
    )
    if kyc_record.status == "approved":
        unlock_member_access_after_kyc(kyc_record=kyc_record, actor=actor)
        send_user_notification(
            user=kyc_record.user,
            chama=kyc_record.chama,
            message=(
                f"KYC approved for {chama_name}. Full access has been unlocked automatically."
            ),
            subject="KYC approved",
            channels=["in_app", "sms"],
            notification_type=NotificationType.SYSTEM,
            idempotency_key=f"kyc-approved:{kyc_record.id}",
            actor=actor,
            route=route,
            route_params=route_params,
            metadata={"kyc_status": kyc_record.status},
        )
        return

    if kyc_record.status != "rejected":
        return

    review_reason = (
        kyc_record.review_note.strip()
        or kyc_record.last_rejection_reason.strip()
        or "Verification requirements were not met."
    )
    send_user_notification(
        user=kyc_record.user,
        chama=kyc_record.chama,
        message=(
            f"KYC rejected for {chama_name}. Reason: {review_reason}. Please resubmit updated documents."
        ),
        subject="KYC rejected",
        channels=["in_app", "sms"],
        notification_type=NotificationType.SYSTEM,
        idempotency_key=f"kyc-rejected:{kyc_record.id}:{kyc_record.rejection_attempts}",
        actor=actor,
        route=route,
        route_params=route_params,
        metadata={"kyc_status": kyc_record.status, "reason": review_reason},
    )

    if kyc_record.rejection_attempts < 3 or kyc_record.escalated_to_system_admin_at:
        return

    notify_system_admins(
        chama=kyc_record.chama,
        subject="KYC requires manual review",
        message=(
            f"KYC for {kyc_record.user.get_full_name()} in {chama_name} has failed "
            f"{kyc_record.rejection_attempts} times and needs manual review."
        ),
        idempotency_key_prefix=f"kyc-manual-review:{kyc_record.id}:{kyc_record.rejection_attempts}",
        actor=actor,
        route=route,
        route_params=route_params,
    )
    kyc_record.escalated_to_system_admin_at = timezone.now()
    kyc_record.save(update_fields=["escalated_to_system_admin_at", "updated_at"])


def notify_join_request_created(*, membership_request, actor=None):
    reviewers = active_role_holders(
        chama=membership_request.chama,
        role=MembershipRole.CHAMA_ADMIN,
    )
    if not reviewers.exists():
        reviewers = Membership.objects.select_related("user").filter(
            chama=membership_request.chama,
            role=MembershipRole.SECRETARY,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        )
    for reviewer in reviewers:
        send_user_notification(
            user=reviewer.user,
            chama=membership_request.chama,
            message=(
                f"{membership_request.user.get_full_name()} requested to join "
                f"{membership_request.chama.name}."
            ),
            subject="New join request",
            channels=["in_app", "push"],
            notification_type=NotificationType.SYSTEM,
            idempotency_key=f"join-request-created:{membership_request.id}:{reviewer.user_id}",
            actor=actor,
            route="chama/detail",
            route_params={"chama_id": str(membership_request.chama_id)},
        )


def notify_join_request_review(
    *,
    membership_request,
    approved: bool,
    actor=None,
):
    status_label = "approved" if approved else "rejected"
    if approved:
        message = (
            f"Your request to join {membership_request.chama.name} was approved. "
            "Open the chama dashboard to review rules, contributions, and upcoming activity."
        )
    else:
        message = f"Your request to join {membership_request.chama.name} was rejected."
        if membership_request.review_note:
            message += f" Reason: {membership_request.review_note.strip()}"
    send_user_notification(
        user=membership_request.user,
        chama=membership_request.chama,
        message=message,
        subject="Join request update",
        channels=["in_app", "push", "sms"] if approved else ["in_app", "push"],
        notification_type=NotificationType.SYSTEM,
        idempotency_key=f"join-request-review:{membership_request.id}:{status_label}",
        actor=actor,
        route="chama/detail",
        route_params={"chama_id": str(membership_request.chama_id)},
        metadata={"review_status": status_label},
    )
