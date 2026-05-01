"""
Seed development data command.

Creates test data for development and testing purposes including:
- Test users
- Chamas with various configurations
- Members with different roles
- Contributions and loans
"""

import random
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus
from apps.finance.models import (
    Contribution,
    ContributionType,
    LedgerDirection,
    LedgerEntry,
    Loan,
    LoanProduct,
    LoanStatus,
    Wallet,
    WalletOwnerType,
)


class Command(BaseCommand):
    help = "Seed development data for testing"

    def add_arguments(self, parser):
        parser.add_argument(
            "--chamas",
            type=int,
            default=3,
            help="Number of chamas to create",
        )
        parser.add_argument(
            "--members-per-chama",
            type=int,
            default=10,
            help="Number of members per chama",
        )
        parser.add_argument(
            "--with-loans",
            action="store_true",
            help="Create sample loans",
        )
        parser.add_argument(
            "--with-contributions",
            action="store_true",
            help="Create sample contributions",
        )
        parser.add_argument(
            "--clean",
            action="store_true",
            help="Clean existing test data first",
        )

    def handle(self, *args, **options):
        chamas_count = options["chamas"]
        members_per_chama = options["members_per_chama"]
        with_loans = options["with_loans"]
        with_contributions = options["with_contributions"]
        clean = options["clean"]

        if clean:
            self._clean_data()
            self.stdout.write(self.style.SUCCESS("Cleaned existing test data"))

        # Create test users if needed
        users = self._ensure_test_users(chamas_count * members_per_chama)
        self.stdout.write(self.style.SUCCESS(f"Using {len(users)} test users"))

        # Create chamas
        for i in range(chamas_count):
            chama = self._create_chama(f"Test Chama {i+1}", users[:members_per_chama])
            self.stdout.write(f"Created chama: {chama.name}")
            
            if with_contributions:
                self._create_contributions(chama, users[:members_per_chama])
                
            if with_loans:
                self._create_loans(chama, users[:members_per_chama])

        self.stdout.write(self.style.SUCCESS("Seeded development data successfully"))

    def _clean_data(self):
        """Clean existing test data."""
        # Delete loans
        Loan.objects.filter(
            member__phone__startswith="test"
        ).delete()
        
        # Delete contributions
        Contribution.objects.filter(
            member__phone__startswith="test"
        ).delete()
        
        # Delete ledger entries
        LedgerEntry.objects.filter(
            chama__name__startswith="Test Chama"
        ).delete()
        
        # Delete wallets
        Wallet.objects.filter(
            owner_type=WalletOwnerType.CHAMA,
            owner_id__in=Chama.objects.filter(name__startswith="Test Chama").values_list("id", flat=True)
        ).delete()
        
        # Delete chamas
        Chama.objects.filter(name__startswith="Test Chama").delete()
        
        # Delete test users
        User.objects.filter(phone__startswith="test").delete()

    def _ensure_test_users(self, count: int) -> list[User]:
        """Ensure have enough we test users."""
        # Find existing test users
        test_users = list(User.objects.filter(phone__startswith="test").order_by("phone"))
        
        if len(test_users) >= count:
            return test_users[:count]
        
        # Create more test users
        needed = count - len(test_users)
        for i in range(needed):
            phone = f"test{random.randint(100000000, 999999999)}"
            try:
                user = User.objects.create_user(
                    phone=phone,
                    password="testpass123",
                    full_name=f"Test User {len(test_users) + i + 1}",
                )
                user.phone_verified = True
                user.save()
                test_users.append(user)
            except Exception as e:
                self.stdout.write(f"Warning: Could not create user {phone}: {e}")
        
        return test_users

    def _create_chama(self, name: str, members: list[User]) -> Chama:
        """Create a chama with members."""
        # Create chama
        chama = Chama.objects.create(
            name=name,
            description=f"Test chama: {name}",
            max_members=len(members) + 5,
        )
        
        # Create chama wallet
        Wallet.objects.create(
            owner_type=WalletOwnerType.CHAMA,
            owner_id=chama.id,
            available_balance=Decimal("0.00"),
            currency="KES",
        )
        
        # Create contribution type
        ContributionType.objects.create(
            chama=chama,
            name="Monthly Contribution",
            frequency="monthly",
            default_amount=Decimal("5000.00"),
            is_active=True,
        )
        
        # Create default loan product
        LoanProduct.objects.create(
            chama=chama,
            name="Standard Loan",
            is_active=True,
            is_default=True,
            max_loan_amount=Decimal("100000.00"),
            contribution_multiple=Decimal("3.00"),
            interest_type="flat",
            interest_rate=Decimal("10.00"),
            min_duration_months=1,
            max_duration_months=12,
            grace_period_days=0,
            late_penalty_type="fixed",
            late_penalty_value=Decimal("500.00"),
        )
        
        # Add members
        for idx, member in enumerate(members):
            role = MembershipRole.MEMBER
            if idx == 0:
                role = MembershipRole.CHAMA_ADMIN
            elif idx == 1:
                role = MembershipRole.TREASURER
            elif idx == 2:
                role = MembershipRole.SECRETARY
            
            Membership.objects.create(
                user=member,
                chama=chama,
                role=role,
                status=MemberStatus.ACTIVE,
                is_active=True,
                is_approved=True,
                approved_at=timezone.now(),
            )
            
            # Create user wallet
            Wallet.objects.get_or_create(
                owner_type=WalletOwnerType.USER,
                owner_id=member.id,
                defaults={
                    "available_balance": Decimal("0.00"),
                    "currency": "KES",
                }
            )
        
        return chama

    def _create_contributions(self, chama: Chama, members: list[User]):
        """Create sample contributions for members."""
        contribution_type = ContributionType.objects.filter(chama=chama).first()
        if not contribution_type:
            return
        
        for member in members[:5]:  # Only first 5 members
            # Create 3 months of contributions
            for month in range(3):
                date_paid = timezone.now().date()
                date_paid = date_paid.replace(month=max(1, date_paid.month - month))
                
                amount = contribution_type.default_amount
                
                contribution = Contribution.objects.create(
                    chama=chama,
                    member=member,
                    contribution_type=contribution_type,
                    amount=amount,
                    date_paid=date_paid,
                    method="mpesa",
                    receipt_code=f"RCP-{chama.id}-{member.id}-{month}-{random.randint(1000, 9999)}",
                )
                
                # Create ledger entry
                LedgerEntry.objects.create(
                    chama=chama,
                    entry_type="contribution",
                    direction=LedgerDirection.CREDIT,
                    amount=amount,
                    currency="KES",
                    idempotency_key=f"contrib-{contribution.id}",
                    narration=f"Contribution for {date_paid.strftime('%Y-%m')}",
                    status="success",
                    provider="internal",
                )

    def _create_loans(self, chama: Chama, members: list[User]):
        """Create sample loans for members."""
        loan_product = LoanProduct.objects.filter(chama=chama, is_default=True).first()
        if not loan_product:
            return
        
        for member in members[3:6]:  # Members 4-6 get loans
            principal = Decimal(random.randint(10000, 50000))
            
            loan = Loan.objects.create(
                chama=chama,
                member=member,
                loan_product=loan_product,
                principal=principal,
                interest_type=loan_product.interest_type,
                interest_rate=loan_product.interest_rate,
                duration_months=6,
                grace_period_days=loan_product.grace_period_days,
                late_penalty_type=loan_product.late_penalty_type,
                late_penalty_value=loan_product.late_penalty_value,
                eligibility_status="eligible",
                recommended_max_amount=loan_product.max_loan_amount,
                status=LoanStatus.DISBURSED,
                approved_at=timezone.now(),
                disbursed_at=timezone.now(),
            )
            
            # Create loan disbursement ledger entry
            LedgerEntry.objects.create(
                chama=chama,
                entry_type="loan_disbursement",
                direction=LedgerDirection.DEBIT,
                amount=principal,
                currency="KES",
                idempotency_key=f"loan-disbursal-{loan.id}",
                narration=f"Loan disbursement for {member.full_name}",
                status="success",
                provider="internal",
                related_loan=loan,
            )
