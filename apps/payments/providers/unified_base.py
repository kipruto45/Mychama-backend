"""
Unified Payment Provider Abstraction Layer.

Defines the interface that all payment providers must implement,
regardless of payment method (M-Pesa, Card, Cash, Bank).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


class PaymentProviderError(Exception):
    """Base exception for payment provider errors."""

    def __init__(
        self,
        message: str,
        provider_error_code: str | None = None,
        provider_error_message: str | None = None,
        is_retryable: bool = False,
    ):
        super().__init__(message)
        self.provider_error_code = provider_error_code
        self.provider_error_message = provider_error_message
        self.is_retryable = is_retryable


class PaymentProviderAuthenticationError(PaymentProviderError):
    """Raised when provider authentication fails."""
    pass


class PaymentProviderNetworkError(PaymentProviderError):
    """Raised when network communication with provider fails."""

    def __init__(self, message: str, **kwargs):
        super().__init__(message, is_retryable=True, **kwargs)


class PaymentProviderInvalidRequestError(PaymentProviderError):
    """Raised when the request to provider is invalid."""
    pass


class PaymentProviderRateLimitError(PaymentProviderError):
    """Raised when provider rate limit is exceeded."""

    def __init__(self, message: str, **kwargs):
        super().__init__(message, is_retryable=True, **kwargs)


@dataclass
class PaymentIntent:
    """Represents a payment intent created with the provider."""

    provider_intent_id: str
    client_secret: str | None = None
    checkout_url: str | None = None
    status: str = "pending"
    amount: Decimal = Decimal("0.00")
    currency: str = "KES"
    metadata: dict[str, Any] | None = None


@dataclass
class PaymentResult:
    """Represents the result of a payment verification."""

    provider_reference: str
    status: str  # success, failed, pending
    amount: Decimal
    currency: str
    payer_reference: str | None = None  # phone, card last4, bank ref
    provider_metadata: dict[str, Any] | None = None
    failure_reason: str | None = None


@dataclass
class WebhookVerificationResult:
    """Represents the result of webhook signature verification."""

    is_valid: bool
    event_type: str | None = None
    provider_reference: str | None = None
    payload: dict[str, Any] | None = None
    error: str | None = None


class PaymentProvider(abc.ABC):
    """
    Abstract base class for payment providers.

    All payment providers must implement this interface to ensure
    consistent behavior across different payment methods.
    """

    @abc.abstractmethod
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
        """
        Create a payment with the provider.

        Args:
            amount: Payment amount
            currency: Currency code (e.g., KES, USD)
            reference: Unique payment reference
            description: Payment description
            payer_phone: Payer phone number
            payer_email: Payer email address
            metadata: Additional metadata
            idempotency_key: Idempotency key to prevent duplicate charges

        Returns:
            PaymentIntent with provider details

        Raises:
            PaymentProviderError: If payment creation fails
        """
        pass

    @abc.abstractmethod
    def verify_payment(
        self,
        provider_reference: str,
    ) -> PaymentResult:
        """
        Verify payment status with the provider.

        Args:
            provider_reference: Provider's payment reference

        Returns:
            PaymentResult with payment details

        Raises:
            PaymentProviderError: If verification fails
        """
        pass

    @abc.abstractmethod
    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str | None,
        headers: dict[str, str] | None = None,
    ) -> WebhookVerificationResult:
        """
        Verify webhook signature from provider.

        Args:
            payload: Raw webhook payload
            signature: Signature header value
            headers: All request headers

        Returns:
            WebhookVerificationResult with verification status

        Raises:
            PaymentProviderError: If verification fails
        """
        pass

    @abc.abstractmethod
    def parse_webhook_event(
        self,
        payload: dict[str, Any],
    ) -> tuple[str, str | None, dict[str, Any]]:
        """
        Parse webhook event to extract payment details.

        Args:
            payload: Webhook payload

        Returns:
            Tuple of (event_type, provider_reference, metadata)

        Raises:
            PaymentProviderError: If parsing fails
        """
        pass

    @abc.abstractmethod
    def refund_payment(
        self,
        provider_reference: str,
        amount: Decimal | None = None,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """
        Refund a payment.

        Args:
            provider_reference: Provider's payment reference
            amount: Refund amount (None for full refund)
            reason: Refund reason
            idempotency_key: Idempotency key

        Returns:
            Dict with refund details

        Raises:
            PaymentProviderError: If refund fails
        """
        pass

    @property
    @abc.abstractmethod
    def provider_name(self) -> str:
        """Return the provider name."""
        pass

    @property
    @abc.abstractmethod
    def payment_method(self) -> str:
        """Return the payment method."""
        pass

    @property
    @abc.abstractmethod
    def supported_currencies(self) -> list[str]:
        """Return list of supported currencies."""
        pass

    def validate_currency(self, currency: str) -> bool:
        """Validate if currency is supported."""
        return currency.upper() in [c.upper() for c in self.supported_currencies]
