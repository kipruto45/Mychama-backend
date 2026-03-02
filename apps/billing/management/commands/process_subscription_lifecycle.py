from django.core.management.base import BaseCommand

from apps.billing.services import process_subscription_lifecycle


class Command(BaseCommand):
    help = "Apply scheduled billing changes, move expired subscriptions into grace, and suspend overdue subscriptions."

    def handle(self, *args, **options):
        result = process_subscription_lifecycle()
        self.stdout.write(
            self.style.SUCCESS(
                "Lifecycle processed: "
                f"{result['scheduled_changes_applied']} scheduled changes, "
                f"{result['grace_marked']} grace updates, "
                f"{result['suspended']} suspensions."
            )
        )
