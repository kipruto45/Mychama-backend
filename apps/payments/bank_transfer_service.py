"""
Bank Transfer Service

Manages bank transfer payments with proof upload and verification.
"""

import logging

from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama

logger = logging.getLogger(__name__)


class BankTransferService:
    """Service for bank transfer processing."""

    @staticmethod
    @transaction.atomic
    def create_bank_transfer(
        chama: Chama,
        user: User,
        amount: float,
        bank_name: str,
        account_number: str,
        reference: str = '',
        notes: str = '',
    ) -> dict:
        """
        Create a bank transfer record.
        Returns transfer details.
        """
        from apps.payments.models import BankTransfer

        # Validate amount
        if amount <= 0:
            raise ValueError("Transfer amount must be greater than 0")

        # Create bank transfer record
        bank_transfer = BankTransfer.objects.create(
            chama=chama,
            user=user,
            amount=amount,
            bank_name=bank_name,
            account_number=account_number,
            reference=reference,
            notes=notes,
            status='pending_verification',
        )

        logger.info(
            f"Bank transfer created: {amount} for {user.full_name} "
            f"in {chama.name}"
        )

        return {
            'id': str(bank_transfer.id),
            'amount': amount,
            'bank_name': bank_name,
            'account_number': account_number,
            'reference': reference,
            'status': 'pending_verification',
            'created_at': bank_transfer.created_at.isoformat(),
        }

    @staticmethod
    @transaction.atomic
    def upload_proof(
        transfer_id: str,
        proof_document,
        user: User,
    ) -> tuple[bool, str]:
        """
        Upload proof document for a bank transfer.
        Returns (success, message).
        """
        from apps.payments.models import BankTransfer

        try:
            bank_transfer = BankTransfer.objects.get(id=transfer_id)

            # Check if user is the one who made the transfer
            if bank_transfer.user != user:
                return False, "Permission denied"

            # Update bank transfer
            bank_transfer.proof_document = proof_document
            bank_transfer.proof_uploaded_at = timezone.now()
            bank_transfer.save(update_fields=[
                'proof_document',
                'proof_uploaded_at',
                'updated_at',
            ])

            logger.info(
                f"Proof uploaded for bank transfer: {transfer_id}"
            )

            return True, "Proof uploaded"

        except BankTransfer.DoesNotExist:
            return False, "Bank transfer not found"

    @staticmethod
    @transaction.atomic
    def verify_transfer(
        transfer_id: str,
        verifier: User,
        approved: bool,
        notes: str = '',
    ) -> tuple[bool, str]:
        """
        Verify a bank transfer.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker
        from apps.payments.models import BankTransfer

        try:
            bank_transfer = BankTransfer.objects.get(id=transfer_id)

            # Check if verifier has permission
            if not PermissionChecker.has_permission(
                verifier,
                Permission.CAN_RECORD_CONTRIBUTIONS,
                str(bank_transfer.chama.id),
            ):
                return False, "Permission denied"

            if approved:
                # Update bank transfer
                bank_transfer.status = 'approved'
                bank_transfer.verified_by = verifier
                bank_transfer.verified_at = timezone.now()
                bank_transfer.verification_notes = notes
                bank_transfer.save(update_fields=[
                    'status',
                    'verified_by',
                    'verified_at',
                    'verification_notes',
                    'updated_at',
                ])

                # Update account balance
                from apps.finance.models import Account
                account = Account.objects.get(
                    chama=bank_transfer.chama,
                    account_type='main',
                )
                account.balance += bank_transfer.amount
                account.save(update_fields=['balance', 'updated_at'])

                logger.info(
                    f"Bank transfer approved: {transfer_id} by {verifier.full_name}"
                )

                return True, "Bank transfer approved"
            else:
                # Update bank transfer
                bank_transfer.status = 'rejected'
                bank_transfer.verified_by = verifier
                bank_transfer.verified_at = timezone.now()
                bank_transfer.verification_notes = notes
                bank_transfer.save(update_fields=[
                    'status',
                    'verified_by',
                    'verified_at',
                    'verification_notes',
                    'updated_at',
                ])

                logger.info(
                    f"Bank transfer rejected: {transfer_id} by {verifier.full_name}"
                )

                return True, "Bank transfer rejected"

        except BankTransfer.DoesNotExist:
            return False, "Bank transfer not found"

    @staticmethod
    def get_pending_transfers(chama: Chama = None) -> list[dict]:
        """
        Get pending bank transfers.
        """
        from apps.payments.models import BankTransfer

        queryset = BankTransfer.objects.filter(status='pending_verification')

        if chama:
            queryset = queryset.filter(chama=chama)

        transfers = queryset.order_by('-created_at')

        return [
            {
                'id': str(transfer.id),
                'amount': transfer.amount,
                'bank_name': transfer.bank_name,
                'account_number': transfer.account_number,
                'reference': transfer.reference,
                'user_name': transfer.user.full_name,
                'notes': transfer.notes,
                'proof_document_url': transfer.proof_document.url if transfer.proof_document else None,
                'created_at': transfer.created_at.isoformat(),
            }
            for transfer in transfers
        ]

    @staticmethod
    def get_transfer_history(
        chama: Chama = None,
        user: User = None,
        status: str = None,
    ) -> list[dict]:
        """
        Get bank transfer history.
        """
        from apps.payments.models import BankTransfer

        queryset = BankTransfer.objects.all()

        if chama:
            queryset = queryset.filter(chama=chama)

        if user:
            queryset = queryset.filter(user=user)

        if status:
            queryset = queryset.filter(status=status)

        transfers = queryset.order_by('-created_at')

        return [
            {
                'id': str(transfer.id),
                'amount': transfer.amount,
                'bank_name': transfer.bank_name,
                'account_number': transfer.account_number,
                'reference': transfer.reference,
                'user_name': transfer.user.full_name,
                'verified_by_name': transfer.verified_by.full_name if transfer.verified_by else None,
                'status': transfer.status,
                'notes': transfer.notes,
                'verification_notes': transfer.verification_notes,
                'created_at': transfer.created_at.isoformat(),
                'verified_at': transfer.verified_at.isoformat() if transfer.verified_at else None,
            }
            for transfer in transfers
        ]

    @staticmethod
    def get_transfer_detail(transfer_id: str) -> dict | None:
        """
        Get detailed bank transfer information.
        """
        from apps.payments.models import BankTransfer

        try:
            transfer = BankTransfer.objects.select_related(
                'user', 'chama', 'verified_by'
            ).get(id=transfer_id)

            return {
                'id': str(transfer.id),
                'amount': transfer.amount,
                'bank_name': transfer.bank_name,
                'account_number': transfer.account_number,
                'reference': transfer.reference,
                'user_id': str(transfer.user.id),
                'user_name': transfer.user.full_name,
                'chama_id': str(transfer.chama.id) if transfer.chama else None,
                'chama_name': transfer.chama.name if transfer.chama else None,
                'verified_by_id': str(transfer.verified_by.id) if transfer.verified_by else None,
                'verified_by_name': transfer.verified_by.full_name if transfer.verified_by else None,
                'status': transfer.status,
                'notes': transfer.notes,
                'verification_notes': transfer.verification_notes,
                'proof_document_url': transfer.proof_document.url if transfer.proof_document else None,
                'created_at': transfer.created_at.isoformat(),
                'verified_at': transfer.verified_at.isoformat() if transfer.verified_at else None,
            }

        except BankTransfer.DoesNotExist:
            return None

    @staticmethod
    def get_bank_transfer_summary(chama: Chama) -> dict:
        """
        Get bank transfer summary for a chama.
        """
        from django.db.models import Count, Sum

        from apps.payments.models import BankTransfer

        summary = BankTransfer.objects.filter(chama=chama).aggregate(
            total=Count('id'),
            pending=Count('id', filter=models.Q(status='pending_verification')),
            approved=Count('id', filter=models.Q(status='approved')),
            rejected=Count('id', filter=models.Q(status='rejected')),
            total_amount=Sum('amount', filter=models.Q(status='approved')),
        )

        return {
            'total_transfers': summary['total'] or 0,
            'pending_transfers': summary['pending'] or 0,
            'approved_transfers': summary['approved'] or 0,
            'rejected_transfers': summary['rejected'] or 0,
            'total_amount': summary['total_amount'] or 0,
            'approval_rate': (
                (summary['approved'] / summary['total'] * 100)
                if summary['total'] > 0 else 0
            ),
        }
