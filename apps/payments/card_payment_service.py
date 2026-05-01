"""
Card Payment Service

Manages card payment processing with hosted checkout and webhook verification.
"""

import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama

logger = logging.getLogger(__name__)


class CardPaymentService:
    """Service for card payment processing."""

    # Card payment provider configuration
    PROVIDER = getattr(settings, 'CARD_PAYMENT_PROVIDER', 'stripe')
    STRIPE_SECRET_KEY = getattr(settings, 'STRIPE_SECRET_KEY', '')
    STRIPE_PUBLISHABLE_KEY = getattr(settings, 'STRIPE_PUBLISHABLE_KEY', '')
    STRIPE_WEBHOOK_SECRET = getattr(settings, 'STRIPE_WEBHOOK_SECRET', '')

    @staticmethod
    @transaction.atomic
    def create_checkout_session(
        chama: Chama,
        user: User,
        amount: float,
        currency: str = 'KES',
        description: str = '',
        success_url: str = '',
        cancel_url: str = '',
    ) -> dict:
        """
        Create a card payment checkout session.
        Returns checkout session details.
        """
        from apps.payments.models import CardCheckoutSession

        # Validate amount
        if amount <= 0:
            raise ValueError("Payment amount must be greater than 0")

        # Generate unique reference
        import uuid
        reference = f"CARD-{uuid.uuid4().hex[:8].upper()}"

        # Create checkout session
        checkout_session = CardCheckoutSession.objects.create(
            chama=chama,
            user=user,
            amount=amount,
            currency=currency,
            reference=reference,
            description=description,
            success_url=success_url,
            cancel_url=cancel_url,
            status='pending',
        )

        logger.info(
            f"Card checkout session created: {reference} for {user.full_name} "
            f"in {chama.name}"
        )

        return {
            'id': str(checkout_session.id),
            'reference': reference,
            'amount': amount,
            'currency': currency,
            'status': 'pending',
            'created_at': checkout_session.created_at.isoformat(),
        }

    @staticmethod
    def create_stripe_checkout_session(
        checkout_session_id: str,
    ) -> dict:
        """
        Create Stripe checkout session.
        Returns Stripe session details.
        """
        from apps.payments.models import CardCheckoutSession

        try:
            checkout_session = CardCheckoutSession.objects.get(
                id=checkout_session_id,
            )

            # TODO: Integrate with Stripe API
            # For now, simulate Stripe session creation
            import uuid
            stripe_session_id = f"cs_{uuid.uuid4().hex[:24]}"
            stripe_session_url = f"https://checkout.stripe.com/pay/{stripe_session_id}"

            # Update checkout session
            checkout_session.stripe_session_id = stripe_session_id
            checkout_session.stripe_session_url = stripe_session_url
            checkout_session.save(update_fields=[
                'stripe_session_id',
                'stripe_session_url',
                'updated_at',
            ])

            logger.info(
                f"Stripe checkout session created: {stripe_session_id} "
                f"for checkout {checkout_session_id}"
            )

            return {
                'stripe_session_id': stripe_session_id,
                'stripe_session_url': stripe_session_url,
            }

        except CardCheckoutSession.DoesNotExist:
            raise ValueError("Checkout session not found")

    @staticmethod
    @transaction.atomic
    def process_webhook(webhook_data: dict, signature: str = '') -> tuple[bool, str]:
        """
        Process card payment webhook.
        Returns (success, message).
        """
        from apps.payments.models import CardWebhook

        try:
            # Verify webhook signature (if provided)
            if signature and CardPaymentService.STRIPE_WEBHOOK_SECRET:
                if not CardPaymentService._verify_webhook_signature(
                    webhook_data, signature
                ):
                    return False, "Invalid webhook signature"

            # Extract event data
            event_type = webhook_data.get('type', '')
            event_data = webhook_data.get('data', {}).get('object', {})

            # Create webhook record
            webhook = CardWebhook.objects.create(
                event_type=event_type,
                event_data=webhook_data,
                signature=signature,
                status='received',
            )

            # Process based on event type
            if event_type == 'checkout.session.completed':
                success, message = CardPaymentService._process_checkout_completed(
                    event_data, webhook
                )
            elif event_type == 'payment_intent.succeeded':
                success, message = CardPaymentService._process_payment_succeeded(
                    event_data, webhook
                )
            elif event_type == 'payment_intent.payment_failed':
                success, message = CardPaymentService._process_payment_failed(
                    event_data, webhook
                )
            else:
                # Unknown event type
                webhook.status = 'ignored'
                webhook.save(update_fields=['status', 'updated_at'])
                return True, f"Ignored event type: {event_type}"

            # Update webhook status
            webhook.status = 'processed' if success else 'failed'
            webhook.processed_at = timezone.now() if success else None
            webhook.save(update_fields=['status', 'processed_at', 'updated_at'])

            return success, message

        except Exception as e:
            logger.error(f"Error processing card webhook: {e}")
            return False, f"Error processing webhook: {e}"

    @staticmethod
    def _verify_webhook_signature(webhook_data: dict, signature: str) -> bool:
        """
        Verify Stripe webhook signature.
        """
        # TODO: Implement Stripe signature verification
        # For now, always return True
        return True

    @staticmethod
    def _process_checkout_completed(event_data: dict, webhook) -> tuple[bool, str]:
        """
        Process checkout.session.completed event.
        """
        from apps.payments.models import CardCheckoutSession

        try:
            session_id = event_data.get('id')
            
            # Find checkout session
            checkout_session = CardCheckoutSession.objects.get(
                stripe_session_id=session_id,
            )

            # Update checkout session
            checkout_session.status = 'completed'
            checkout_session.stripe_payment_intent_id = event_data.get('payment_intent')
            checkout_session.completed_at = timezone.now()
            checkout_session.save(update_fields=[
                'status',
                'stripe_payment_intent_id',
                'completed_at',
                'updated_at',
            ])

            # Update payment intent
            from apps.payments.models import PaymentIntent
            payment_intent = PaymentIntent.objects.filter(
                reference=checkout_session.reference,
            ).first()

            if payment_intent:
                payment_intent.status = 'completed'
                payment_intent.save(update_fields=['status', 'updated_at'])

                # Update account balance
                from apps.finance.models import Account
                account = Account.objects.get(
                    chama=checkout_session.chama,
                    account_type='main',
                )
                account.balance += checkout_session.amount
                account.save(update_fields=['balance', 'updated_at'])

            logger.info(
                f"Card payment completed: {checkout_session.reference} "
                f"Session: {session_id}"
            )

            return True, "Payment completed"

        except CardCheckoutSession.DoesNotExist:
            logger.warning(f"Checkout session not found: {session_id}")
            return False, "Checkout session not found"

    @staticmethod
    def _process_payment_succeeded(event_data: dict, webhook) -> tuple[bool, str]:
        """
        Process payment_intent.succeeded event.
        """
        # TODO: Implement payment intent processing
        return True, "Payment succeeded"

    @staticmethod
    def _process_payment_failed(event_data: dict, webhook) -> tuple[bool, str]:
        """
        Process payment_intent.payment_failed event.
        """
        from apps.payments.models import CardCheckoutSession

        try:
            payment_intent_id = event_data.get('id')
            
            # Find checkout session
            checkout_session = CardCheckoutSession.objects.get(
                stripe_payment_intent_id=payment_intent_id,
            )

            # Update checkout session
            checkout_session.status = 'failed'
            checkout_session.failure_reason = event_data.get('last_payment_error', {}).get('message', 'Unknown error')
            checkout_session.save(update_fields=[
                'status',
                'failure_reason',
                'updated_at',
            ])

            logger.warning(
                f"Card payment failed: {checkout_session.reference} "
                f"Reason: {checkout_session.failure_reason}"
            )

            return True, "Payment failed"

        except CardCheckoutSession.DoesNotExist:
            logger.warning(f"Checkout session not found for payment intent: {payment_intent_id}")
            return False, "Checkout session not found"

    @staticmethod
    def get_checkout_session(checkout_session_id: str) -> dict | None:
        """
        Get checkout session details.
        """
        from apps.payments.models import CardCheckoutSession

        try:
            session = CardCheckoutSession.objects.get(id=checkout_session_id)

            return {
                'id': str(session.id),
                'reference': session.reference,
                'amount': session.amount,
                'currency': session.currency,
                'status': session.status,
                'stripe_session_id': session.stripe_session_id,
                'stripe_session_url': session.stripe_session_url,
                'stripe_payment_intent_id': session.stripe_payment_intent_id,
                'failure_reason': session.failure_reason,
                'user_name': session.user.full_name,
                'chama_name': session.chama.name if session.chama else None,
                'created_at': session.created_at.isoformat(),
                'completed_at': session.completed_at.isoformat() if session.completed_at else None,
            }

        except CardCheckoutSession.DoesNotExist:
            return None

    @staticmethod
    def get_payment_history(
        chama: Chama = None,
        user: User = None,
        status: str = None,
    ) -> list[dict]:
        """
        Get card payment history.
        """
        from apps.payments.models import CardCheckoutSession

        queryset = CardCheckoutSession.objects.all()

        if chama:
            queryset = queryset.filter(chama=chama)

        if user:
            queryset = queryset.filter(user=user)

        if status:
            queryset = queryset.filter(status=status)

        sessions = queryset.order_by('-created_at')

        return [
            {
                'id': str(session.id),
                'reference': session.reference,
                'amount': session.amount,
                'currency': session.currency,
                'status': session.status,
                'user_name': session.user.full_name,
                'chama_name': session.chama.name if session.chama else None,
                'created_at': session.created_at.isoformat(),
                'completed_at': session.completed_at.isoformat() if session.completed_at else None,
            }
            for session in sessions
        ]
