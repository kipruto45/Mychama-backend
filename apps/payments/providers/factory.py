"""
Unified Payment Provider Factory.

Factory for creating payment provider instances for the supported
payment methods and configuration.
"""

from __future__ import annotations

import logging

from .base import CardPaymentProvider
from .bank_provider import BankProvider
from .cash_provider import CashProvider
from .mpesa_provider import MpesaProvider
from .unified_base import PaymentProvider, PaymentProviderError
from .wallet_provider import WalletProvider

logger = logging.getLogger(__name__)


class PaymentProviderFactory:
    """Factory for creating payment provider instances."""

    _providers: dict[str, dict[str, type[PaymentProvider]]] = {
        "mpesa": {
            "safaricom": MpesaProvider,
        },
        "bank": {
            "manual": BankProvider,
        },
        "cash": {
            "manual": CashProvider,
        },
        "wallet": {
            "internal": WalletProvider,
        },
    }

    _instance_cache: dict[str, PaymentProvider] = {}

    @classmethod
    def get_provider(
        cls,
        payment_method: str,
        provider_name: str | None = None,
    ) -> PaymentProvider:
        """
        Get a payment provider instance.

        Args:
            payment_method: Payment method (mpesa or cash)
            provider_name: Name of the provider.
                          If None, uses the default provider from settings.

        Returns:
            PaymentProvider instance

        Raises:
            PaymentProviderError: If provider is not configured or available
        """
        if payment_method not in cls._providers:
            raise PaymentProviderError(
                f"Unknown payment method: {payment_method}"
            )

        if provider_name is None:
            provider_name = cls._get_default_provider(payment_method)

        cache_key = f"{payment_method}:{provider_name}"

        if cache_key in cls._instance_cache:
            return cls._instance_cache[cache_key]

        if provider_name not in cls._providers[payment_method]:
            raise PaymentProviderError(
                f"Unknown provider {provider_name} for payment method {payment_method}"
            )

        provider_class = cls._providers[payment_method][provider_name]

        try:
            instance = provider_class()
            cls._instance_cache[cache_key] = instance
            logger.info("Payment provider initialized: %s:%s", payment_method, provider_name)
            return instance
        except Exception as e:
            logger.error("Failed to initialize payment provider %s:%s: %s", payment_method, provider_name, e)
            raise PaymentProviderError(
                f"Failed to initialize provider {provider_name}: {e}"
            )

    @classmethod
    def _get_default_provider(cls, payment_method: str) -> str:
        """Get default provider for payment method."""
        defaults = {
            "mpesa": "safaricom",
            "cash": "manual",
            "bank": "manual",
            "wallet": "internal",
        }
        return defaults.get(payment_method, "manual")

    @classmethod
    def register_provider(
        cls,
        payment_method: str,
        name: str,
        provider_class: type[PaymentProvider],
    ) -> None:
        """
        Register a custom payment provider.

        Args:
            payment_method: Payment method
            name: Provider name
            provider_class: Provider class
        """
        if payment_method not in cls._providers:
            cls._providers[payment_method] = {}

        cls._providers[payment_method][name.lower()] = provider_class
        logger.info("Custom payment provider registered: %s:%s", payment_method, name)

    @classmethod
    def get_available_providers(cls) -> dict[str, list[str]]:
        """Get list of available providers by payment method."""
        return {
            method: list(providers.keys())
            for method, providers in cls._providers.items()
        }

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the provider instance cache."""
        cls._instance_cache.clear()


class CardProviderFactory:
    """Legacy card-provider shim that now rejects unsupported card requests."""

    @classmethod
    def get_provider(cls, provider_name: str | None = None) -> CardPaymentProvider:
        raise PaymentProviderError("Card payments are not supported. Please use M-Pesa or cash.")

    @classmethod
    def get_available_providers(cls) -> list[str]:
        return []

    @classmethod
    def clear_cache(cls) -> None:
        PaymentProviderFactory.clear_cache()
