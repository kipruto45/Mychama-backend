"""
Cash Payment Service

Manages cash payment recording with permission checks and approval flow.
"""

import logging

from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama

logger = logging.getLogger(__name__)


class CashPaymentService:
    """Service for cash payment processing."""

    @staticmethod
    @transaction.atomic
    def record_cash_payment(
        chama: Chama,
        user: User,
        amount: float,
        recorded_by: User,
        notes: str = '',
        receipt_number: str = '',
    ) -> dict:
        """
        Record a cash payment.
        Returns payment details.
        """
        from apps.payments.models import CashPayment

        # Validate amount
        if amount <= 0:
            raise ValueError("Payment amount must be greater than 0")

        # Create cash payment record
        cash_payment = CashPayment.objects.create(
            chama=chama,
            user=user,
            amount=amount,
            recorded_by=recorded_by,
            notes=notes,
            receipt_number=receipt_number,
            status='pending',
        )

        logger.info(
            f"Cash payment recorded: {amount} for {user.full_name} "
            f"in {chama.name} by {recorded_by.full_name}"
        )

        return {
            'id': str(cash_payment.id),
            'amount': amount,
            'user_name': user.full_name,
            'recorded_by_name': recorded_by.full_name,
            'status': 'pending',
            'created_at': cash_payment.created_at.isoformat(),
        }

    @staticmethod
    @transaction.atomic
    def approve_cash_payment(
        payment_id: str,
        approver: User,
        notes: str = '',
    ) -> tuple[bool, str]:
        """
        Approve a cash payment.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker
        from apps.payments.models import CashPayment

        try:
            cash_payment = CashPayment.objects.get(id=payment_id)

            # Check if approver has permission
            if not PermissionChecker.has_permission(
                approver,
                Permission.CAN_RECORD_CONTRIBUTIONS,
                str(cash_payment.chama.id),
            ):
                return False, "Permission denied"

            # Cannot approve your own payment
            if cash_payment.recorded_by == approver:
                return False, "Cannot approve your own payment"

            # Update cash payment
            cash_payment.status = 'approved'
            cash_payment.approved_by = approver
            cash_payment.approved_at = timezone.now()
            cash_payment.approval_notes = notes
            cash_payment.save(update_fields=[
                'status',
                'approved_by',
                'approved_at',
                'approval_notes',
                'updated_at',
            ])

            # Update account balance
            from apps.finance.models import Account
            account = Account.objects.get(
                chama=cash_payment.chama,
                account_type='main',
            )
            account.balance += cash_payment.amount
            account.save(update_fields=['balance', 'updated_at'])

            logger.info(
                f"Cash payment approved: {payment_id} by {approver.full_name}"
            )

            return True, "Cash payment approved"

        except CashPayment.DoesNotExist:
            return False, "Cash payment not found"

    @staticmethod
    @transaction.atomic
    def reject_cash_payment(
        payment_id: str,
        rejector: User,
        reason: str,
    ) -> tuple[bool, str]:
        """
        Reject a cash payment.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker
        from apps.payments.models import CashPayment

        try:
            cash_payment = CashPayment.objects.get(id=payment_id)

            # Check if rejector has permission
            if not PermissionChecker.has_permission(
                rejector,
                Permission.CAN_RECORD_CONTRIBUTIONS,
                str(cash_payment.chama.id),
            ):
                return False, "Permission denied"

            # Update cash payment
            cash_payment.status = 'rejected'
            cash_payment.rejected_by = rejector
            cash_payment.rejected_at = timezone.now()
            cash_payment.rejection_reason = reason
            cash_payment.save(update_fields=[
                'status',
                'rejected_by',
                'rejected_at',
                'rejection_reason',
                'updated_at',
            ])

            logger.info(
                f"Cash payment rejected: {payment_id} by {rejector.full_name}"
            )

            return True, "Cash payment rejected"

        except CashPayment.DoesNotExist:
            return False, "Cash payment not found"

    @staticmethod
    def get_pending_payments(chama: Chama = None) -> list[dict]:
        """
        Get pending cash payments.
        """
        from apps.payments.models import CashPayment

        queryset = CashPayment.objects.filter(status='pending')

        if chama:
            queryset = queryset.filter(chama=chama)

        payments = queryset.order_by('-created_at')

        return [
            {
                'id': str(payment.id),
                'amount': payment.amount,
                'user_name': payment.user.full_name,
                'recorded_by_name': payment.recorded_by.full_name,
                'notes': payment.notes,
                'receipt_number': payment.receipt_number,
                'created_at': payment.created_at.isoformat(),
            }
            for payment in payments
        ]

    @staticmethod
    def get_payment_history(
        chama: Chama = None,
        user: User = None,
        status: str = None,
    ) -> list[dict]:
        """
        Get cash payment history.
        """
        from apps.payments.models import CashPayment

        queryset = CashPayment.objects.all()

        if chama:
            queryset = queryset.filter(chama=chama)

        if user:
            queryset = queryset.filter(user=user)

        if status:
            queryset = queryset.filter(status=status)

        payments = queryset.order_by('-created_at')

        return [
            {
                'id': str(payment.id),
                'amount': payment.amount,
                'user_name': payment.user.full_name,
                'recorded_by_name': payment.recorded_by.full_name,
                'approved_by_name': payment.approved_by.full_name if payment.approved_by else None,
                'status': payment.status,
                'notes': payment.notes,
                'receipt_number': payment.receipt_number,
                'created_at': payment.created_at.isoformat(),
                'approved_at': payment.approved_at.isoformat() if payment.approved_at else None,
            }
            for payment in payments
        ]

    @staticmethod
    def get_cash_summary(chama: Chama) -> dict:
        """
        Get cash payment summary for a chama.
        """
        from django.db.models import Count, Sum

        from apps.payments.models import CashPayment

        summary = CashPayment.objects.filter(chama=chama).aggregate(
            total=Count('id'),
            pending=Count('id', filter=models.Q(status='pending')),
            approved=Count('id', filter=models.Q(status='approved')),
            rejected=Count('id', filter=models.Q(status='rejected')),
            total_amount=Sum('amount', filter=models.Q(status='approved')),
        )

        return {
            'total_payments': summary['total'] or 0,
            'pending_payments': summary['pending'] or 0,
            'approved_payments': summary['approved'] or 0,
            'rejected_payments': summary['rejected'] or 0,
            'total_amount': summary['total_amount'] or 0,
            'approval_rate': (
                (summary['approved'] / summary['total'] * 100)
                if summary['total'] > 0 else 0
            ),
        }
