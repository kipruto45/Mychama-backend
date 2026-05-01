"""
Finance and Ledger Service

Manages ledger-based accounting with immutable postings and snapshots.
"""

import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama

logger = logging.getLogger(__name__)


class LedgerService:
    """Service for ledger-based accounting."""

    @staticmethod
    @transaction.atomic
    def create_journal_entry(
        chama: Chama,
        entry_type: str,
        description: str,
        reference: str = '',
        created_by: User = None,
    ) -> dict:
        """
        Create a journal entry.
        Returns journal entry details.
        """
        from apps.finance.models import JournalEntry

        journal_entry = JournalEntry.objects.create(
            chama=chama,
            entry_type=entry_type,
            description=description,
            reference=reference,
            created_by=created_by,
        )

        logger.info(
            f"Journal entry created: {entry_type} for {chama.name}"
        )

        return {
            'id': str(journal_entry.id),
            'entry_type': entry_type,
            'description': description,
            'reference': reference,
            'created_at': journal_entry.created_at.isoformat(),
        }

    @staticmethod
    @transaction.atomic
    def create_ledger_entry(
        journal_entry_id: str,
        account_id: str,
        entry_type: str,  # 'debit' or 'credit'
        amount: float,
        description: str = '',
    ) -> dict:
        """
        Create a ledger entry.
        Returns ledger entry details.
        """
        from apps.finance.models import Account, JournalEntry, LedgerEntry

        try:
            journal_entry = JournalEntry.objects.get(id=journal_entry_id)
            account = Account.objects.get(id=account_id)

            # Validate amount
            if amount <= 0:
                raise ValueError("Amount must be greater than 0")

            # Create ledger entry
            ledger_entry = LedgerEntry.objects.create(
                journal_entry=journal_entry,
                account=account,
                entry_type=entry_type,
                amount=Decimal(str(amount)),
                description=description,
            )

            # Update account balance
            if entry_type == 'debit':
                account.balance += Decimal(str(amount))
            else:  # credit
                account.balance -= Decimal(str(amount))

            account.save(update_fields=['balance', 'updated_at'])

            logger.info(
                f"Ledger entry created: {entry_type} {amount} for account {account.name}"
            )

            return {
                'id': str(ledger_entry.id),
                'account_id': str(account.id),
                'account_name': account.name,
                'entry_type': entry_type,
                'amount': float(amount),
                'balance': float(account.balance),
            }

        except JournalEntry.DoesNotExist:
            raise ValueError("Journal entry not found")
        except Account.DoesNotExist:
            raise ValueError("Account not found")

    @staticmethod
    @transaction.atomic
    def post_journal_entry(
        journal_entry_id: str,
        ledger_entries: list[dict],
    ) -> tuple[bool, str]:
        """
        Post a journal entry with ledger entries.
        Returns (success, message).
        """
        from apps.finance.models import JournalEntry

        try:
            journal_entry = JournalEntry.objects.get(id=journal_entry_id)

            if journal_entry.is_posted:
                return False, "Journal entry is already posted"

            # Validate debits and credits balance
            total_debits = sum(
                entry['amount'] for entry in ledger_entries
                if entry['entry_type'] == 'debit'
            )
            total_credits = sum(
                entry['amount'] for entry in ledger_entries
                if entry['entry_type'] == 'credit'
            )

            if abs(total_debits - total_credits) > 0.01:
                return False, "Debits and credits do not balance"

            # Create ledger entries
            for entry_data in ledger_entries:
                LedgerService.create_ledger_entry(
                    journal_entry_id=journal_entry_id,
                    account_id=entry_data['account_id'],
                    entry_type=entry_data['entry_type'],
                    amount=entry_data['amount'],
                    description=entry_data.get('description', ''),
                )

            # Mark journal entry as posted
            journal_entry.is_posted = True
            journal_entry.posted_at = timezone.now()
            journal_entry.save(update_fields=['is_posted', 'posted_at', 'updated_at'])

            logger.info(f"Journal entry posted: {journal_entry_id}")

            return True, "Journal entry posted"

        except JournalEntry.DoesNotExist:
            return False, "Journal entry not found"

    @staticmethod
    def get_account_balance(account_id: str) -> dict:
        """
        Get account balance and details.
        """
        from apps.finance.models import Account

        try:
            account = Account.objects.get(id=account_id)

            return {
                'id': str(account.id),
                'name': account.name,
                'account_type': account.account_type,
                'balance': float(account.balance),
                'currency': account.currency,
                'is_active': account.is_active,
            }

        except Account.DoesNotExist:
            return None

    @staticmethod
    def get_chama_accounts(chama: Chama) -> list[dict]:
        """
        Get all accounts for a chama.
        """
        from apps.finance.models import Account

        accounts = Account.objects.filter(chama=chama, is_active=True)

        return [
            {
                'id': str(account.id),
                'name': account.name,
                'account_type': account.account_type,
                'balance': float(account.balance),
                'currency': account.currency,
            }
            for account in accounts
        ]

    @staticmethod
    def get_ledger_entries(
        account_id: str = None,
        journal_entry_id: str = None,
        date_from: timezone.datetime = None,
        date_to: timezone.datetime = None,
    ) -> list[dict]:
        """
        Get ledger entries with filtering.
        """
        from apps.finance.models import LedgerEntry

        queryset = LedgerEntry.objects.all()

        if account_id:
            queryset = queryset.filter(account_id=account_id)

        if journal_entry_id:
            queryset = queryset.filter(journal_entry_id=journal_entry_id)

        if date_from:
            queryset = queryset.filter(created_at__gte=date_from)

        if date_to:
            queryset = queryset.filter(created_at__lte=date_to)

        entries = queryset.order_by('-created_at')

        return [
            {
                'id': str(entry.id),
                'journal_entry_id': str(entry.journal_entry.id),
                'account_id': str(entry.account.id),
                'account_name': entry.account.name,
                'entry_type': entry.entry_type,
                'amount': float(entry.amount),
                'description': entry.description,
                'created_at': entry.created_at.isoformat(),
            }
            for entry in entries
        ]

    @staticmethod
    def get_trial_balance(chama: Chama) -> dict:
        """
        Get trial balance for a chama.
        """
        from django.db.models import Sum

        from apps.finance.models import Account

        accounts = Account.objects.filter(chama=chama, is_active=True)

        trial_balance = []
        total_debits = Decimal('0')
        total_credits = Decimal('0')

        for account in accounts:
            # Calculate balance from ledger entries
            from apps.finance.models import LedgerEntry
            ledger_entries = LedgerEntry.objects.filter(account=account)

            debits = ledger_entries.filter(entry_type='debit').aggregate(
                total=Sum('amount')
            )['total'] or Decimal('0')

            credits = ledger_entries.filter(entry_type='credit').aggregate(
                total=Sum('amount')
            )['total'] or Decimal('0')

            balance = debits - credits

            trial_balance.append({
                'account_id': str(account.id),
                'account_name': account.name,
                'account_type': account.account_type,
                'debit': float(debits) if balance > 0 else 0,
                'credit': float(abs(credits)) if balance < 0 else 0,
                'balance': float(balance),
            })

            if balance > 0:
                total_debits += balance
            else:
                total_credits += abs(balance)

        return {
            'accounts': trial_balance,
            'total_debits': float(total_debits),
            'total_credits': float(total_credits),
            'is_balanced': abs(total_debits - total_credits) < 0.01,
        }

    @staticmethod
    def create_financial_snapshot(chama: Chama) -> dict:
        """
        Create a financial snapshot for a chama.
        """
        from django.db.models import Sum

        from apps.finance.models import Account, FinancialSnapshot

        # Get all accounts
        accounts = Account.objects.filter(chama=chama, is_active=True)

        # Calculate totals
        total_balance = accounts.aggregate(
            total=Sum('balance')
        )['total'] or Decimal('0')

        # Get contribution totals
        from apps.finance.models import Contribution
        contributions = Contribution.objects.filter(
            membership__chama=chama,
        ).aggregate(
            total=Sum('amount'),
            paid=Sum('amount_paid'),
        )

        # Get loan totals
        from apps.finance.models import Loan
        loans = Loan.objects.filter(chama=chama).aggregate(
            total_borrowed=Sum('principal_amount'),
            total_repaid=Sum('amount_repaid'),
        )

        # Create snapshot
        snapshot = FinancialSnapshot.objects.create(
            chama=chama,
            total_balance=total_balance,
            total_contributions=contributions['total'] or Decimal('0'),
            total_contributions_paid=contributions['paid'] or Decimal('0'),
            total_loans_borrowed=loans['total_borrowed'] or Decimal('0'),
            total_loans_repaid=loans['total_repaid'] or Decimal('0'),
            snapshot_date=timezone.now(),
        )

        logger.info(f"Financial snapshot created for {chama.name}")

        return {
            'id': str(snapshot.id),
            'total_balance': float(snapshot.total_balance),
            'total_contributions': float(snapshot.total_contributions),
            'total_contributions_paid': float(snapshot.total_contributions_paid),
            'total_loans_borrowed': float(snapshot.total_loans_borrowed),
            'total_loans_repaid': float(snapshot.total_loans_repaid),
            'snapshot_date': snapshot.snapshot_date.isoformat(),
        }

    @staticmethod
    def get_financial_snapshots(chama: Chama) -> list[dict]:
        """
        Get financial snapshots for a chama.
        """
        from apps.finance.models import FinancialSnapshot

        snapshots = FinancialSnapshot.objects.filter(chama=chama).order_by('-snapshot_date')

        return [
            {
                'id': str(snapshot.id),
                'total_balance': float(snapshot.total_balance),
                'total_contributions': float(snapshot.total_contributions),
                'total_contributions_paid': float(snapshot.total_contributions_paid),
                'total_loans_borrowed': float(snapshot.total_loans_borrowed),
                'total_loans_repaid': float(snapshot.total_loans_repaid),
                'snapshot_date': snapshot.snapshot_date.isoformat(),
            }
            for snapshot in snapshots
        ]

    @staticmethod
    @transaction.atomic
    def post_payout(
        *,
        chama: Chama,
        payout_id: str,
        amount: Decimal,
        created_by: User | None = None,
    ):
        """
        Post a payout to a member wallet (chama wallet -> member wallet).

        Returns the debit-side `LedgerEntry` for tracking.
        """
        from apps.finance.models import (
            LedgerDirection,
            LedgerEntry,
            LedgerEntryType,
            LedgerStatus,
            Wallet,
            WalletOwnerType,
        )

        from apps.payouts.models import Payout

        payout = (
            Payout.objects.select_related("member__user", "chama")
            .select_for_update()
            .filter(id=payout_id, chama=chama)
            .first()
        )
        if not payout:
            raise ValueError("Payout not found.")

        recipient = payout.member.user
        amount = Decimal(str(amount))
        if amount <= Decimal("0.00"):
            raise ValueError("Amount must be greater than zero.")

        operation_key = f"payout:{payout.id}"
        debit_key = f"{operation_key}:debit"
        credit_key = f"{operation_key}:credit"

        existing = LedgerEntry.objects.filter(chama=chama, idempotency_key=debit_key).first()
        if existing:
            return existing

        chama_wallet, _ = Wallet.objects.select_for_update().get_or_create(
            owner_type=WalletOwnerType.CHAMA,
            owner_id=str(chama.id),
            defaults={
                "available_balance": Decimal("0.00"),
                "locked_balance": Decimal("0.00"),
                "currency": payout.currency or "KES",
            },
        )
        member_wallet, _ = Wallet.objects.select_for_update().get_or_create(
            owner_type=WalletOwnerType.USER,
            owner_id=str(recipient.id),
            defaults={
                "available_balance": Decimal("0.00"),
                "locked_balance": Decimal("0.00"),
                "currency": payout.currency or "KES",
            },
        )

        if chama_wallet.available_balance < amount:
            raise ValueError("Insufficient chama wallet balance for payout.")

        chama_wallet.available_balance = Decimal(str(chama_wallet.available_balance)) - amount
        member_wallet.available_balance = Decimal(str(member_wallet.available_balance)) + amount
        chama_wallet.save(update_fields=["available_balance", "updated_at"])
        member_wallet.save(update_fields=["available_balance", "updated_at"])

        import uuid
        reference = f"PO-{uuid.uuid4().hex[:10].upper()}"
        actor = (
            created_by
            or getattr(payout, "chairperson_approved_by", None)
            or getattr(payout, "treasurer_reviewed_by", None)
            or None
        )
        zero = Decimal("0.00")
        target_label = f"Payout from {getattr(chama, 'name', 'chama')}"

        debit_entry = LedgerEntry.objects.create(
            wallet=chama_wallet,
            chama=chama,
            entry_type=LedgerEntryType.PAYOUT,
            direction=LedgerDirection.DEBIT,
            amount=amount,
            debit=amount,
            credit=zero,
            currency=payout.currency or chama_wallet.currency or "KES",
            status=LedgerStatus.SUCCESS,
            provider="internal",
            provider_reference=reference,
            idempotency_key=debit_key,
            narration=f"Payout to {getattr(recipient, 'full_name', 'member')}.",
            meta={
                "payout_id": str(payout.id),
                "member_id": str(getattr(recipient, "id", "")),
                "member_phone": str(getattr(recipient, "phone", "") or ""),
                "target_label": target_label,
            },
            created_by=actor,
            updated_by=actor,
        )
        LedgerEntry.objects.create(
            wallet=member_wallet,
            chama=chama,
            entry_type=LedgerEntryType.PAYOUT,
            direction=LedgerDirection.CREDIT,
            amount=amount,
            debit=zero,
            credit=amount,
            currency=payout.currency or member_wallet.currency or "KES",
            status=LedgerStatus.SUCCESS,
            provider="internal",
            provider_reference=reference,
            idempotency_key=credit_key,
            narration=target_label,
            meta={
                "payout_id": str(payout.id),
                "target_label": target_label,
                "payout_label": target_label,
            },
            created_by=actor,
            updated_by=actor,
        )

        return debit_entry
