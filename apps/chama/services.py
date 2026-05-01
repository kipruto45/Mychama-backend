from datetime import timedelta
import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

logger = logging.getLogger(__name__)

from apps.chama.models import (
    Chama,
    ChamaContributionSetting,
    ChamaFinanceSetting,
    ChamaMeetingSetting,
    ChamaNotificationSetting,
    ChamaSettings,
    ChamaPrivacy,
    Invite,
    InviteLink,
    InviteStatus,
    LoanPolicy,
    Membership,
    MembershipRole,
    MemberStatus,
    RoleDelegation,
)
from apps.accounts.models import MemberKYC, MemberKYCStatus
from core.audit import create_audit_log

User = get_user_model()

ADMIN_EQUIVALENT_ROLES = {
    MembershipRole.SUPERADMIN,
    MembershipRole.CHAMA_ADMIN,
    
    MembershipRole.ADMIN,
}

ROLE_CANONICAL_MAP = {
    MembershipRole.SUPERADMIN: MembershipRole.CHAMA_ADMIN,
    
    MembershipRole.ADMIN: MembershipRole.CHAMA_ADMIN,
}

ROLE_PRIORITY = {
    MembershipRole.SUPERADMIN: 100,
    MembershipRole.ADMIN: 90,
    MembershipRole.CHAMA_ADMIN: 80,
    MembershipRole.TREASURER: 70,
    MembershipRole.SECRETARY: 60,
    MembershipRole.AUDITOR: 50,
    MembershipRole.MEMBER: 10,
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


class InviteServiceError(Exception):
    pass


class ChamaOnboardingError(Exception):
    pass


class ChamaOnboardingService:
    DEFAULT_ACCOUNT_MAP = {
        "cash": ("CASH", "Cash Account", "asset"),
        "mpesa": ("MPESA", "M-Pesa Collection Account", "asset"),
        "contributions": ("CONTRIB", "Contributions Income", "income"),
        "expenses": ("EXPENSE", "Expense Control", "expense"),
        "loans": ("LOANREC", "Loan Receivable", "asset"),
        "fines": ("FINEINC", "Fine Income", "income"),
    }

    @staticmethod
    @transaction.atomic
    def create_chama_with_defaults(*, payload: dict, actor):
        from apps.automations.domain_services import send_user_notification
        from apps.finance.models import Account, LoanProduct
        from apps.finance.models import ContributionType as FinanceContributionType
        from apps.payouts.models import PayoutRotation

        contribution_setup = payload["contribution_setup"]
        finance_settings = payload["finance_settings"]
        meeting_settings = payload["meeting_settings"]
        membership_rules = payload["membership_rules"]
        notification_defaults = payload.get("notification_defaults") or {}
        payout_rules = payload.get("payout_rules") or {}
        loan_rules = payload.get("loan_rules") or {}
        governance_rules = payload.get("governance_rules") or {}

        privacy = payload.get("privacy", ChamaPrivacy.INVITE_ONLY)
        join_mode = "auto_join" if privacy == ChamaPrivacy.OPEN and not membership_rules["invite_only"] else "approval_required"
        minimum_members_to_start = max(int(governance_rules.get("minimum_members_to_start", 3)), 2)
        max_members = max(int(membership_rules["max_members"]), minimum_members_to_start)
        quorum_percentage = int(governance_rules.get("quorum_percentage", meeting_settings["quorum_percentage"]))
        missed_payment_penalty_amount = governance_rules.get(
            "missed_payment_penalty_amount",
            contribution_setup["late_fine_amount"],
        )
        resolved_loans_enabled = bool(loan_rules.get("loans_enabled", finance_settings["loans_enabled"]))
        max_loan_amount = loan_rules.get("max_loan_amount", contribution_setup["amount"] * 10)
        interest_rate = loan_rules.get("interest_rate", "12.00")
        repayment_period_months = int(loan_rules.get("repayment_period_months", 12))
        approval_layers = int(loan_rules.get("approval_layers", 2))
        payout_rotation_order = str(payout_rules.get("rotation_order", "member_join_order")).strip() or "member_join_order"
        payout_trigger_mode = str(payout_rules.get("trigger_mode", "manual")).strip() or "manual"
        payout_method = str(payout_rules.get("payout_method", "mpesa")).strip() or "mpesa"
        constitution_summary = str(governance_rules.get("constitution_summary", "")).strip()

        chama = Chama.objects.create(
            name=payload["name"].strip(),
            description=str(payload.get("description", "")).strip(),
            county=payload["county"],
            subcounty=payload["subcounty"],
            currency=finance_settings.get("currency", "KES"),
            status="active",
            privacy=privacy,
            chama_type=payload["chama_type"],
            join_mode=join_mode,
            allow_public_join=privacy == ChamaPrivacy.OPEN and not membership_rules["invite_only"],
            require_approval=membership_rules["approval_required"],
            max_members=max_members,
            setup_completed=True,
            setup_step=6,
            created_by=actor,
            updated_by=actor,
        )

        now = timezone.now()
        membership = Membership.objects.create(
            user=actor,
            chama=chama,
            role=MembershipRole.CHAMA_ADMIN,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            joined_at=now,
            approved_at=now,
            approved_by=actor,
            created_by=actor,
            updated_by=actor,
        )

        default_invite_link = InviteLink.objects.create(
            chama=chama,
            created_by=actor,
            preassigned_role=MembershipRole.MEMBER,
            approval_required=membership_rules["approval_required"],
            max_uses=max(int(membership_rules["max_members"]) - 1, 1),
            expires_at=now + timedelta(days=30),
            is_active=True,
            updated_by=actor,
        )

        ChamaContributionSetting.objects.create(
            chama=chama,
            contribution_amount=contribution_setup["amount"],
            contribution_frequency=contribution_setup["frequency"],
            due_day=contribution_setup["due_day"],
            grace_period_days=contribution_setup["grace_period_days"],
            late_fine_amount=contribution_setup["late_fine_amount"],
            created_by=actor,
            updated_by=actor,
        )
        ChamaFinanceSetting.objects.create(
            chama=chama,
            currency=finance_settings["currency"],
            payment_methods=finance_settings["payment_methods"],
            loans_enabled=resolved_loans_enabled,
            fines_enabled=finance_settings["fines_enabled"],
            approval_rule=finance_settings["approval_rule"],
            created_by=actor,
            updated_by=actor,
        )
        ChamaMeetingSetting.objects.create(
            chama=chama,
            meeting_frequency=meeting_settings["meeting_frequency"],
            quorum_percentage=quorum_percentage,
            voting_enabled=meeting_settings["voting_enabled"],
            created_by=actor,
            updated_by=actor,
        )
        ChamaSettings.objects.update_or_create(
            chama=chama,
            defaults={
                "join_approval_policy": "admin" if membership_rules["approval_required"] else "auto",
                "meeting_frequency": meeting_settings["meeting_frequency"],
                "voting_quorum_percent": quorum_percentage,
                "grace_period_days": contribution_setup["grace_period_days"],
                "late_penalty_type": "flat",
                "late_penalty_amount": missed_payment_penalty_amount,
                "created_by": actor,
                "updated_by": actor,
            },
        )
        ChamaNotificationSetting.objects.create(
            chama=chama,
            member_join_alerts=notification_defaults.get("member_join_alerts", True),
            payment_received_alerts=notification_defaults.get("payment_received_alerts", True),
            meeting_reminders=notification_defaults.get("meeting_reminders", True),
            loan_updates=notification_defaults.get("loan_updates", True),
            created_by=actor,
            updated_by=actor,
        )

        FinanceContributionType.objects.create(
            chama=chama,
            name="Standard Contribution",
            frequency=contribution_setup["frequency"],
            default_amount=contribution_setup["amount"],
            is_active=True,
            created_by=actor,
            updated_by=actor,
        )

        LoanPolicy.objects.update_or_create(
            chama=chama,
            defaults={
                "loans_enabled": resolved_loans_enabled,
                "max_member_loan_amount": max_loan_amount,
                "interest_rate": interest_rate,
                "min_repayment_period": 1,
                "max_repayment_period": repayment_period_months,
                "require_treasurer_approval": approval_layers >= 2,
                "require_admin_approval": approval_layers >= 2,
                "require_committee_vote": approval_layers >= 3,
                "committee_threshold_amount": max_loan_amount if approval_layers >= 3 else 0,
                "late_fee_type": "fixed",
                "late_fee_value": missed_payment_penalty_amount,
                "grace_period_days": contribution_setup["grace_period_days"],
                "created_by": actor,
                "updated_by": actor,
            },
        )

        if resolved_loans_enabled:
            LoanProduct.objects.create(
                chama=chama,
                name="Default Loan Product",
                is_active=True,
                is_default=True,
                max_loan_amount=max_loan_amount,
                contribution_multiple=2,
                interest_type="flat",
                interest_rate=interest_rate,
                min_duration_months=1,
                max_duration_months=repayment_period_months,
                grace_period_days=7,
                late_penalty_type="fixed",
                late_penalty_value=missed_payment_penalty_amount or 0,
                minimum_membership_months=1,
                minimum_contribution_months=1,
                require_treasurer_review=True,
                require_separate_disburser=True,
                created_by=actor,
                updated_by=actor,
            )

        rotation, _ = PayoutRotation.objects.get_or_create(
            chama=chama,
            defaults={
                "members_in_rotation": [str(membership.id)],
                "current_position": 0,
                "rotation_cycle": 1,
                "created_by": actor,
                "updated_by": actor,
            },
        )
        if not rotation.members_in_rotation:
            rotation.members_in_rotation = [str(membership.id)]
            rotation.updated_by = actor
            rotation.save(update_fields=["members_in_rotation", "updated_by", "updated_at"])

        if constitution_summary:
            from apps.governance.models import ChamaRule, RuleCategory, RuleStatus

            ChamaRule.objects.create(
                chama=chama,
                category=RuleCategory.CONSTITUTION,
                title="Founding Constitution",
                description="Constitution captured during chama creation workflow.",
                content=constitution_summary,
                status=RuleStatus.ACTIVE,
                effective_date=timezone.now().date(),
                requires_acknowledgment=False,
                created_by=actor,
                updated_by=actor,
            )

        for code, name, account_type in ChamaOnboardingService.DEFAULT_ACCOUNT_MAP.values():
            Account.objects.get_or_create(
                chama=chama,
                code=code,
                defaults={
                    "name": name,
                    "type": account_type,
                    "system_managed": True,
                    "created_by": actor,
                    "updated_by": actor,
                },
            )

        create_audit_log(
            actor=actor,
            chama_id=chama.id,
            action="chama_created",
            entity_type="Chama",
            entity_id=chama.id,
            metadata={
                "creator_membership_id": str(membership.id),
                "privacy": privacy,
                "payment_methods": finance_settings["payment_methods"],
                "loans_enabled": resolved_loans_enabled,
                "fines_enabled": finance_settings["fines_enabled"],
                "max_members": max_members,
                "minimum_members_to_start": minimum_members_to_start,
                "payout_rotation_order": payout_rotation_order,
                "payout_trigger_mode": payout_trigger_mode,
                "default_payout_method": payout_method,
                "loan_approval_layers": approval_layers,
                "loan_repayment_period_months": repayment_period_months,
                "default_invite_link_id": str(default_invite_link.id),
                "join_code": chama.join_code,
            },
        )
        send_user_notification(
            user=actor,
            chama=chama,
            message=(
                f"{chama.name} is ready. You were assigned Chairperson access and "
                "a shareable invite link was generated for your first members."
            ),
            subject="Chama created",
            channels=["in_app"],
            notification_type="system",
            idempotency_key=f"chama-created:{chama.id}:{actor.id}",
            actor=actor,
            route="chama/detail",
            route_params={"chama_id": str(chama.id)},
            metadata={
                "join_code": chama.join_code,
                "default_invite_link_id": str(default_invite_link.id),
                "default_invite_token": default_invite_link.build_presented_token(),
                "default_invite_code": default_invite_link.code,
            },
        )
        return chama


class InviteService:
    @staticmethod
    def _require_invite_permission(actor, chama: Chama) -> Membership:
        membership = Membership.objects.filter(
            user=actor,
            chama=chama,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        ).first()
        if not membership:
            raise InviteServiceError("Active chama membership is required.")
        effective_role = get_effective_role(actor, chama.id, membership)
        if effective_role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.ADMIN,
            MembershipRole.SECRETARY,
        }:
            raise InviteServiceError("Only authorized chama administrators can manage invites.")
        return membership

    @staticmethod
    def _ensure_role_assignable(actor_role: str | None, requested_role: str) -> str:
        requested = canonicalize_role(requested_role) or MembershipRole.MEMBER
        actor_rank = ROLE_PRIORITY.get(actor_role or MembershipRole.MEMBER, 0)
        requested_rank = ROLE_PRIORITY.get(requested, 0)
        if requested_rank >= actor_rank and actor_role != MembershipRole.SUPERADMIN:
            raise InviteServiceError("You cannot invite someone into an equal or higher role.")
        return requested

    @staticmethod
    def _match_identity(invite: Invite, user) -> bool:
        if invite.invitee_user_id and invite.invitee_user_id != user.id:
            return False
        if invite.invitee_phone and invite.invitee_phone != getattr(user, "phone", ""):
            return False
        if invite.invitee_email and invite.invitee_email.lower() != getattr(user, "email", "").lower():
            return False
        return True

    @staticmethod
    def create_invite(*, chama: Chama, payload: dict, actor):
        from apps.automations.domain_services import send_user_notification
        from apps.notifications.sms import send_sms_message

        membership = InviteService._require_invite_permission(actor, chama)
        actor_role = get_effective_role(actor, chama.id, membership)
        role_to_assign = InviteService._ensure_role_assignable(
            actor_role,
            payload.get("role_to_assign", MembershipRole.MEMBER),
        )
        invitee_user = None
        invitee_user_id = payload.get("invitee_user_id")
        if invitee_user_id:
            invitee_user = get_object_or_404(User, id=invitee_user_id)

        invitee_phone = payload.get("invitee_phone", "") or getattr(invitee_user, "phone", "")
        invitee_email = payload.get("invitee_email", "") or getattr(invitee_user, "email", "")

        if invitee_user and Membership.objects.filter(
            user=invitee_user,
            chama=chama,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        ).exists():
            raise InviteServiceError("Invitee is already an active member of this chama.")

        duplicate_query = Invite.objects.filter(
            chama=chama,
            status=InviteStatus.PENDING,
        )
        if invitee_phone:
            duplicate_query = duplicate_query.filter(invitee_phone=invitee_phone)
        elif invitee_email:
            duplicate_query = duplicate_query.filter(invitee_email=invitee_email)
        elif invitee_user:
            duplicate_query = duplicate_query.filter(invitee_user=invitee_user)
        if duplicate_query.exists():
            raise InviteServiceError("There is already a pending invite for this recipient.")

        invite = Invite.objects.create(
            chama=chama,
            invited_by=actor,
            invitee_phone=invitee_phone,
            invitee_email=invitee_email,
            invitee_user=invitee_user,
            phone=invitee_phone,
            email=invitee_email,
            identifier=invitee_phone or invitee_email or str(invitee_user.id),
            role=role_to_assign,
            role_to_assign=role_to_assign,
            token=Invite.generate_token(),
            code=Invite.generate_code(),
            status=InviteStatus.PENDING,
            max_uses=payload.get("max_uses", 1),
            expires_at=timezone.now() + timedelta(days=payload.get("expires_in_days", 7)),
            created_by=actor,
            updated_by=actor,
        )
        create_audit_log(
            actor=actor,
            chama_id=chama.id,
            action="invite_created",
            entity_type="Invite",
            entity_id=invite.id,
            metadata={
                "invitee_phone": invite.invitee_phone,
                "invitee_email": invite.invitee_email,
                "invitee_user_id": str(invite.invitee_user_id or ""),
                "role_to_assign": invite.role_to_assign,
                "code": invite.code,
            },
        )
        invite_link = invite.build_presented_token()
        invite_message = (
            f"You have been invited to join {chama.name}. "
            f"Use code {invite.code} or open {invite_link} before {invite.expires_at:%Y-%m-%d}."
        )
        if invite.invitee_user:
            send_user_notification(
                user=invite.invitee_user,
                chama=chama,
                message=invite_message,
                subject="You have been invited",
                channels=["in_app", "sms"],
                notification_type="system",
                idempotency_key=f"invite-created:{invite.id}:{invite.invitee_user_id}",
                actor=actor,
                route="invite",
                route_params={"invite_code": invite.code},
                metadata={"invite_code": invite.code},
            )
        elif invite.invitee_phone:
            try:
                send_sms_message(
                    phone_number=invite.invitee_phone,
                    message=invite_message,
                )
            except Exception:
                pass
        return invite

    @staticmethod
    def accept_invite(*, presented_token: str, actor):
        from apps.automations.domain_services import send_user_notification

        invite = Invite.resolve_presented_token(presented_token, queryset=Invite.objects.select_related("chama"))
        if not invite or not invite.is_valid():
            raise InviteServiceError("Invite token is invalid or expired.")
        
        # KYC check is now informational only - users can join and complete KYC in dashboard
        has_approved_kyc = MemberKYC.objects.filter(
            user=actor,
            status=MemberKYCStatus.APPROVED,
            account_frozen_for_compliance=False,
            requires_reverification=False,
        ).exists()
        
        if not has_approved_kyc:
            logger.info(
                f"User {actor.id} joining without approved KYC - reminder will be shown in dashboard"
            )
        
        if not InviteService._match_identity(invite, actor):
            raise InviteServiceError("Invite is restricted to another user identity.")
        existing_membership = Membership.objects.filter(
            user=actor,
            chama=invite.chama,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            exited_at__isnull=True,
        ).first()
        if existing_membership:
            raise InviteServiceError("You are already a member of this chama.")
        membership, _ = Membership.objects.get_or_create(
            user=actor,
            chama=invite.chama,
            defaults={
                "role": invite.role_to_assign or invite.role,
                "status": MemberStatus.ACTIVE,
                "is_active": True,
                "is_approved": True,
                "joined_at": timezone.now(),
                "approved_at": timezone.now(),
                "approved_by": invite.invited_by,
                "created_by": invite.invited_by,
                "updated_by": actor,
            },
        )
        membership.role = invite.role_to_assign or invite.role
        membership.status = MemberStatus.ACTIVE
        membership.is_active = True
        membership.is_approved = True
        membership.approved_at = timezone.now()
        membership.approved_by = invite.invited_by
        membership.updated_by = actor
        membership.save()

        invite.status = InviteStatus.ACCEPTED
        invite.accepted_by = actor
        invite.accepted_at = timezone.now()
        invite.use_count += 1
        invite.updated_by = actor
        invite.save(update_fields=["status", "accepted_by", "accepted_at", "use_count", "updated_by", "updated_at"])
        create_audit_log(
            actor=actor,
            chama_id=invite.chama_id,
            action="invite_accepted",
            entity_type="Invite",
            entity_id=invite.id,
            metadata={"membership_id": str(membership.id), "role": membership.role},
        )
        send_user_notification(
            user=invite.invited_by,
            chama=invite.chama,
            message=(
                f"{actor.get_full_name()} accepted the invite to join {invite.chama.name}."
            ),
            subject="Invite accepted",
            channels=["in_app"],
            notification_type="system",
            idempotency_key=f"invite-accepted:{invite.id}:{invite.invited_by_id}",
            actor=actor,
            route="chama/detail",
            route_params={"chama_id": str(invite.chama_id)},
            metadata={"accepted_user_id": str(actor.id)},
        )
        return invite, membership

    @staticmethod
    def accept_invite_code(*, code: str, actor):
        invite = Invite.resolve_code(
            code,
            queryset=Invite.objects.select_related("chama"),
        )
        if not invite:
            raise InviteServiceError("Invite code is invalid.")
        return InviteService.accept_invite(
            presented_token=invite.build_presented_token(),
            actor=actor,
        )

    @staticmethod
    def decline_invite(*, invite: Invite, actor, reason: str = ""):
        from apps.automations.domain_services import send_user_notification

        if not invite.is_valid():
            raise InviteServiceError("Invite cannot be declined in its current state.")
        if not InviteService._match_identity(invite, actor):
            raise InviteServiceError("Invite is restricted to another user identity.")
        invite.status = InviteStatus.DECLINED
        invite.declined_at = timezone.now()
        invite.updated_by = actor
        invite.save(update_fields=["status", "declined_at", "updated_by", "updated_at"])
        create_audit_log(
            actor=actor,
            chama_id=invite.chama_id,
            action="invite_declined",
            entity_type="Invite",
            entity_id=invite.id,
            metadata={"reason": reason},
        )
        send_user_notification(
            user=invite.invited_by,
            chama=invite.chama,
            message=(
                f"{actor.get_full_name()} declined the invite for {invite.chama.name}."
            ),
            subject="Invite declined",
            channels=["in_app"],
            notification_type="system",
            idempotency_key=f"invite-declined:{invite.id}:{invite.invited_by_id}",
            actor=actor,
            route="chama/detail",
            route_params={"chama_id": str(invite.chama_id)},
            metadata={"reason": reason},
        )
        return invite

    @staticmethod
    def revoke_invite(*, invite: Invite, actor, reason: str = ""):
        from apps.automations.domain_services import send_user_notification

        InviteService._require_invite_permission(actor, invite.chama)
        invite.status = InviteStatus.REVOKED
        invite.revoked_at = timezone.now()
        invite.revoked_by = actor
        invite.revoke_reason = reason or "Revoked by administrator."
        invite.updated_by = actor
        invite.save(
            update_fields=[
                "status",
                "revoked_at",
                "revoked_by",
                "revoke_reason",
                "updated_by",
                "updated_at",
            ]
        )
        create_audit_log(
            actor=actor,
            chama_id=invite.chama_id,
            action="invite_revoked",
            entity_type="Invite",
            entity_id=invite.id,
            metadata={"reason": invite.revoke_reason},
        )
        if invite.invitee_user:
            send_user_notification(
                user=invite.invitee_user,
                chama=invite.chama,
                message=(
                    f"Your invite to join {invite.chama.name} was revoked. "
                    f"Reason: {invite.revoke_reason}"
                ),
                subject="Invite revoked",
                channels=["in_app"],
                notification_type="system",
                idempotency_key=f"invite-revoked:{invite.id}:{invite.invitee_user_id}",
                actor=actor,
                route="invite",
                route_params={"invite_code": invite.code},
                metadata={"reason": invite.revoke_reason},
            )
        return invite
