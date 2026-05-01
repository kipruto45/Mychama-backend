"""Signals for Payout workflow."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.payments.models import PaymentIntent
from apps.payments.unified_models import PaymentStatus

from .models import Payout
from .services import PayoutService


@receiver(post_save, sender=PaymentIntent)
def handle_payment_intent_status_change(sender, instance, created, update_fields, **kwargs):
    """
    Handle payment status updates and call appropriate payout handlers.

    This is triggered when a PaymentIntent (used for payouts) completes
    or fails, so we can update the Payout status accordingly.
    """
    # Only process if this is for a payout (purpose_id should point to Payout)
    if not instance.purpose_id or instance.purpose == "other":
        return

    # Only process on status change
    if not update_fields or "status" not in update_fields:
        return

    try:
        # Check if this PaymentIntent is linked to a Payout
        payout = Payout.objects.filter(payment_intent=instance).first()
        if not payout:
            return

        if instance.status == PaymentStatus.SUCCESS:
            PayoutService.handle_payment_success(instance.id)
        elif instance.status == PaymentStatus.FAILED:
            PayoutService.handle_payment_failure(
                instance.id,
                failure_reason=instance.failure_reason,
                failure_code=instance.failure_code,
            )
    except Exception as e:
        # Log error but don't raise to prevent signal issues
        print(f"Error handling payment intent update: {e}")


def ready():
    """Called when app is ready."""
    pass
