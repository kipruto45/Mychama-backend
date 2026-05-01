"""
Service layer for wallet transfers, chama payments, and loan updates.
Implements business logic and ledger entry creation.
"""

from decimal import Decimal
from uuid import uuid4
from datetime import datetime, timedelta
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.conf import settings

from apps.finance.models import (
    WalletTransfer,
    WalletTransferStatus,
    ChamaPayment,
    ChamaPaymentStatus,
    LoanUpdateRequest,
    Wallet,
    WalletOwnerType,
    LedgerEntry,
    LedgerEntryType,
    LedgerDirection,
    LedgerStatus,
    JournalEntry,
    JournalEntrySource,
    Loan,
    LoanStatus,
    Contribution,
)
from apps.chama.models import Chama
from core.audit import create_audit_log


class WalletTransferService:
    """Service for handling member-to-member wallet transfers."""

    @staticmethod
    def request_transfer(chama_id: str, sender_id: str, recipient_id: str, amount: Decimal, description: str = "", reference: str = "") -> WalletTransfer:
        """
        Initiate a wallet transfer between two members.
        
        Args:
            chama_id: ID of the chama
            sender_id: ID of sender (member)
            recipient_id: ID of recipient (member)
            amount: Transfer amount
            description: Optional transfer description
            reference: Optional reference code
            
        Returns:
            WalletTransfer instance
            
        Raises:
            ValidationError if transfer cannot be created
        """
        with transaction.atomic():
            # Validate chama exists
            chama = Chama.objects.get(id=chama_id)
            
            # Get or create wallets
            sender_wallet = Wallet.objects.get_or_create(
                owner_type=WalletOwnerType.USER,
                owner_id=str(sender_id),
                defaults={"currency": "KES"},
            )[0]
            
            recipient_wallet = Wallet.objects.get_or_create(
                owner_type=WalletOwnerType.USER,
                owner_id=str(recipient_id),
                defaults={"currency": "KES"},
            )[0]
            
            # Validate sender has sufficient balance
            if sender_wallet.available_balance < amount:
                raise ValidationError(
                    f"Insufficient balance. Available: {sender_wallet.available_balance}, Requested: {amount}"
                )
            
            # Generate reference if not provided
            if not reference:
                reference = f"XFRT-{uuid4().hex[:12].upper()}"
            
            # Create transfer record
            transfer = WalletTransfer.objects.create(
                chama_id=chama_id,
                sender_id=sender_id,
                recipient_id=recipient_id,
                amount=amount,
                currency="KES",
                reference=reference,
                description=description,
                status=WalletTransferStatus.INITIATED,
            )
            
            return transfer

    @staticmethod
    def complete_transfer(transfer_id: str, actor=None) -> WalletTransfer:
        """
        Complete a wallet transfer by creating ledger entries.
        
        Args:
            transfer_id: ID of transfer to complete
            actor: User performing the action (for audit)
            
        Returns:
            Updated WalletTransfer instance
            
        Raises:
            ValidationError if transfer cannot be completed
        """
        transfer = WalletTransfer.objects.select_related("chama", "sender", "recipient").get(id=transfer_id)
        
        if transfer.status != WalletTransferStatus.INITIATED:
            raise ValidationError(f"Cannot complete transfer with status {transfer.status}")
        
        with transaction.atomic():
            # Get wallets
            sender_wallet = Wallet.objects.get(
                owner_type=WalletOwnerType.USER,
                owner_id=str(transfer.sender_id)
            )
            recipient_wallet = Wallet.objects.get(
                owner_type=WalletOwnerType.USER,
                owner_id=str(transfer.recipient_id)
            )
            
            # Verify balance again
            if sender_wallet.available_balance < transfer.amount:
                transfer.status = WalletTransferStatus.FAILED
                transfer.failure_reason = "Insufficient balance at completion"
                transfer.save()
                raise ValidationError("Insufficient balance for transfer")
            
            # Create journal entry
            idempotency_key = f"xfrt:{transfer.reference}"
            journal_entry = JournalEntry.objects.create(
                chama_id=transfer.chama_id,
                reference=transfer.reference,
                description=f"Wallet transfer from {transfer.sender.get_full_name()} to {transfer.recipient.get_full_name()}",
                source_type=JournalEntrySource.PAYMENT,
                source_id=transfer.id,
                created_by=actor,
                idempotency_key=idempotency_key,
            )
            
            # Create debit entry for sender
            debit_entry = LedgerEntry.objects.create(
                wallet=sender_wallet,
                chama_id=transfer.chama_id,
                journal_entry=journal_entry,
                entry_type=LedgerEntryType.WALLET_TRANSFER,
                direction=LedgerDirection.DEBIT,
                amount=transfer.amount,
                debit=transfer.amount,
                currency=transfer.currency,
                status=LedgerStatus.SUCCESS,
                provider="internal",
                idempotency_key=f"debit:{idempotency_key}",
                narration=f"Transfer out to {transfer.recipient.get_full_name()}",
                meta={"transfer_id": str(transfer.id)},
            )
            
            # Create credit entry for recipient
            credit_entry = LedgerEntry.objects.create(
                wallet=recipient_wallet,
                chama_id=transfer.chama_id,
                journal_entry=journal_entry,
                entry_type=LedgerEntryType.WALLET_TRANSFER,
                direction=LedgerDirection.CREDIT,
                amount=transfer.amount,
                credit=transfer.amount,
                currency=transfer.currency,
                status=LedgerStatus.SUCCESS,
                provider="internal",
                idempotency_key=f"credit:{idempotency_key}",
                narration=f"Transfer in from {transfer.sender.get_full_name()}",
                meta={"transfer_id": str(transfer.id)},
            )
            
            # Update transfer status
            transfer.status = WalletTransferStatus.SUCCESS
            transfer.completed_at = timezone.now()
            transfer.ledger_entry = debit_entry  # Link to debit entry
            transfer.save()
            
            # Update wallet balances
            sender_wallet.available_balance -= transfer.amount
            sender_wallet.save()
            recipient_wallet.available_balance += transfer.amount
            recipient_wallet.save()
            
            # Create audit log
            create_audit_log(
                action="wallet_transfer_completed",
                object_type="WalletTransfer",
                object_id=str(transfer.id),
                changes={
                    "sender": str(transfer.sender_id),
                    "recipient": str(transfer.recipient_id),
                    "amount": str(transfer.amount),
                },
                actor=actor,
            )
            
            return transfer


class ChamaPaymentService:
    """Service for handling member-to-chama wallet payments."""

    @staticmethod
    def request_payment(chama_id: str, member_id: str, amount: Decimal, contribution_type_id: str = "", description: str = "", reference: str = "") -> ChamaPayment:
        """
        Initiate a chama wallet payment/contribution.
        
        Args:
            chama_id: ID of the chama
            member_id: ID of member making payment
            amount: Payment amount
            contribution_type_id: Optional contribution type ID
            description: Optional payment description
            reference: Optional reference code
            
        Returns:
            ChamaPayment instance
        """
        with transaction.atomic():
            # Validate chama exists
            chama = Chama.objects.get(id=chama_id)
            
            # Get or create member wallet
            member_wallet = Wallet.objects.get_or_create(
                owner_type=WalletOwnerType.USER,
                owner_id=str(member_id),
                defaults={"currency": "KES"},
            )[0]
            
            # Get or create chama wallet
            chama_wallet = Wallet.objects.get_or_create(
                owner_type=WalletOwnerType.CHAMA,
                owner_id=str(chama_id),
                defaults={"currency": "KES"},
            )[0]
            
            # Validate member has sufficient balance
            if member_wallet.available_balance < amount:
                raise ValidationError(
                    f"Insufficient balance. Available: {member_wallet.available_balance}, Requested: {amount}"
                )
            
            # Generate reference if not provided
            if not reference:
                reference = f"CHPAY-{uuid4().hex[:12].upper()}"
            
            # Create payment record
            payment = ChamaPayment.objects.create(
                chama_id=chama_id,
                member_id=member_id,
                amount=amount,
                currency="KES",
                reference=reference,
                contribution_type_id=contribution_type_id or None,
                description=description,
                status=ChamaPaymentStatus.INITIATED,
            )
            
            return payment

    @staticmethod
    def complete_payment(payment_id: str, actor=None) -> ChamaPayment:
        """
        Complete a chama payment by creating ledger entries and contribution record.
        
        Args:
            payment_id: ID of payment to complete
            actor: User performing the action (for audit)
            
        Returns:
            Updated ChamaPayment instance
        """
        payment = ChamaPayment.objects.select_related("chama", "member", "contribution_type").get(id=payment_id)
        
        if payment.status != ChamaPaymentStatus.INITIATED:
            raise ValidationError(f"Cannot complete payment with status {payment.status}")
        
        with transaction.atomic():
            # Get wallets
            member_wallet = Wallet.objects.get(
                owner_type=WalletOwnerType.USER,
                owner_id=str(payment.member_id)
            )
            chama_wallet = Wallet.objects.get(
                owner_type=WalletOwnerType.CHAMA,
                owner_id=str(payment.chama_id)
            )
            
            # Verify balance again
            if member_wallet.available_balance < payment.amount:
                payment.status = ChamaPaymentStatus.FAILED
                payment.failure_reason = "Insufficient balance at completion"
                payment.save()
                raise ValidationError("Insufficient balance for payment")
            
            # Create journal entry
            idempotency_key = f"chpay:{payment.reference}"
            journal_entry = JournalEntry.objects.create(
                chama_id=payment.chama_id,
                reference=payment.reference,
                description=f"Wallet contribution from {payment.member.get_full_name()} to chama",
                source_type=JournalEntrySource.CONTRIBUTION,
                source_id=payment.id,
                created_by=actor,
                idempotency_key=idempotency_key,
            )
            
            # Create debit entry for member wallet
            debit_entry = LedgerEntry.objects.create(
                wallet=member_wallet,
                chama_id=payment.chama_id,
                journal_entry=journal_entry,
                entry_type=LedgerEntryType.CONTRIBUTION,
                direction=LedgerDirection.DEBIT,
                amount=payment.amount,
                debit=payment.amount,
                currency=payment.currency,
                status=LedgerStatus.SUCCESS,
                provider="internal",
                idempotency_key=f"debit:{idempotency_key}",
                narration=f"Contribution to {payment.chama.name}",
                meta={"payment_id": str(payment.id)},
            )
            
            # Create credit entry for chama wallet
            credit_entry = LedgerEntry.objects.create(
                wallet=chama_wallet,
                chama_id=payment.chama_id,
                journal_entry=journal_entry,
                entry_type=LedgerEntryType.CONTRIBUTION,
                direction=LedgerDirection.CREDIT,
                amount=payment.amount,
                credit=payment.amount,
                currency=payment.currency,
                status=LedgerStatus.SUCCESS,
                provider="internal",
                idempotency_key=f"credit:{idempotency_key}",
                narration=f"Contribution from {payment.member.get_full_name()}",
                meta={"payment_id": str(payment.id)},
            )
            
            # Create contribution record (if contribution type is specified)
            contribution = None
            if payment.contribution_type_id:
                contribution = Contribution.objects.create(
                    chama_id=payment.chama_id,
                    member_id=payment.member_id,
                    contribution_type_id=payment.contribution_type_id,
                    amount=payment.amount,
                    date_paid=timezone.now().date(),
                    method="WALLET",
                    receipt_code=payment.reference,
                    recorded_by=actor,
                )
            
            # Update payment status
            payment.status = ChamaPaymentStatus.SUCCESS
            payment.completed_at = timezone.now()
            payment.ledger_entry = debit_entry
            payment.contribution = contribution
            payment.save()
            
            # Update wallet balances
            member_wallet.available_balance -= payment.amount
            member_wallet.save()
            chama_wallet.available_balance += payment.amount
            chama_wallet.save()
            
            # Create audit log
            create_audit_log(
                action="chama_payment_completed",
                object_type="ChamaPayment",
                object_id=str(payment.id),
                changes={
                    "member": str(payment.member_id),
                    "chama": str(payment.chama_id),
                    "amount": str(payment.amount),
                },
                actor=actor,
            )
            
            return payment


class LoanUpdateService:
    """Service for handling loan amount updates."""

    @staticmethod
    def request_update(loan_id: str, new_principal: Decimal = None, new_duration_months: int = None, new_interest_rate: Decimal = None, reason: str = "") -> LoanUpdateRequest:
        """
        Request to update loan terms.
        
        Args:
            loan_id: ID of loan to update
            new_principal: New principal amount (optional)
            new_duration_months: New duration in months (optional)
            new_interest_rate: New interest rate (optional)
            reason: Reason for update
            
        Returns:
            LoanUpdateRequest instance
        """
        loan = Loan.objects.get(id=loan_id)
        
        # Validate loan is in updatable status
        if loan.status not in [LoanStatus.REQUESTED, LoanStatus.APPROVED]:
            raise ValidationError(
                f"Cannot update loan with status '{loan.status}'. Only REQUESTED or APPROVED loans can be updated."
            )
        
        # Create update request
        update_request = LoanUpdateRequest.objects.create(
            loan=loan,
            requested_principal=new_principal,
            requested_duration_months=new_duration_months,
            requested_interest_rate=new_interest_rate,
            reason=reason,
            old_principal=loan.principal,
            old_duration_months=loan.duration_months,
            old_interest_rate=loan.interest_rate,
            status="requested",
        )
        
        # Create audit log
        changes = {}
        if new_principal:
            changes["principal"] = f"{loan.principal} -> {new_principal}"
        if new_duration_months:
            changes["duration_months"] = f"{loan.duration_months} -> {new_duration_months}"
        if new_interest_rate:
            changes["interest_rate"] = f"{loan.interest_rate} -> {new_interest_rate}"
        
        create_audit_log(
            action="loan_update_requested",
            object_type="LoanUpdateRequest",
            object_id=str(update_request.id),
            changes=changes,
        )
        
        return update_request

    @staticmethod
    def approve_update(update_request_id: str, notes: str = "", actor=None) -> LoanUpdateRequest:
        """
        Approve a loan update request.
        
        Args:
            update_request_id: ID of update request
            notes: Approval notes
            actor: User approving the request
            
        Returns:
            Updated LoanUpdateRequest instance
        """
        update_request = LoanUpdateRequest.objects.select_related("loan").get(id=update_request_id)
        
        if update_request.status != "requested":
            raise ValidationError(f"Cannot approve request with status '{update_request.status}'")
        
        update_request.status = "approved"
        update_request.reviewed_by = actor
        update_request.reviewed_at = timezone.now()
        update_request.review_notes = notes
        update_request.save()
        
        create_audit_log(
            action="loan_update_approved",
            object_type="LoanUpdateRequest",
            object_id=str(update_request.id),
            changes={"status": "requested -> approved"},
            actor=actor,
        )
        
        return update_request

    @staticmethod
    def reject_update(update_request_id: str, notes: str = "", actor=None) -> LoanUpdateRequest:
        """
        Reject a loan update request.
        
        Args:
            update_request_id: ID of update request
            notes: Rejection notes
            actor: User rejecting the request
            
        Returns:
            Updated LoanUpdateRequest instance
        """
        update_request = LoanUpdateRequest.objects.get(id=update_request_id)
        
        if update_request.status != "requested":
            raise ValidationError(f"Cannot reject request with status '{update_request.status}'")
        
        update_request.status = "rejected"
        update_request.reviewed_by = actor
        update_request.reviewed_at = timezone.now()
        update_request.review_notes = notes
        update_request.save()
        
        create_audit_log(
            action="loan_update_rejected",
            object_type="LoanUpdateRequest",
            object_id=str(update_request.id),
            changes={"status": "requested -> rejected"},
            actor=actor,
        )
        
        return update_request

    @staticmethod
    def apply_update(update_request_id: str, actor=None) -> LoanUpdateRequest:
        """
        Apply an approved loan update to the loan.
        
        Args:
            update_request_id: ID of update request to apply
            actor: User applying the update
            
        Returns:
            Updated LoanUpdateRequest instance
        """
        with transaction.atomic():
            update_request = LoanUpdateRequest.objects.select_related("loan").get(id=update_request_id)
            
            if update_request.status != "approved":
                raise ValidationError(f"Cannot apply request with status '{update_request.status}'")
            
            loan = update_request.loan
            changes = {}
            
            # Apply updates
            if update_request.requested_principal:
                changes["principal"] = f"{loan.principal} -> {update_request.requested_principal}"
                loan.principal = update_request.requested_principal
            
            if update_request.requested_duration_months:
                changes["duration_months"] = f"{loan.duration_months} -> {update_request.requested_duration_months}"
                loan.duration_months = update_request.requested_duration_months
            
            if update_request.requested_interest_rate is not None:
                changes["interest_rate"] = f"{loan.interest_rate} -> {update_request.requested_interest_rate}"
                loan.interest_rate = update_request.requested_interest_rate
            
            loan.save()
            
            # Mark update as applied
            update_request.status = "applied"
            update_request.applied_at = timezone.now()
            update_request.applied_by = actor
            update_request.save()
            
            # Create audit log
            create_audit_log(
                action="loan_update_applied",
                object_type="LoanUpdateRequest",
                object_id=str(update_request.id),
                changes=changes,
                actor=actor,
            )
            
            return update_request
