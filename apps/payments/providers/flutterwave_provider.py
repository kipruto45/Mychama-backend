"""
Flutterwave card payment provider implementation.

Implements the card payment provider interface for Flutterwave.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from decimal import Decimal
from typing import Any

import requests
from django.conf import settings

from core.safe_http import OutboundPolicy, UnsafeOutboundRequest, safe_request

from .base import (
    CardPaymentIntent,
    CardPaymentProvider,
    CardPaymentProviderAuthenticationError,
    CardPaymentProviderError,
    CardPaymentProviderInvalidRequestError,
    CardPaymentProviderNetworkError,
    CardPaymentResult,
    WebhookVerificationResult,
)

logger = logging.getLogger(__name__)


class FlutterwaveCardProvider(CardPaymentProvider):
    """Flutterwave card payment provider implementation."""

    BASE_URL = "https://api.flutterwave.com/v3"

    def __init__(self):
        self.secret_key = getattr(settings, "FLUTTERWAVE_SECRET_KEY", "")
        self.public_key = getattr(settings, "FLUTTERWAVE_PUBLIC_KEY", "")
        self.webhook_secret = getattr(settings, "FLUTTERWAVE_WEBHOOK_SECRET", "")

        if not self.secret_key:
            raise CardPaymentProviderError("Flutterwave secret key not configured")

        self.headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }

    @property
    def provider_name(self) -> str:
        return "flutterwave"

    @property
    def payment_method(self) -> str:
        return "card"

    @property
    def supported_currencies(self) -> list[str]:
        return ["KES", "USD", "EUR", "GBP", "UGX", "TZS", "RWF", "GHS", "NGN", "ZAR", "XAF", "XOF"]

    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make HTTP request to Flutterwave API."""
        url = f"{self.BASE_URL}{endpoint}"

        try:
            response = safe_request(
                method,
                url,
                headers=self.headers,
                json=data,
                policy=OutboundPolicy(allowed_hosts={"api.flutterwave.com"}),
                timeout=30,
            )

            response_data = response.json()

            if response.status_code == 401:
                raise CardPaymentProviderAuthenticationError(
                    "Flutterwave authentication failed",
                    provider_error_code="401",
                    provider_error_message=response_data.get("message", "Unauthorized"),
                )

            if response.status_code == 400:
                raise CardPaymentProviderInvalidRequestError(
                    f"Invalid Flutterwave request: {response_data.get('message', 'Bad request')}",
                    provider_error_code="400",
                    provider_error_message=response_data.get("message", "Bad request"),
                )

            if response.status_code != 200:
                raise CardPaymentProviderError(
                    f"Flutterwave API error: {response_data.get('message', 'Unknown error')}",
                    provider_error_code=str(response.status_code),
                    provider_error_message=response_data.get("message", "Unknown error"),
                )

            return response_data

        except (requests.exceptions.RequestException, UnsafeOutboundRequest) as e:
            logger.error("Flutterwave network error: %s", e)
            raise CardPaymentProviderNetworkError(
                f"Flutterwave network error: {e}",
                provider_error_message=str(e),
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
        """Create a Flutterwave payment link."""
        if not self.validate_currency(currency):
            raise CardPaymentProviderInvalidRequestError(
                f"Currency {currency} not supported by Flutterwave"
            )

        try:
            payload = {
                "tx_ref": reference,
                "amount": str(amount),
                "currency": currency.upper(),
                "redirect_url": getattr(
                    settings,
                    "FLUTTERWAVE_REDIRECT_URL",
                    "https://mychama.com/payments/callback",
                ),
                "payment_options": "card",
                "meta": {
                    "reference": reference,
                    "platform": "mychama",
                    **(metadata or {}),
                },
                "customer": {
                    "email": customer_email or "customer@mychama.com",
                    "phone_number": customer_phone or "",
                    "name": "MyChama User",
                },
                "customizations": {
                    "title": "MyChama Payment",
                    "description": description,
                    "logo": getattr(settings, "FLUTTERWAVE_LOGO_URL", ""),
                },
            }

            response_data = self._make_request("POST", "/payments", payload)

            if response_data.get("status") != "success":
                raise CardPaymentProviderError(
                    f"Flutterwave payment creation failed: {response_data.get('message', 'Unknown error')}",
                    provider_error_message=response_data.get("message", "Unknown error"),
                )

            data = response_data.get("data", {})

            logger.info(
                "Flutterwave payment link created: %s for reference %s",
                data.get("link"),
                reference,
            )

            return CardPaymentIntent(
                provider_intent_id=data.get("id", reference),
                checkout_url=data.get("link"),
                status="pending",
                amount=amount,
                currency=currency.upper(),
                metadata=payload["meta"],
            )

        except CardPaymentProviderError:
            raise
        except Exception as e:
            logger.error("Flutterwave payment creation failed: %s", e)
            raise CardPaymentProviderError(
                f"Flutterwave payment creation failed: {e}",
                provider_error_message=str(e),
            )

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
        """Verify payment status with Flutterwave."""
        try:
            response_data = self._make_request("GET", f"/transactions/{provider_reference}/verify")

            if response_data.get("status") != "success":
                raise CardPaymentProviderError(
                    f"Flutterwave verification failed: {response_data.get('message', 'Unknown error')}",
                    provider_error_message=response_data.get("message", "Unknown error"),
                )

            data = response_data.get("data", {})

            status_map = {
                "successful": "success",
                "failed": "failed",
                "pending": "pending",
            }

            status = status_map.get(data.get("status"), "pending")

            card = data.get("card", {})
            card_brand = card.get("type")
            card_last4 = card.get("last_4digits")

            return CardPaymentResult(
                provider_reference=str(data.get("id", provider_reference)),
                status=status,
                amount=Decimal(str(data.get("amount", 0))),
                currency=data.get("currency", "KES").upper(),
                card_brand=card_brand,
                card_last4=card_last4,
                authorization_code=data.get("auth_code"),
                provider_metadata={
                    "flutterwave_status": data.get("status"),
                    "payment_type": data.get("payment_type"),
                    "flw_ref": data.get("flw_ref"),
                },
                failure_reason=data.get("processor_response") if status == "failed" else None,
            )

        except CardPaymentProviderError:
            raise
        except Exception as e:
            logger.error("Flutterwave payment verification failed: %s", e)
            raise CardPaymentProviderError(
                f"Flutterwave payment verification failed: {e}",
                provider_error_message=str(e),
            )

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str | None,
        headers: dict[str, str] | None = None,
    ) -> WebhookVerificationResult:
        """Verify Flutterwave webhook signature."""
        if not self.webhook_secret:
            logger.warning("Flutterwave webhook secret not configured")
            return WebhookVerificationResult(
                is_valid=False,
                error="Webhook secret not configured",
            )

        if not signature:
            return WebhookVerificationResult(
                is_valid=False,
                error="Missing Flutterwave signature header",
            )

        try:
            expected_signature = hmac.new(
                self.webhook_secret.encode("utf-8"),
                payload,
                hashlib.sha256,
            ).hexdigest()

            if not hmac.compare_digest(expected_signature, signature):
                return WebhookVerificationResult(
                    is_valid=False,
                    error="Invalid Flutterwave signature",
                )

            import json
            payload_dict = json.loads(payload)

            event_type = payload_dict.get("event", "")
            data = payload_dict.get("data", {})
            provider_reference = str(data.get("id", ""))

            return WebhookVerificationResult(
                is_valid=True,
                event_type=event_type,
                provider_reference=provider_reference,
                payload={
                    "event": event_type,
                    "data": data,
                },
            )

        except json.JSONDecodeError as e:
            logger.error("Flutterwave webhook JSON decode error: %s", e)
            return WebhookVerificationResult(
                is_valid=False,
                error=f"Invalid JSON payload: {e}",
            )
        except Exception as e:
            logger.error("Flutterwave webhook verification error: %s", e)
            return WebhookVerificationResult(
                is_valid=False,
                error=f"Verification error: {e}",
            )

    def parse_webhook_event(
        self,
        payload: dict[str, Any],
    ) -> tuple[str, str | None, dict[str, Any]]:
        """Parse Flutterwave webhook event."""
        event_type = payload.get("event", "")
        data = payload.get("data", {})

        provider_reference = str(
            data.get("payment_intent")
            or data.get("tx_ref")
            or data.get("flw_ref")
            or data.get("id", "")
        )
        metadata = {
            "flutterwave_status": data.get("status"),
            "amount": data.get("amount"),
            "currency": data.get("currency"),
            "reference": data.get("tx_ref") or data.get("reference"),
            "provider_case_reference": data.get("dispute_id") or data.get("id"),
            "payment_intent_id": data.get("payment_intent"),
            "provider_transaction_reference": data.get("flw_ref") or data.get("id"),
            "dispute_status": data.get("dispute_status") or data.get("status"),
            "dispute_reason": data.get("narration") or data.get("reason"),
        }

        return event_type, provider_reference, metadata

    def refund_payment(
        self,
        provider_reference: str,
        amount: Decimal | None = None,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Refund a Flutterwave payment."""
        try:
            payload = {
                "id": provider_reference,
            }

            if amount is not None:
                payload["amount"] = str(amount)

            response_data = self._make_request("POST", "/transactions/refund", payload)

            if response_data.get("status") != "success":
                raise CardPaymentProviderError(
                    f"Flutterwave refund failed: {response_data.get('message', 'Unknown error')}",
                    provider_error_message=response_data.get("message", "Unknown error"),
                )

            data = response_data.get("data", {})

            logger.info(
                "Flutterwave refund created: %s for payment %s",
                data.get("id"),
                provider_reference,
            )

            return {
                "refund_id": str(data.get("id")),
                "status": data.get("status"),
                "amount": Decimal(str(data.get("amount", 0))),
                "currency": data.get("currency", "KES").upper(),
            }

        except CardPaymentProviderError:
            raise
        except Exception as e:
            logger.error("Flutterwave refund failed: %s", e)
            raise CardPaymentProviderError(
                f"Flutterwave refund failed: {e}",
                provider_error_message=str(e),
            )
