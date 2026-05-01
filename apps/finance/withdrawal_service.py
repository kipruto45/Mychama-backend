"""
Withdrawals and Disbursement Service

Manages withdrawal requests, approval flow, and payout tracking.
"""

import logging

from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama

logger = logging.getLogger(__name__)


class WithdrawalService:
    """Service for managing withdrawals."""

    @staticmethod
    @transaction.atomic
    def create_withdrawal_request(
        chama: Chama,
        user: User,
        amount: float,
        reason: str,
        beneficiary_name: str,
        beneficiary_account: str,
        beneficiary_bank: str = '',
        notes: str = '',
    ) -> dict:
        """
        Create a withdrawal request.
        Returns withdrawal details.
        """
        from apps.finance.models import Account, Withdrawal

        # Validate amount
        if amount <= 0:
            raise ValueError("Withdrawal amount must be greater than 0")

        # Check account balance
        account = Account.objects.get(chama=chama, account_type='main')
        if account.balance < amount:
            raise ValueError("Insufficient funds")

        # Create withdrawal request
        withdrawal = Withdrawal.objects.create(
            chama=chama,
            user=user,
            amount=amount,
            reason=reason,
            beneficiary_name=beneficiary_name,
            beneficiary_account=beneficiary_account,
            beneficiary_bank=beneficiary_bank,
            notes=notes,
            status='pending',
        )

        logger.info(
            f"Withdrawal request created: {amount} for {user.full_name} "
            f"in {chama.name}"
        )

        return {
            'id': str(withdrawal.id),
            'amount': amount,
            'reason': reason,
            'beneficiary_name': beneficiary_name,
            'beneficiary_account': beneficiary_account,
            'status': 'pending',
            'created_at': withdrawal.created_at.isoformat(),
        }

    @staticmethod
    @transaction.atomic
    def approve_withdrawal(
        withdrawal_id: str,
        approver: User,
        notes: str = '',
    ) -> tuple[bool, str]:
        """
        Approve a withdrawal request.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker
        from apps.finance.models import Account, Withdrawal

        try:
            withdrawal = Withdrawal.objects.get(id=withdrawal_id)

            # Check if approver has permission
            if not PermissionChecker.has_permission(
                approver,
                Permission.CAN_APPROVE_LOAN,
                str(withdrawal.chama.id),
            ):
                return False, "Permission denied"

            # Cannot approve your own withdrawal
            if withdrawal.user == approver:
                return False, "Cannot approve your own withdrawal"

            # Check account balance
            account = Account.objects.get(chama=withdrawal.chama, account_type='main')
            if account.balance < withdrawal.amount:
                return False, "Insufficient funds"

            # Update withdrawal
            withdrawal.status = 'approved'
            withdrawal.approved_by = approver
            withdrawal.approved_at = timezone.now()
            withdrawal.approval_notes = notes
            withdrawal.save(update_fields=[
                'status',
                'approved_by',
                'approved_at',
                'approval_notes',
                'updated_at',
            ])

            logger.info(
                f"Withdrawal approved: {withdrawal_id} by {approver.full_name}"
            )

            return True, "Withdrawal approved"

        except Withdrawal.DoesNotExist:
            return False, "Withdrawal not found"

    @staticmethod
    @transaction.atomic
    def reject_withdrawal(
        withdrawal_id: str,
        rejector: User,
        reason: str,
    ) -> tuple[bool, str]:
        """
        Reject a withdrawal request.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker
        from apps.finance.models import Withdrawal

        try:
            withdrawal = Withdrawal.objects.get(id=withdrawal_id)

            # Check if rejector has permission
            if not PermissionChecker.has_permission(
                rejector,
                Permission.CAN_APPROVE_LOAN,
                str(withdrawal.chama.id),
            ):
                return False, "Permission denied"

            # Update withdrawal
            withdrawal.status = 'rejected'
            withdrawal.rejected_by = rejector
            withdrawal.rejected_at = timezone.now()
            withdrawal.rejection_reason = reason
            withdrawal.save(update_fields=[
                'status',
                'rejected_by',
                'rejected_at',
                'rejection_reason',
                'updated_at',
            ])

            logger.info(
                f"Withdrawal rejected: {withdrawal_id} by {rejector.full_name}"
            )

            return True, "Withdrawal rejected"

        except Withdrawal.DoesNotExist:
            return False, "Withdrawal not found"

    @staticmethod
    @transaction.atomic
    def disburse_withdrawal(
        withdrawal_id: str,
        payment_method: str,
        reference: str = '',
        notes: str = '',
    ) -> tuple[bool, str]:
        """
        Disburse an approved withdrawal.
        Returns (success, message).
        """
        from apps.finance.models import Account, Transaction, Withdrawal

        try:
            withdrawal = Withdrawal.objects.get(id=withdrawal_id)

            if withdrawal.status != 'approved':
                return False, "Withdrawal is not approved"

            # Check account balance
            account = Account.objects.get(chama=withdrawal.chama, account_type='main')
            if account.balance < withdrawal.amount:
                return False, "Insufficient funds"

            # Create transaction
            Transaction.objects.create(
                chama=withdrawal.chama,
                transaction_type='withdrawal',
                amount=withdrawal.amount,
                payment_method=payment_method,
                reference=reference,
                notes=notes,
                created_by=withdrawal.user,
            )

            # Update withdrawal
            withdrawal.status = 'disbursed'
            withdrawal.disbursed_at = timezone.now()
            withdrawal.payment_method = payment_method
            withdrawal.payment_reference = reference
            withdrawal.payment_notes = notes
            withdrawal.save(update_fields=[
                'status',
                'disbursed_at',
                'payment_method',
                'payment_reference',
                'payment_notes',
                'updated_at',
            ])

            # Update account balance
            account.balance -= withdrawal.amount
            account.save(update_fields=['balance', 'updated_at'])

            logger.info(
                f"Withdrawal disbursed: {withdrawal_id} via {payment_method}"
            )

            return True, "Withdrawal disbursed"

        except Withdrawal.DoesNotExist:
            return False, "Withdrawal not found"

    @staticmethod
    def get_pending_withdrawals(chama: Chama = None) -> list[dict]:
        """
        Get pending withdrawal requests.
        """
        from apps.finance.models import Withdrawal

        queryset = Withdrawal.objects.filter(status='pending')

        if chama:
            queryset = queryset.filter(chama=chama)

        withdrawals = queryset.order_by('-created_at')

        return [
            {
                'id': str(withdrawal.id),
                'amount': withdrawal.amount,
                'reason': withdrawal.reason,
                'user_name': withdrawal.user.full_name,
                'beneficiary_name': withdrawal.beneficiary_name,
                'beneficiary_account': withdrawal.beneficiary_account,
                'beneficiary_bank': withdrawal.beneficiary_bank,
                'notes': withdrawal.notes,
                'created_at': withdrawal.created_at.isoformat(),
            }
            for withdrawal in withdrawals
        ]

    @staticmethod
    def get_withdrawal_history(
        chama: Chama = None,
        user: User = None,
        status: str = None,
    ) -> list[dict]:
        """
        Get withdrawal history with filtering.
        """
        from apps.finance.models import Withdrawal

        queryset = Withdrawal.objects.all()

        if chama:
            queryset = queryset.filter(chama=chama)

        if user:
            queryset = queryset.filter(user=user)

        if status:
            queryset = queryset.filter(status=status)

        withdrawals = queryset.order_by('-created_at')

        return [
            {
                'id': str(withdrawal.id),
                'amount': withdrawal.amount,
                'reason': withdrawal.reason,
                'user_name': withdrawal.user.full_name,
                'approved_by_name': withdrawal.approved_by.full_name if withdrawal.approved_by else None,
                'status': withdrawal.status,
                'beneficiary_name': withdrawal.beneficiary_name,
                'beneficiary_account': withdrawal.beneficiary_account,
                'beneficiary_bank': withdrawal.beneficiary_bank,
                'notes': withdrawal.notes,
                'approval_notes': withdrawal.approval_notes,
                'rejection_reason': withdrawal.rejection_reason,
                'payment_method': withdrawal.payment_method,
                'payment_reference': withdrawal.payment_reference,
                'created_at': withdrawal.created_at.isoformat(),
                'approved_at': withdrawal.approved_at.isoformat() if withdrawal.approved_at else None,
                'disbursed_at': withdrawal.disbursed_at.isoformat() if withdrawal.disbursed_at else None,
            }
            for withdrawal in withdrawals
        ]

    @staticmethod
    def get_withdrawal_summary(chama: Chama) -> dict:
        """
        Get withdrawal summary for a chama.
        """
        from django.db.models import Count, Sum

        from apps.finance.models import Withdrawal

        summary = Withdrawal.objects.filter(chama=chama).aggregate(
            total=Count('id'),
            pending=Count('id', filter=models.Q(status='pending')),
            approved=Count('id', filter=models.Q(status='approved')),
            disbursed=Count('id', filter=models.Q(status='disbursed')),
            rejected=Count('id', filter=models.Q(status='rejected')),
            total_amount=Sum('amount'),
            disbursed_amount=Sum('amount', filter=models.Q(status='disbursed')),
        )

        return {
            'total_withdrawals': summary['total'] or 0,
            'pending_withdrawals': summary['pending'] or 0,
            'approved_withdrawals': summary['approved'] or 0,
            'disbursed_withdrawals': summary['disbursed'] or 0,
            'rejected_withdrawals': summary['rejected'] or 0,
            'total_amount': summary['total_amount'] or 0,
            'disbursed_amount': summary['disbursed_amount'] or 0,
            'approval_rate': (
                (summary['approved'] / summary['total'] * 100)
                if summary['total'] > 0 else 0
            ),
        }

    @staticmethod
    def check_liquidity(chama: Chama, amount: float) -> dict:
        """
        Check if chama has sufficient liquidity for a withdrawal.
        """
        from apps.finance.models import Account

        account = Account.objects.get(chama=chama, account_type='main')

        return {
            'available_balance': float(account.balance),
            'requested_amount': float(amount),
            'sufficient': account.balance >= amount,
            'shortfall': float(amount - account.balance) if account.balance < amount else 0,
        }
