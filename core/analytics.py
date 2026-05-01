"""
Analytics Service for MyChama.

Provides integration with:
- PostHog for product analytics
- Firebase Analytics for mobile analytics
- Custom event tracking
"""

import logging
from typing import Any

from django.conf import settings
from posthog import Posthog

logger = logging.getLogger(__name__)


class AnalyticsService:
    """
    Unified analytics service for tracking events across platforms.
    """

    _posthog_client: Posthog | None = None
    _initialized: bool = False

    @classmethod
    def initialize(cls):
        """Initialize analytics clients."""
        if cls._initialized:
            return

        # Initialize PostHog
        posthog_api_key = getattr(settings, "POSTHOG_API_KEY", "")
        posthog_host = getattr(settings, "POSTHOG_HOST", "https://app.posthog.com")

        if posthog_api_key:
            try:
                cls._posthog_client = Posthog(
                    project_api_key=posthog_api_key,
                    host=posthog_host,
                    disable_geoip=False,
                )
                logger.info("PostHog analytics initialized")
            except Exception as e:
                logger.error(f"Failed to initialize PostHog: {e}")
        else:
            logger.warning("PostHog API key not configured")

        cls._initialized = True

    @classmethod
    def get_posthog_client(cls) -> Posthog | None:
        """Get PostHog client instance."""
        if not cls._initialized:
            cls.initialize()
        return cls._posthog_client

    @classmethod
    def track_event(
        cls,
        event_name: str,
        user_id: str | None = None,
        properties: dict[str, Any] | None = None,
        groups: dict[str, str] | None = None,
    ):
        """
        Track an analytics event.

        Args:
            event_name: Name of the event
            user_id: User identifier
            properties: Event properties
            groups: Group associations (e.g., chama_id)
        """
        client = cls.get_posthog_client()
        if not client:
            return

        try:
            # Prepare properties
            event_properties = properties or {}

            # Add system properties
            event_properties["$app_version"] = getattr(
                settings, "APP_VERSION", "1.0.0"
            )
            event_properties["$environment"] = "production" if not settings.DEBUG else "development"

            # Track event
            client.capture(
                distinct_id=user_id or "anonymous",
                event=event_name,
                properties=event_properties,
                groups=groups or {},
            )

            logger.debug(f"Tracked event: {event_name} for user: {user_id}")
        except Exception as e:
            logger.error(f"Failed to track event {event_name}: {e}")

    @classmethod
    def identify_user(
        cls,
        user_id: str,
        properties: dict[str, Any] | None = None,
    ):
        """
        Identify a user with their properties.

        Args:
            user_id: User identifier
            properties: User properties (email, name, etc.)
        """
        client = cls.get_posthog_client()
        if not client:
            return

        try:
            client.identify(
                distinct_id=user_id,
                properties=properties or {},
            )
            logger.debug(f"Identified user: {user_id}")
        except Exception as e:
            logger.error(f"Failed to identify user {user_id}: {e}")

    @classmethod
    def track_page_view(
        cls,
        user_id: str,
        page_name: str,
        properties: dict[str, Any] | None = None,
    ):
        """
        Track a page view.

        Args:
            user_id: User identifier
            page_name: Name of the page
            properties: Page properties
        """
        cls.track_event(
            event_name="$pageview",
            user_id=user_id,
            properties={
                "$current_url": page_name,
                **(properties or {}),
            },
        )

    @classmethod
    def track_user_action(
        cls,
        user_id: str,
        action: str,
        target: str,
        properties: dict[str, Any] | None = None,
    ):
        """
        Track a user action.

        Args:
            user_id: User identifier
            action: Action performed (e.g., "clicked", "submitted")
            target: Target of action (e.g., "contribution_button")
            properties: Action properties
        """
        cls.track_event(
            event_name=f"{action}_{target}",
            user_id=user_id,
            properties={
                "action": action,
                "target": target,
                **(properties or {}),
            },
        )

    @classmethod
    def track_chama_event(
        cls,
        user_id: str,
        chama_id: str,
        event_name: str,
        properties: dict[str, Any] | None = None,
    ):
        """
        Track a chama-related event.

        Args:
            user_id: User identifier
            chama_id: Chama identifier
            event_name: Event name
            properties: Event properties
        """
        cls.track_event(
            event_name=event_name,
            user_id=user_id,
            properties=properties or {},
            groups={"chama": chama_id},
        )

    @classmethod
    def track_financial_event(
        cls,
        user_id: str,
        event_type: str,
        amount: float,
        currency: str = "KES",
        properties: dict[str, Any] | None = None,
    ):
        """
        Track a financial event.

        Args:
            user_id: User identifier
            event_type: Type of financial event
            amount: Transaction amount
            currency: Currency code
            properties: Event properties
        """
        cls.track_event(
            event_name=f"financial_{event_type}",
            user_id=user_id,
            properties={
                "amount": amount,
                "currency": currency,
                **(properties or {}),
            },
        )

    @classmethod
    def track_error(
        cls,
        user_id: str | None,
        error_type: str,
        error_message: str,
        properties: dict[str, Any] | None = None,
    ):
        """
        Track an error event.

        Args:
            user_id: User identifier
            error_type: Type of error
            error_message: Error message
            properties: Error properties
        """
        cls.track_event(
            event_name="error_occurred",
            user_id=user_id,
            properties={
                "error_type": error_type,
                "error_message": error_message,
                **(properties or {}),
            },
        )

    @classmethod
    def track_performance(
        cls,
        user_id: str | None,
        metric_name: str,
        value: float,
        properties: dict[str, Any] | None = None,
    ):
        """
        Track a performance metric.

        Args:
            user_id: User identifier
            metric_name: Name of the metric
            value: Metric value
            properties: Metric properties
        """
        cls.track_event(
            event_name=f"performance_{metric_name}",
            user_id=user_id,
            properties={
                "metric_name": metric_name,
                "value": value,
                **(properties or {}),
            },
        )

    @classmethod
    def flush(cls):
        """Flush analytics data."""
        client = cls.get_posthog_client()
        if client:
            try:
                client.flush()
                logger.debug("Analytics data flushed")
            except Exception as e:
                logger.error(f"Failed to flush analytics: {e}")


# Convenience functions
def track_event(event_name: str, **kwargs):
    """Track an analytics event."""
    AnalyticsService.track_event(event_name, **kwargs)


def identify_user(user_id: str, **kwargs):
    """Identify a user."""
    AnalyticsService.identify_user(user_id, **kwargs)


def track_page_view(user_id: str, page_name: str, **kwargs):
    """Track a page view."""
    AnalyticsService.track_page_view(user_id, page_name, **kwargs)


def track_user_action(user_id: str, action: str, target: str, **kwargs):
    """Track a user action."""
    AnalyticsService.track_user_action(user_id, action, target, **kwargs)


def track_chama_event(user_id: str, chama_id: str, event_name: str, **kwargs):
    """Track a chama event."""
    AnalyticsService.track_chama_event(user_id, chama_id, event_name, **kwargs)


def track_financial_event(user_id: str, event_type: str, amount: float, **kwargs):
    """Track a financial event."""
    AnalyticsService.track_financial_event(user_id, event_type, amount, **kwargs)


def track_error(user_id: str | None, error_type: str, error_message: str, **kwargs):
    """Track an error."""
    AnalyticsService.track_error(user_id, error_type, error_message, **kwargs)


def track_performance(user_id: str | None, metric_name: str, value: float, **kwargs):
    """Track a performance metric."""
    AnalyticsService.track_performance(user_id, metric_name, value, **kwargs)
