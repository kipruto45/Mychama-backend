"""
Firebase Analytics Service for MyChama.

Provides integration with Firebase Analytics for mobile app analytics.
"""

import logging
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


class FirebaseAnalyticsService:
    """
    Firebase Analytics service for tracking mobile app events.
    """

    _initialized: bool = False
    _firebase_app = None

    @classmethod
    def initialize(cls):
        """Initialize Firebase Analytics."""
        if cls._initialized:
            return

        firebase_enabled = getattr(settings, "FIREBASE_ANALYTICS_ENABLED", False)
        if not firebase_enabled:
            logger.info("Firebase Analytics is disabled")
            cls._initialized = True
            return

        try:
            import firebase_admin
            from firebase_admin import credentials

            # Try to initialize Firebase
            if not firebase_admin._apps:
                # Try service account
                service_account = getattr(settings, "FIREBASE_SERVICE_ACCOUNT", None)
                if service_account:
                    import json
                    try:
                        cred = credentials.Certificate(
                            json.loads(service_account)
                            if isinstance(service_account, str)
                            else service_account
                        )
                        firebase_admin.initialize_app(cred)
                        cls._firebase_app = firebase_admin.get_app()
                        logger.info("Firebase Analytics initialized with service account")
                    except Exception as e:
                        logger.error(f"Failed to initialize Firebase with service account: {e}")

                # Try service account file
                if not cls._firebase_app:
                    service_file = getattr(settings, "FIREBASE_SERVICE_ACCOUNT_FILE", None)
                    if service_file:
                        try:
                            cred = credentials.Certificate(service_file)
                            firebase_admin.initialize_app(cred)
                            cls._firebase_app = firebase_admin.get_app()
                            logger.info("Firebase Analytics initialized with service file")
                        except Exception as e:
                            logger.error(f"Failed to initialize Firebase with service file: {e}")

            cls._initialized = True
        except ImportError:
            logger.warning("firebase-admin not installed, Firebase Analytics unavailable")
            cls._initialized = True
        except Exception as e:
            logger.error(f"Failed to initialize Firebase Analytics: {e}")
            cls._initialized = True

    @classmethod
    def is_available(cls) -> bool:
        """Check if Firebase Analytics is available."""
        if not cls._initialized:
            cls.initialize()
        return cls._firebase_app is not None

    @classmethod
    def track_event(
        cls,
        event_name: str,
        user_id: str | None = None,
        properties: dict[str, Any] | None = None,
    ):
        """
        Track a Firebase Analytics event.

        Args:
            event_name: Name of the event
            user_id: User identifier
            properties: Event properties
        """
        if not cls.is_available():
            return

        try:
            from firebase_admin import analytics

            # Prepare event parameters
            params = properties or {}

            # Add user ID if provided
            if user_id:
                params["user_id"] = user_id

            # Create analytics event
            event = analytics.Event(
                name=event_name,
                params=params,
            )

            # Log event
            analytics.log_event(cls._firebase_app, event)

            logger.debug(f"Firebase Analytics event tracked: {event_name}")
        except Exception as e:
            logger.error(f"Failed to track Firebase Analytics event {event_name}: {e}")

    @classmethod
    def set_user_properties(
        cls,
        user_id: str,
        properties: dict[str, Any],
    ):
        """
        Set user properties in Firebase Analytics.

        Args:
            user_id: User identifier
            properties: User properties
        """
        if not cls.is_available():
            return

        try:
            from firebase_admin import analytics

            # Set user properties
            for key, value in properties.items():
                analytics.set_user_property(
                    cls._firebase_app,
                    key=key,
                    value=str(value),
                )

            logger.debug(f"Firebase Analytics user properties set for user: {user_id}")
        except Exception as e:
            logger.error(f"Failed to set Firebase Analytics user properties: {e}")

    @classmethod
    def track_screen_view(
        cls,
        user_id: str,
        screen_name: str,
        screen_class: str | None = None,
    ):
        """
        Track a screen view.

        Args:
            user_id: User identifier
            screen_name: Name of the screen
            screen_class: Screen class name
        """
        properties = {"screen_name": screen_name}
        if screen_class:
            properties["screen_class"] = screen_class

        cls.track_event(
            event_name="screen_view",
            user_id=user_id,
            properties=properties,
        )

    @classmethod
    def track_login(
        cls,
        user_id: str,
        method: str = "email",
    ):
        """
        Track a login event.

        Args:
            user_id: User identifier
            method: Login method
        """
        cls.track_event(
            event_name="login",
            user_id=user_id,
            properties={"method": method},
        )

    @classmethod
    def track_sign_up(
        cls,
        user_id: str,
        method: str = "email",
    ):
        """
        Track a sign up event.

        Args:
            user_id: User identifier
            method: Sign up method
        """
        cls.track_event(
            event_name="sign_up",
            user_id=user_id,
            properties={"method": method},
        )

    @classmethod
    def track_purchase(
        cls,
        user_id: str,
        value: float,
        currency: str = "KES",
        transaction_id: str | None = None,
    ):
        """
        Track a purchase event.

        Args:
            user_id: User identifier
            value: Purchase value
            currency: Currency code
            transaction_id: Transaction identifier
        """
        properties = {
            "value": value,
            "currency": currency,
        }
        if transaction_id:
            properties["transaction_id"] = transaction_id

        cls.track_event(
            event_name="purchase",
            user_id=user_id,
            properties=properties,
        )

    @classmethod
    def track_contribution(
        cls,
        user_id: str,
        amount: float,
        chama_id: str,
        contribution_type: str = "regular",
    ):
        """
        Track a contribution event.

        Args:
            user_id: User identifier
            amount: Contribution amount
            chama_id: Chama identifier
            contribution_type: Type of contribution
        """
        cls.track_event(
            event_name="contribution",
            user_id=user_id,
            properties={
                "amount": amount,
                "chama_id": chama_id,
                "contribution_type": contribution_type,
            },
        )

    @classmethod
    def track_loan_application(
        cls,
        user_id: str,
        amount: float,
        chama_id: str,
    ):
        """
        Track a loan application event.

        Args:
            user_id: User identifier
            amount: Loan amount
            chama_id: Chama identifier
        """
        cls.track_event(
            event_name="loan_application",
            user_id=user_id,
            properties={
                "amount": amount,
                "chama_id": chama_id,
            },
        )

    @classmethod
    def track_meeting_attendance(
        cls,
        user_id: str,
        meeting_id: str,
        chama_id: str,
    ):
        """
        Track a meeting attendance event.

        Args:
            user_id: User identifier
            meeting_id: Meeting identifier
            chama_id: Chama identifier
        """
        cls.track_event(
            event_name="meeting_attendance",
            user_id=user_id,
            properties={
                "meeting_id": meeting_id,
                "chama_id": chama_id,
            },
        )


# Convenience functions
def track_firebase_event(event_name: str, **kwargs):
    """Track a Firebase Analytics event."""
    FirebaseAnalyticsService.track_event(event_name, **kwargs)


def set_firebase_user_properties(user_id: str, **kwargs):
    """Set Firebase Analytics user properties."""
    FirebaseAnalyticsService.set_user_properties(user_id, **kwargs)


def track_firebase_screen_view(user_id: str, screen_name: str, **kwargs):
    """Track a screen view."""
    FirebaseAnalyticsService.track_screen_view(user_id, screen_name, **kwargs)


def track_firebase_login(user_id: str, method: str = "email"):
    """Track a login event."""
    FirebaseAnalyticsService.track_login(user_id, method)


def track_firebase_sign_up(user_id: str, method: str = "email"):
    """Track a sign up event."""
    FirebaseAnalyticsService.track_sign_up(user_id, method)


def track_firebase_purchase(user_id: str, value: float, currency: str = "KES", **kwargs):
    """Track a purchase event."""
    FirebaseAnalyticsService.track_purchase(user_id, value, currency, **kwargs)


def track_firebase_contribution(user_id: str, amount: float, chama_id: str, **kwargs):
    """Track a contribution event."""
    FirebaseAnalyticsService.track_contribution(user_id, amount, chama_id, **kwargs)


def track_firebase_loan_application(user_id: str, amount: float, chama_id: str):
    """Track a loan application event."""
    FirebaseAnalyticsService.track_loan_application(user_id, amount, chama_id)


def track_firebase_meeting_attendance(user_id: str, meeting_id: str, chama_id: str):
    """Track a meeting attendance event."""
    FirebaseAnalyticsService.track_meeting_attendance(user_id, meeting_id, chama_id)
