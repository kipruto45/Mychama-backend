"""Celery tasks for asynchronous payout operations."""

from celery import shared_task
from django.utils import timezone

from .models import Payout, PayoutStatus
from .services import PayoutService


@shared_task(bind=True, max_retries=3)
def process_pending_payouts(self):
    """
    Process all pending payouts.
    
    Checks for eligible payouts and moves them through the workflow.
    Run periodically via celery beat.
    """
    try:
        # Find payouts awaiting treasurer review
        pending = Payout.objects.filter(
            status=PayoutStatus.AWAITING_TREASURER_REVIEW
        ).select_related("chama", "member")

        for payout in pending:
            PayoutService.send_to_treasurer_review(payout.id)

        return f"Processed {pending.count()} pending payouts"
    except Exception as exc:
        # Retry with exponential backoff
        self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@shared_task(bind=True, max_retries=3)
def retry_failed_payouts(self):
    """
    Automatically retry failed payouts.
    
    Run periodically to attempt payment retry for failed payouts
    that haven't exceeded max retries.
    """
    try:
        failed = Payout.objects.filter(
            status=PayoutStatus.FAILED,
        ).select_related("chama", "member")

        retried_count = 0
        for payout in failed:
            if payout.can_retry():
                try:
                    PayoutService.retry_failed_payout(payout.id)
                    retried_count += 1
                except Exception as e:
                    print(f"Error retrying payout {payout.id}: {e}")

        return f"Retried {retried_count} failed payouts"
    except Exception as exc:
        self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@shared_task(bind=True, max_retries=2)
def generate_payout_receipts(self):
    """
    Generate receipts for completed payouts.
    
    Async task to generate PDF receipts without blocking the API.
    """
    try:
        completed = Payout.objects.filter(
            status=PayoutStatus.SUCCESS,
            receipt_generated_at__isnull=True,
        ).select_related("chama", "member")

        for payout in completed:
            PayoutService._generate_receipt(payout)

        return f"Generated {completed.count()} receipts"
    except Exception as exc:
        self.retry(exc=exc, countdown=300 * (2 ** self.request.retries))


@shared_task(bind=True, max_retries=2)
def send_payout_reminders(self):
    """
    Send reminders for upcoming payout contributions.
    
    Notifies members to pay contributions before rotation payout.
    """
    try:
        from apps.chama.models import Chama
        from apps.notifications.services import NotificationService

        # For each active chama, send contribution reminders
        chamas = Chama.objects.filter(status="active")
        reminder_count = 0

        for chama in chamas:
            members = chama.memberships.filter(status="active")
            for member in members:
                NotificationService.create_notification(
                    user=member.user,
                    notification_type="CONTRIBUTION_REMINDER",
                    title="Upcoming Contribution",
                    message=(
                        f"Contribute to {chama.name} to stay eligible "
                        "for the next payout cycle."
                    ),
                    reference_id=chama.id,
                    channels=["PUSH", "SMS"],
                )
                reminder_count += 1

        return f"Sent {reminder_count} contribution reminders"
    except Exception as exc:
        self.retry(exc=exc, countdown=300 * (2 ** self.request.retries))


@shared_task
def cleanup_expired_payouts():
    """
    Clean up old/expired payout records.
    
    Archive payouts older than 90 days.
    """
    from datetime import timedelta

    cutoff_date = timezone.now() - timedelta(days=90)
    archived = Payout.objects.filter(
        status__in=[PayoutStatus.SUCCESS, PayoutStatus.CANCELLED],
        updated_at__lt=cutoff_date,
    ).update(metadata={"archived": True})

    return f"Archived {archived} old payouts"
