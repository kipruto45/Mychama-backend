"""
Unified Payment Celery Tasks for MyChama.

Async tasks for unified payment operations.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from celery import shared_task
from django.utils import timezone

from apps.payments.unified_models import (
    PaymentIntent,
    PaymentMethod,
    PaymentReconciliationCase,
    PaymentStatus,
    PaymentWebhook,
    ReconciliationCaseStatus,
    ReconciliationMismatchType,
)
from apps.payments.unified_services import PaymentServiceError, UnifiedPaymentService

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="payments.verify_payment",
)
def verify_payment(self, intent_id: str) -> dict[str, Any]:
    """
    Verify payment status with provider.

    Args:
        intent_id: Payment intent ID

    Returns:
        Dict with verification result
    """
    try:
        intent = UnifiedPaymentService.verify_payment(intent_id)

        return {
            "status": "success",
            "intent_id": str(intent.id),
            "payment_status": intent.status,
        }

    except PaymentServiceError as e:
        logger.error("Payment verification failed for %s: %s", intent_id, e)

        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)

        return {
            "status": "failed",
            "intent_id": intent_id,
            "error": str(e),
        }

    except Exception as e:
        logger.error("Unexpected error verifying payment %s: %s", intent_id, e)

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
    name="payments.process_webhook",
)
def process_webhook(
    self,
    payment_method: str,
    provider_name: str,
    payload: bytes,
    signature: str | None,
    headers: dict[str, str] | None = None,
    source_ip: str | None = None,
) -> dict[str, Any]:
    """
    Process payment webhook asynchronously.

    Args:
        payment_method: Payment method
        provider_name: Provider name
        payload: Raw webhook payload
        signature: Signature header
        headers: All request headers
        source_ip: Source IP address

    Returns:
        Dict with processing result
    """
    try:
        webhook_log = UnifiedPaymentService.process_webhook(
            payment_method=payment_method,
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

    except PaymentServiceError as e:
        logger.error("Webhook processing failed: %s", e)

        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)

        return {
            "status": "failed",
            "error": str(e),
        }

    except Exception as e:
        logger.error("Unexpected error processing webhook: %s", e)

        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)

        return {
            "status": "failed",
            "error": str(e),
        }


@shared_task(name="payments.expire_pending_payments")
def expire_pending_payments() -> dict[str, Any]:
    """
    Expire pending payments that have passed their expiration time.

    Returns:
        Dict with expiration result
    """
    try:
        now = timezone.now()

        expired_count = PaymentIntent.objects.filter(
            status__in=[
                PaymentStatus.INITIATED,
                PaymentStatus.PENDING_AUTHENTICATION,
                PaymentStatus.PENDING,
                PaymentStatus.PENDING_VERIFICATION,
            ],
            expires_at__lt=now,
        ).update(
            status=PaymentStatus.EXPIRED,
            failure_reason="Payment expired",
            failure_code="expired",
        )

        logger.info("Expired %d pending payments", expired_count)

        return {
            "status": "success",
            "expired_count": expired_count,
        }

    except Exception as e:
        logger.error("Failed to expire pending payments: %s", e)
        return {
            "status": "failed",
            "error": str(e),
        }


@shared_task(name="payments.reconcile_payments")
def reconcile_payments() -> dict[str, Any]:
    """
    Reconcile payments with providers.

    Checks pending payments and verifies their status.

    Returns:
        Dict with reconciliation result
    """
    try:
        now = timezone.now()
        pending_intents = PaymentIntent.objects.filter(
            status__in=[
                PaymentStatus.PENDING,
                PaymentStatus.PENDING_AUTHENTICATION,
            ],
            created_at__gte=now - timedelta(hours=24),
        )

        reconciled_count = 0
        failed_count = 0
        cases_opened = 0

        for intent in pending_intents:
            try:
                UnifiedPaymentService.verify_payment(intent.id)
                reconciled_count += 1
            except Exception as e:
                logger.error(
                    "Failed to reconcile payment %s: %s",
                    intent.id,
                    e,
                )
                failed_count += 1

        stale_provider_intents = PaymentIntent.objects.filter(
            payment_method__in=[PaymentMethod.MPESA],
            status__in=[
                PaymentStatus.PENDING,
                PaymentStatus.PENDING_AUTHENTICATION,
            ],
            created_at__lte=now - timedelta(minutes=15),
        )
        for intent in stale_provider_intents:
            existing_case = PaymentReconciliationCase.objects.filter(
                payment_intent=intent,
                mismatch_type=ReconciliationMismatchType.CALLBACK_MISSING,
                case_status__in=[
                    ReconciliationCaseStatus.OPEN,
                    ReconciliationCaseStatus.IN_REVIEW,
                ],
            ).exists()
            if existing_case:
                continue
            UnifiedPaymentService._flag_reconciliation_issue(
                intent=intent,
                issue_type=ReconciliationMismatchType.CALLBACK_MISSING,
                summary="Provider callback or webhook has not confirmed this payment within the expected window.",
                metadata={
                    "payment_method": intent.payment_method,
                    "provider": intent.provider,
                    "provider_intent_id": intent.provider_intent_id,
                },
                expected_amount=intent.amount,
                expected_reference=intent.reference,
                received_reference=intent.provider_intent_id,
            )
            cases_opened += 1

        logger.info(
            "Payment reconciliation completed: %d reconciled, %d failed, %d cases opened",
            reconciled_count,
            failed_count,
            cases_opened,
        )

        return {
            "status": "success",
            "reconciled_count": reconciled_count,
            "failed_count": failed_count,
            "cases_opened": cases_opened,
        }

    except Exception as e:
        logger.error("Payment reconciliation failed: %s", e)
        return {
            "status": "failed",
            "error": str(e),
        }


@shared_task(name="payments.send_payment_notification")
def send_payment_notification(
    intent_id: str,
    notification_type: str,
) -> dict[str, Any]:
    """
    Send notification for payment event.

    Args:
        intent_id: Payment intent ID
        notification_type: Type of notification (initiated, success, failed)

    Returns:
        Dict with notification result
    """
    try:
        intent = PaymentIntent.objects.select_related("user", "chama").get(
            id=intent_id
        )

        {
            "intent_id": str(intent.id),
            "amount": str(intent.amount),
            "currency": intent.currency,
            "chama_name": intent.chama.name,
            "status": intent.status,
            "reference": intent.reference,
            "payment_method": intent.payment_method,
        }

        if notification_type == "initiated":
            logger.info(
                "Payment initiated notification for user %s: %s %s via %s",
                intent.user_id,
                intent.amount,
                intent.currency,
                intent.payment_method,
            )
        elif notification_type == "success":
            logger.info(
                "Payment success notification for user %s: %s %s via %s",
                intent.user_id,
                intent.amount,
                intent.currency,
                intent.payment_method,
            )
        elif notification_type == "failed":
            logger.info(
                "Payment failed notification for user %s: %s %s via %s",
                intent.user_id,
                intent.amount,
                intent.currency,
                intent.payment_method,
            )

        return {
            "status": "success",
            "intent_id": intent_id,
            "notification_type": notification_type,
        }

    except PaymentIntent.DoesNotExist:
        logger.error("Payment intent not found for notification: %s", intent_id)
        return {
            "status": "failed",
            "error": "Payment intent not found",
        }

    except Exception as e:
        logger.error("Failed to send payment notification: %s", e)
        return {
            "status": "failed",
            "error": str(e),
        }


@shared_task(name="payments.cleanup_old_webhooks")
def cleanup_old_webhooks(days_to_keep: int = 90) -> dict[str, Any]:
    """
    Clean up old payment webhook logs.

    Args:
        days_to_keep: Number of days to keep webhook logs

    Returns:
        Dict with cleanup result
    """
    try:
        cutoff_date = timezone.now() - timedelta(days=days_to_keep)

        deleted_count, _ = PaymentWebhook.objects.filter(
            created_at__lt=cutoff_date,
        ).delete()

        logger.info(
            "Cleaned up %d old payment webhook logs (older than %d days)",
            deleted_count,
            days_to_keep,
        )

        return {
            "status": "success",
            "deleted_count": deleted_count,
        }

    except Exception as e:
        logger.error("Failed to cleanup old webhooks: %s", e)
        return {
            "status": "failed",
            "error": str(e),
        }
