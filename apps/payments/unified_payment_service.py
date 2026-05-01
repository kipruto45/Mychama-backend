"""
Unified Payments Service

Manages payment intents, provider adapters, and transaction history.
"""

import logging

from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama

logger = logging.getLogger(__name__)


class UnifiedPaymentService:
    """Service for managing unified payments."""

    @staticmethod
    @transaction.atomic
    def create_payment_intent(
        chama: Chama,
        user: User,
        amount: float,
        payment_method: str,
        description: str = '',
        metadata: dict = None,
    ) -> dict:
        """
        Create a payment intent.
        Returns payment intent details.
        """
        from apps.payments.models import PaymentIntent

        # Validate amount
        if amount <= 0:
            raise ValueError("Payment amount must be greater than 0")

        # Generate unique reference
        import uuid
        reference = f"PAY-{uuid.uuid4().hex[:8].upper()}"

        # Create payment intent
        payment_intent = PaymentIntent.objects.create(
            chama=chama,
            user=user,
            amount=amount,
            payment_method=payment_method,
            reference=reference,
            description=description,
            metadata=metadata or {},
            status='pending',
        )

        logger.info(
            f"Payment intent created: {reference} for {user.full_name} "
            f"in {chama.name}"
        )

        return {
            'id': str(payment_intent.id),
            'reference': reference,
            'amount': amount,
            'payment_method': payment_method,
            'status': 'pending',
            'created_at': payment_intent.created_at.isoformat(),
        }

    @staticmethod
    @transaction.atomic
    def process_payment(
        payment_intent_id: str,
        provider_response: dict = None,
    ) -> tuple[bool, str]:
        """
        Process a payment intent.
        Returns (success, message).
        """
        from apps.payments.models import PaymentIntent, PaymentTransaction

        try:
            payment_intent = PaymentIntent.objects.get(id=payment_intent_id)

            if payment_intent.status != 'pending':
                return False, f"Payment intent is not pending (status: {payment_intent.status})"

            # Create transaction
            transaction = PaymentTransaction.objects.create(
                payment_intent=payment_intent,
                chama=payment_intent.chama,
                user=payment_intent.user,
                amount=payment_intent.amount,
                payment_method=payment_intent.payment_method,
                reference=payment_intent.reference,
                status='processing',
                provider_response=provider_response or {},
            )

            # Process based on payment method
            success = False
            message = ""

            if payment_intent.payment_method == 'mpesa':
                success, message = UnifiedPaymentService._process_mpesa_payment(
                    payment_intent, transaction, provider_response
                )
            elif payment_intent.payment_method == 'card':
                success, message = UnifiedPaymentService._process_card_payment(
                    payment_intent, transaction, provider_response
                )
            elif payment_intent.payment_method == 'bank':
                success, message = UnifiedPaymentService._process_bank_payment(
                    payment_intent, transaction, provider_response
                )
            elif payment_intent.payment_method == 'cash':
                success, message = UnifiedPaymentService._process_cash_payment(
                    payment_intent, transaction, provider_response
                )
            else:
                success = False
                message = f"Unsupported payment method: {payment_intent.payment_method}"

            # Update transaction status
            transaction.status = 'completed' if success else 'failed'
            transaction.completed_at = timezone.now() if success else None
            transaction.save(update_fields=['status', 'completed_at', 'updated_at'])

            # Update payment intent status
            payment_intent.status = 'completed' if success else 'failed'
            payment_intent.save(update_fields=['status', 'updated_at'])

            if success:
                # Update account balance
                from apps.finance.models import Account
                account = Account.objects.get(
                    chama=payment_intent.chama,
                    account_type='main',
                )
                account.balance += payment_intent.amount
                account.save(update_fields=['balance', 'updated_at'])

            return success, message

        except PaymentIntent.DoesNotExist:
            return False, "Payment intent not found"

    @staticmethod
    def _process_mpesa_payment(
        payment_intent,
        transaction,
        provider_response,
    ) -> tuple[bool, str]:
        """Process M-Pesa payment."""
        # TODO: Integrate with M-Pesa API
        # For now, simulate success
        return True, "M-Pesa payment processed"

    @staticmethod
    def _process_card_payment(
        payment_intent,
        transaction,
        provider_response,
    ) -> tuple[bool, str]:
        """Process card payment."""
        # TODO: Integrate with card payment provider
        # For now, simulate success
        return True, "Card payment processed"

    @staticmethod
    def _process_bank_payment(
        payment_intent,
        transaction,
        provider_response,
    ) -> tuple[bool, str]:
        """Process bank transfer payment."""
        # TODO: Integrate with bank API
        # For now, simulate success
        return True, "Bank payment processed"

    @staticmethod
    def _process_cash_payment(
        payment_intent,
        transaction,
        provider_response,
    ) -> tuple[bool, str]:
        """Process cash payment."""
        # Cash payments are always successful
        return True, "Cash payment recorded"

    @staticmethod
    def get_payment_history(
        chama: Chama = None,
        user: User = None,
        status: str = None,
        payment_method: str = None,
    ) -> list[dict]:
        """
        Get payment history with filtering.
        """
        from apps.payments.models import PaymentTransaction

        queryset = PaymentTransaction.objects.all()

        if chama:
            queryset = queryset.filter(chama=chama)

        if user:
            queryset = queryset.filter(user=user)

        if status:
            queryset = queryset.filter(status=status)

        if payment_method:
            queryset = queryset.filter(payment_method=payment_method)

        transactions = queryset.order_by('-created_at')

        return [
            {
                'id': str(txn.id),
                'reference': txn.reference,
                'amount': txn.amount,
                'payment_method': txn.payment_method,
                'status': txn.status,
                'user_name': txn.user.full_name,
                'chama_name': txn.chama.name if txn.chama else None,
                'created_at': txn.created_at.isoformat(),
                'completed_at': txn.completed_at.isoformat() if txn.completed_at else None,
            }
            for txn in transactions
        ]

    @staticmethod
    def get_payment_detail(payment_id: str) -> dict | None:
        """
        Get detailed payment information.
        """
        from apps.payments.models import PaymentTransaction

        try:
            payment = PaymentTransaction.objects.select_related(
                'user', 'chama', 'payment_intent'
            ).get(id=payment_id)

            return {
                'id': str(payment.id),
                'reference': payment.reference,
                'amount': payment.amount,
                'payment_method': payment.payment_method,
                'status': payment.status,
                'user_id': str(payment.user.id),
                'user_name': payment.user.full_name,
                'chama_id': str(payment.chama.id) if payment.chama else None,
                'chama_name': payment.chama.name if payment.chama else None,
                'description': payment.payment_intent.description if payment.payment_intent else '',
                'metadata': payment.payment_intent.metadata if payment.payment_intent else {},
                'provider_response': payment.provider_response,
                'created_at': payment.created_at.isoformat(),
                'completed_at': payment.completed_at.isoformat() if payment.completed_at else None,
            }

        except PaymentTransaction.DoesNotExist:
            return None

    @staticmethod
    def get_payment_summary(chama: Chama) -> dict:
        """
        Get payment summary for a chama.
        """
        from django.db.models import Count, Sum

        from apps.payments.models import PaymentTransaction

        summary = PaymentTransaction.objects.filter(chama=chama).aggregate(
            total_amount=Sum('amount'),
            total_count=Count('id'),
            completed_count=Count('id', filter=models.Q(status='completed')),
            failed_count=Count('id', filter=models.Q(status='failed')),
            pending_count=Count('id', filter=models.Q(status='pending')),
        )

        # Get by payment method
        by_method = PaymentTransaction.objects.filter(
            chama=chama,
            status='completed',
        ).values('payment_method').annotate(
            total=Sum('amount'),
            count=Count('id'),
        )

        return {
            'total_amount': summary['total_amount'] or 0,
            'total_count': summary['total_count'] or 0,
            'completed_count': summary['completed_count'] or 0,
            'failed_count': summary['failed_count'] or 0,
            'pending_count': summary['pending_count'] or 0,
            'success_rate': (
                (summary['completed_count'] / summary['total_count'] * 100)
                if summary['total_count'] > 0 else 0
            ),
            'by_payment_method': {
                item['payment_method']: {
                    'total': item['total'] or 0,
                    'count': item['count'] or 0,
                }
                for item in by_method
            },
        }
