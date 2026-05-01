"""
Stripe card payment provider implementation.

Implements the card payment provider interface for Stripe.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import stripe
from django.conf import settings

from .base import (
    CardPaymentIntent,
    CardPaymentProvider,
    CardPaymentProviderAuthenticationError,
    CardPaymentProviderError,
    CardPaymentProviderInvalidRequestError,
    CardPaymentProviderNetworkError,
    CardPaymentProviderRateLimitError,
    CardPaymentResult,
    WebhookVerificationResult,
)

logger = logging.getLogger(__name__)


class StripeCardProvider(CardPaymentProvider):
    """Stripe card payment provider implementation."""

    def __init__(self):
        self.api_key = getattr(settings, "STRIPE_SECRET_KEY", "")
        self.webhook_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")
        self.publishable_key = getattr(settings, "STRIPE_PUBLISHABLE_KEY", "")

        if not self.api_key:
            raise CardPaymentProviderError("Stripe API key not configured")

        stripe.api_key = self.api_key
        stripe.api_version = "2023-10-16"

    @property
    def provider_name(self) -> str:
        return "stripe"

    @property
    def payment_method(self) -> str:
        return "card"

    @property
    def supported_currencies(self) -> list[str]:
        return ["KES", "USD", "EUR", "GBP", "UGX", "TZS", "RWF", "GHS", "NGN", "ZAR"]

    def _convert_amount_to_smallest_unit(self, amount: Decimal, currency: str) -> int:
        """
        Convert amount to smallest currency unit (e.g., cents for USD).

        Stripe expects amounts in smallest currency unit.
        """
        currency_multipliers = {
            "KES": 100,  # cents
            "USD": 100,
            "EUR": 100,
            "GBP": 100,
            "UGX": 1,  # no smaller unit
            "TZS": 100,
            "RWF": 1,  # no smaller unit
            "GHS": 100,
            "NGN": 100,
            "ZAR": 100,
        }
        multiplier = currency_multipliers.get(currency.upper(), 100)
        return int(amount * multiplier)

    def _handle_stripe_error(self, error: stripe.error.StripeError) -> None:
        """Convert Stripe errors to provider errors."""
        if isinstance(error, stripe.error.AuthenticationError):
            raise CardPaymentProviderAuthenticationError(
                "Stripe authentication failed",
                provider_error_code=error.code,
                provider_error_message=str(error),
            )
        elif isinstance(error, stripe.error.InvalidRequestError):
            raise CardPaymentProviderInvalidRequestError(
                f"Invalid Stripe request: {error}",
                provider_error_code=error.code,
                provider_error_message=str(error),
            )
        elif isinstance(error, stripe.error.RateLimitError):
            raise CardPaymentProviderRateLimitError(
                "Stripe rate limit exceeded",
                provider_error_code=error.code,
                provider_error_message=str(error),
            )
        elif isinstance(error, stripe.error.APIConnectionError):
            raise CardPaymentProviderNetworkError(
                "Stripe network error",
                provider_error_code=error.code,
                provider_error_message=str(error),
            )
        else:
            raise CardPaymentProviderError(
                f"Stripe error: {error}",
                provider_error_code=getattr(error, "code", None),
                provider_error_message=str(error),
            )

    def create_payment_intent(
        self,
        amount: Decimal,
        currency: str,
        reference: str,
        description: str,
        customer_email: str | None = None,
        customer_phone: str | None = None,
        metadata: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> CardPaymentIntent:
        """Create a Stripe PaymentIntent."""
        if not self.validate_currency(currency):
            raise CardPaymentProviderInvalidRequestError(
                f"Currency {currency} not supported by Stripe"
            )

        try:
            amount_smallest = self._convert_amount_to_smallest_unit(amount, currency)

            intent_metadata = {
                "reference": reference,
                "platform": "mychama",
                **(metadata or {}),
            }

            intent_params = {
                "amount": amount_smallest,
                "currency": currency.lower(),
                "metadata": intent_metadata,
                "description": description,
                "payment_method_types": ["card"],
                "capture_method": "automatic",
            }

            if customer_email:
                intent_params["receipt_email"] = customer_email

            if idempotency_key:
                intent_params["idempotency_key"] = idempotency_key

            intent = stripe.PaymentIntent.create(**intent_params)

            logger.info(
                "Stripe PaymentIntent created: %s for reference %s",
                intent.id,
                reference,
            )

            return CardPaymentIntent(
                provider_intent_id=intent.id,
                client_secret=intent.client_secret,
                status=intent.status,
                amount=amount,
                currency=currency.upper(),
                metadata=intent_metadata,
            )

        except stripe.error.StripeError as e:
            logger.error("Stripe PaymentIntent creation failed: %s", e)
            self._handle_stripe_error(e)

    def create_payment(
        self,
        amount: Decimal,
        currency: str,
        reference: str,
        description: str,
        payer_phone: str | None = None,
        payer_email: str | None = None,
        metadata: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> CardPaymentIntent:
        """Unified adapter for card payments."""
        return self.create_payment_intent(
            amount=amount,
            currency=currency,
            reference=reference,
            description=description,
            customer_email=payer_email,
            customer_phone=payer_phone,
            metadata=metadata,
            idempotency_key=idempotency_key,
        )

    def verify_payment(self, provider_reference: str) -> CardPaymentResult:
        """Verify payment status with Stripe."""
        try:
            intent = stripe.PaymentIntent.retrieve(provider_reference)

            status_map = {
                "succeeded": "success",
                "processing": "pending",
                "requires_payment_method": "pending",
                "requires_confirmation": "pending",
                "requires_action": "pending",
                "canceled": "failed",
            }

            status = status_map.get(intent.status, "pending")

            card_brand = None
            card_last4 = None
            authorization_code = None

            if intent.payment_method:
                payment_method = stripe.PaymentMethod.retrieve(intent.payment_method)
                if payment_method.card:
                    card_brand = payment_method.card.brand
                    card_last4 = payment_method.card.last4

            failure_reason = None
            if intent.last_payment_error:
                failure_reason = intent.last_payment_error.message

            return CardPaymentResult(
                provider_reference=intent.id,
                status=status,
                amount=Decimal(str(intent.amount)) / 100,
                currency=intent.currency.upper(),
                card_brand=card_brand,
                card_last4=card_last4,
                authorization_code=authorization_code,
                provider_metadata={
                    "stripe_status": intent.status,
                    "payment_method": intent.payment_method,
                    "charges": intent.charges.data if intent.charges else [],
                },
                failure_reason=failure_reason,
            )

        except stripe.error.StripeError as e:
            logger.error("Stripe payment verification failed: %s", e)
            self._handle_stripe_error(e)

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str | None,
        headers: dict[str, str] | None = None,
    ) -> WebhookVerificationResult:
        """Verify Stripe webhook signature."""
        if not self.webhook_secret:
            logger.warning("Stripe webhook secret not configured")
            return WebhookVerificationResult(
                is_valid=False,
                error="Webhook secret not configured",
            )

        if not signature:
            return WebhookVerificationResult(
                is_valid=False,
                error="Missing Stripe signature header",
            )

        try:
            event = stripe.Webhook.construct_event(
                payload, signature, self.webhook_secret
            )
            normalized_payload = (
                event.to_dict_recursive()
                if hasattr(event, "to_dict_recursive")
                else {
                    "type": event.type,
                    "data": {
                        "object": (
                            event.data.object.to_dict_recursive()
                            if hasattr(event.data.object, "to_dict_recursive")
                            else dict(event.data.object)
                        )
                    },
                }
            )

            event_type = event.type
            provider_reference = None
            data_object = normalized_payload.get("data", {}).get("object", {})

            if event_type.startswith("payment_intent."):
                provider_reference = data_object.get("id")
            elif event_type.startswith("charge.dispute."):
                provider_reference = (
                    data_object.get("payment_intent")
                    or data_object.get("charge")
                    or data_object.get("id")
                )

            return WebhookVerificationResult(
                is_valid=True,
                event_type=event_type,
                provider_reference=provider_reference,
                payload=normalized_payload,
            )

        except stripe.error.SignatureVerificationError as e:
            logger.warning("Stripe webhook signature verification failed: %s", e)
            return WebhookVerificationResult(
                is_valid=False,
                error=f"Invalid signature: {e}",
            )
        except Exception as e:
            logger.error("Stripe webhook verification error: %s", e)
            return WebhookVerificationResult(
                is_valid=False,
                error=f"Verification error: {e}",
            )

    def parse_webhook_event(
        self,
        payload: dict[str, Any],
    ) -> tuple[str, str | None, dict[str, Any]]:
        """Parse Stripe webhook event."""
        event_type = payload.get("type", "")
        data_object = payload.get("data", {}).get("object", {})

        provider_reference = None
        metadata = {}

        if event_type.startswith("payment_intent."):
            provider_reference = data_object.get("id")
            metadata = {
                "stripe_status": data_object.get("status"),
                "amount": data_object.get("amount"),
                "currency": data_object.get("currency"),
                "reference": (data_object.get("metadata") or {}).get("reference"),
            }
        elif event_type.startswith("charge.dispute."):
            provider_reference = (
                data_object.get("payment_intent")
                or data_object.get("charge")
                or data_object.get("id")
            )
            metadata = {
                "dispute_status": data_object.get("status"),
                "dispute_reason": data_object.get("reason"),
                "amount": (
                    Decimal(str(data_object.get("amount", 0))) / 100
                    if data_object.get("amount") is not None
                    else None
                ),
                "currency": str(data_object.get("currency") or "").upper(),
                "provider_case_reference": data_object.get("id"),
                "payment_intent_id": data_object.get("payment_intent"),
                "charge_reference": data_object.get("charge"),
                "reference": (data_object.get("metadata") or {}).get("reference"),
            }

        return event_type, provider_reference, metadata

    def refund_payment(
        self,
        provider_reference: str,
        amount: Decimal | None = None,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Refund a Stripe payment."""
        try:
            refund_params = {"payment_intent": provider_reference}

            if amount is not None:
                refund_params["amount"] = self._convert_amount_to_smallest_unit(
                    amount, "KES"
                )

            if reason:
                refund_params["reason"] = reason

            if idempotency_key:
                refund_params["idempotency_key"] = idempotency_key

            refund = stripe.Refund.create(**refund_params)

            logger.info(
                "Stripe refund created: %s for payment %s",
                refund.id,
                provider_reference,
            )

            return {
                "refund_id": refund.id,
                "status": refund.status,
                "amount": Decimal(str(refund.amount)) / 100,
                "currency": refund.currency.upper(),
            }

        except stripe.error.StripeError as e:
            logger.error("Stripe refund failed: %s", e)
            self._handle_stripe_error(e)
