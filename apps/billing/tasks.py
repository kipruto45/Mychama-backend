from __future__ import annotations

import logging

from celery import shared_task

from apps.billing.services import (
    cleanup_credit_reservations,
    process_payment_retries,
    process_subscription_lifecycle,
    reset_usage_cycles,
    send_credit_expiry_reminders,
    send_failed_payment_reminders,
    send_renewal_reminders,
)

logger = logging.getLogger(__name__)


@shared_task
def billing_process_subscription_lifecycle():
    result = process_subscription_lifecycle()
    logger.info("billing_process_subscription_lifecycle result=%s", result)
    return result


@shared_task
def billing_retry_payments():
    processed = process_payment_retries()
    logger.info("billing_retry_payments processed=%s", processed)
    return processed


@shared_task
def billing_reset_usage():
    reset_count = reset_usage_cycles()
    logger.info("billing_reset_usage reset_count=%s", reset_count)
    return reset_count


@shared_task
def billing_send_renewal_reminders():
    sent = send_renewal_reminders()
    logger.info("billing_send_renewal_reminders sent=%s", sent)
    return sent


@shared_task
def billing_send_failed_payment_reminders():
    sent = send_failed_payment_reminders()
    logger.info("billing_send_failed_payment_reminders sent=%s", sent)
    return sent


@shared_task
def billing_send_credit_expiry_reminders():
    sent = send_credit_expiry_reminders()
    logger.info("billing_send_credit_expiry_reminders sent=%s", sent)
    return sent


@shared_task
def billing_cleanup_credit_reservations():
    result = cleanup_credit_reservations()
    logger.info("billing_cleanup_credit_reservations result=%s", result)
    return result
