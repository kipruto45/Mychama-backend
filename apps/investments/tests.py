from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus
from apps.finance.models import Wallet, WalletOwnerType

from .models import InvestmentProduct, MemberInvestmentPosition


class MemberInvestmentsApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.member = user_model.objects.create_user(
            phone="+254711000100",
            full_name="Investment Member",
            password="password123",
        )
        self.admin = user_model.objects.create_user(
            phone="+254711000101",
            full_name="Investment Admin",
            password="password123",
        )
        self.chama = Chama.objects.create(name="Investments Chama", currency="KES")
        Membership.objects.create(
            user=self.member,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            joined_at=timezone.now() - timedelta(days=120),
        )
        Membership.objects.create(
            user=self.admin,
            chama=self.chama,
            role=MembershipRole.CHAMA_ADMIN,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            joined_at=timezone.now() - timedelta(days=240),
        )
        Wallet.objects.create(
            owner_type=WalletOwnerType.USER,
            owner_id=self.member.id,
            available_balance=Decimal("50000.00"),
            locked_balance=Decimal("0.00"),
            currency="KES",
            created_by=self.member,
            updated_by=self.member,
        )
        self.product = InvestmentProduct.objects.create(
            chama=self.chama,
            code="FIXED-30",
            name="Fixed Income 30",
            description="Low-volatility 30 day product",
            minimum_amount=Decimal("1000.00"),
            expected_return_rate=Decimal("12.00"),
            projected_return_min_rate=Decimal("10.00"),
            projected_return_max_rate=Decimal("14.00"),
            term_days=30,
            lock_in_days=7,
            created_by=self.admin,
            updated_by=self.admin,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.member)

    def test_member_can_list_investment_products(self):
        response = self.client.get(f"/api/v1/investments/products/?chama_id={self.chama.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["code"], "FIXED-30")

    def test_wallet_funded_investment_debits_wallet_and_returns_active_position(self):
        response = self.client.post(
            "/api/v1/investments/member/positions/",
            {
                "chama_id": str(self.chama.id),
                "product_id": str(self.product.id),
                "amount": "2500.00",
                "funding_source": "wallet",
                "wallet_amount": "2500.00",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.json())
        payload = response.json()
        self.assertEqual(payload["status"], "active")
        self.assertEqual(payload["principal_amount"], "2500.00")

        wallet = Wallet.objects.get(owner_type=WalletOwnerType.USER, owner_id=self.member.id)
        self.assertEqual(wallet.available_balance, Decimal("47500.00"))

    def test_portfolio_summary_includes_member_investments(self):
        MemberInvestmentPosition.objects.create(
            chama=self.chama,
            product=self.product,
            member=self.member,
            funding_source="wallet",
            currency="KES",
            principal_amount=Decimal("4000.00"),
            wallet_funded_amount=Decimal("4000.00"),
            current_value=Decimal("4120.00"),
            accrued_returns=Decimal("120.00"),
            available_returns=Decimal("120.00"),
            expected_value_at_maturity=Decimal("4120.00"),
            funded_at=timezone.now() - timedelta(days=31),
            maturity_date=timezone.now() - timedelta(days=1),
            status="matured",
            created_by=self.member,
            updated_by=self.member,
        )

        response = self.client.get(
            f"/api/v1/investments/member/portfolio/summary/?chama_id={self.chama.id}"
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["currency"], "KES")
        self.assertEqual(payload["active_count"], 0)
        self.assertEqual(payload["matured_count"], 1)

    def test_member_can_utilize_returns_to_wallet(self):
        investment = MemberInvestmentPosition.objects.create(
            chama=self.chama,
            product=self.product,
            member=self.member,
            funding_source="wallet",
            currency="KES",
            principal_amount=Decimal("10000.00"),
            wallet_funded_amount=Decimal("10000.00"),
            current_value=Decimal("10000.00"),
            expected_value_at_maturity=Decimal("10100.00"),
            funded_at=timezone.now() - timedelta(days=30),
            maturity_date=timezone.now() + timedelta(days=5),
            status="active",
            created_by=self.member,
            updated_by=self.member,
        )

        response = self.client.post(
            f"/api/v1/investments/member/positions/{investment.id}/utilize/",
            {
                "amount": "90.00",
                "action_type": "wallet",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.json())
        wallet = Wallet.objects.get(owner_type=WalletOwnerType.USER, owner_id=self.member.id)
        self.assertEqual(wallet.available_balance, Decimal("50090.00"))

    def test_member_can_submit_wallet_redemption(self):
        investment = MemberInvestmentPosition.objects.create(
            chama=self.chama,
            product=self.product,
            member=self.member,
            funding_source="wallet",
            currency="KES",
            principal_amount=Decimal("7000.00"),
            wallet_funded_amount=Decimal("7000.00"),
            current_value=Decimal("7100.00"),
            accrued_returns=Decimal("100.00"),
            available_returns=Decimal("100.00"),
            expected_value_at_maturity=Decimal("7100.00"),
            funded_at=timezone.now() - timedelta(days=45),
            maturity_date=timezone.now() - timedelta(days=5),
            status="matured",
            created_by=self.member,
            updated_by=self.member,
        )

        response = self.client.post(
            f"/api/v1/investments/member/positions/{investment.id}/redeem/",
            {
                "redemption_type": "full",
                "destination": "wallet",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(response.json()["status"], "completed")
