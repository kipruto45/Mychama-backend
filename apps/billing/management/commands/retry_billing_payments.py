from django.core.management.base import BaseCommand

from apps.billing.services import process_payment_retries


class Command(BaseCommand):
    help = "Advance the billing retry queue for subscriptions in grace period."

    def handle(self, *args, **options):
        processed = process_payment_retries()
        self.stdout.write(
            self.style.SUCCESS(f"Processed {processed} billing retry candidates.")
        )
