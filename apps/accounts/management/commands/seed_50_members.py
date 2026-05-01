"""
Seed 50 member accounts with realistic chama activity.

Creates:
- users + credentials
- active memberships + preferences + KYC
- contribution types, contributions, goals
- loans, installments, repayments, penalties
- payment intents + STK/B2C records + withdrawal approvals
- ledger entries for contribution, loan and withdrawal flows
- meetings + attendance + agenda items
- notifications and issues
"""

from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import MemberKYC, MemberKYCStatus, UserPreference
from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus
from apps.finance.models import (
    Contribution,
    ContributionFrequency,
    ContributionGoal,
    ContributionGoalStatus,
    ContributionType,
    InstallmentSchedule,
    InstallmentStatus,
    LedgerDirection,
    LedgerEntry,
    LedgerEntryType,
    LedgerStatus,
    Loan,
    LoanEligibilityStatus,
    LoanInterestType,
    LoanPenaltyType,
    LoanProduct,
    LoanStatus,
    Penalty,
    PenaltyStatus,
    Repayment,
)
from apps.finance.services import FinanceService
from apps.issues.models import (
    Issue,
    IssueCategory,
    IssueComment,
    IssuePriority,
    IssueStatus,
)
from apps.meetings.models import (
    AgendaItem,
    AgendaItemStatus,
    Attendance,
    AttendanceStatus,
    Meeting,
)
from apps.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationInboxStatus,
    NotificationPriority,
    NotificationStatus,
    NotificationType,
)
from apps.payments.models import (
    MpesaB2CPayout,
    MpesaB2CStatus,
    MpesaSTKTransaction,
    PaymentDispute,
    PaymentDisputeCategory,
    PaymentDisputeStatus,
    PaymentIntent,
    PaymentIntentStatus,
    PaymentIntentType,
    PaymentPurpose,
    PaymentRefund,
    PaymentRefundStatus,
    WithdrawalApprovalLog,
    WithdrawalApprovalStep,
)

User = get_user_model()

FIRST_NAMES = [
    "Amina",
    "Brian",
    "Caroline",
    "David",
    "Esther",
    "Felix",
    "Grace",
    "Hassan",
    "Irene",
    "James",
    "Kevin",
    "Lilian",
    "Moses",
    "Naomi",
    "Oscar",
    "Purity",
    "Quincy",
    "Ruth",
    "Samuel",
    "Tabitha",
    "Umar",
    "Victor",
    "Winnie",
    "Yusuf",
    "Zawadi",
]

LAST_NAMES = [
    "Njuguna",
    "Wekesa",
    "Odhiambo",
    "Kamau",
    "Mutua",
    "Kiptoo",
    "Mwangi",
    "Akinyi",
    "Otieno",
    "Wanjiru",
    "Kariuki",
    "Njeri",
    "Kiprop",
    "Auma",
    "Muthoni",
]


class Command(BaseCommand):
    help = "Seed 50 members with full chama activity and credentials."

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=50,
            help="Number of members to generate (default: 50).",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=20260227,
            help="Random seed for deterministic data.",
        )
        parser.add_argument(
            "--chama-name",
            type=str,
            default="Digital Chama Alpha",
            help="Chama name to seed into.",
        )
        parser.add_argument(
            "--all-users",
            action="store_true",
            help=(
                "Seed all active users already in the selected chama(s) instead of only "
                "creating synthetic members."
            ),
        )
        parser.add_argument(
            "--all-chamas",
            action="store_true",
            help=(
                "Apply seed activity across all existing chamas. "
                "Recommended with --all-users."
            ),
        )

    @transaction.atomic
    def handle(self, *args, **options):
        count = max(1, int(options["count"]))
        rng = random.Random(int(options["seed"]))
        chama_name = str(options["chama_name"]).strip() or "Digital Chama Alpha"
        all_users_mode = bool(options.get("all_users"))
        all_chamas_mode = bool(options.get("all_chamas"))
        now = timezone.now()
        today = timezone.localdate()

        self.stdout.write(self.style.SUCCESS("Starting full member seed..."))
        chamas: list[Chama] = []
        if all_chamas_mode:
            chamas = list(Chama.objects.order_by("name"))
            if not chamas:
                chama, _ = Chama.objects.get_or_create(
                    name=chama_name,
                    defaults={
                        "description": "Seeded chama dataset for member dashboards.",
                        "join_code": "ALPHA2026",
                        "allow_public_join": True,
                        "require_approval": True,
                        "max_members": 300,
                    },
                )
                chamas = [chama]
        else:
            chama, _ = Chama.objects.get_or_create(
                name=chama_name,
                defaults={
                    "description": "Seeded chama dataset for member dashboards.",
                    "join_code": "ALPHA2026",
                    "allow_public_join": True,
                    "require_approval": True,
                    "max_members": 300,
                },
            )
            chamas = [chama]

        admin_user = self._ensure_user(
            phone="+254700000001",
            full_name="System Admin",
            email="admin@digitalchama.co.ke",
            password="Admin123!",
            is_staff=True,
            is_superuser=True,
        )
        treasurer_user = self._ensure_user(
            phone="+254700000003",
            full_name="Main Treasurer",
            email="treasurer@digitalchama.co.ke",
            password="Treasurer123!",
        )
        secretary_user = self._ensure_user(
            phone="+254700000002",
            full_name="Main Secretary",
            email="secretary@digitalchama.co.ke",
            password="Secretary123!",
        )

        credentials_lines = [
            "DIGITAL CHAMA SEEDED MEMBER CREDENTIALS",
            f"Generated At: {now.isoformat()}",
            f"Scope: {'all chamas' if all_chamas_mode else chama_name}",
            f"Mode: {'all users' if all_users_mode else f'generate {count} users'}",
            "",
            "chama,phone,password,full_name,email,notes",
        ]

        seeded_members = []
        for chama in chamas:
            self._ensure_membership(
                user=admin_user,
                chama=chama,
                role=MembershipRole.CHAMA_ADMIN,
                approver=admin_user,
                joined_at=now - timedelta(days=480),
            )
            self._ensure_membership(
                user=treasurer_user,
                chama=chama,
                role=MembershipRole.TREASURER,
                approver=admin_user,
                joined_at=now - timedelta(days=420),
            )
            self._ensure_membership(
                user=secretary_user,
                chama=chama,
                role=MembershipRole.SECRETARY,
                approver=admin_user,
                joined_at=now - timedelta(days=390),
            )

            contribution_types = self._ensure_contribution_types(chama, admin_user)
            default_loan_product = self._ensure_loan_product(chama, admin_user)
            meetings = self._ensure_meetings(chama, secretary_user, now)

            target_members = self._resolve_member_targets(
                chama=chama,
                count=count,
                all_users_mode=all_users_mode,
                now=now,
                admin_user=admin_user,
            )
            if not target_members:
                self.stdout.write(
                    self.style.WARNING(
                        f"No eligible active members found in {chama.name}; skipping."
                    )
                )
                continue

            self.stdout.write(
                f"Seeding {len(target_members)} member profiles in {chama.name}..."
            )
            for target in target_members:
                user = target["user"]
                membership = target["membership"]
                password = target["password"]
                member_index = target["member_index"]

                self._ensure_preferences_and_kyc(
                    user=user,
                    chama=chama,
                    reviewer=secretary_user,
                    id_number=f"{31_000_000 + member_index}",
                )

                self._seed_member_activity(
                    rng=rng,
                    user=user,
                    chama=chama,
                    member_index=member_index,
                    today=today,
                    now=now,
                    contribution_types=contribution_types,
                    loan_product=default_loan_product,
                    meetings=meetings,
                    admin_user=admin_user,
                    treasurer_user=treasurer_user,
                    secretary_user=secretary_user,
                )

                seeded_members.append((membership, password))
                credentials_lines.append(
                    ",".join(
                        [
                            chama.name,
                            user.phone,
                            password or "",
                            user.full_name,
                            user.email or "",
                            "" if password else "existing password unchanged",
                        ]
                    )
                )

        credentials_path = Path("/tmp/member_credentials_50.txt")
        credentials_path.write_text("\n".join(credentials_lines) + "\n", encoding="utf-8")

        self.stdout.write(self.style.SUCCESS("Seed complete."))
        self.stdout.write(f"Chamas processed: {len(chamas)}")
        self.stdout.write(f"Members seeded/updated: {len(seeded_members)}")
        self.stdout.write(f"Credentials file: {credentials_path}")
        self.stdout.write("")
        self.stdout.write("Sample credentials:")
        for membership, password in seeded_members[:5]:
            display_password = password or "<unchanged>"
            self.stdout.write(
                f"- {membership.user.phone} / {display_password} ({membership.user.full_name})"
            )

    def _resolve_member_targets(
        self,
        *,
        chama: Chama,
        count: int,
        all_users_mode: bool,
        now,
        admin_user: User,
    ) -> list[dict]:
        if all_users_mode:
            memberships = (
                Membership.objects.select_related("user")
                .filter(
                    chama=chama,
                    is_active=True,
                    is_approved=True,
                    status=MemberStatus.ACTIVE,
                    exited_at__isnull=True,
                    user__is_active=True,
                )
                .exclude(user__is_superuser=True)
                .order_by("user__phone")
            )
            targets = []
            for idx, membership in enumerate(memberships, start=1):
                targets.append(
                    {
                        "user": membership.user,
                        "membership": membership,
                        "password": None,
                        "member_index": self._stable_member_index(
                            membership.user,
                            fallback=idx,
                        ),
                    }
                )
            return targets

        targets = []
        for index in range(1, count + 1):
            first_name = FIRST_NAMES[(index - 1) % len(FIRST_NAMES)]
            last_name = LAST_NAMES[((index - 1) * 3) % len(LAST_NAMES)]
            full_name = f"{first_name} {last_name}"
            phone = f"+25471{5000000 + index:07d}"
            email = (
                f"{first_name.lower()}.{last_name.lower()}.{index:03d}"
                "@members.digitalchama.test"
            )
            password = f"Member{index:03d}!"

            user = self._ensure_user(
                phone=phone,
                full_name=full_name,
                email=email,
                password=password,
            )
            membership = self._ensure_membership(
                user=user,
                chama=chama,
                role=MembershipRole.MEMBER,
                approver=admin_user,
                joined_at=now - timedelta(days=30 + (index * 5)),
            )
            targets.append(
                {
                    "user": user,
                    "membership": membership,
                    "password": password,
                    "member_index": index,
                }
            )
        return targets

    @staticmethod
    def _stable_member_index(user: User, fallback: int) -> int:
        digits = "".join(ch for ch in str(user.phone or "") if ch.isdigit())
        if len(digits) >= 7:
            return int(digits[-7:])
        return fallback

    def _seed_member_activity(
        self,
        *,
        rng: random.Random,
        user: User,
        chama: Chama,
        member_index: int,
        today,
        now,
        contribution_types: list[ContributionType],
        loan_product: LoanProduct,
        meetings: list[Meeting],
        admin_user: User,
        treasurer_user: User,
        secretary_user: User,
    ) -> None:
        contributions = self._seed_contributions(
            rng=rng,
            user=user,
            chama=chama,
            contribution_types=contribution_types,
            recorder=treasurer_user,
            months=12,
            member_index=member_index,
            today=today,
        )
        self._seed_goal(
            user=user,
            chama=chama,
            member_index=member_index,
            total_contributions=sum(
                (c.amount for c in contributions),
                Decimal("0.00"),
            ),
            actor=user,
            today=today,
        )
        loan = self._seed_loan(
            rng=rng,
            user=user,
            chama=chama,
            member_index=member_index,
            loan_product=loan_product,
            approver=admin_user,
            disburser=treasurer_user,
            today=today,
        )
        self._seed_penalty(
            user=user,
            chama=chama,
            member_index=member_index,
            issuer=treasurer_user,
            resolver=admin_user,
            today=today,
        )
        self._seed_payment_intents(
            rng=rng,
            user=user,
            chama=chama,
            contributions=contributions,
            loan=loan,
            member_index=member_index,
            now=now,
            admin_user=admin_user,
            treasurer_user=treasurer_user,
        )
        self._seed_withdrawals(
            rng=rng,
            user=user,
            chama=chama,
            member_index=member_index,
            now=now,
            treasurer_user=treasurer_user,
            admin_user=admin_user,
        )
        self._seed_member_payment_support(
            user=user,
            chama=chama,
            member_index=member_index,
            admin_user=admin_user,
            now=now,
        )
        self._seed_notifications(
            user=user,
            chama=chama,
            member_index=member_index,
            actor=admin_user,
            now=now,
        )
        self._seed_attendance(
            rng=rng,
            user=user,
            meetings=meetings,
            actor=secretary_user,
        )
        self._seed_issue(
            user=user,
            chama=chama,
            member_index=member_index,
            actor=admin_user,
            loan=loan,
            now=now,
        )

    def _ensure_user(
        self,
        *,
        phone: str,
        full_name: str,
        email: str,
        password: str,
        is_staff: bool = False,
        is_superuser: bool = False,
    ) -> User:
        user, created = User.objects.get_or_create(
            phone=phone,
            defaults={
                "full_name": full_name,
                "email": email,
                "is_active": True,
                "is_staff": is_staff,
                "is_superuser": is_superuser,
                "phone_verified": True,
                "phone_verified_at": timezone.now(),
            },
        )

        changed = False
        if user.full_name != full_name:
            user.full_name = full_name
            changed = True
        if user.email != email:
            user.email = email
            changed = True
        if not user.phone_verified:
            user.phone_verified = True
            user.phone_verified_at = timezone.now()
            changed = True
        if not user.is_active:
            user.is_active = True
            changed = True
        if user.is_staff != is_staff:
            user.is_staff = is_staff
            changed = True
        if user.is_superuser != is_superuser:
            user.is_superuser = is_superuser
            changed = True

        if not user.check_password(password):
            user.set_password(password)
            changed = True

        if changed or created:
            user.save()

        return user

    def _ensure_membership(
        self,
        *,
        user: User,
        chama: Chama,
        role: str,
        approver: User,
        joined_at,
    ) -> Membership:
        membership, _ = Membership.objects.get_or_create(
            user=user,
            chama=chama,
            defaults={
                "role": role,
                "status": MemberStatus.ACTIVE,
                "is_active": True,
                "is_approved": True,
                "joined_at": joined_at,
                "approved_at": timezone.now(),
                "approved_by": approver,
            },
        )

        membership.role = role
        membership.status = MemberStatus.ACTIVE
        membership.is_active = True
        membership.is_approved = True
        if not membership.joined_at:
            membership.joined_at = joined_at
        membership.approved_by = approver
        membership.approved_at = membership.approved_at or timezone.now()
        membership.exited_at = None
        membership.suspension_reason = ""
        membership.exit_reason = ""
        membership.save()
        return membership

    def _ensure_preferences_and_kyc(
        self,
        *,
        user: User,
        chama: Chama,
        reviewer: User,
        id_number: str,
    ) -> None:
        UserPreference.objects.update_or_create(
            user=user,
            defaults={
                "active_chama": chama,
                "low_data_mode": False,
                "ussd_enabled": True,
                "prefer_sms": True,
                "prefer_email": True,
                "prefer_in_app": True,
            },
        )

        MemberKYC.objects.update_or_create(
            user=user,
            chama=chama,
            defaults={
                "id_number": id_number,
                "status": MemberKYCStatus.APPROVED,
                "review_note": "Auto-approved seeded KYC record.",
                "reviewed_by": reviewer,
                "reviewed_at": timezone.now(),
            },
        )

    def _ensure_contribution_types(self, chama: Chama, actor: User) -> list[ContributionType]:
        defaults = [
            ("Monthly Contribution", ContributionFrequency.MONTHLY, Decimal("5000.00")),
            ("Welfare Contribution", ContributionFrequency.MONTHLY, Decimal("1500.00")),
            ("Development Fund", ContributionFrequency.QUARTERLY, Decimal("3000.00")),
        ]
        rows = []
        for name, frequency, amount in defaults:
            contribution_type, _ = ContributionType.objects.get_or_create(
                chama=chama,
                name=name,
                defaults={
                    "frequency": frequency,
                    "default_amount": amount,
                    "is_active": True,
                    "created_by": actor,
                    "updated_by": actor,
                },
            )
            if not contribution_type.is_active:
                contribution_type.is_active = True
                contribution_type.updated_by = actor
                contribution_type.save(update_fields=["is_active", "updated_by", "updated_at"])
            rows.append(contribution_type)
        return rows

    def _ensure_loan_product(self, chama: Chama, actor: User) -> LoanProduct:
        product, _ = LoanProduct.objects.get_or_create(
            chama=chama,
            name="Standard Member Loan",
            defaults={
                "is_active": True,
                "is_default": True,
                "max_loan_amount": Decimal("300000.00"),
                "contribution_multiple": Decimal("3.00"),
                "interest_type": LoanInterestType.FLAT,
                "interest_rate": Decimal("12.00"),
                "min_duration_months": 3,
                "max_duration_months": 18,
                "grace_period_days": 7,
                "late_penalty_type": LoanPenaltyType.FIXED,
                "late_penalty_value": Decimal("500.00"),
                "early_repayment_discount_percent": Decimal("2.00"),
                "minimum_membership_months": 2,
                "minimum_contribution_months": 2,
                "block_if_unpaid_penalties": False,
                "block_if_overdue_loans": True,
                "require_treasurer_review": False,
                "require_separate_disburser": False,
                "created_by": actor,
                "updated_by": actor,
            },
        )

        if not product.is_default:
            LoanProduct.objects.filter(chama=chama, is_default=True).exclude(id=product.id).update(is_default=False)
            product.is_default = True
            product.is_active = True
            product.updated_by = actor
            product.save(update_fields=["is_default", "is_active", "updated_by", "updated_at"])
        return product

    def _ensure_meetings(self, chama: Chama, actor: User, now):
        meetings = []
        for offset in range(-4, 4):
            meeting_date = now + timedelta(days=offset * 14)
            meeting, _ = Meeting.objects.get_or_create(
                chama=chama,
                title=f"General Meeting {meeting_date:%b %Y}",
                defaults={
                    "date": meeting_date,
                    "agenda": "Monthly updates, loans, contributions and resolutions.",
                    "quorum_percentage": 50,
                    "created_by": actor,
                    "updated_by": actor,
                },
            )
            AgendaItem.objects.get_or_create(
                meeting=meeting,
                title="Review contributions and loan performance",
                proposed_by=actor,
                defaults={
                    "description": "Standing agenda item for operational review.",
                    "status": AgendaItemStatus.APPROVED,
                    "approved_by": actor,
                    "approved_at": timezone.now(),
                    "created_by": actor,
                    "updated_by": actor,
                },
            )
            meetings.append(meeting)
        return meetings

    def _seed_contributions(
        self,
        *,
        rng: random.Random,
        user: User,
        chama: Chama,
        contribution_types: list[ContributionType],
        recorder: User,
        months: int,
        member_index: int,
        today,
    ) -> list[Contribution]:
        rows = []
        monthly = contribution_types[0]
        welfare = contribution_types[1]
        for month_offset in range(months):
            date_paid = today - timedelta(days=(month_offset * 30) + rng.randint(0, 6))
            ctype = welfare if month_offset % 4 == 0 else monthly
            amount = (
                Decimal("3500.00")
                + Decimal(member_index % 6) * Decimal("500.00")
                + Decimal(rng.randint(0, 5)) * Decimal("250.00")
            )
            receipt_code = (
                f"CONT-{chama.id.hex[:4]}-{member_index:07d}-{month_offset:02d}"
            )
            contribution, _ = Contribution.objects.get_or_create(
                receipt_code=receipt_code,
                defaults={
                    "chama": chama,
                    "member": user,
                    "contribution_type": ctype,
                    "amount": amount,
                    "date_paid": date_paid,
                    "method": "mpesa",
                    "recorded_by": recorder,
                    "created_by": recorder,
                    "updated_by": recorder,
                },
            )
            rows.append(contribution)
        return rows

    def _seed_goal(
        self,
        *,
        user: User,
        chama: Chama,
        member_index: int,
        total_contributions: Decimal,
        actor: User,
        today,
    ) -> None:
        target = max(Decimal("30000.00"), (total_contributions * Decimal("1.20")).quantize(Decimal("0.01")))
        current = min(target, (total_contributions * Decimal("0.55")).quantize(Decimal("0.01")))
        completed = member_index % 9 == 0
        due_offset_days = 120 + (member_index % 180)
        ContributionGoal.objects.update_or_create(
            chama=chama,
            member=user,
            title="Emergency Savings Goal",
            defaults={
                "target_amount": target,
                "current_amount": target if completed else current,
                "due_date": today + timedelta(days=due_offset_days),
                "status": ContributionGoalStatus.COMPLETED if completed else ContributionGoalStatus.ACTIVE,
                "is_active": not completed,
                "created_by": actor,
                "updated_by": actor,
            },
        )

    def _seed_loan(
        self,
        *,
        rng: random.Random,
        user: User,
        chama: Chama,
        member_index: int,
        loan_product: LoanProduct,
        approver: User,
        disburser: User,
        today,
    ) -> Loan | None:
        status_cycle = [LoanStatus.ACTIVE, LoanStatus.DISBURSED, LoanStatus.PAID, LoanStatus.REQUESTED]
        status = status_cycle[member_index % len(status_cycle)]
        principal = Decimal(40000 + ((member_index % 10) * 10000))
        duration = 6 + (member_index % 6)

        loan_ref = f"SEED-LOAN-{chama.id.hex[:4]}-{member_index:07d}"
        loan, _ = Loan.objects.get_or_create(
            chama=chama,
            member=user,
            disbursement_reference=loan_ref,
            defaults={
                "loan_product": loan_product,
                "principal": principal,
                "interest_type": LoanInterestType.FLAT,
                "interest_rate": Decimal("12.00"),
                "duration_months": duration,
                "grace_period_days": 7,
                "late_penalty_type": LoanPenaltyType.FIXED,
                "late_penalty_value": Decimal("500.00"),
                "early_repayment_discount_percent": Decimal("2.00"),
                "eligibility_status": LoanEligibilityStatus.ELIGIBLE,
                "eligibility_reason": "Seeded eligible member profile.",
                "recommended_max_amount": principal * Decimal("1.50"),
                "status": status,
                "approved_at": timezone.now() - timedelta(days=75),
                "approved_by": approver,
                "disbursed_at": timezone.now() - timedelta(days=70),
                "disbursed_by": disburser,
                "created_by": approver,
                "updated_by": approver,
            },
        )

        loan.loan_product = loan_product
        loan.principal = principal
        loan.interest_type = LoanInterestType.FLAT
        loan.interest_rate = Decimal("12.00")
        loan.duration_months = duration
        loan.grace_period_days = 7
        loan.late_penalty_type = LoanPenaltyType.FIXED
        loan.late_penalty_value = Decimal("500.00")
        loan.early_repayment_discount_percent = Decimal("2.00")
        loan.eligibility_status = LoanEligibilityStatus.ELIGIBLE
        loan.eligibility_reason = "Seeded eligible member profile."
        loan.recommended_max_amount = principal * Decimal("1.50")
        loan.status = status
        if status in {LoanStatus.ACTIVE, LoanStatus.DISBURSED, LoanStatus.PAID}:
            loan.approved_by = approver
            loan.approved_at = loan.approved_at or timezone.now() - timedelta(days=75)
            loan.disbursed_by = disburser
            loan.disbursed_at = loan.disbursed_at or timezone.now() - timedelta(days=70)
        loan.updated_by = approver
        loan.save()

        Repayment.objects.filter(loan=loan).delete()
        FinanceService.generate_schedule(loan)

        installments = list(InstallmentSchedule.objects.filter(loan=loan).order_by("due_date", "created_at"))
        if not installments:
            return loan

        if status == LoanStatus.PAID:
            base_date = today - timedelta(days=30 * loan.duration_months)
            paid_installments = len(installments)
        elif status == LoanStatus.REQUESTED:
            base_date = today + timedelta(days=10)
            paid_installments = 0
        else:
            base_date = today - timedelta(days=90)
            paid_installments = max(1, len(installments) // 2)

        for idx, installment in enumerate(installments):
            installment.due_date = base_date + timedelta(days=(idx + 1) * 30)
            if idx < paid_installments:
                installment.status = InstallmentStatus.PAID
            elif installment.due_date < today and idx == paid_installments:
                installment.status = InstallmentStatus.OVERDUE
            else:
                installment.status = InstallmentStatus.DUE
            installment.updated_by = disburser
            installment.save(update_fields=["due_date", "status", "updated_by", "updated_at"])

            if idx < paid_installments:
                receipt_code = (
                    f"RPY-{chama.id.hex[:4]}-{member_index:07d}-{idx:02d}"
                )
                Repayment.objects.get_or_create(
                    receipt_code=receipt_code,
                    defaults={
                        "loan": loan,
                        "amount": installment.expected_amount,
                        "date_paid": installment.due_date,
                        "method": "mpesa",
                        "recorded_by": disburser,
                        "created_by": disburser,
                        "updated_by": disburser,
                    },
                )

        if status == LoanStatus.PAID:
            loan.status = LoanStatus.PAID
        elif status in {LoanStatus.DISBURSED, LoanStatus.ACTIVE}:
            loan.status = LoanStatus.ACTIVE
        loan.updated_by = disburser
        loan.save(update_fields=["status", "updated_by", "updated_at"])
        return loan

    def _seed_penalty(
        self,
        *,
        user: User,
        chama: Chama,
        member_index: int,
        issuer: User,
        resolver: User,
        today,
    ) -> None:
        if member_index % 7 != 0:
            return

        paid = member_index % 14 == 0
        penalty, _ = Penalty.objects.update_or_create(
            chama=chama,
            member=user,
            reason="Late contribution for scheduled cycle.",
            defaults={
                "amount": Decimal("600.00"),
                "due_date": today + timedelta(days=14),
                "status": PenaltyStatus.PAID if paid else PenaltyStatus.UNPAID,
                "issued_by": issuer,
                "resolved_by": resolver if paid else None,
                "resolved_at": timezone.now() - timedelta(days=1) if paid else None,
                "created_by": issuer,
                "updated_by": resolver if paid else issuer,
            },
        )
        if paid and penalty.status != PenaltyStatus.PAID:
            penalty.status = PenaltyStatus.PAID
            penalty.resolved_by = resolver
            penalty.resolved_at = timezone.now() - timedelta(days=1)
            penalty.updated_by = resolver
            penalty.save(update_fields=["status", "resolved_by", "resolved_at", "updated_by", "updated_at"])

    def _seed_payment_intents(
        self,
        *,
        rng: random.Random,
        user: User,
        chama: Chama,
        contributions: list[Contribution],
        loan: Loan | None,
        member_index: int,
        now,
        admin_user: User,
        treasurer_user: User,
    ) -> None:
        for idx in range(6):
            amount = contributions[idx % len(contributions)].amount if contributions else Decimal("5000.00")
            status = PaymentIntentStatus.SUCCESS if idx < 4 else PaymentIntentStatus.PENDING
            contribution_ref = contributions[idx % len(contributions)] if contributions else None
            created_at = now - timedelta(days=(idx * 7) + rng.randint(0, 2))
            intent = self._upsert_intent(
                chama=chama,
                user=user,
                member_index=member_index,
                seq=idx,
                intent_type=PaymentIntentType.DEPOSIT,
                purpose=PaymentPurpose.CONTRIBUTION,
                reference_type="CONTRIBUTION",
                reference_id=contribution_ref.id if contribution_ref else None,
                amount=amount,
                status=status,
                metadata={"source": "seed", "channel": "stk"},
                created_at=created_at,
            )
            if status == PaymentIntentStatus.SUCCESS:
                self._upsert_stk_transaction(intent=intent, member_index=member_index, seq=idx)
                self._upsert_ledger_entry(
                    chama=chama,
                    member=user,
                    idempotency_key=f"seed:ledger:{member_index:07d}:{idx:03d}:contribution",
                    entry_type=LedgerEntryType.CONTRIBUTION,
                    direction=LedgerDirection.CREDIT,
                    amount=amount,
                    narration="Seeded contribution payment posted.",
                    reference_type="Contribution",
                    reference_id=contribution_ref.id if contribution_ref else None,
                    related_payment=intent,
                    actor=user,
                    created_at=created_at,
                )

        if not loan:
            return

        for idx in range(2):
            created_at = now - timedelta(days=(idx * 12) + 3)
            intent = self._upsert_intent(
                chama=chama,
                user=user,
                member_index=member_index,
                seq=100 + idx,
                intent_type=PaymentIntentType.LOAN_REPAYMENT,
                purpose=PaymentPurpose.LOAN_REPAYMENT,
                reference_type="LOAN",
                reference_id=loan.id,
                amount=Decimal("3500.00") + Decimal(idx * 1000),
                status=PaymentIntentStatus.SUCCESS,
                metadata={"source": "seed", "kind": "loan_repayment"},
                created_at=created_at,
            )
            self._upsert_stk_transaction(intent=intent, member_index=member_index, seq=100 + idx)
            self._upsert_ledger_entry(
                chama=chama,
                member=user,
                idempotency_key=f"seed:ledger:{member_index:07d}:{100 + idx:03d}:loan_repay",
                entry_type=LedgerEntryType.LOAN_REPAYMENT,
                direction=LedgerDirection.CREDIT,
                amount=intent.amount,
                narration="Seeded loan repayment posted.",
                reference_type="Loan",
                reference_id=loan.id,
                related_payment=intent,
                related_loan=loan,
                actor=user,
                created_at=created_at,
            )

        if loan.status in {LoanStatus.ACTIVE, LoanStatus.DISBURSED, LoanStatus.PAID}:
            disbursed_at = loan.disbursed_at or (now - timedelta(days=70))
            disbursement_intent = self._upsert_intent(
                chama=chama,
                user=user,
                member_index=member_index,
                seq=190,
                intent_type=PaymentIntentType.LOAN_DISBURSEMENT,
                purpose=PaymentPurpose.OTHER,
                reference_type="LOAN",
                reference_id=loan.id,
                amount=loan.principal,
                status=PaymentIntentStatus.SUCCESS,
                metadata={"source": "seed", "kind": "loan_disbursement"},
                created_at=disbursed_at,
            )
            self._upsert_withdrawal_approval_logs(
                intent=disbursement_intent,
                requester=user,
                treasurer=treasurer_user,
                admin=admin_user,
                include_treasurer=True,
                include_admin=True,
                note="Auto-seeded disbursement approvals.",
            )
            payout = self._upsert_b2c_payout(
                intent=disbursement_intent,
                member_index=member_index,
                seq=190,
                status=MpesaB2CStatus.SUCCESS,
                processed_at=disbursed_at,
                actor=treasurer_user,
            )
            self._upsert_ledger_entry(
                chama=chama,
                member=user,
                idempotency_key=f"seed:ledger:{member_index:07d}:190:loan_disbursement",
                entry_type=LedgerEntryType.LOAN_DISBURSEMENT,
                direction=LedgerDirection.DEBIT,
                amount=loan.principal,
                narration="Seeded loan disbursement posted.",
                reference_type="Loan",
                reference_id=loan.id,
                related_payment=disbursement_intent,
                related_payout=payout,
                related_loan=loan,
                actor=treasurer_user,
                created_at=disbursed_at,
            )

    def _upsert_intent(
        self,
        *,
        chama: Chama,
        user: User,
        member_index: int,
        seq: int,
        intent_type: str,
        purpose: str,
        reference_type: str,
        reference_id,
        amount: Decimal,
        status: str,
        metadata: dict,
        created_at,
    ) -> PaymentIntent:
        idempotency_key = f"seed:{member_index:07d}:{seq:03d}:{intent_type}"
        payload_metadata = {"member_id": str(user.id), **(metadata or {})}
        intent, _ = PaymentIntent.objects.get_or_create(
            chama=chama,
            idempotency_key=idempotency_key,
            defaults={
                "intent_type": intent_type,
                "purpose": purpose,
                "reference_type": reference_type,
                "reference_id": reference_id,
                "amount": amount,
                "phone": user.phone,
                "status": status,
                "metadata": payload_metadata,
                "created_by": user,
                "updated_by": user,
            },
        )

        intent.intent_type = intent_type
        intent.purpose = purpose
        intent.reference_type = reference_type
        intent.reference_id = reference_id
        intent.amount = amount
        intent.phone = user.phone
        intent.status = status
        intent.metadata = payload_metadata
        intent.created_by = user
        intent.updated_by = user
        intent.save()
        PaymentIntent.objects.filter(id=intent.id).update(created_at=created_at, updated_at=created_at)
        return intent

    def _upsert_stk_transaction(self, *, intent: PaymentIntent, member_index: int, seq: int) -> None:
        checkout_request_id = f"ws_{intent.chama.id.hex[:4]}_{member_index:07d}_{seq:03d}"
        stk, _ = MpesaSTKTransaction.objects.get_or_create(
            checkout_request_id=checkout_request_id,
            defaults={
                "chama": intent.chama,
                "intent": intent,
                "phone": intent.phone,
                "amount": intent.amount,
                "merchant_request_id": f"MRQ-{intent.chama.id.hex[:4]}-{member_index:07d}-{seq:03d}",
                "result_code": 0,
                "result_desc": "The service request is processed successfully.",
                "mpesa_receipt_number": f"R{intent.chama.id.hex[:3]}{member_index:07d}{seq:03d}",
                "transaction_date": timezone.now() - timedelta(days=2),
                "status": PaymentIntentStatus.SUCCESS,
                "processed_at": timezone.now() - timedelta(days=2),
                "created_by": intent.created_by,
                "updated_by": intent.updated_by,
            },
        )
        changed = False
        if stk.intent_id != intent.id:
            stk.intent = intent
            changed = True
        if stk.chama_id != intent.chama_id:
            stk.chama = intent.chama
            changed = True
        if stk.amount != intent.amount:
            stk.amount = intent.amount
            changed = True
        if stk.phone != intent.phone:
            stk.phone = intent.phone
            changed = True
        if stk.status != PaymentIntentStatus.SUCCESS:
            stk.status = PaymentIntentStatus.SUCCESS
            changed = True
        if changed:
            stk.updated_by = intent.updated_by
            stk.save()

    def _seed_withdrawals(
        self,
        *,
        rng: random.Random,
        user: User,
        chama: Chama,
        member_index: int,
        now,
        treasurer_user: User,
        admin_user: User,
    ) -> None:
        success_amount = (
            Decimal("1200.00")
            + Decimal(member_index % 5) * Decimal("600.00")
            + Decimal(rng.randint(0, 4)) * Decimal("150.00")
        )
        success_created_at = now - timedelta(days=5 + rng.randint(0, 6))
        success_intent = self._upsert_intent(
            chama=chama,
            user=user,
            member_index=member_index,
            seq=300,
            intent_type=PaymentIntentType.WITHDRAWAL,
            purpose=PaymentPurpose.OTHER,
            reference_type="WALLET",
            reference_id=None,
            amount=success_amount,
            status=PaymentIntentStatus.SUCCESS,
            metadata={"source": "seed", "kind": "withdrawal_success"},
            created_at=success_created_at,
        )
        self._upsert_withdrawal_approval_logs(
            intent=success_intent,
            requester=user,
            treasurer=treasurer_user,
            admin=admin_user,
            include_treasurer=True,
            include_admin=True,
            note="Auto-seeded member withdrawal approvals.",
        )
        payout = self._upsert_b2c_payout(
            intent=success_intent,
            member_index=member_index,
            seq=300,
            status=MpesaB2CStatus.SUCCESS,
            processed_at=success_created_at + timedelta(minutes=9),
            actor=admin_user,
        )
        self._upsert_ledger_entry(
            chama=chama,
            member=user,
            idempotency_key=f"seed:ledger:{member_index:07d}:300:withdrawal",
            entry_type=LedgerEntryType.WITHDRAWAL,
            direction=LedgerDirection.DEBIT,
            amount=success_amount,
            narration="Seeded member withdrawal payout posted.",
            reference_type="PaymentIntent",
            reference_id=success_intent.id,
            related_payment=success_intent,
            related_payout=payout,
            actor=user,
            created_at=success_created_at,
        )

        pending_amount = (
            Decimal("900.00")
            + Decimal(member_index % 4) * Decimal("500.00")
            + Decimal(rng.randint(0, 3)) * Decimal("100.00")
        )
        pending_intent = self._upsert_intent(
            chama=chama,
            user=user,
            member_index=member_index,
            seq=301,
            intent_type=PaymentIntentType.WITHDRAWAL,
            purpose=PaymentPurpose.OTHER,
            reference_type="WALLET",
            reference_id=None,
            amount=pending_amount,
            status=PaymentIntentStatus.PENDING,
            metadata={"source": "seed", "kind": "withdrawal_pending"},
            created_at=now - timedelta(days=1),
        )
        self._upsert_withdrawal_approval_logs(
            intent=pending_intent,
            requester=user,
            treasurer=treasurer_user,
            admin=admin_user,
            include_treasurer=False,
            include_admin=False,
            note="Awaiting approvals.",
        )

    def _seed_member_payment_support(
        self,
        *,
        user: User,
        chama: Chama,
        member_index: int,
        admin_user: User,
        now,
    ) -> None:
        latest_success_deposit = (
            PaymentIntent.objects.filter(
                chama=chama,
                created_by=user,
                intent_type=PaymentIntentType.DEPOSIT,
                status=PaymentIntentStatus.SUCCESS,
            )
            .order_by("-created_at")
            .first()
        )
        if not latest_success_deposit:
            return

        refund_amount = min(latest_success_deposit.amount, Decimal("1500.00"))
        PaymentRefund.objects.update_or_create(
            chama=chama,
            idempotency_key=f"seed:refund:{chama.id.hex[:4]}:{member_index:07d}",
            defaults={
                "payment_intent": latest_success_deposit,
                "amount": refund_amount,
                "reason": "Member requested refund on seeded payment.",
                "status": PaymentRefundStatus.REQUESTED,
                "requested_by": user,
                "approved_by": None,
                "processed_by": None,
                "processed_at": None,
                "notes": "Auto-seeded refund request.",
                "created_by": user,
                "updated_by": user,
            },
        )

        dispute_status = (
            PaymentDisputeStatus.RESOLVED
            if member_index % 4 == 0
            else PaymentDisputeStatus.OPEN
        )
        PaymentDispute.objects.update_or_create(
            chama=chama,
            payment_intent=latest_success_deposit,
            opened_by=user,
            reference=f"seed-dispute-{chama.id.hex[:4]}-{member_index:07d}",
            defaults={
                "category": PaymentDisputeCategory.OTHER,
                "reason": "Member-raised seeded payment dispute.",
                "status": dispute_status,
                "resolution_notes": (
                    "Resolved by admin during seed run."
                    if dispute_status == PaymentDisputeStatus.RESOLVED
                    else ""
                ),
                "resolved_by": (
                    admin_user if dispute_status == PaymentDisputeStatus.RESOLVED else None
                ),
                "resolved_at": (
                    now - timedelta(days=1)
                    if dispute_status == PaymentDisputeStatus.RESOLVED
                    else None
                ),
                "created_by": user,
                "updated_by": admin_user if dispute_status == PaymentDisputeStatus.RESOLVED else user,
            },
        )

    def _upsert_withdrawal_approval_logs(
        self,
        *,
        intent: PaymentIntent,
        requester: User,
        treasurer: User,
        admin: User,
        include_treasurer: bool,
        include_admin: bool,
        note: str,
    ) -> None:
        steps = [
            (WithdrawalApprovalStep.REQUESTED, requester, note or "Request created."),
        ]
        if include_treasurer:
            steps.append(
                (
                    WithdrawalApprovalStep.TREASURER_APPROVED,
                    treasurer,
                    "Approved by treasurer.",
                )
            )
        if include_admin:
            steps.append(
                (
                    WithdrawalApprovalStep.ADMIN_APPROVED,
                    admin,
                    "Approved by admin.",
                )
            )

        for step, actor, notes in steps:
            log, _ = WithdrawalApprovalLog.objects.get_or_create(
                chama=intent.chama,
                payment_intent=intent,
                step=step,
                defaults={
                    "actor": actor,
                    "notes": notes,
                    "created_by": actor,
                    "updated_by": actor,
                },
            )
            if log.actor_id != actor.id or log.notes != notes:
                log.actor = actor
                log.notes = notes
                log.updated_by = actor
                log.save(update_fields=["actor", "notes", "updated_by", "updated_at"])

    def _upsert_b2c_payout(
        self,
        *,
        intent: PaymentIntent,
        member_index: int,
        seq: int,
        status: str,
        processed_at,
        actor: User,
    ) -> MpesaB2CPayout:
        originator_id = f"OC_{intent.chama.id.hex[:4]}_{member_index:07d}_{seq:03d}"
        payout, _ = MpesaB2CPayout.objects.get_or_create(
            originator_conversation_id=originator_id,
            defaults={
                "chama": intent.chama,
                "intent": intent,
                "phone": intent.phone,
                "amount": intent.amount,
                "command_id": "BusinessPayment",
                "remarks": "Seeded payout",
                "occasion": str(intent.reference_id or "")[:120],
                "conversation_id": f"AG_{intent.chama.id.hex[:4]}_{member_index:07d}_{seq:03d}",
                "response_code": "0",
                "response_description": "Accepted for processing",
                "result_code": "0",
                "result_desc": "The service request is processed successfully.",
                "transaction_id": f"TX{intent.chama.id.hex[:3]}{member_index:07d}{seq:03d}",
                "receipt_number": f"B2C{intent.chama.id.hex[:3]}{member_index:07d}{seq:03d}",
                "status": status,
                "processed_at": processed_at,
                "created_by": actor,
                "updated_by": actor,
            },
        )
        payout.chama = intent.chama
        payout.intent = intent
        payout.phone = intent.phone
        payout.amount = intent.amount
        payout.status = status
        payout.processed_at = processed_at
        payout.result_code = "0" if status == MpesaB2CStatus.SUCCESS else payout.result_code
        payout.result_desc = (
            "The service request is processed successfully."
            if status == MpesaB2CStatus.SUCCESS
            else payout.result_desc
        )
        payout.updated_by = actor
        payout.save()
        return payout

    def _upsert_ledger_entry(
        self,
        *,
        chama: Chama,
        member: User,
        idempotency_key: str,
        entry_type: str,
        direction: str,
        amount: Decimal,
        narration: str,
        reference_type: str,
        reference_id,
        actor: User,
        created_at,
        related_payment: PaymentIntent | None = None,
        related_payout: MpesaB2CPayout | None = None,
        related_loan: Loan | None = None,
    ) -> LedgerEntry:
        meta = {
            "reference_type": reference_type,
            "reference_id": str(reference_id) if reference_id else "",
            "member_id": str(member.id),
            "seed": True,
        }
        entry, _ = LedgerEntry.objects.get_or_create(
            chama=chama,
            idempotency_key=idempotency_key,
            defaults={
                "entry_type": entry_type,
                "direction": direction,
                "amount": amount,
                "status": LedgerStatus.SUCCESS,
                "provider": "mpesa",
                "provider_reference": str(reference_id or "")[:100],
                "related_payment": related_payment,
                "related_payout": related_payout,
                "related_loan": related_loan,
                "meta": meta,
                "narration": narration,
                "created_by": actor,
                "updated_by": actor,
            },
        )
        entry.entry_type = entry_type
        entry.direction = direction
        entry.amount = amount
        entry.status = LedgerStatus.SUCCESS
        entry.provider = "mpesa"
        entry.provider_reference = str(reference_id or "")[:100]
        entry.related_payment = related_payment
        entry.related_payout = related_payout
        entry.related_loan = related_loan
        entry.meta = meta
        entry.narration = narration
        entry.updated_by = actor
        entry.save()
        LedgerEntry.objects.filter(id=entry.id).update(
            created_at=created_at,
            updated_at=created_at,
        )
        return entry

    def _seed_notifications(
        self,
        *,
        user: User,
        chama: Chama,
        member_index: int,
        actor: User,
        now,
    ) -> None:
        notification_specs = [
            (
                NotificationType.CONTRIBUTION_REMINDER,
                NotificationCategory.PAYMENTS,
                "Contribution Reminder",
                "Your monthly contribution is due soon.",
                NotificationInboxStatus.UNREAD,
            ),
            (
                NotificationType.LOAN_UPDATE,
                NotificationCategory.LOANS,
                "Loan Status Update",
                "Your loan account has been updated successfully.",
                NotificationInboxStatus.READ,
            ),
            (
                NotificationType.MEETING_NOTIFICATION,
                NotificationCategory.MEETINGS,
                "Upcoming Meeting",
                "A chama meeting is scheduled in the next few days.",
                NotificationInboxStatus.UNREAD,
            ),
        ]

        for idx, (n_type, category, subject, message, inbox_status) in enumerate(notification_specs):
            idempotency_key = (
                f"seed:notify:{chama.id.hex[:4]}:{member_index:07d}:{idx}"
            )
            read_at = now - timedelta(days=2) if inbox_status == NotificationInboxStatus.READ else None
            notification, _ = Notification.objects.get_or_create(
                idempotency_key=idempotency_key,
                defaults={
                    "chama": chama,
                    "recipient": user,
                    "type": n_type,
                    "category": category,
                    "priority": NotificationPriority.NORMAL,
                    "status": NotificationStatus.SENT,
                    "inbox_status": inbox_status,
                    "subject": subject,
                    "message": message,
                    "send_email": False,
                    "send_sms": False,
                    "send_push": False,
                    "email": user.email or "",
                    "phone": user.phone,
                    "sent_at": now - timedelta(days=idx + 1),
                    "read_at": read_at,
                    "created_by": actor,
                    "updated_by": actor,
                },
            )
            if notification.inbox_status != inbox_status:
                notification.inbox_status = inbox_status
                notification.read_at = read_at
                notification.updated_by = actor
                notification.save(update_fields=["inbox_status", "read_at", "updated_by", "updated_at"])

    def _seed_attendance(
        self,
        *,
        rng: random.Random,
        user: User,
        meetings: list[Meeting],
        actor: User,
    ) -> None:
        statuses = [
            AttendanceStatus.PRESENT,
            AttendanceStatus.PRESENT,
            AttendanceStatus.PRESENT,
            AttendanceStatus.LATE,
            AttendanceStatus.ABSENT,
        ]
        for meeting in meetings:
            status = rng.choice(statuses)
            attendance, created = Attendance.objects.get_or_create(
                meeting=meeting,
                member=user,
                defaults={
                    "status": status,
                    "notes": "Auto-seeded attendance record.",
                    "created_by": actor,
                    "updated_by": actor,
                },
            )
            if not created and attendance.status != status:
                attendance.status = status
                attendance.updated_by = actor
                attendance.save(update_fields=["status", "updated_by", "updated_at"])

    def _seed_issue(
        self,
        *,
        user: User,
        chama: Chama,
        member_index: int,
        actor: User,
        loan: Loan | None,
        now,
    ) -> None:
        is_resolved = member_index % 3 == 0
        categories = [
            IssueCategory.FINANCE,
            IssueCategory.LOAN,
            IssueCategory.TECHNICAL,
            IssueCategory.OTHER,
        ]
        category = categories[member_index % len(categories)]
        issue, _ = Issue.objects.get_or_create(
            chama=chama,
            title=f"Member support request #{chama.id.hex[:4]}-{member_index:07d}",
            created_by=user,
            defaults={
                "description": "Seeded issue for dashboard activity and support workflows.",
                "category": category,
                "priority": IssuePriority.MEDIUM,
                "status": IssueStatus.RESOLVED if is_resolved else IssueStatus.OPEN,
                "loan": loan,
                "assigned_to": actor,
                "resolved_at": now - timedelta(days=2) if is_resolved else None,
                "created_by": user,
                "updated_by": actor,
            },
        )
        if issue.category != category or issue.status != (
            IssueStatus.RESOLVED if is_resolved else IssueStatus.OPEN
        ):
            issue.category = category
            issue.status = IssueStatus.RESOLVED if is_resolved else IssueStatus.OPEN
            issue.resolved_at = now - timedelta(days=2) if is_resolved else None
            issue.assigned_to = actor
            issue.updated_by = actor
            issue.save(
                update_fields=[
                    "category",
                    "status",
                    "resolved_at",
                    "assigned_to",
                    "updated_by",
                    "updated_at",
                ]
            )

        IssueComment.objects.get_or_create(
            issue=issue,
            author=actor,
            message="We are reviewing this request and will update shortly.",
            defaults={
                "is_internal": False,
                "created_by": actor,
                "updated_by": actor,
            },
        )
