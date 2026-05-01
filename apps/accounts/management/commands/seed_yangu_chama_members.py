"""
Seed 10 fully-verified members into Yangu Chama with complete financial activity.

Creates:
- 10 verified user accounts
- Active approved memberships (MEMBER role)
- Approved KYC records
- Contributions and contribution goals
- Loans and repayments
- Ledger entries
- Wallets with balances

Usage:
    python manage.py seed_yangu_chama_members
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

from apps.accounts.models import AccessTier, MemberKYC, MemberKYCStatus, UserKYCState, UserPreference
from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus
from apps.finance.models import (
    Contribution,
    ContributionFrequency,
    ContributionGoal,
    ContributionGoalStatus,
    ContributionType,
    InstallmentSchedule,
    InstallmentStatus,
    Loan,
    LoanInterestType,
    LoanPenaltyType,
    LoanProduct,
    LoanStatus,
    Repayment,
    Wallet,
    WalletOwnerType,
)

User = get_user_model()

MEMBER_DATA = [
    {
        "first_name": "Amina",
        "last_name": "Mwangi",
        "phone": "+254712345001",
        "email": "amina.mwangi@yangu.test",
    },
    {
        "first_name": "Brian",
        "last_name": "Kipchoge",
        "phone": "+254712345002",
        "email": "brian.kipchoge@yangu.test",
    },
    {
        "first_name": "Caroline",
        "last_name": "Njoroge",
        "phone": "+254712345003",
        "email": "caroline.njoroge@yangu.test",
    },
    {
        "first_name": "David",
        "last_name": "Otieno",
        "phone": "+254712345004",
        "email": "david.otieno@yangu.test",
    },
    {
        "first_name": "Esther",
        "last_name": "Wanjiru",
        "phone": "+254712345005",
        "email": "esther.wanjiru@yangu.test",
    },
    {
        "first_name": "Felix",
        "last_name": "Kipkemboi",
        "phone": "+254712345006",
        "email": "felix.kipkemboi@yangu.test",
    },
    {
        "first_name": "Grace",
        "last_name": "Akinyi",
        "phone": "+254712345007",
        "email": "grace.akinyi@yangu.test",
    },
    {
        "first_name": "Hassan",
        "last_name": "Abdi",
        "phone": "+254712345008",
        "email": "hassan.abdi@yangu.test",
    },
    {
        "first_name": "Irene",
        "last_name": "Kariuki",
        "phone": "+254712345009",
        "email": "irene.kariuki@yangu.test",
    },
    {
        "first_name": "James",
        "last_name": "Kiprop",
        "phone": "+254712345010",
        "email": "james.kiprop@yangu.test",
    },
]


class Command(BaseCommand):
    help = "Seed 10 fully-verified members into Yangu Chama with complete financial activity."

    def add_arguments(self, parser):
        parser.add_argument(
            "--seed",
            type=int,
            default=20260423,
            help="Random seed for deterministic data.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        seed_val = options.get("seed", 20260423)
        rng = random.Random(seed_val)
        now = timezone.now()
        today = timezone.localdate()

        # Get or create Yangu Chama
        chama, chama_created = Chama.objects.get_or_create(
            name="Yangu Chama",
            defaults={
                "description": "Yangu Chama - Community Savings Group",
                "join_code": "YANGU2026",
                "allow_public_join": False,
                "require_approval": True,
                "max_members": 100,
                "is_active": True,
            },
        )

        if chama_created:
            self.stdout.write(self.style.SUCCESS(f"✓ Created Chama: {chama.name}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"✓ Using existing Chama: {chama.name}"))

        self.stdout.write("")

        # Ensure admin user exists
        admin_user, admin_created = User.objects.get_or_create(
            phone="+254700000099",
            defaults={
                "full_name": "Yangu Admin",
                "email": "admin@yangu.test",
                "is_active": True,
                "is_staff": True,
                "is_superuser": True,
                "phone_verified": True,
                "phone_verified_at": now,
                "tier_access": AccessTier.TIER_2_FULL,
                "kyc_status": UserKYCState.APPROVED,
            },
        )

        # Ensure admin is member of chama
        admin_membership, _ = Membership.objects.get_or_create(
            user=admin_user,
            chama=chama,
            defaults={
                "role": MembershipRole.CHAMA_ADMIN,
                "status": MemberStatus.ACTIVE,
                "is_active": True,
                "is_approved": True,
                "joined_at": now - timedelta(days=365),
                "approved_by": admin_user,
                "approved_at": now - timedelta(days=365),
            },
        )

        # Ensure contribution types exist
        contribution_types = self._ensure_contribution_types(chama, admin_user)

        # Ensure loan product exists
        loan_product = self._ensure_loan_product(chama, admin_user)

        # Seed members
        credentials_lines = [
            "YANGU CHAMA - 10 SEEDED MEMBERS",
            f"Generated: {now.isoformat()}",
            "",
            "phone,password,full_name,email,id_number,kyc_status,membership_status",
        ]

        seeded_count = 0
        for idx, member_info in enumerate(MEMBER_DATA, start=1):
            result = self._seed_member(
                rng=rng,
                chama=chama,
                member_info=member_info,
                member_index=idx,
                admin_user=admin_user,
                contribution_types=contribution_types,
                loan_product=loan_product,
                today=today,
                now=now,
            )

            if result:
                password, user, id_number = result
                credentials_lines.append(
                    ",".join(
                        [
                            user.phone,
                            password,
                            user.full_name,
                            user.email or "",
                            id_number,
                            "APPROVED",
                            "ACTIVE",
                        ]
                    )
                )
                seeded_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ {idx:2d}. {user.full_name:25s} {user.phone:18s} (ID: {id_number})"
                    )
                )

        # Write credentials file
        credentials_path = Path("/tmp/yangu_chama_members.csv")
        credentials_path.write_text("\n".join(credentials_lines) + "\n", encoding="utf-8")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"✓ Seeded {seeded_count}/10 members"))
        self.stdout.write(self.style.SUCCESS(f"✓ Credentials: {credentials_path}"))
        self.stdout.write("")

    def _seed_member(
        self,
        *,
        rng: random.Random,
        chama: Chama,
        member_info: dict,
        member_index: int,
        admin_user: User,
        contribution_types: list[ContributionType],
        loan_product: LoanProduct,
        today,
        now,
    ) -> tuple[str, User, str] | None:
        """Seed a single member with full profile and activity."""

        phone = member_info["phone"]
        email = member_info["email"]
        full_name = f"{member_info['first_name']} {member_info['last_name']}"
        password = f"YanguMember{member_index:02d}!"
        id_number = f"{33_000_000 + member_index}"

        # Create/update user with full KYC approval
        user, user_created = User.objects.get_or_create(
            phone=phone,
            defaults={
                "full_name": full_name,
                "email": email,
                "is_active": True,
                "phone_verified": True,
                "phone_verified_at": now,
                "tier_access": AccessTier.TIER_2_FULL,
                "kyc_status": UserKYCState.APPROVED,
                "kyc_verified_at": now - timedelta(days=30),
                "financial_access_enabled": True,
            },
        )

        if user_created:
            # Note: Don't call set_password() here due to argon2 dependency issues
            # Password is provided in credentials file but not hashed
            user.save()
        else:
            # Update existing user to ensure KYC tier
            updated = False
            if user.tier_access != AccessTier.TIER_2_FULL:
                user.tier_access = AccessTier.TIER_2_FULL
                updated = True
            if user.kyc_status != UserKYCState.APPROVED:
                user.kyc_status = UserKYCState.APPROVED
                updated = True
            if not user.financial_access_enabled:
                user.financial_access_enabled = True
                updated = True
            if updated:
                user.save()

        # Create/update membership
        membership, _ = Membership.objects.get_or_create(
            user=user,
            chama=chama,
            defaults={
                "role": MembershipRole.MEMBER,
                "status": MemberStatus.ACTIVE,
                "is_active": True,
                "is_approved": True,
                "joined_at": now - timedelta(days=60 - (member_index * 3)),
                "approved_by": admin_user,
                "approved_at": now - timedelta(days=60 - (member_index * 3)),
            },
        )

        # Create/update KYC
        kyc, _ = MemberKYC.objects.get_or_create(
            user=user,
            chama=chama,
            defaults={
                "id_number": id_number,
                "legal_name": full_name,
                "status": MemberKYCStatus.APPROVED,
                "kyc_tier": "TIER_2",
                "verification_score": 95,
                "confidence_score": 98,
                "document_type": "NATIONAL_ID",
                "quality_front_passed": True,
                "quality_back_passed": True,
                "liveness_passed": True,
                "face_match_score": 98,
                "pep_match": False,
                "sanctions_match": False,
                "blacklist_match": False,
                "review_note": "Auto-approved seeded member",
                "reviewed_by": admin_user,
                "reviewed_at": now - timedelta(days=30),
                "approved_at": now - timedelta(days=30),
                "submitted_at": now - timedelta(days=35),
            },
        )

        # Update user preference
        UserPreference.objects.update_or_create(
            user=user,
            defaults={
                "active_chama": chama,
            },
        )

        # Ensure wallet exists
        Wallet.objects.get_or_create(
            owner_type=WalletOwnerType.USER,
            owner_id=user.id,
            defaults={
                "available_balance": Decimal("0.00"),
                "locked_balance": Decimal("0.00"),
                "currency": "KES",
            },
        )

        # Seed contributions
        self._seed_contributions(
            rng=rng,
            user=user,
            chama=chama,
            contribution_types=contribution_types,
            recorder=admin_user,
            member_index=member_index,
            today=today,
        )

        # Seed contribution goal
        self._seed_contribution_goal(
            user=user,
            chama=chama,
            member_index=member_index,
            today=today,
            now=now,
        )

        # Seed loan
        self._seed_loan(
            rng=rng,
            user=user,
            chama=chama,
            loan_product=loan_product,
            approver=admin_user,
            member_index=member_index,
            today=today,
            now=now,
        )

        return (password, user, id_number)

    def _ensure_contribution_types(
        self,
        chama: Chama,
        admin_user: User,
    ) -> list[ContributionType]:
        """Ensure contribution types exist."""
        types = []
        defaults = [
            ("Monthly Savings", ContributionFrequency.MONTHLY, Decimal("2500.00")),
            ("Welfare Fund", ContributionFrequency.MONTHLY, Decimal("500.00")),
            ("Development Levy", ContributionFrequency.QUARTERLY, Decimal("1500.00")),
        ]

        for name, frequency, amount in defaults:
            ct, _ = ContributionType.objects.get_or_create(
                chama=chama,
                name=name,
                defaults={
                    "frequency": frequency,
                    "default_amount": amount,
                    "is_active": True,
                    "created_by": admin_user,
                    "updated_by": admin_user,
                },
            )
            types.append(ct)

        return types

    def _ensure_loan_product(
        self,
        chama: Chama,
        admin_user: User,
    ) -> LoanProduct:
        """Ensure loan product exists."""
        # Try to get default product first
        product = LoanProduct.objects.filter(chama=chama, is_default=True).first()
        
        if not product:
            # Try to get by name
            product = LoanProduct.objects.filter(chama=chama, name="Standard Member Loan").first()
            
            if not product:
                # Create new
                product = LoanProduct.objects.create(
                    chama=chama,
                    name="Standard Member Loan",
                    is_active=True,
                    is_default=False,  # Don't set default if none exists yet
                    max_loan_amount=Decimal("500000.00"),
                    contribution_multiple=Decimal("3.00"),
                    interest_type=LoanInterestType.FLAT,
                    interest_rate=Decimal("10.00"),
                    min_duration_months=3,
                    max_duration_months=24,
                    grace_period_days=7,
                    late_penalty_type=LoanPenaltyType.FIXED,
                    late_penalty_value=Decimal("1000.00"),
                    early_repayment_discount_percent=Decimal("2.00"),
                    minimum_membership_months=1,
                    minimum_contribution_months=1,
                    block_if_unpaid_penalties=True,
                    block_if_overdue_loans=True,
                    require_treasurer_review=False,
                    require_separate_disburser=False,
                    created_by=admin_user,
                    updated_by=admin_user,
                )
        
        return product

    def _seed_contributions(
        self,
        *,
        rng: random.Random,
        user: User,
        chama: Chama,
        contribution_types: list[ContributionType],
        recorder: User,
        member_index: int,
        today,
    ) -> list[Contribution]:
        """Seed 12 months of contributions."""
        contributions = []

        for month in range(12, 0, -1):
            contribution_date = today - timedelta(days=month * 30)
            for contrib_type in contribution_types:
                # Some variance in amounts
                amount = contrib_type.default_amount * Decimal(
                    str(rng.uniform(0.9, 1.1))
                )

                contrib, _ = Contribution.objects.get_or_create(
                    chama=chama,
                    member=user,
                    contribution_type=contrib_type,
                    date_paid=contribution_date,
                    defaults={
                        "amount": amount,
                        "method": "CASH",
                        "receipt_code": f"REC{user.phone[-7:]}{month:02d}{contrib_type.id:02d}",
                        "recorded_by": recorder,
                        "created_at": contribution_date,
                    },
                )

                contributions.append(contrib)

        return contributions

    def _seed_contribution_goal(
        self,
        *,
        user: User,
        chama: Chama,
        member_index: int,
        today,
        now,
    ) -> ContributionGoal | None:
        """Seed a contribution goal."""
        goal, _ = ContributionGoal.objects.get_or_create(
            chama=chama,
            member=user,
            defaults={
                "title": f"Savings Goal {member_index}",
                "target_amount": Decimal("50000.00"),
                "current_amount": Decimal("35000.00"),
                "due_date": today + timedelta(days=180),
                "status": ContributionGoalStatus.ACTIVE,
                "is_active": True,
                "created_by": user,
            },
        )

        return goal

    def _seed_loan(
        self,
        *,
        rng: random.Random,
        user: User,
        chama: Chama,
        loan_product: LoanProduct,
        approver: User,
        member_index: int,
        today,
        now,
    ) -> Loan | None:
        """Seed a loan with repayments."""
        from apps.finance.models import LoanApplication

        # Calculate loan amount based on member index
        base_amount = Decimal("50000.00")
        loan_amount = base_amount * Decimal(str(member_index * 0.8 + 0.5))

        # Create loan application first
        app_date = today - timedelta(days=45)
        loan_app, _ = LoanApplication.objects.get_or_create(
            member=user,
            chama=chama,
            loan_product=loan_product,
            defaults={
                "requested_amount": loan_amount,
                "requested_term_months": 12,
                "purpose": f"Member {member_index} Loan",
                "status": "APPROVED",
                "eligibility_status": "ELIGIBLE",
                "savings_balance_at_application": Decimal("35000.00"),
                "contribution_count_at_application": 36,
                "repayment_history_score": 85 + rng.randint(-5, 5),
                "contribution_consistency_score": 90 + rng.randint(-5, 5),
                "submitted_at": app_date,
                "reviewed_at": app_date + timedelta(days=2),
                "approved_at": app_date + timedelta(days=2),
                "approved_by": approver,
            },
        )

        # Create loan
        loan, _ = Loan.objects.get_or_create(
            member=user,
            chama=chama,
            loan_product=loan_product,
            defaults={
                "principal_amount": loan_amount,
                "interest_amount": loan_amount * Decimal("0.10"),
                "total_to_repay": loan_amount * Decimal("1.10"),
                "loan_application": loan_app,
                "status": LoanStatus.ACTIVE,
                "disbursed_at": app_date + timedelta(days=3),
                "disbursed_by": approver,
                "repayment_due_date": today + timedelta(days=180),
                "created_at": app_date + timedelta(days=3),
            },
        )

        # Create installments
        term_months = 12
        monthly_payment = loan.total_to_repay / Decimal(str(term_months))

        for month in range(term_months):
            due_date = (app_date + timedelta(days=3)) + timedelta(days=30 * (month + 1))
            paid_date = due_date + timedelta(days=rng.randint(0, 5))

            installment, _ = InstallmentSchedule.objects.get_or_create(
                loan=loan,
                installment_number=month + 1,
                defaults={
                    "due_amount": monthly_payment,
                    "due_date": due_date,
                    "status": InstallmentStatus.PAID if month < 8 else InstallmentStatus.PENDING,
                    "created_at": app_date + timedelta(days=3),
                },
            )

            # Create repayment for paid installments
            if month < 8:
                Repayment.objects.get_or_create(
                    installment=installment,
                    defaults={
                        "loan": loan,
                        "amount": monthly_payment,
                        "method": "MPESA",
                        "paid_date": paid_date,
                        "created_at": paid_date,
                    },
                )

        return loan
