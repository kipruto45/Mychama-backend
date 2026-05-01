"""
Payment Providers Module
Multi-provider payment support for subscriptions
"""
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


@dataclass
class PaymentResult:
    """Result of a payment operation"""
    success: bool
    transaction_id: str | None = None
    error_message: str | None = None
    provider_response: dict[str, Any] | None = None
    checkout_url: str | None = None


class PaymentProvider(ABC):
    """Abstract base class for payment providers"""
    
    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Unique identifier for this provider"""
        pass
    
    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable name"""
        pass
    
    @abstractmethod
    def create_checkout_session(
        self,
        plan_id: int,
        plan_name: str,
        amount: Decimal,
        currency: str,
        billing_cycle: str,
        customer_email: str,
        customer_phone: str,
        chama_id: str,
        success_url: str,
        cancel_url: str,
    ) -> PaymentResult:
        """Create a checkout session for subscription"""
        pass
    
    @abstractmethod
    def handle_webhook(self, payload: bytes, signature: str) -> dict[str, Any] | None:
        """Handle webhook events from payment provider"""
        pass
    
    @abstractmethod
    def cancel_subscription(self, subscription_id: str) -> PaymentResult:
        """Cancel an active subscription"""
        pass
    
    @abstractmethod
    def get_subscription_status(self, subscription_id: str) -> dict[str, Any] | None:
        """Get current subscription status"""
        pass


class StripeProvider(PaymentProvider):
    """Stripe payment provider"""
    
    def __init__(self):
        self.stripe_api_key = None  # Set from settings
        self.webhook_secret = None
    
    @property
    def provider_id(self) -> str:
        return 'stripe'
    
    @property
    def provider_name(self) -> str:
        return 'Stripe'
    
    def create_checkout_session(
        self,
        plan_id: int,
        plan_name: str,
        amount: Decimal,
        currency: str,
        billing_cycle: str,
        customer_email: str,
        customer_phone: str,
        chama_id: str,
        success_url: str,
        cancel_url: str,
    ) -> PaymentResult:
        """Create Stripe checkout session"""
        try:
            import stripe
            stripe.api_key = self.stripe_api_key
            
            # Map currency to Stripe format
            stripe_currency = currency.lower()
            
            # Create session
            session = stripe.checkout.Session.create(
                payment_method_types=['card', 'mpesa'],
                line_items=[{
                    'price_data': {
                        'currency': stripe_currency,
                        'unit_amount': int(amount * 100),  # Convert to cents
                        'recurring': {
                            'interval': 'month' if billing_cycle == 'monthly' else 'year',
                        },
                        'product_data': {
                            'name': f'{plan_name} - Chama Subscription',
                            'description': f'Monthly subscription for Chama {chama_id}',
                        },
                    },
                    'quantity': 1,
                }],
                mode='subscription',
                customer_email=customer_email,
                success_url=success_url,
                cancel_url=cancel_url,
                metadata={
                    'chama_id': chama_id,
                    'plan_id': str(plan_id),
                    'billing_cycle': billing_cycle,
                },
            )
            
            return PaymentResult(
                success=True,
                transaction_id=session.id,
                checkout_url=session.url,
                provider_response={'session_id': session.id, 'session_url': session.url}
            )
        except ImportError:
            logger.warning("Stripe not installed")
            return PaymentResult(success=False, error_message="Stripe not configured")
        except Exception as e:
            logger.error(f"Stripe checkout error: {e}")
            return PaymentResult(success=False, error_message=str(e))
    
    def handle_webhook(self, payload: bytes, signature: str) -> dict[str, Any] | None:
        """Handle Stripe webhook"""
        try:
            import stripe
            stripe.api_key = self.stripe_api_key
            
            event = stripe.Webhook.construct_event(
                payload, signature, self.webhook_secret
            )
            
            # Handle event types
            if event['type'] == 'checkout.session.completed':
                return {
                    'event': 'payment_succeeded',
                    'data': event['data']['object'],
                }
            elif event['type'] == 'customer.subscription.updated':
                return {
                    'event': 'subscription_updated',
                    'data': event['data']['object'],
                }
            elif event['type'] == 'customer.subscription.deleted':
                return {
                    'event': 'subscription_cancelled',
                    'data': event['data']['object'],
                }
            elif event['type'] == 'invoice.payment_failed':
                return {
                    'event': 'payment_failed',
                    'data': event['data']['object'],
                }
            
            return None
        except Exception as e:
            logger.error(f"Stripe webhook error: {e}")
            return None
    
    def cancel_subscription(self, subscription_id: str) -> PaymentResult:
        """Cancel Stripe subscription"""
        try:
            import stripe
            stripe.api_key = self.stripe_api_key
            
            subscription = stripe.Subscription.modify(
                subscription_id,
                cancel_at_period_end=True
            )
            
            return PaymentResult(
                success=True,
                provider_response={'subscription_id': subscription.id, 'cancel_at': subscription.cancel_at}
            )
        except Exception as e:
            logger.error(f"Stripe cancel error: {e}")
            return PaymentResult(success=False, error_message=str(e))
    
    def get_subscription_status(self, subscription_id: str) -> dict[str, Any] | None:
        """Get Stripe subscription status"""
        try:
            import stripe
            stripe.api_key = self.stripe_api_key
            
            subscription = stripe.Subscription.retrieve(subscription_id)
            
            return {
                'status': subscription.status,
                'current_period_end': subscription.current_period_end,
                'cancel_at_period_end': subscription.cancel_at_period_end,
            }
        except Exception as e:
            logger.error(f"Stripe status error: {e}")
            return None


class PayPalProvider(PaymentProvider):
    """PayPal payment provider"""
    
    def __init__(self):
        self.client_id = None
        self.client_secret = None
        self.mode = 'sandbox'  # sandbox or live
    
    @property
    def provider_id(self) -> str:
        return 'paypal'
    
    @property
    def provider_name(self) -> str:
        return 'PayPal'
    
    def create_checkout_session(
        self,
        plan_id: int,
        plan_name: str,
        amount: Decimal,
        currency: str,
        billing_cycle: str,
        customer_email: str,
        customer_phone: str,
        chama_id: str,
        success_url: str,
        cancel_url: str,
    ) -> PaymentResult:
        """Create PayPal subscription"""
        # PayPal implementation would use their REST API
        # This is a placeholder for the actual implementation
        return PaymentResult(
            success=False,
            error_message="PayPal integration coming soon"
        )
    
    def handle_webhook(self, payload: bytes, signature: str) -> dict[str, Any] | None:
        """Handle PayPal webhook"""
        # PayPal webhook handling
        return None
    
    def cancel_subscription(self, subscription_id: str) -> PaymentResult:
        """Cancel PayPal subscription"""
        return PaymentResult(
            success=False,
            error_message="PayPal integration coming soon"
        )
    
    def get_subscription_status(self, subscription_id: str) -> dict[str, Any] | None:
        """Get PayPal subscription status"""
        return None


class MpesaProvider(PaymentProvider):
    """M-Pesa payment provider for Kenyan market"""
    
    def __init__(self):
        self.consumer_key = None
        self.consumer_secret = None
        self.shortcode = None
        self.passkey = None
    
    @property
    def provider_id(self) -> str:
        return 'mpesa'
    
    @property
    def provider_name(self) -> str:
        return 'M-Pesa'
    
    def create_checkout_session(
        self,
        plan_id: int,
        plan_name: str,
        amount: Decimal,
        currency: str,
        billing_cycle: str,
        customer_email: str,
        customer_phone: str,
        chama_id: str,
        success_url: str,
        cancel_url: str,
    ) -> PaymentResult:
        """Initiate M-Pesa STK Push"""
        try:
            if not customer_phone:
                return PaymentResult(
                    success=False,
                    error_message='A billing contact phone number is required for M-Pesa checkout.',
                )

            if getattr(settings, 'MPESA_USE_STUB', True):
                checkout_request_id = f'ws_CO_{uuid.uuid4().hex[:24]}'
                merchant_request_id = f'MR_{uuid.uuid4().hex[:20]}'
            else:
                from apps.payments.mpesa_client import MpesaClient

                client = MpesaClient()
                response_payload = client.initiate_stk_push(
                    phone_number=customer_phone,
                    amount=str(amount.quantize(Decimal('0.01'))),
                    account_reference=f'SUB-{plan_id}-{str(chama_id)[:8]}',
                    transaction_desc=f'{plan_name} subscription',
                    callback_url=f"{client.callback_base_url}/api/v1/billing/webhooks/mpesa/",
                )
                checkout_request_id = str(
                    response_payload.get('CheckoutRequestID') or f'ws_CO_{uuid.uuid4().hex[:24]}'
                )
                merchant_request_id = str(response_payload.get('MerchantRequestID') or '')

            return PaymentResult(
                success=True,
                transaction_id=checkout_request_id,
                checkout_url=success_url,
                provider_response={
                    'checkout_request_id': checkout_request_id,
                    'merchant_request_id': merchant_request_id,
                    'redirect_to': success_url,
                },
            )
        except Exception as exc:
            logger.error(f"M-Pesa checkout error: {exc}")
            return PaymentResult(success=False, error_message=str(exc))
    
    def handle_webhook(self, payload: bytes, signature: str) -> dict[str, Any] | None:
        """Handle M-Pesa webhook (callback)"""
        try:
            import json

            data = json.loads(payload.decode('utf-8'))
            body = data.get('Body', {}) if isinstance(data, dict) else {}
            callback = body.get('stkCallback', {})
            checkout_request_id = str(callback.get('CheckoutRequestID') or '').strip()
            result_code = int(callback.get('ResultCode') or 0)
            metadata = {
                'merchant_request_id': str(callback.get('MerchantRequestID') or '').strip(),
                'checkout_request_id': checkout_request_id,
            }
            items = callback.get('CallbackMetadata', {}).get('Item', []) or []
            receipt = ''
            amount = None
            for item in items:
                name = item.get('Name')
                value = item.get('Value')
                if name == 'MpesaReceiptNumber':
                    receipt = str(value or '').strip()
                elif name == 'Amount':
                    try:
                        amount = Decimal(str(value))
                    except Exception:
                        amount = None
            event_name = 'payment_succeeded' if result_code == 0 else 'payment_failed'
            return {
                'event': event_name,
                'data': {
                    'id': checkout_request_id,
                    'payment_reference': receipt,
                    'amount': amount,
                    'metadata': metadata,
                    'raw': data,
                    'result_code': result_code,
                    'result_desc': str(callback.get('ResultDesc') or ''),
                },
            }
        except Exception as exc:
            logger.error(f"M-Pesa webhook parse error: {exc}")
            return None
    
    def cancel_subscription(self, subscription_id: str) -> PaymentResult:
        """Cancel M-Pesa subscription (requires manual refund)"""
        return PaymentResult(
            success=False,
            error_message="M-Pesa subscriptions require manual cancellation"
        )
    
    def get_subscription_status(self, subscription_id: str) -> dict[str, Any] | None:
        """Get M-Pesa subscription status"""
        return None


class ManualProvider(PaymentProvider):
    """Manual/offline payment provider"""
    
    @property
    def provider_id(self) -> str:
        return 'manual'
    
    @property
    def provider_name(self) -> str:
        return 'Manual Payment'
    
    def create_checkout_session(
        self,
        plan_id: int,
        plan_name: str,
        amount: Decimal,
        currency: str,
        billing_cycle: str,
        customer_email: str,
        customer_phone: str,
        chama_id: str,
        success_url: str,
        cancel_url: str,
    ) -> PaymentResult:
        """Manual payment - redirect to instructions"""
        transaction_id = f"manual_{uuid.uuid4().hex}"
        return PaymentResult(
            success=True,
            transaction_id=transaction_id,
            checkout_url=(
                f'/billing/payment-instructions?plan={plan_id}&chama={chama_id}'
                f'&cycle={billing_cycle}&provider=manual&session_id={transaction_id}'
            ),
            provider_response={
                'instructions': f'Please make payment of {currency} {amount} to our account and upload proof of payment.'
            }
        )
    
    def handle_webhook(self, payload: bytes, signature: str) -> dict[str, Any] | None:
        """Manual payments don't have webhooks"""
        return None
    
    def cancel_subscription(self, subscription_id: str) -> PaymentResult:
        """Cancel manual subscription"""
        return PaymentResult(success=True)
    
    def get_subscription_status(self, subscription_id: str) -> dict[str, Any] | None:
        """Manual subscriptions are managed in Django"""
        return {'status': 'active', 'managed_by': 'django'}


class PaymentProviderFactory:
    """Factory for creating payment provider instances"""
    
    _providers: dict[str, PaymentProvider] = {}
    _initialized = False
    
    @classmethod
    def initialize(cls):
        """Initialize providers from Django settings"""
        # Initialize Stripe
        stripe_key = getattr(settings, 'STRIPE_SECRET_KEY', None)
        if stripe_key:
            provider = StripeProvider()
            provider.stripe_api_key = stripe_key
            provider.webhook_secret = getattr(settings, 'STRIPE_WEBHOOK_SECRET', None)
            cls._providers['stripe'] = provider
        
        # Initialize PayPal
        paypal_client = getattr(settings, 'PAYPAL_CLIENT_ID', None)
        if paypal_client:
            provider = PayPalProvider()
            provider.client_id = paypal_client
            provider.client_secret = getattr(settings, 'PAYPAL_CLIENT_SECRET', None)
            provider.mode = getattr(settings, 'PAYPAL_MODE', 'sandbox')
            cls._providers['paypal'] = provider
        
        # Initialize M-Pesa
        mpesa_key = getattr(settings, 'MPESA_CONSUMER_KEY', None)
        if mpesa_key:
            provider = MpesaProvider()
            provider.consumer_key = mpesa_key
            provider.consumer_secret = getattr(settings, 'MPESA_CONSUMER_SECRET', None)
            provider.shortcode = getattr(settings, 'MPESA_SHORTCODE', None)
            provider.passkey = getattr(settings, 'MPESA_PASSKEY', None)
            cls._providers['mpesa'] = provider
        
        # Always add manual provider
        cls._providers['manual'] = ManualProvider()
        
        cls._initialized = True
    
    @classmethod
    def get_provider(cls, provider_id: str) -> PaymentProvider:
        """Get provider by ID"""
        if not cls._initialized:
            cls.initialize()
        
        provider = cls._providers.get(provider_id)
        if not provider:
            # Fallback to manual
            provider = cls._providers.get('manual', ManualProvider())
        
        return provider
    
    @classmethod
    def get_available_providers(cls) -> list:
        """Get list of available providers"""
        if not cls._initialized:
            cls.initialize()
        
        return [
            {
                'id': p.provider_id,
                'name': p.provider_name,
            }
            for p in cls._providers.values()
        ]
    
    @classmethod
    def create_checkout(
        cls,
        provider_id: str,
        plan_id: int,
        plan_name: str,
        amount: Decimal,
        currency: str,
        billing_cycle: str,
        customer_email: str,
        customer_phone: str,
        chama_id: str,
        success_url: str,
        cancel_url: str,
    ) -> PaymentResult:
        """Create checkout session with specified provider"""
        provider = cls.get_provider(provider_id)
        return provider.create_checkout_session(
            plan_id=plan_id,
            plan_name=plan_name,
            amount=amount,
            currency=currency,
            billing_cycle=billing_cycle,
            customer_email=customer_email,
            customer_phone=customer_phone,
            chama_id=chama_id,
            success_url=success_url,
            cancel_url=cancel_url,
        )
