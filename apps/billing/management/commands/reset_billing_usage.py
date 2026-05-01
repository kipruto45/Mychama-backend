from django.core.management.base import BaseCommand

from apps.billing.services import reset_usage_cycles


class Command(BaseCommand):
    help = "Reset billing usage counters whose metering cycle has ended."

    def handle(self, *args, **options):
        reset_count = reset_usage_cycles()
        self.stdout.write(
            self.style.SUCCESS(f"Reset {reset_count} billing usage metrics.")
        )
