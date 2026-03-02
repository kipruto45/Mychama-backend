from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.chama.models import Chama
from apps.finance.models import (
    InstallmentSchedule,
    InstallmentStatus,
    LedgerDirection,
    LedgerEntry,
    LedgerEntryType,
    Loan,
    LoanStatus,
    Penalty,
    PenaltyStatus,
)
from apps.finance.summary import get_chama_financial_snapshot


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
        self.assertEqual(snapshot.cash_in_total, Decimal("120.00"))
        self.assertEqual(snapshot.cash_out_total, Decimal("0.00"))
