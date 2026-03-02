from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.chama.models import Chama

from .services import ensure_trial_subscription


@receiver(post_save, sender=Chama)
def create_trial_subscription_for_new_chama(sender, instance, created, **kwargs):
    """Provision the one-time 30-day free trial when a chama is created."""
    if not created:
        return

    ensure_trial_subscription(instance)
