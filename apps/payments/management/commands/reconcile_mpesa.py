"""
Reconcile M-Pesa transactions command.

Reconciles M-Pesa transactions with ledger entries to ensure:
1. All successful payments have corresponding ledger entries
2. All ledger entries have valid payment references
3. Identifies and reports discrepancies
"""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.chama.models import Chama
from apps.finance.models import (
    LedgerDirection,
    LedgerEntry,
    LedgerEntryType,
    LedgerStatus,
)
from apps.payments.models import (
    MpesaB2CStatus,
    MpesaB2CPayout,
    MpesaC2BTransaction,
    MpesaTransaction,
    MpesaTransactionStatus,
    PaymentIntent,
    PaymentIntentStatus,
    PaymentIntentType,
)


class Command(BaseCommand):
    help = "Reconcile M-Pesa transactions with ledger entries"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=7,
            help="Number of days to reconcile (default: 7)",
        )
        parser.add_argument(
            "--chama-id",
            type=str,
            help="Specific chama ID to reconcile",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show discrepancies without fixing",
        )
        parser.add_argument(
            "--fix",
            action="store_true",
            help="Automatically fix discrepancies",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Show detailed output",
        )

    def handle(self, *args, **options):
        days = options["days"]
        chama_id = options["chama_id"]
        dry_run = options["dry_run"]
        fix = options["fix"]
        verbose = options["verbose"]

        if dry_run and fix:
            raise CommandError("Cannot use --dry-run with --fix")

        # Calculate date range
        end_date = timezone.now()
        start_date = end_date - timedelta(days=days)

        self.stdout.write(f"Reconciling M-Pesa transactions from {start_date.date()} to {end_date.date()}")

        results = {
            "c2b_processed": 0,
            "c2b_missing_ledger": 0,
            "stk_processed": 0,
            "stk_missing_ledger": 0,
            "payouts_processed": 0,
            "payouts_missing_ledger": 0,
            "ledger_missing_payment": 0,
            "discrepancies": [],
        }

        # Get chama filter
        chama_filter = {}
        if chama_id:
            chama_filter["chama_id"] = chama_id

        # 1. Reconcile C2B Transactions
        self.stdout.write("\n--- Reconciling C2B Transactions ---")
        c2b_transactions = MpesaC2BTransaction.objects.filter(
            created_at__gte=start_date,
            created_at__lte=end_date,
            processing_status="POSTED",
            **chama_filter,
        )

        for txn in c2b_transactions:
            results["c2b_processed"] += 1
            
            # Check if there's a corresponding ledger entry
            has_ledger = LedgerEntry.objects.filter(
                chama=txn.chama,
                provider="mpesa",
                provider_reference=txn.receipt_number,
            ).exists()

            if not has_ledger:
                results["c2b_missing_ledger"] += 1
                results["discrepancies"].append({
                    "type": "C2B_MISSING_LEDGER",
                    "chama": str(txn.chama_id),
                    "amount": str(txn.amount),
                    "receipt": txn.receipt_number,
                })
                
                if fix and not dry_run:
                    self._create_ledger_from_c2b(txn)

        # 2. Reconcile STK Transactions
        self.stdout.write("--- Reconciling STK Transactions ---")
        stk_transactions = MpesaTransaction.objects.filter(
            created_at__gte=start_date,
            created_at__lte=end_date,
            status=MpesaTransactionStatus.SUCCESS,
            **chama_filter,
        )

        for txn in stk_transactions:
            results["stk_processed"] += 1
            
            has_ledger = LedgerEntry.objects.filter(
                chama=txn.chama,
                provider="mpesa",
                provider_reference=txn.receipt_number,
            ).exists()

            if not has_ledger:
                results["stk_missing_ledger"] += 1
                results["discrepancies"].append({
                    "type": "STK_MISSING_LEDGER",
                    "chama": str(txn.chama_id),
                    "amount": str(txn.amount),
                    "receipt": txn.receipt_number,
                })
                
                if fix and not dry_run:
                    self._create_ledger_from_stk(txn)

        # 3. Reconcile B2C Payouts
        self.stdout.write("--- Reconciling B2C Payouts ---")
        payouts = MpesaB2CPayout.objects.filter(
            created_at__gte=start_date,
            created_at__lte=end_date,
            status=MpesaB2CStatus.SUCCESS,
            **chama_filter,
        )

        for payout in payouts:
            results["payouts_processed"] += 1
            
            has_ledger = LedgerEntry.objects.filter(
                chama=payout.chama,
                provider="mpesa",
                provider_reference=payout.receipt_number,
            ).exists()

            if not has_ledger:
                results["payouts_missing_ledger"] += 1
                results["discrepancies"].append({
                    "type": "B2C_MISSING_LEDGER",
                    "chama": str(payout.chama_id),
                    "amount": str(payout.amount),
                    "receipt": payout.receipt_number,
                })

        # 4. Check for ledger entries without payment references
        self.stdout.write("--- Checking Ledger Entries ---")
        ledger_entries = LedgerEntry.objects.filter(
            created_at__gte=start_date,
            created_at__lte=end_date,
            provider="mpesa",
            provider_reference__isnull=False,
            **chama_filter,
        )

        for entry in ledger_entries:
            # Check if payment exists
            has_payment = (
                MpesaC2BTransaction.objects.filter(receipt_number=entry.provider_reference).exists()
                or MpesaTransaction.objects.filter(receipt_number=entry.provider_reference).exists()
                or MpesaB2CPayout.objects.filter(receipt_number=entry.provider_reference).exists()
            )
            
            if not has_payment:
                results["ledger_missing_payment"] += 1
                results["discrepancies"].append({
                    "type": "LEDGER_MISSING_PAYMENT",
                    "chama": str(entry.chama_id),
                    "amount": str(entry.amount),
                    "reference": entry.provider_reference,
                })

        # Print summary
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("RECONCILIATION SUMMARY")
        self.stdout.write("=" * 60)
        
        self.stdout.write(f"\nC2B Transactions: {results['c2b_processed']} processed")
        self.stdout.write(f"  - Missing ledger: {results['c2b_missing_ledger']}")
        
        self.stdout.write(f"\nSTK Transactions: {results['stk_processed']} processed")
        self.stdout.write(f"  - Missing ledger: {results['stk_missing_ledger']}")
        
        self.stdout.write(f"\nB2C Payouts: {results['payouts_processed']} processed")
        self.stdout.write(f"  - Missing ledger: {results['payouts_missing_ledger']}")
        
        self.stdout.write(f"\nLedger Entries: {results['ledger_missing_payment']} missing payment reference")

        total_discrepancies = len(results["discrepancies"])
        
        if total_discrepancies > 0:
            self.stdout.write(self.style.WARNING(f"\n⚠️  Found {total_discrepancies} discrepancies"))
            
            if verbose:
                self.stdout.write("\n--- DISCREPANCIES ---")
                for disc in results["discrepancies"][:20]:  # Show first 20
                    self.stdout.write(f"  {disc['type']}: {disc}")
                if total_discrepancies > 20:
                    self.stdout.write(f"  ... and {total_discrepancies - 20} more")
            
            if fix and not dry_run:
                self.stdout.write(self.style.SUCCESS(f"\n✅ Fixed {total_discrepancies} discrepancies"))
            elif dry_run:
                self.stdout.write(self.style.WARNING("\n🔍 DRY RUN - No changes made"))
            else:
                self.stdout.write(self.style.WARNING("\n💡 Use --fix to automatically create missing ledger entries"))
        else:
            self.stdout.write(self.style.SUCCESS("\n✅ No discrepancies found"))

    def _create_ledger_from_c2b(self, txn: MpesaC2BTransaction):
        """Create a ledger entry from a C2B transaction."""
        with transaction.atomic():
            LedgerEntry.objects.create(
                chama=txn.chama,
                entry_type=LedgerEntryType.CONTRIBUTION,
                direction=LedgerDirection.CREDIT,
                amount=txn.amount,
                currency="KES",
                status=LedgerStatus.SUCCESS,
                provider="mpesa",
                provider_reference=txn.receipt_number,
                idempotency_key=f"c2b-{txn.id}",
                narration=f"C2B Payment - {txn.receipt_number}",
            )
            self.stdout.write(f"  Created ledger entry for C2B {txn.receipt_number}")

    def _create_ledger_from_stk(self, txn: MpesaTransaction):
        """Create a ledger entry from an STK transaction."""
        purpose_map = {
            "contribution": LedgerEntryType.CONTRIBUTION,
            "repayment": LedgerEntryType.LOAN_REPAYMENT,
            "penalty": LedgerEntryType.PENALTY,
        }
        
        entry_type = purpose_map.get(txn.purpose, LedgerEntryType.CONTRIBUTION)
        
        with transaction.atomic():
            LedgerEntry.objects.create(
                chama=txn.chama,
                entry_type=entry_type,
                direction=LedgerDirection.CREDIT,
                amount=txn.amount,
                currency="KES",
                status=LedgerStatus.SUCCESS,
                provider="mpesa",
                provider_reference=txn.receipt_number,
                idempotency_key=f"stk-{txn.id}",
                narration=f"STK Payment - {txn.receipt_number}",
            )
            self.stdout.write(f"  Created ledger entry for STK {txn.receipt_number}")
