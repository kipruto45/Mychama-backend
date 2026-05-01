from django.core.management.base import BaseCommand

from apps.billing.services import cleanup_credit_reservations


class Command(BaseCommand):
    help = "Release stale reserved billing credits from abandoned pending invoices."

    def handle(self, *args, **options):
        result = cleanup_credit_reservations()
        self.stdout.write(
            self.style.SUCCESS(
                "Released stale credit reservations for "
                f"{result['released_invoices']} invoice(s) "
                f"and {result['released_allocations']} allocation(s)."
            )
        )
