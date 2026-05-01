"""
Card payment Celery tasks for MyChama.

Async tasks for card payment operations.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from celery import shared_task
from django.utils import timezone

from apps.payments.card_models import (
    CardPaymentIntent,
    CardPaymentStatus,
    CardPaymentWebhook,
)
from apps.payments.card_services import CardPaymentService, CardPaymentServiceError

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="payments.verify_card_payment",
)
def verify_card_payment(self, intent_id: str) -> dict[str, Any]:
    """
    Verify card payment status with provider.

    Args:
        intent_id: Payment intent ID

    Returns:
        Dict with verification result
    """
    try:
        intent = CardPaymentService.verify_payment(intent_id)

        return {
            "status": "success",
            "intent_id": str(intent.id),
            "payment_status": intent.status,
        }

    except CardPaymentServiceError as e:
        logger.error("Card payment verification failed for %s: %s", intent_id, e)

        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)

        return {
            "status": "failed",
            "intent_id": intent_id,
            "error": str(e),
        }

    except Exception as e:
        logger.error("Unexpected error verifying card payment %s: %s", intent_id, e)

        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)

        return {
            "status": "failed",
            "intent_id": intent_id,
            "error": str(e),
        }


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="payments.process_card_webhook",
)
def process_card_webhook(
    self,
    provider_name: str,
    payload: bytes,
    signature: str | None,
    headers: dict[str, str] | None = None,
    source_ip: str | None = None,
) -> dict[str, Any]:
    """
    Process card payment webhook asynchronously.

    Args:
        provider_name: Provider name
        payload: Raw webhook payload
        signature: Signature header
        headers: All request headers
        source_ip: Source IP address

    Returns:
        Dict with processing result
    """
    try:
        webhook_log = CardPaymentService.process_webhook(
            provider_name=provider_name,
            payload=payload,
            signature=signature,
            headers=headers,
            source_ip=source_ip,
        )

        return {
            "status": "success" if webhook_log.processed else "failed",
            "webhook_id": str(webhook_log.id),
            "event_type": webhook_log.event_type,
            "provider_reference": webhook_log.provider_reference,
            "error": webhook_log.processing_error if not webhook_log.processed else None,
        }

    except CardPaymentServiceError as e:
        logger.error("Card webhook processing failed: %s", e)

        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)

        return {
            "status": "failed",
            "error": str(e),
        }

    except Exception as e:
        logger.error("Unexpected error processing card webhook: %s", e)

        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)

        return {
            "status": "failed",
            "error": str(e),
        }


@shared_task(name="payments.expire_pending_card_payments")
def expire_pending_card_payments() -> dict[str, Any]:
    """
    Expire pending card payments that have passed their expiration time.

    Returns:
        Dict with expiration result
    """
    try:
        now = timezone.now()

        expired_count = CardPaymentIntent.objects.filter(
            status__in=[
                CardPaymentStatus.INITIATED,
                CardPaymentStatus.PENDING_AUTHENTICATION,
                CardPaymentStatus.PENDING,
            ],
            expires_at__lt=now,
        ).update(
            status=CardPaymentStatus.EXPIRED,
            failure_reason="Payment expired",
            failure_code="expired",
        )

        logger.info("Expired %d pending card payments", expired_count)

        return {
            "status": "success",
            "expired_count": expired_count,
        }

    except Exception as e:
        logger.error("Failed to expire pending card payments: %s", e)
        return {
            "status": "failed",
            "error": str(e),
        }


@shared_task(name="payments.reconcile_card_payments")
def reconcile_card_payments() -> dict[str, Any]:
    """
    Reconcile card payments with providers.

    Checks pending payments and verifies their status.

    Returns:
        Dict with reconciliation result
    """
    try:
        pending_intents = CardPaymentIntent.objects.filter(
            status__in=[
                CardPaymentStatus.PENDING,
                CardPaymentStatus.PENDING_AUTHENTICATION,
            ],
            created_at__gte=timezone.now() - timedelta(hours=24),
        )

        reconciled_count = 0
        failed_count = 0

        for intent in pending_intents:
            try:
                CardPaymentService.verify_payment(intent.id)
                reconciled_count += 1
            except Exception as e:
                logger.error(
                    "Failed to reconcile payment %s: %s",
                    intent.id,
                    e,
                )
                failed_count += 1

        logger.info(
            "Card payment reconciliation completed: %d reconciled, %d failed",
            reconciled_count,
            failed_count,
        )

        return {
            "status": "success",
            "reconciled_count": reconciled_count,
            "failed_count": failed_count,
        }

    except Exception as e:
        logger.error("Card payment reconciliation failed: %s", e)
        return {
            "status": "failed",
            "error": str(e),
        }


@shared_task(name="payments.send_card_payment_notification")
def send_card_payment_notification(
    intent_id: str,
    notification_type: str,
) -> dict[str, Any]:
    """
    Send notification for card payment event.

    Args:
        intent_id: Payment intent ID
        notification_type: Type of notification (initiated, success, failed)

    Returns:
        Dict with notification result
    """
    try:
        intent = CardPaymentIntent.objects.select_related("user", "chama").get(
            id=intent_id
        )

        {
            "intent_id": str(intent.id),
            "amount": str(intent.amount),
            "currency": intent.currency,
            "chama_name": intent.chama.name,
            "status": intent.status,
            "reference": intent.reference,
        }

        if notification_type == "initiated":
            logger.info(
                "Payment initiated notification for user %s: %s %s",
                intent.user_id,
                intent.amount,
                intent.currency,
            )
        elif notification_type == "success":
            logger.info(
                "Payment success notification for user %s: %s %s",
                intent.user_id,
                intent.amount,
                intent.currency,
            )
        elif notification_type == "failed":
            logger.info(
                "Payment failed notification for user %s: %s %s",
                intent.user_id,
                intent.amount,
                intent.currency,
            )

        return {
            "status": "success",
            "intent_id": intent_id,
            "notification_type": notification_type,
        }

    except CardPaymentIntent.DoesNotExist:
        logger.error("Payment intent not found for notification: %s", intent_id)
        return {
            "status": "failed",
            "error": "Payment intent not found",
        }

    except Exception as e:
        logger.error("Failed to send card payment notification: %s", e)
        return {
            "status": "failed",
            "error": str(e),
        }


@shared_task(name="payments.cleanup_old_card_webhooks")
def cleanup_old_card_webhooks(days_to_keep: int = 90) -> dict[str, Any]:
    """
    Clean up old card payment webhook logs.

    Args:
        days_to_keep: Number of days to keep webhook logs

    Returns:
        Dict with cleanup result
    """
    try:
        cutoff_date = timezone.now() - timedelta(days=days_to_keep)

        deleted_count, _ = CardPaymentWebhook.objects.filter(
            created_at__lt=cutoff_date,
        ).delete()

        logger.info(
            "Cleaned up %d old card payment webhook logs (older than %d days)",
            deleted_count,
            days_to_keep,
        )

        return {
            "status": "success",
            "deleted_count": deleted_count,
        }

    except Exception as e:
        logger.error("Failed to cleanup old card webhooks: %s", e)
        return {
            "status": "failed",
            "error": str(e),
        }
