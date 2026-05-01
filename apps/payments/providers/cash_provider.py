"""
Cash Payment Provider Implementation.

Implements the payment provider interface for cash payments.
Cash payments require manual verification by authorized personnel.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any

from .unified_base import (
    PaymentIntent,
    PaymentProvider,
    PaymentProviderError,
    PaymentResult,
    WebhookVerificationResult,
)

logger = logging.getLogger(__name__)


class CashProvider(PaymentProvider):
    """Cash payment provider implementation."""

    @property
    def provider_name(self) -> str:
        return "manual"

    @property
    def payment_method(self) -> str:
        return "cash"

    @property
    def supported_currencies(self) -> list[str]:
        return ["KES", "USD", "EUR", "GBP", "UGX", "TZS", "RWF", "GHS", "NGN", "ZAR"]

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
    ) -> PaymentIntent:
        """Create cash payment intent."""
        if not self.validate_currency(currency):
            raise PaymentProviderError(f"Currency {currency} not supported")

        try:
            # Cash payments don't need provider integration
            # They just need to be recorded and verified manually
            provider_token = (idempotency_key or uuid.uuid4().hex)[:20]
            return PaymentIntent(
                provider_intent_id=f"cash_{provider_token}",
                status="pending_verification",
                amount=amount,
                currency=currency.upper(),
                metadata={
                    "reference": reference,
                    "description": description,
                    **(metadata or {}),
                },
            )

        except Exception as e:
            logger.error("Cash payment creation failed: %s", e)
            raise PaymentProviderError(f"Cash payment creation failed: {e}")

    def verify_payment(self, provider_reference: str) -> PaymentResult:
        """Verify cash payment status."""
        # Cash payments are verified manually by authorized personnel
        # This method returns the current status from our database
        return PaymentResult(
            provider_reference=provider_reference,
            status="pending_verification",
            amount=Decimal("0.00"),
            currency="KES",
            payer_reference="",
            provider_metadata={
                "verification_type": "manual",
                "requires_approval": True,
            },
        )

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str | None,
        headers: dict[str, str] | None = None,
    ) -> WebhookVerificationResult:
        """Cash payments don't use webhooks."""
        return WebhookVerificationResult(
            is_valid=False,
            error="Cash payments do not use webhooks",
        )

    def parse_webhook_event(
        self,
        payload: dict[str, Any],
    ) -> tuple[str, str | None, dict[str, Any]]:
        """Cash payments don't use webhooks."""
        return "cash.manual", None, payload

    def refund_payment(
        self,
        provider_reference: str,
        amount: Decimal | None = None,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Refund cash payment."""
        # Cash refunds are handled manually
        return {
            "refund_id": f"cash_refund_{provider_reference}",
            "status": "pending_verification",
            "amount": amount,
            "currency": "KES",
        }
