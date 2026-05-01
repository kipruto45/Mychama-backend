from django.apps import AppConfig


class AppConfig(AppConfig):
    """Unified app configuration for Digital Chama mobile API layer."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.app'
    verbose_name = 'Digital Chama App API'
