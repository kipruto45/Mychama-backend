from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.chama.models import (
    Chama,
    ChamaContributionSetting,
    LoanPolicy,
    Membership,
    MembershipRole,
    MemberStatus,
)
from apps.billing.models import Plan, Subscription
from apps.billing.entitlements import PLAN_ENTITLEMENTS
from apps.finance.models import (
    Contribution,
    ContributionSchedule,
    ContributionScheduleStatus,
    ContributionType,
    InstallmentSchedule,
    InstallmentStatus,
    LedgerDirection,
    LedgerEntry,
    LedgerEntryType,
    Loan,
    LoanApplication,
    LoanApplicationStatus,
    LoanAuditLog,
    LoanGuarantor,
    LoanGuarantorStatus,
    LoanProduct,
    LoanStatus,
    Penalty,
    PenaltyStatus,
    Wallet,
    WalletOwnerType,
)
from apps.finance.services import FinanceService
from apps.finance.summary import get_chama_financial_snapshot
from apps.finance.tasks import (
    contributions_cycle_completion_check,
    contributions_schedule_automation_sweep,
    loans_auto_close_when_paid,
)
from apps.payments.unified_models import (
    PaymentIntent,
    PaymentMethod,
    PaymentPurpose,
    PaymentReceipt,
    PaymentStatus,
    PaymentTransaction,
    TransactionStatus,
)


class ChamaFinancialSnapshotTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            phone="+254711000001",
            full_name="Finance Snapshot User",
            password="password123",
        )
        self.chama = Chama.objects.create(name="Snapshot Chama")

    def test_snapshot_updates_from_ledger_and_finance_state_changes(self):
        loan = Loan.objects.create(
            chama=self.chama,
            member=self.user,
            principal=Decimal("250.00"),
            interest_rate=Decimal("10.00"),
            duration_months=2,
            status=LoanStatus.ACTIVE,
        )
        InstallmentSchedule.objects.create(
            loan=loan,
            due_date=date(2026, 3, 1),
            expected_amount=Decimal("275.00"),
            status=InstallmentStatus.OVERDUE,
        )
        Penalty.objects.create(
            chama=self.chama,
            member=self.user,
            amount=Decimal("80.00"),
            reason="Late contribution",
            due_date=date(2026, 3, 5),
            status=PenaltyStatus.UNPAID,
            issued_by=self.user,
        )

        LedgerEntry.objects.create(
            chama=self.chama,
            entry_type=LedgerEntryType.CONTRIBUTION,
            direction=LedgerDirection.CREDIT,
            amount=Decimal("500.00"),
            idempotency_key="snapshot:contribution",
            narration="Contribution posted",
            created_by=self.user,
        )
        LedgerEntry.objects.create(
            chama=self.chama,
            entry_type=LedgerEntryType.LOAN_DISBURSEMENT,
            direction=LedgerDirection.DEBIT,
            amount=Decimal("200.00"),
            idempotency_key="snapshot:loan-disbursement",
            narration="Loan disbursed",
            created_by=self.user,
        )

        snapshot = get_chama_financial_snapshot(self.chama)

        self.assertEqual(snapshot.contributions_total, Decimal("500.00"))
        self.assertEqual(snapshot.cash_in_total, Decimal("500.00"))
        self.assertEqual(snapshot.cash_out_total, Decimal("200.00"))
        self.assertEqual(snapshot.loan_disbursements_total, Decimal("200.00"))
        self.assertEqual(snapshot.outstanding_loans_total, Decimal("275.00"))
        self.assertEqual(snapshot.active_loan_count, 1)
        self.assertEqual(snapshot.overdue_loan_count, 1)
        self.assertEqual(snapshot.unpaid_penalties_total, Decimal("80.00"))
        self.assertEqual(snapshot.unpaid_penalties_count, 1)

    def test_snapshot_rebuilds_if_missing(self):
        LedgerEntry.objects.create(
            chama=self.chama,
            entry_type=LedgerEntryType.CONTRIBUTION,
            direction=LedgerDirection.CREDIT,
            amount=Decimal("120.00"),
            idempotency_key="snapshot:rebuild",
            narration="Contribution posted",
            created_by=self.user,
        )

        self.chama.financial_snapshot.delete()

        snapshot = get_chama_financial_snapshot(self.chama)

        self.assertEqual(snapshot.contributions_total, Decimal("120.00"))


class AllTransactionsFeedTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.member = user_model.objects.create_user(
            phone="+254711000010",
            full_name="Transactions Member",
            password="password123",
        )
        self.treasurer = user_model.objects.create_user(
            phone="+254711000011",
            full_name="Transactions Treasurer",
            password="password123",
        )
        self.chama = Chama.objects.create(name="Transactions Chama")

        pro_plan, _ = Plan.objects.get_or_create(
            code=Plan.PRO,
            defaults={
                "name": "Pro",
                "description": "Pro plan (test)",
                "monthly_price": 0,
                "yearly_price": 0,
                "features": PLAN_ENTITLEMENTS["PRO"],
                "is_active": True,
                "sort_order": 2,
            },
        )
        if pro_plan.features != PLAN_ENTITLEMENTS["PRO"]:
            pro_plan.features = PLAN_ENTITLEMENTS["PRO"]
            pro_plan.save(update_fields=["features"])
        Subscription.objects.create(
            chama=self.chama,
            plan=pro_plan,
            status=Subscription.ACTIVE,
            provider=Subscription.MANUAL,
            billing_cycle=Subscription.MONTHLY,
            current_period_start=timezone.now() - timedelta(days=1),
            current_period_end=timezone.now() + timedelta(days=30),
        )

        Membership.objects.create(
            user=self.member,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            joined_at=timezone.now() - timedelta(days=90),
        )
        Membership.objects.create(
            user=self.treasurer,
            chama=self.chama,
            role=MembershipRole.TREASURER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            joined_at=timezone.now() - timedelta(days=120),
        )

        self.client = APIClient()
        self.client.force_authenticate(self.treasurer)

        self.contribution_type = ContributionType.objects.create(
            chama=self.chama,
            name="Monthly Savings",
            default_amount=Decimal("1000.00"),
            frequency="monthly",
        )

        FinanceService.post_contribution(
            {
                "chama_id": str(self.chama.id),
                "member_id": str(self.member.id),
                "contribution_type_id": str(self.contribution_type.id),
                "amount": "1000.00",
                "date_paid": timezone.localdate().isoformat(),
                "method": "mpesa",
                "receipt_code": "TXN-CONTRIB-001",
                "idempotency_key": "txn-feed-contribution-1",
            },
            self.treasurer,
        )

        self.pending_intent = PaymentIntent.objects.create(
            chama=self.chama,
            user=self.member,
            amount=Decimal("250.00"),
            currency="KES",
            purpose=PaymentPurpose.CONTRIBUTION,
            description="Pending contribution payment",
            payment_method=PaymentMethod.MPESA,
            provider="safaricom",
            provider_intent_id="provider-pending-1",
            status=PaymentStatus.PENDING,
            idempotency_key="txn-feed-pending-1",
            metadata={"target_label": "May contribution"},
            created_by=self.member,
        )

        # Standalone ledger entry (no journal) to ensure it appears in the feed.
        self.wallet_entry = LedgerEntry.objects.create(
            chama=self.chama,
            entry_type=LedgerEntryType.WALLET_TOPUP,
            direction=LedgerDirection.CREDIT,
            amount=Decimal("500.00"),
            debit=Decimal("0.00"),
            credit=Decimal("500.00"),
            currency="KES",
            status="success",
            provider="mpesa",
            provider_reference="MPESA-TOPUP-1",
            idempotency_key="txn-feed-ledger-topup-1",
            narration="Wallet top-up recorded",
            created_by=self.treasurer,
        )

    def test_transactions_feed_returns_unified_items(self):
        response = self.client.get(
            "/api/v1/finance/transactions",
            {"chama_id": str(self.chama.id), "limit": 50},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"]
        items = payload["items"]
        refs = {item["ref"] for item in items}

        self.assertTrue(any(ref.startswith("journal_") for ref in refs))
        self.assertIn(f"payment_{self.pending_intent.id}", refs)
        self.assertIn(f"ledger_{self.wallet_entry.id}", refs)

    def test_transactions_feed_detail_returns_lines_for_journal(self):
        list_response = self.client.get(
            "/api/v1/finance/transactions",
            {"chama_id": str(self.chama.id), "limit": 10},
        )
        self.assertEqual(list_response.status_code, 200)
        items = list_response.json()["data"]["items"]
        journal_ref = next(item["ref"] for item in items if item["ref"].startswith("journal_"))

        detail = self.client.get(
            f"/api/v1/finance/transactions/{journal_ref}",
            {"chama_id": str(self.chama.id)},
        )
        self.assertEqual(detail.status_code, 200)
        transaction = detail.json()["data"]["transaction"]
        self.assertEqual(transaction["source"], "journal_entry")
        self.assertGreaterEqual(len(transaction.get("lines") or []), 2)

    def test_transactions_feed_filters_by_category(self):
        response = self.client.get(
            "/api/v1/finance/transactions",
            {"chama_id": str(self.chama.id), "category": "inflow", "limit": 50},
        )
        self.assertEqual(response.status_code, 200)
        items = response.json()["data"]["items"]
        self.assertTrue(items)
        self.assertTrue(all(item["category"] == "inflow" for item in items))


class LoanLifecycleServiceTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(
            phone="+254711000010",
            full_name="Loan Admin",
            password="password123",
        )
        self.member = user_model.objects.create_user(
            phone="+254711000011",
            full_name="Loan Member",
            password="password123",
        )
        self.guarantor = user_model.objects.create_user(
            phone="+254711000012",
            full_name="Loan Guarantor",
            password="password123",
        )
        self.chama = Chama.objects.create(name="Lifecycle Chama")
        self.member_membership = Membership.objects.create(
            user=self.member,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            joined_at=timezone.now() - timedelta(days=150),
        )
        Membership.objects.create(
            user=self.admin,
            chama=self.chama,
            role=MembershipRole.CHAMA_ADMIN,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            joined_at=timezone.now() - timedelta(days=200),
        )
        Membership.objects.create(
            user=self.guarantor,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            joined_at=timezone.now() - timedelta(days=200),
        )
        self.policy = LoanPolicy.objects.create(
            chama=self.chama,
            min_membership_days=90,
            min_contribution_cycles=0,
            loan_cap_multiplier=Decimal("0.00"),
            reserve_liquidity_amount=Decimal("0.00"),
            default_after_days_overdue=30,
            recovery_review_after_days_overdue=14,
            restrict_new_loans_on_overdue=True,
            restrict_member_privileges_on_default=True,
            restrict_withdrawals_on_default=True,
            restrict_voting_on_default=True,
            restrict_invites_on_default=True,
            notify_guarantors_on_overdue=True,
        )
        self.loan_product = LoanProduct.objects.create(
            chama=self.chama,
            name="Standard Loan",
            is_active=True,
            is_default=True,
            max_loan_amount=Decimal("100000.00"),
            contribution_multiple=Decimal("0.00"),
            interest_rate=Decimal("12.00"),
            min_duration_months=1,
            max_duration_months=12,
            grace_period_days=0,
            late_penalty_type="fixed",
            late_penalty_value=Decimal("500.00"),
            minimum_membership_months=0,
            minimum_contribution_months=0,
            block_if_unpaid_penalties=True,
            block_if_overdue_loans=True,
        )
        self.contribution_type = ContributionType.objects.create(
            chama=self.chama,
            name="Monthly Savings",
            default_amount=Decimal("1000.00"),
            frequency="monthly",
        )
        Contribution.objects.create(
            chama=self.chama,
            member=self.member,
            contribution_type=self.contribution_type,
            amount=Decimal("15000.00"),
            date_paid=timezone.localdate() - timedelta(days=30),
            receipt_code="CONTRIB-MEMBER-1",
            recorded_by=self.admin,
        )
        Contribution.objects.create(
            chama=self.chama,
            member=self.guarantor,
            contribution_type=self.contribution_type,
            amount=Decimal("25000.00"),
            date_paid=timezone.localdate() - timedelta(days=30),
            receipt_code="CONTRIB-GUARANTOR-1",
            recorded_by=self.admin,
        )
        cash_account = FinanceService._get_or_create_account(self.chama, "cash")
        LedgerEntry.objects.create(
            chama=self.chama,
            account=cash_account,
            entry_type=LedgerEntryType.CONTRIBUTION,
            direction=LedgerDirection.DEBIT,
            amount=Decimal("50000.00"),
            debit=Decimal("50000.00"),
            credit=Decimal("0.00"),
            currency="KES",
            status="success",
            idempotency_key="lifecycle-liquidity",
            narration="Seed liquidity",
            created_by=self.admin,
        )

    def test_eligibility_rejects_member_when_loan_requests_are_restricted(self):
        self.member_membership.can_request_loan = False
        self.member_membership.restriction_reason = "loan_policy:loan_request_blocked"
        self.member_membership.save(
            update_fields=["can_request_loan", "restriction_reason", "updated_at"]
        )

        result = FinanceService.evaluate_loan_eligibility(
            chama=self.chama,
            member=self.member,
            principal=Decimal("10000.00"),
            duration_months=6,
            loan_product=self.loan_product,
        )

        self.assertFalse(result.eligible)
        self.assertIn(
            "Member is currently restricted from requesting loans.",
            result.reasons,
        )

    def test_check_loan_eligibility_returns_structured_decision_payload(self):
        payload = FinanceService.check_loan_eligibility(
            {
                "chama_id": str(self.chama.id),
                "member_id": str(self.member.id),
                "loan_product_id": str(self.loan_product.id),
                "principal": Decimal("10000.00"),
                "duration_months": 6,
            },
            self.admin,
        )

        self.assertTrue(payload["eligible"])
        self.assertIn("policy_summary", payload)
        self.assertIn("policy_checks", payload)
        self.assertIn("calculated_metrics", payload)
        self.assertIn("savings_summary", payload)
        self.assertIn("approval_requirements", payload)
        self.assertEqual(payload["requested_amount_validation"]["within_limit"], True)
        self.assertGreater(len(payload["policy_checks"]), 0)

    def test_request_loan_application_persists_eligibility_snapshot_and_audit_log(self):
        application = FinanceService.request_loan_application(
            {
                "chama_id": str(self.chama.id),
                "member_id": str(self.member.id),
                "loan_product_id": str(self.loan_product.id),
                "requested_amount": Decimal("9000.00"),
                "requested_term_months": 6,
                "purpose": "Business stock",
            },
            self.member,
        )

        self.assertEqual(application.eligible_amount_at_application, application.recommended_max_amount)
        self.assertGreater(application.savings_balance_at_application, Decimal("0.00"))
        self.assertGreater(application.contribution_count_at_application, 0)
        self.assertGreater(application.repayment_history_score, Decimal("0.00"))
        self.assertIn("policy_checks", application.eligibility_snapshot)
        self.assertTrue(
            LoanAuditLog.objects.filter(
                loan_application=application,
                action="application_submitted",
            ).exists()
        )

    def test_refresh_loan_delinquency_defaults_loan_and_triggers_recovery_controls(self):
        loan = Loan.objects.create(
            chama=self.chama,
            member=self.member,
            loan_product=self.loan_product,
            principal=Decimal("10000.00"),
            outstanding_principal=Decimal("10000.00"),
            outstanding_interest=Decimal("0.00"),
            outstanding_penalty=Decimal("0.00"),
            total_due=Decimal("10000.00"),
            interest_type=self.loan_product.interest_type,
            interest_rate=Decimal("12.00"),
            duration_months=6,
            grace_period_days=0,
            late_penalty_type="fixed",
            late_penalty_value=Decimal("500.00"),
            status=LoanStatus.ACTIVE,
            final_status="active",
            created_by=self.admin,
            updated_by=self.admin,
        )
        LoanGuarantor.objects.create(
            loan=loan,
            guarantor=self.guarantor,
            guaranteed_amount=Decimal("10000.00"),
            status=LoanGuarantorStatus.ACCEPTED,
            created_by=self.admin,
            updated_by=self.admin,
        )
        InstallmentSchedule.objects.create(
            loan=loan,
            due_date=timezone.localdate() - timedelta(days=35),
            expected_amount=Decimal("2000.00"),
            expected_principal=Decimal("1800.00"),
            expected_interest=Decimal("200.00"),
            expected_penalty=Decimal("0.00"),
            status=InstallmentStatus.DUE,
            created_by=self.admin,
            updated_by=self.admin,
        )

        FinanceService.refresh_loan_delinquency(str(loan.id), actor=self.admin)

        loan.refresh_from_db()
        self.member_membership.refresh_from_db()
        guarantor_record = LoanGuarantor.objects.get(loan=loan, guarantor=self.guarantor)

        self.assertEqual(loan.status, LoanStatus.DEFAULTED)
        self.assertEqual(loan.escalation_level, "recovery")
        self.assertIsNotNone(loan.defaulted_at)
        self.assertEqual(loan.final_status, "defaulted_recovering")
        self.assertEqual(loan.outstanding_penalty, Decimal("500.00"))

        self.assertTrue(self.member_membership.loan_default_risk)
        self.assertFalse(self.member_membership.can_request_loan)
        self.assertFalse(self.member_membership.can_withdraw_savings)
        self.assertFalse(self.member_membership.can_vote)
        self.assertEqual(guarantor_record.status, LoanGuarantorStatus.AT_RISK)


class MemberWalletWorkflowApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.member = user_model.objects.create_user(
            phone="+254711000020",
            full_name="Wallet Member",
            password="password123",
        )
        self.member.financial_access_enabled = True
        self.member.save(update_fields=["financial_access_enabled"])
        self.other_member = user_model.objects.create_user(
            phone="+254711000021",
            full_name="Other Member",
            password="password123",
        )
        self.other_member.financial_access_enabled = True
        self.other_member.save(update_fields=["financial_access_enabled"])
        self.admin = user_model.objects.create_user(
            phone="+254711000022",
            full_name="Wallet Admin",
            password="password123",
        )
        self.admin.financial_access_enabled = True
        self.admin.save(update_fields=["financial_access_enabled"])
        self.chama = Chama.objects.create(name="Wallet Chama")
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
            user=self.other_member,
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
            joined_at=timezone.now() - timedelta(days=180),
        )
        self.client = APIClient()
        self.client.force_authenticate(self.member)
        self.contribution_type = ContributionType.objects.create(
            chama=self.chama,
            name="Monthly Savings",
            default_amount=Decimal("2500.00"),
            frequency="monthly",
        )
        self.contribution = Contribution.objects.create(
            chama=self.chama,
            member=self.member,
            contribution_type=self.contribution_type,
            amount=Decimal("2500.00"),
            date_paid=timezone.localdate() - timedelta(days=3),
            receipt_code="WALLET-CONTRIB-001",
            recorded_by=self.admin,
        )
        self.loan = Loan.objects.create(
            chama=self.chama,
            member=self.member,
            principal=Decimal("12000.00"),
            interest_rate=Decimal("12.00"),
            duration_months=6,
            status=LoanStatus.ACTIVE,
            outstanding_principal=Decimal("9000.00"),
            outstanding_interest=Decimal("900.00"),
            outstanding_penalty=Decimal("0.00"),
            total_due=Decimal("9900.00"),
            due_date=timezone.localdate() + timedelta(days=12),
            approved_at=timezone.now() - timedelta(days=40),
            disbursed_at=timezone.now() - timedelta(days=39),
            created_by=self.admin,
        )
        self.installment = InstallmentSchedule.objects.create(
            loan=self.loan,
            due_date=timezone.localdate() + timedelta(days=12),
            expected_amount=Decimal("2100.00"),
            paid_amount=Decimal("0.00"),
            status=InstallmentStatus.PENDING,
        )

        self.success_intent = PaymentIntent.objects.create(
            chama=self.chama,
            user=self.member,
            contribution=self.contribution,
            amount=Decimal("2500.00"),
            currency="KES",
            purpose=PaymentPurpose.CONTRIBUTION,
            description="Monthly contribution",
            payment_method=PaymentMethod.MPESA,
            provider="safaricom",
            provider_intent_id="provider-success-1",
            status=PaymentStatus.SUCCESS,
            idempotency_key="wallet-success-1",
            metadata={
                "contribution_id": str(self.contribution.id),
                "contribution_type_id": str(self.contribution_type.id),
                "contribution_type_name": self.contribution_type.name,
                "target_label": "April contribution",
            },
            completed_at=timezone.now() - timedelta(days=2),
            created_by=self.member,
        )
        self.success_transaction = PaymentTransaction.objects.create(
            payment_intent=self.success_intent,
            provider="mpesa",
            reference="TXN-SUCCESS-1",
            provider_reference="MPESA-SUCCESS-1",
            provider_name="safaricom",
            payment_method=PaymentMethod.MPESA,
            amount=Decimal("2500.00"),
            currency="KES",
            status=TransactionStatus.VERIFIED,
        )
        self.success_receipt = PaymentReceipt.objects.create(
            payment_intent=self.success_intent,
            transaction=self.success_transaction,
            receipt_number="RCP-WALLET-001",
            reference_number="REF-WALLET-001",
            amount=Decimal("2500.00"),
            currency="KES",
            payment_method=PaymentMethod.MPESA,
            issued_by=self.admin,
        )

        self.pending_intent = PaymentIntent.objects.create(
            chama=self.chama,
            user=self.member,
            amount=Decimal("2100.00"),
            currency="KES",
            purpose=PaymentPurpose.LOAN_REPAYMENT,
            description="Loan repayment",
            payment_method=PaymentMethod.MPESA,
            provider="safaricom",
            provider_intent_id="provider-pending-1",
            status=PaymentStatus.PENDING,
            idempotency_key="wallet-pending-1",
            metadata={
                "loan_id": str(self.loan.id),
                "installment_id": str(self.installment.id),
                "target_label": "May installment",
            },
            created_by=self.member,
        )

        PaymentIntent.objects.create(
            chama=self.chama,
            user=self.other_member,
            amount=Decimal("999.00"),
            currency="KES",
            purpose=PaymentPurpose.CONTRIBUTION,
            description="Other member payment",
            payment_method=PaymentMethod.MPESA,
            provider="safaricom",
            provider_intent_id="provider-other-1",
            status=PaymentStatus.SUCCESS,
            idempotency_key="wallet-other-1",
            created_by=self.other_member,
        )

        LedgerEntry.objects.create(
            chama=self.chama,
            related_loan=self.loan,
            entry_type=LedgerEntryType.LOAN_DISBURSEMENT,
            direction=LedgerDirection.CREDIT,
            amount=Decimal("12000.00"),
            debit=Decimal("0.00"),
            credit=Decimal("12000.00"),
            currency="KES",
            status="success",
            idempotency_key="wallet-ledger-disbursement",
            narration="Loan disbursed to member",
            created_by=self.admin,
        )

    def test_member_wallet_workspace_returns_safe_member_only_summary(self):
        response = self.client.get(
            "/api/v1/finance/member-wallet/workspace",
            {"chama_id": str(self.chama.id)},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"]
        self.assertEqual(payload["balance_state"], "pending_update")
        self.assertEqual(payload["summary_cards"]["recent_contribution_payments_count"], 1)
        self.assertEqual(payload["summary_cards"]["active_loan_id"], str(self.loan.id))
        self.assertEqual(len(payload["recent_activity"]), 3)
        references = {item["reference"] for item in payload["recent_activity"]}
        self.assertIn("TXN-SUCCESS-1", references)
        self.assertNotIn("wallet-other-1", references)

    def test_member_wallet_activity_filters_pending_items(self):
        response = self.client.get(
            "/api/v1/finance/member-wallet/activity",
            {
                "chama_id": str(self.chama.id),
                "filter": "pending",
            },
        )

        self.assertEqual(response.status_code, 200)
        items = response.json()["data"]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"], "pending")
        self.assertEqual(items[0]["loan_id"], str(self.loan.id))

    def test_member_wallet_transaction_detail_supports_payment_and_ledger_rows(self):
        payment_response = self.client.get(
            f"/api/v1/finance/member-wallet/transactions/payment_{self.success_intent.id}",
            {"chama_id": str(self.chama.id)},
        )
        self.assertEqual(payment_response.status_code, 200)
        payment_transaction = payment_response.json()["data"]["transaction"]
        self.assertEqual(payment_transaction["receipt_number"], "RCP-WALLET-001")
        self.assertEqual(payment_transaction["contribution_id"], str(self.contribution.id))
        self.assertTrue(payment_transaction["linked_contribution_available"])

        ledger_entry = LedgerEntry.objects.get(idempotency_key="wallet-ledger-disbursement")
        ledger_response = self.client.get(
            f"/api/v1/finance/member-wallet/transactions/ledger_{ledger_entry.id}",
            {"chama_id": str(self.chama.id)},
        )
        self.assertEqual(ledger_response.status_code, 200)
        ledger_transaction = ledger_response.json()["data"]["transaction"]
        self.assertEqual(ledger_transaction["type"], "loan_disbursement")
        self.assertEqual(ledger_transaction["direction"], "inflow")
        self.assertEqual(ledger_transaction["loan_id"], str(self.loan.id))

    def test_member_wallet_workspace_exposes_member_wallet_controls(self):
        wallet = Wallet.objects.create(
            owner_type=WalletOwnerType.USER,
            owner_id=self.member.id,
            available_balance=Decimal("1800.00"),
            locked_balance=Decimal("200.00"),
            currency="KES",
        )

        response = self.client.get(
            "/api/v1/finance/member-wallet/workspace",
            {"chama_id": str(self.chama.id)},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"]
        self.assertEqual(payload["available_balance"], "1800.00")
        self.assertEqual(payload["withdrawable_balance"], "1800.00")
        self.assertEqual(payload["pending_balance"], "200.00")
        self.assertEqual(payload["limits"]["withdrawable_balance"], "1800.00")
        self.assertEqual(payload["methods"]["deposit_methods"][0]["key"], "mpesa")
        self.assertEqual(payload["methods"]["withdrawal_methods"][0]["key"], "mpesa")
        wallet.refresh_from_db()
        self.assertEqual(wallet.available_balance, Decimal("1800.00"))

    @override_settings(MPESA_CONSUMER_KEY="test-key", MPESA_CONSUMER_SECRET="test-secret")
    def test_member_can_create_and_refresh_wallet_deposit(self):
        response = self.client.post(
            "/api/v1/finance/member-wallet/deposits",
            {
                "chama_id": str(self.chama.id),
                "amount": "500.00",
                "payment_method": "mpesa",
                "phone": "+254711000020",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()["data"]
        self.assertIn(payload["state"], {"initiated", "processing"})

        refresh_response = self.client.post(
            f"/api/v1/finance/member-wallet/deposits/{payload['intent_id']}",
            {"chama_id": str(self.chama.id)},
            format="json",
        )
        self.assertEqual(refresh_response.status_code, 200)
        refreshed = refresh_response.json()["data"]
        self.assertEqual(refreshed["state"], "success")
        self.assertEqual(refreshed["transaction"]["type"], "wallet_deposit")
        self.assertEqual(refreshed["transaction"]["direction"], "inflow")
        self.assertTrue(refreshed["transaction"]["receipt_available"])
        wallet = Wallet.objects.get(owner_type=WalletOwnerType.USER, owner_id=self.member.id)
        self.assertEqual(wallet.available_balance, Decimal("500.00"))

    def test_member_can_submit_wallet_withdrawal_request(self):
        from apps.accounts.models import MemberKYC, MemberKYCStatus, MemberKYCTier
        from apps.security.pin_service import PinService, PinType

        MemberKYC.objects.create(
            user=self.member,
            chama=self.chama,
            id_number="12345678",
            status=MemberKYCStatus.APPROVED,
            kyc_tier=MemberKYCTier.TIER_2,
        )
        PinService.set_pin(self.member, "1234", PinType.WITHDRAWAL)

        Wallet.objects.create(
            owner_type=WalletOwnerType.USER,
            owner_id=self.member.id,
            available_balance=Decimal("2400.00"),
            locked_balance=Decimal("0.00"),
            currency="KES",
        )

        response = self.client.post(
            "/api/v1/finance/member-wallet/withdrawals",
            {
                "chama_id": str(self.chama.id),
                "amount": "700.00",
                "payment_method": "mpesa",
                "phone": "+254711000020",
                "pin": "1234",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()["data"]
        self.assertEqual(payload["state"], "pending_processing")
        self.assertEqual(payload["transaction"]["type"], "wallet_withdrawal")
        self.assertEqual(payload["transaction"]["status"], "pending")
        wallet = Wallet.objects.get(owner_type=WalletOwnerType.USER, owner_id=self.member.id)
        self.assertEqual(wallet.available_balance, Decimal("1700.00"))
        self.assertEqual(wallet.locked_balance, Decimal("700.00"))

    def test_member_can_transfer_wallet_balance_to_another_member(self):
        Wallet.objects.create(
            owner_type=WalletOwnerType.USER,
            owner_id=self.member.id,
            available_balance=Decimal("1200.00"),
            locked_balance=Decimal("0.00"),
            currency="KES",
        )
        Wallet.objects.create(
            owner_type=WalletOwnerType.USER,
            owner_id=self.other_member.id,
            available_balance=Decimal("300.00"),
            locked_balance=Decimal("0.00"),
            currency="KES",
        )

        response = self.client.post(
            "/api/v1/finance/member-wallet/transfers",
            {
                "chama_id": str(self.chama.id),
                "recipient_member_id": str(self.other_member.id),
                "amount": "250.00",
                "note": "Thanks",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()["data"]
        self.assertTrue(payload["transaction_ref"].startswith("ledger_"))

        sender_wallet = Wallet.objects.get(owner_type=WalletOwnerType.USER, owner_id=self.member.id)
        recipient_wallet = Wallet.objects.get(owner_type=WalletOwnerType.USER, owner_id=self.other_member.id)
        self.assertEqual(sender_wallet.available_balance, Decimal("950.00"))
        self.assertEqual(recipient_wallet.available_balance, Decimal("550.00"))

        detail = self.client.get(
            f"/api/v1/finance/member-wallet/transactions/{payload['transaction_ref']}",
            {"chama_id": str(self.chama.id)},
        )
        self.assertEqual(detail.status_code, 200)
        transaction = detail.json()["data"]["transaction"]
        self.assertEqual(transaction["type"], "wallet_transfer")
        self.assertEqual(transaction["direction"], "outflow")

    def test_member_can_send_contribution_from_wallet(self):
        Wallet.objects.create(
            owner_type=WalletOwnerType.USER,
            owner_id=self.member.id,
            available_balance=Decimal("2000.00"),
            locked_balance=Decimal("0.00"),
            currency="KES",
        )
        contribution_type = ContributionType.objects.create(
            chama=self.chama,
            name="Monthly",
            frequency="monthly",
            default_amount=Decimal("500.00"),
            is_active=True,
            created_by=self.admin,
            updated_by=self.admin,
        )

        response = self.client.post(
            "/api/v1/finance/member-wallet/contributions",
            {
                "chama_id": str(self.chama.id),
                "contribution_type_id": str(contribution_type.id),
                "amount": "500.00",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()["data"]
        self.assertTrue(payload["intent_id"])
        self.assertEqual(payload["transaction"]["type"], "contribution_payment")
        self.assertTrue(payload["transaction"]["receipt_available"])

        wallet = Wallet.objects.get(owner_type=WalletOwnerType.USER, owner_id=self.member.id)
        self.assertEqual(wallet.available_balance, Decimal("1500.00"))

    def test_member_can_create_bank_wallet_deposit_and_sees_instructions(self):
        from apps.chama.models import PaymentProviderConfig

        PaymentProviderConfig.objects.create(
            chama=self.chama,
            provider_type="bank",
            bank_name="Test Bank",
            bank_account_number="1234567890",
            is_active=True,
            created_by=self.admin,
            updated_by=self.admin,
        )

        response = self.client.post(
            "/api/v1/finance/member-wallet/deposits",
            {
                "chama_id": str(self.chama.id),
                "amount": "800.00",
                "payment_method": "bank",
                "phone": "",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()["data"]
        self.assertEqual(payload["state"], "pending")
        self.assertEqual(payload["instructions"]["type"], "bank_transfer")
        self.assertEqual(payload["instructions"]["bank_name"], "Test Bank")
        self.assertTrue(payload["instructions"]["transfer_reference"])

    @override_settings(MPESA_USE_STUB=True)
    def test_member_wallet_withdrawal_refresh_can_complete_in_stub_mode(self):
        from apps.accounts.models import MemberKYC, MemberKYCStatus, MemberKYCTier
        from apps.security.pin_service import PinService, PinType

        MemberKYC.objects.create(
            user=self.member,
            chama=self.chama,
            id_number="12345678",
            status=MemberKYCStatus.APPROVED,
            kyc_tier=MemberKYCTier.TIER_2,
        )
        PinService.set_pin(self.member, "1234", PinType.WITHDRAWAL)

        Wallet.objects.create(
            owner_type=WalletOwnerType.USER,
            owner_id=self.member.id,
            available_balance=Decimal("2400.00"),
            locked_balance=Decimal("0.00"),
            currency="KES",
        )

        response = self.client.post(
            "/api/v1/finance/member-wallet/withdrawals",
            {
                "chama_id": str(self.chama.id),
                "amount": "700.00",
                "payment_method": "mpesa",
                "phone": "+254711000020",
                "pin": "1234",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        intent_id = response.json()["data"]["intent_id"]

        refresh_response = self.client.post(
            f"/api/v1/finance/member-wallet/withdrawals/{intent_id}",
            {"chama_id": str(self.chama.id)},
            format="json",
        )
        self.assertEqual(refresh_response.status_code, 200)
        refreshed = refresh_response.json()["data"]
        self.assertEqual(refreshed["state"], "approved_completed")

        wallet = Wallet.objects.get(owner_type=WalletOwnerType.USER, owner_id=self.member.id)
        self.assertEqual(wallet.locked_balance, Decimal("0.00"))


class MemberContributionWorkspaceApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.client = APIClient()
        self.member = user_model.objects.create_user(
            phone="+254711010101",
            full_name="Contribution Member",
            password="password123",
        )
        self.other_member = user_model.objects.create_user(
            phone="+254711010102",
            full_name="Other Member",
            password="password123",
        )
        self.admin = user_model.objects.create_user(
            phone="+254711010103",
            full_name="Contribution Admin",
            password="password123",
        )
        self.chama = Chama.objects.create(name="Contribution Workspace Chama")
        ChamaContributionSetting.objects.create(
            chama=self.chama,
            contribution_amount=Decimal("1000.00"),
            contribution_frequency="monthly",
            due_day=10,
            grace_period_days=3,
            late_fine_amount=Decimal("200.00"),
        )
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
            user=self.other_member,
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
        self.monthly_type = ContributionType.objects.create(
            chama=self.chama,
            name="Monthly",
            frequency="monthly",
            default_amount=Decimal("2000.00"),
            is_active=True,
        )
        self.welfare_type = ContributionType.objects.create(
            chama=self.chama,
            name="Welfare",
            frequency="monthly",
            default_amount=Decimal("500.00"),
            is_active=True,
        )
        self.member_contribution = Contribution.objects.create(
            chama=self.chama,
            member=self.member,
            contribution_type=self.monthly_type,
            amount=Decimal("1200.00"),
            date_paid=timezone.localdate() - timedelta(days=2),
            method="mpesa",
            receipt_code="RCP-MEMBER-WORKSPACE-1",
            recorded_by=self.admin,
        )
        Contribution.objects.create(
            chama=self.chama,
            member=self.other_member,
            contribution_type=self.monthly_type,
            amount=Decimal("500.00"),
            date_paid=timezone.localdate() - timedelta(days=1),
            method="mpesa",
            receipt_code="RCP-MEMBER-WORKSPACE-OTHER",
            recorded_by=self.admin,
        )
        Penalty.objects.create(
            chama=self.chama,
            member=self.member,
            amount=Decimal("300.00"),
            reason="Late contribution",
            due_date=timezone.localdate() + timedelta(days=5),
            status=PenaltyStatus.UNPAID,
            issued_by=self.admin,
        )
        PaymentIntent.objects.create(
            chama=self.chama,
            user=self.member,
            amount=Decimal("800.00"),
            currency="KES",
            purpose=PaymentPurpose.CONTRIBUTION,
            description="Pending monthly contribution",
            payment_method=PaymentMethod.MPESA,
            provider="safaricom",
            provider_intent_id="provider-intent-1",
            status=PaymentStatus.PENDING,
            idempotency_key="member-workspace-pending-intent",
            reference="PAY-WORKSPACE-PENDING",
        )

    def test_member_workspace_returns_member_scoped_summary(self):
        self.client.force_authenticate(self.member)

        response = self.client.get(
            f"/api/v1/finance/member-contributions/workspace?chama_id={self.chama.id}"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("summary", payload)
        self.assertIn("obligations", payload)
        self.assertIn("pending_payment", payload)
        self.assertEqual(payload["recent_contributions"][0]["id"], str(self.member_contribution.id))
        self.assertGreaterEqual(len(payload["obligations"]), 2)
        self.assertEqual(payload["pending_payment"]["reference"], "PAY-WORKSPACE-PENDING")
        self.assertEqual(payload["penalties"]["count"], 1)

    def test_member_contribution_detail_blocks_other_members(self):
        self.client.force_authenticate(self.other_member)

        response = self.client.get(f"/api/v1/finance/contributions/{self.member_contribution.id}/")

        self.assertEqual(response.status_code, 403)

    def test_member_penalties_endpoint_returns_member_scoped_penalties(self):
        self.client.force_authenticate(self.member)

        response = self.client.get(
            f"/api/v1/finance/member-contributions/penalties?chama_id={self.chama.id}"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["reason"], "Late contribution")
        self.assertEqual(payload[0]["outstanding_amount"], "300.00")


class MemberLoanWorkspaceApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.client = APIClient()
        self.member = user_model.objects.create_user(
            phone="+254711020101",
            full_name="Loan Workspace Member",
            password="password123",
        )
        self.admin = user_model.objects.create_user(
            phone="+254711020102",
            full_name="Loan Workspace Admin",
            password="password123",
        )
        self.other_member = user_model.objects.create_user(
            phone="+254711020103",
            full_name="Other Loan Member",
            password="password123",
        )
        self.chama = Chama.objects.create(name="Loan Workspace Chama", currency="KES")
        Membership.objects.create(
            user=self.member,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            joined_at=timezone.now() - timedelta(days=200),
        )
        Membership.objects.create(
            user=self.admin,
            chama=self.chama,
            role=MembershipRole.CHAMA_ADMIN,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            joined_at=timezone.now() - timedelta(days=250),
        )
        Membership.objects.create(
            user=self.other_member,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            joined_at=timezone.now() - timedelta(days=220),
        )
        LoanPolicy.objects.create(
            chama=self.chama,
            min_membership_days=90,
            min_contribution_cycles=0,
            loan_cap_multiplier=Decimal("2.00"),
            reserve_liquidity_amount=Decimal("0.00"),
            default_after_days_overdue=30,
            recovery_review_after_days_overdue=14,
        )
        self.loan_product = LoanProduct.objects.create(
            chama=self.chama,
            name="Member Loan",
            is_active=True,
            is_default=True,
            max_loan_amount=Decimal("60000.00"),
            contribution_multiple=Decimal("3.00"),
            interest_rate=Decimal("12.00"),
            min_duration_months=3,
            max_duration_months=12,
            grace_period_days=5,
            late_penalty_type="fixed",
            late_penalty_value=Decimal("250.00"),
            minimum_membership_months=0,
            minimum_contribution_months=0,
            block_if_unpaid_penalties=True,
            block_if_overdue_loans=True,
        )
        self.contribution_type = ContributionType.objects.create(
            chama=self.chama,
            name="Monthly Savings",
            default_amount=Decimal("1000.00"),
            frequency="monthly",
        )
        Contribution.objects.create(
            chama=self.chama,
            member=self.member,
            contribution_type=self.contribution_type,
            amount=Decimal("20000.00"),
            date_paid=timezone.localdate() - timedelta(days=20),
            receipt_code="LOAN-WS-CONTRIB-1",
            recorded_by=self.admin,
        )
        cash_account = FinanceService._get_or_create_account(self.chama, "cash")
        LedgerEntry.objects.create(
            chama=self.chama,
            account=cash_account,
            entry_type=LedgerEntryType.CONTRIBUTION,
            direction=LedgerDirection.DEBIT,
            amount=Decimal("90000.00"),
            debit=Decimal("90000.00"),
            credit=Decimal("0.00"),
            currency="KES",
            status="success",
            idempotency_key="member-loan-workspace-liquidity",
            narration="Seed liquidity",
            created_by=self.admin,
        )
        self.application = LoanApplication.objects.create(
            chama=self.chama,
            member=self.member,
            loan_product=self.loan_product,
            requested_amount=Decimal("15000.00"),
            requested_term_months=6,
            purpose="School fees",
            status=LoanApplicationStatus.SUBMITTED,
            eligibility_status="eligible",
            recommended_max_amount=Decimal("40000.00"),
            created_by=self.member,
            updated_by=self.member,
        )
        self.loan = Loan.objects.create(
            chama=self.chama,
            member=self.member,
            loan_product=self.loan_product,
            principal=Decimal("12000.00"),
            outstanding_principal=Decimal("8000.00"),
            outstanding_interest=Decimal("500.00"),
            outstanding_penalty=Decimal("0.00"),
            total_due=Decimal("8500.00"),
            interest_type=self.loan_product.interest_type,
            interest_rate=Decimal("12.00"),
            duration_months=6,
            grace_period_days=5,
            late_penalty_type="fixed",
            late_penalty_value=Decimal("250.00"),
            status=LoanStatus.ACTIVE,
            created_by=self.admin,
            updated_by=self.admin,
        )
        InstallmentSchedule.objects.create(
            loan=self.loan,
            due_date=timezone.localdate() + timedelta(days=7),
            expected_amount=Decimal("2200.00"),
            expected_principal=Decimal("2000.00"),
            expected_interest=Decimal("200.00"),
            expected_penalty=Decimal("0.00"),
            status=InstallmentStatus.DUE,
            created_by=self.admin,
            updated_by=self.admin,
        )

    def test_member_loan_workspace_returns_member_summary(self):
        self.client.force_authenticate(self.member)

        response = self.client.get(
            f"/api/v1/finance/member-loans/workspace?chama_id={self.chama.id}"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("summary", payload)
        self.assertIn("eligibility", payload)
        self.assertEqual(payload["summary"]["loan_state"], "active")
        self.assertEqual(payload["active_application"]["id"], str(self.application.id))
        self.assertEqual(payload["active_loan"]["id"], str(self.loan.id))
        self.assertEqual(
            payload["active_loan"]["next_installment"]["expected_amount"],
            "2200.00",
        )
        self.assertEqual(payload["loan_rules"]["default_product"]["name"], "Member Loan")
        self.assertIn("policy_checks", payload["eligibility"])
        self.assertIn("policy_summary", payload["eligibility"])
        self.assertIn("approval_requirements", payload["eligibility"])

    def test_member_loan_workspace_requires_membership(self):
        outsider = get_user_model().objects.create_user(
            phone="+254711020199",
            full_name="Loan Outsider",
            password="password123",
        )
        self.client.force_authenticate(outsider)

        response = self.client.get(
            f"/api/v1/finance/member-loans/workspace?chama_id={self.chama.id}"
        )

        self.assertEqual(response.status_code, 403)


class ContributionAutomationTaskTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(
            phone="+254711030001",
            full_name="Contribution Admin",
            password="password123",
        )
        self.member = user_model.objects.create_user(
            phone="+254711030002",
            full_name="Contribution Member",
            password="password123",
        )
        self.chama = Chama.objects.create(name="Contribution Automation Chama")
        Membership.objects.create(
            user=self.admin,
            chama=self.chama,
            role=MembershipRole.CHAMA_ADMIN,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            joined_at=timezone.now() - timedelta(days=180),
        )
        Membership.objects.create(
            user=self.member,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            joined_at=timezone.now() - timedelta(days=90),
        )
        ChamaContributionSetting.objects.create(
            chama=self.chama,
            contribution_amount=Decimal("1000.00"),
            contribution_frequency="monthly",
            due_day=10,
            grace_period_days=2,
            late_fine_amount=Decimal("150.00"),
        )

    @patch("apps.notifications.services.NotificationService.send_notification")
    def test_contribution_sweep_uses_chama_settings_for_penalty_fallback(self, send_notification_mock):
        schedule = ContributionSchedule.objects.create(
            chama=self.chama,
            member=self.member,
            amount=Decimal("1000.00"),
            scheduled_date=timezone.localdate() - timedelta(days=3),
            status=ContributionScheduleStatus.PENDING,
            is_active=True,
            created_by=self.admin,
            updated_by=self.admin,
        )

        result = contributions_schedule_automation_sweep()
        payload = result.get("result", result)

        self.assertEqual(payload["marked_missed"], 1)
        self.assertEqual(payload["penalties_created"], 1)
        schedule.refresh_from_db()
        self.assertEqual(schedule.status, ContributionScheduleStatus.MISSED)
        penalty = Penalty.objects.get(chama=self.chama, member=self.member)
        self.assertEqual(penalty.amount, Decimal("150.00"))
        self.assertGreaterEqual(send_notification_mock.call_count, 2)


class LoanAndContributionWorkflowTaskTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(
            phone="+254711040001",
            full_name="Workflow Admin",
            password="password123",
        )
        self.member = user_model.objects.create_user(
            phone="+254711040002",
            full_name="Workflow Member",
            password="password123",
        )
        self.chama = Chama.objects.create(name="Workflow Chama")
        Membership.objects.create(
            user=self.admin,
            chama=self.chama,
            role=MembershipRole.CHAMA_ADMIN,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            joined_at=timezone.now() - timedelta(days=180),
        )
        Membership.objects.create(
            user=self.member,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            joined_at=timezone.now() - timedelta(days=180),
        )

    @patch("apps.notifications.services.NotificationService.send_notification")
    def test_loans_auto_close_sends_member_notification(self, send_notification_mock):
        loan = Loan.objects.create(
            chama=self.chama,
            member=self.member,
            principal=Decimal("1000.00"),
            interest_rate=Decimal("10.00"),
            duration_months=6,
            status=LoanStatus.ACTIVE,
        )
        loan.repayments.create(
            amount=Decimal("1000.00"),
            date_paid=timezone.localdate(),
            method="mpesa",
            receipt_code="RCPT-RPY-001",
            created_by=self.member,
            updated_by=self.member,
        )

        result = loans_auto_close_when_paid()
        payload = result.get("result", result)
        self.assertGreaterEqual(payload.get("cleared", 0), 1)
        loan.refresh_from_db()
        self.assertEqual(loan.status, LoanStatus.PAID)
        self.assertGreaterEqual(send_notification_mock.call_count, 1)

    @patch("apps.notifications.services.NotificationService.send_notification")
    def test_contribution_cycle_completion_notifies_admin(self, send_notification_mock):
        ContributionSchedule.objects.create(
            chama=self.chama,
            member=self.member,
            amount=Decimal("500.00"),
            scheduled_date=timezone.localdate(),
            frequency="monthly",
            status=ContributionScheduleStatus.PAID,
            is_active=True,
            created_by=self.admin,
            updated_by=self.admin,
        )

        result = contributions_cycle_completion_check()
        payload = result.get("result", result)
        self.assertGreaterEqual(payload.get("completed_cycles", 0), 1)
        self.assertGreaterEqual(send_notification_mock.call_count, 1)
