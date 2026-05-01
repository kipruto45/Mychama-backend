"""App configuration for payouts module."""

from django.apps import AppConfig


class PayoutsConfig(AppConfig):
    """Configuration class for the payouts app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.payouts"
    verbose_name = "Payouts Management"

    def ready(self):
        """Import signals when app is ready."""
        import apps.payouts.signals  # noqa: F401
