"""
Internal Wallet Payment Provider Implementation.

Used for in-app wallet funding sources (e.g., paying a contribution from wallet balance).
This provider settles instantly and does not call any external network.
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


class WalletProvider(PaymentProvider):
    """Internal wallet payment provider implementation."""

    @property
    def provider_name(self) -> str:
        return "internal"

    @property
    def payment_method(self) -> str:
        return "wallet"

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
        if not self.validate_currency(currency):
            raise PaymentProviderError(f"Currency {currency} not supported")

        provider_token = (idempotency_key or uuid.uuid4().hex)[:20]
        provider_reference = f"wallet_{provider_token}"
        logger.info("Wallet payment settled: %s for reference %s", provider_reference, reference)
        return PaymentIntent(
            provider_intent_id=provider_reference,
            status="success",
            amount=amount,
            currency=currency.upper(),
            metadata={
                "reference": reference,
                "description": description,
                **(metadata or {}),
            },
        )

    def verify_payment(self, provider_reference: str) -> PaymentResult:
        return PaymentResult(
            provider_reference=provider_reference,
            status="success",
            amount=Decimal("0.00"),
            currency="KES",
            payer_reference="",
            provider_metadata={"verification_type": "internal"},
        )

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str | None,
        headers: dict[str, str] | None = None,
    ) -> WebhookVerificationResult:
        return WebhookVerificationResult(
            is_valid=False,
            error="Wallet provider does not use webhooks",
        )

    def parse_webhook_event(
        self,
        payload: dict[str, Any],
    ) -> tuple[str, str | None, dict[str, Any]]:
        return "wallet.internal", None, payload

    def refund_payment(
        self,
        provider_reference: str,
        amount: Decimal | None = None,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return {
            "refund_id": f"wallet_refund_{provider_reference}",
            "status": "success",
            "amount": amount,
            "currency": "KES",
        }

