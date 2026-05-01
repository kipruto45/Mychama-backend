from __future__ import annotations

import secrets
import string
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.kyc.services import sync_user_access_state
from apps.accounts.models import MemberKYC, MemberKYCDocumentType, MemberKYCStatus
from apps.billing.models import FeatureOverride
from apps.chama.models import (
    Chama,
    ChamaContributionSetting,
    ChamaFinanceSetting,
    ChamaMeetingSetting,
    ChamaNotificationSetting,
    InviteLink,
    JoinCodeMode,
    Membership,
    MembershipRole,
    MemberStatus,
)
from apps.chama.services import ChamaOnboardingService

User = get_user_model()


def _generate_password(length: int = 18) -> str:
    if length < 12:
        length = 12
    alphabet = string.ascii_letters + string.digits
    symbols = "!@#$%^&*()-_=+"
    required = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
        secrets.choice(symbols),
    ]
    remaining = [secrets.choice(alphabet + symbols) for _ in range(length - len(required))]
    chars = required + remaining
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def _generate_unique_phone() -> str:
    # Reserve a dev/test range to avoid colliding with real members.
    for _ in range(5000):
        candidate = f"+254701{secrets.randbelow(1000000):06d}"
        if not User.objects.filter(phone=candidate).exists():
            return candidate
    raise CommandError("Unable to generate a unique phone number.")


def _ensure_verified_user(*, user, email: str | None) -> None:
    now = timezone.now()
    update_fields: set[str] = set()
    if not user.phone_verified:
        user.phone_verified = True
        user.phone_verified_at = user.phone_verified_at or now
        update_fields |= {"phone_verified", "phone_verified_at"}
    if email:
        if user.email != email:
            user.email = email
            update_fields.add("email")
        if not getattr(user, "email_verified", False):
            user.email_verified = True
            user.email_verified_at = getattr(user, "email_verified_at", None) or now
            update_fields |= {"email_verified", "email_verified_at"}
    if update_fields:
        user.save(update_fields=list(update_fields))
    sync_user_access_state(user)


def _ensure_kyc_approved(*, user) -> MemberKYC:
    existing = (
        MemberKYC.objects.filter(user=user, status=MemberKYCStatus.APPROVED)
        .order_by("-approved_at", "-updated_at")
        .first()
    )
    if existing:
        return existing

    now = timezone.now()
    record = MemberKYC.objects.create(
        user=user,
        chama=None,
        onboarding_path="create_chama",
        status=MemberKYCStatus.APPROVED,
        document_type=MemberKYCDocumentType.NATIONAL_ID,
        id_number=f"DEVKYC-{secrets.token_hex(4).upper()}",
        legal_name=user.full_name,
        phone_number=user.phone,
        quality_front_passed=True,
        quality_back_passed=True,
        liveness_passed=True,
        processed_at=now,
        approved_at=now,
        created_at=now,
        updated_at=now,
    )
    sync_user_access_state(user)
    return record


def _ensure_chama_fully_configured(*, chama: Chama, actor) -> None:
    from apps.finance.models import Account, LoanProduct
    from apps.finance.models import ContributionType as FinanceContributionType

    # Ensure onboarding flags.
    updates: set[str] = set()
    if chama.status != "active":
        chama.status = "active"
        updates.add("status")
    if not chama.setup_completed:
        chama.setup_completed = True
        updates.add("setup_completed")
    if chama.setup_step < 6:
        chama.setup_step = 6
        updates.add("setup_step")
    if chama.join_mode != JoinCodeMode.APPROVAL_REQUIRED:
        chama.join_mode = JoinCodeMode.APPROVAL_REQUIRED
        updates.add("join_mode")
    if updates:
        chama.updated_by = actor
        updates.add("updated_by")
        chama.save(update_fields=list(updates | {"updated_at"}))

    # Ensure one-to-one settings exist.
    ChamaContributionSetting.objects.get_or_create(
        chama=chama,
        defaults={
            "contribution_amount": Decimal("500.00"),
            "contribution_frequency": "monthly",
            "due_day": 5,
            "grace_period_days": 3,
            "late_fine_amount": Decimal("50.00"),
            "created_by": actor,
            "updated_by": actor,
        },
    )
    ChamaFinanceSetting.objects.get_or_create(
        chama=chama,
        defaults={
            "currency": "KES",
            "payment_methods": ["mpesa"],
            "loans_enabled": True,
            "fines_enabled": True,
            "approval_rule": "maker_checker",
            "created_by": actor,
            "updated_by": actor,
        },
    )
    ChamaMeetingSetting.objects.get_or_create(
        chama=chama,
        defaults={
            "meeting_frequency": "monthly",
            "quorum_percentage": 50,
            "voting_enabled": True,
            "created_by": actor,
            "updated_by": actor,
        },
    )
    ChamaNotificationSetting.objects.get_or_create(
        chama=chama,
        defaults={
            "member_join_alerts": True,
            "payment_received_alerts": True,
            "meeting_reminders": True,
            "loan_updates": True,
            "created_by": actor,
            "updated_by": actor,
        },
    )

    # Ensure at least one active invite link exists.
    now = timezone.now()
    link = (
        InviteLink.objects.filter(
            chama=chama,
            is_active=True,
            revoked_at__isnull=True,
            expires_at__gt=now,
        )
        .order_by("-created_at")
        .first()
    )
    if not link:
        InviteLink.objects.create(
            chama=chama,
            token=InviteLink.generate_token(),
            requires_signature=False,
            created_by=actor,
            preassigned_role=MembershipRole.MEMBER,
            approval_required=True,
            max_uses=max(int(chama.max_members) - 1, 1),
            expires_at=now + timezone.timedelta(days=30),
            is_active=True,
            updated_by=actor,
        )

    # Enable AI basic for the chama by default (dev convenience).
    FeatureOverride.objects.update_or_create(
        chama=chama,
        feature_key="ai_basic",
        defaults={"value": True, "created_by": actor},
    )

    # Ensure finance defaults exist (accounts + contribution type + default loan product).
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

    contribution_setting = getattr(chama, "contribution_setting", None)
    if contribution_setting:
        FinanceContributionType.objects.get_or_create(
            chama=chama,
            name="Standard Contribution",
            defaults={
                "frequency": contribution_setting.contribution_frequency,
                "default_amount": contribution_setting.contribution_amount,
                "is_active": True,
                "created_by": actor,
                "updated_by": actor,
            },
        )

        finance_setting = getattr(chama, "finance_setting", None)
        loans_enabled = bool(getattr(finance_setting, "loans_enabled", True))
        if loans_enabled and not LoanProduct.objects.filter(chama=chama, is_default=True).exists():
            LoanProduct.objects.create(
                chama=chama,
                name="Default Loan Product",
                is_active=True,
                is_default=True,
                max_loan_amount=contribution_setting.contribution_amount * 10,
                contribution_multiple=2,
                interest_type="flat",
                interest_rate="12.00",
                min_duration_months=1,
                max_duration_months=12,
                grace_period_days=7,
                late_penalty_type="fixed",
                late_penalty_value=contribution_setting.late_fine_amount or 0,
                minimum_membership_months=1,
                minimum_contribution_months=1,
                require_treasurer_review=True,
                require_separate_disburser=True,
                created_by=actor,
                updated_by=actor,
            )


class Command(BaseCommand):
    help = "Bootstrap a verified chama admin + fully configured chama, and fill memberships to max."

    def add_arguments(self, parser):
        parser.add_argument("--full-name", type=str, required=True)
        parser.add_argument("--email", type=str, required=True)
        parser.add_argument("--phone", type=str, default="", help="Optional; generated if omitted.")
        parser.add_argument("--password", type=str, default="", help="Optional; generated if omitted.")
        parser.add_argument("--chama-name", type=str, default="Yangu Chama")
        parser.add_argument("--max-members", type=int, default=12)
        parser.add_argument("--county", type=str, default="Nairobi")
        parser.add_argument("--subcounty", type=str, default="Westlands")
        parser.add_argument("--chama-type", type=str, default="savings")

    def handle(self, *args, **options):
        full_name = str(options["full_name"]).strip()
        email = str(options["email"]).strip()
        phone = str(options.get("phone") or "").strip() or _generate_unique_phone()
        password = str(options.get("password") or "").strip() or _generate_password()
        chama_name = str(options.get("chama_name") or "Yangu Chama").strip()
        max_members = max(int(options.get("max_members") or 12), 1)

        # Resolve or create the admin user.
        candidates = User.objects.filter(email__iexact=email).order_by("-date_joined")
        if candidates.count() > 1:
            raise CommandError(f"Multiple users already exist with email {email}. Provide --phone.")
        user = candidates.first()
        created = False
        if not user:
            user = User.objects.create_user(
                phone=phone,
                password=password,
                full_name=full_name,
                email=email,
                is_active=True,
                is_staff=False,
                is_superuser=False,
            )
            created = True
        else:
            # Keep email stable; set/update password and phone when provided/generated.
            if phone and user.phone != phone:
                # Avoid changing phone if it would collide.
                if User.objects.filter(phone=phone).exclude(id=user.id).exists():
                    raise CommandError(f"Phone {phone} is already in use.")
                user.phone = phone
            user.full_name = full_name
            user.email = email
            user.is_active = True
            user.is_staff = False
            user.is_superuser = False
            user.set_password(password)
            user.save(
                update_fields=[
                    "phone",
                    "full_name",
                    "email",
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "password",
                    "updated_at" if hasattr(user, "updated_at") else "date_joined",
                ]
                if hasattr(user, "updated_at")
                else [
                    "phone",
                    "full_name",
                    "email",
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "password",
                ]
            )

        _ensure_verified_user(user=user, email=email)
        _ensure_kyc_approved(user=user)

        # Create or reuse the chama.
        chama = Chama.objects.filter(name=chama_name).first()
        if not chama:
            payload = {
                "name": chama_name,
                "description": "A fully configured chama for testing and demos.",
                "county": str(options.get("county") or "Nairobi").strip(),
                "subcounty": str(options.get("subcounty") or "Westlands").strip(),
                "chama_type": str(options.get("chama_type") or "savings").strip(),
                "privacy": "invite_only",
                "contribution_setup": {
                    "amount": Decimal("500.00"),
                    "frequency": "monthly",
                    "due_day": 5,
                    "grace_period_days": 3,
                    "late_fine_amount": Decimal("50.00"),
                },
                "finance_settings": {
                    "currency": "KES",
                    "payment_methods": ["mpesa"],
                    "loans_enabled": True,
                    "fines_enabled": True,
                    "approval_rule": "maker_checker",
                },
                "meeting_settings": {
                    "meeting_frequency": "monthly",
                    "quorum_percentage": 50,
                    "voting_enabled": True,
                },
                "membership_rules": {
                    "invite_only": True,
                    "approval_required": True,
                    "max_members": max_members,
                },
                "notification_defaults": {
                    "member_join_alerts": True,
                    "payment_received_alerts": True,
                    "meeting_reminders": True,
                    "loan_updates": True,
                },
            }
            chama = ChamaOnboardingService.create_chama_with_defaults(payload=payload, actor=user)
        else:
            # Ensure the user is a chama admin.
            now = timezone.now()
            Membership.objects.update_or_create(
                user=user,
                chama=chama,
                defaults={
                    "role": MembershipRole.CHAMA_ADMIN,
                    "status": MemberStatus.ACTIVE,
                    "is_active": True,
                    "is_approved": True,
                    "joined_at": now,
                    "approved_at": now,
                    "approved_by": user,
                    "created_by": user,
                    "updated_by": user,
                },
            )
            if chama.max_members != max_members:
                chama.max_members = max_members
                chama.updated_by = user
                chama.save(update_fields=["max_members", "updated_by", "updated_at"])

        _ensure_chama_fully_configured(chama=chama, actor=user)

        # Fill the chama to capacity with approved active members.
        now = timezone.now()
        active_approved = Membership.objects.filter(
            chama=chama,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        ).count()

        to_create = max(int(chama.max_members) - active_approved, 0)
        created_members: list[User] = []
        for _ in range(to_create):
            member_phone = _generate_unique_phone()
            member_password = _generate_password(14)
            member = User.objects.create_user(
                phone=member_phone,
                password=member_password,
                full_name=f"Yangu Member {member_phone[-4:]}",
                is_active=True,
                is_staff=False,
                is_superuser=False,
            )
            _ensure_verified_user(user=member, email=None)
            created_members.append(member)

        # Create memberships and assign core roles.
        for index, member in enumerate(created_members):
            role = MembershipRole.MEMBER
            if index == 0:
                role = MembershipRole.TREASURER
            elif index == 1:
                role = MembershipRole.SECRETARY
            Membership.objects.update_or_create(
                user=member,
                chama=chama,
                defaults={
                    "role": role,
                    "status": MemberStatus.ACTIVE,
                    "is_active": True,
                    "is_approved": True,
                    "joined_at": now,
                    "approved_at": now,
                    "approved_by": user,
                    "created_by": user,
                    "updated_by": user,
                },
            )

        # Ensure max_members matches active approved count (true "full" state).
        final_active = Membership.objects.filter(
            chama=chama,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        ).count()
        if chama.max_members != final_active:
            chama.max_members = final_active
            chama.updated_by = user
            chama.save(update_fields=["max_members", "updated_by", "updated_at"])

        self.stdout.write(self.style.SUCCESS("✓ Chama admin + chama bootstrapped"))
        self.stdout.write(f"admin_full_name={user.full_name}")
        self.stdout.write(f"admin_email={user.email}")
        self.stdout.write(f"admin_phone={user.phone}")
        self.stdout.write(f"admin_password={password}")
        self.stdout.write(f"phone_verified={user.phone_verified}")
        self.stdout.write(f"otp_verified={user.otp_verified}")
        self.stdout.write(f"email_verified={getattr(user, 'email_verified', False)}")
        self.stdout.write(f"tier_access={getattr(user, 'tier_access', '')}")
        self.stdout.write(f"kyc_status={getattr(user, 'kyc_status', '')}")
        self.stdout.write(f"chama_id={chama.id}")
        self.stdout.write(f"chama_name={chama.name}")
        self.stdout.write(f"join_code={chama.join_code}")
        self.stdout.write(f"setup_completed={chama.setup_completed}")
        self.stdout.write(f"active_approved_members={final_active}")
        self.stdout.write(f"max_members={chama.max_members}")
