import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.payments.mpesa_client import MpesaClient


class Command(BaseCommand):
    help = "Register Safaricom C2B validation and confirmation URLs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--allow-stub",
            action="store_true",
            help="Allow execution even when MPESA_USE_STUB=True.",
        )

    def handle(self, *args, **options):
        use_stub = getattr(settings, "MPESA_USE_STUB", True)
        if use_stub and not options["allow_stub"]:
            raise CommandError(
                "MPESA_USE_STUB=True. Use --allow-stub to test registration payload only."
            )

        if use_stub and options["allow_stub"]:
            callback_base = str(settings.DARAJA_CALLBACK_BASE_URL).rstrip("/")
            payload = {
                "ShortCode": settings.DARAJA_SHORTCODE,
                "ResponseType": "Completed",
                "ConfirmationURL": (
                    f"{callback_base}/api/v1/payments/callbacks/c2b/confirmation"
                ),
                "ValidationURL": (
                    f"{callback_base}/api/v1/payments/callbacks/c2b/validation"
                ),
                "mode": "stub",
                "sent": False,
            }
            self.stdout.write(
                self.style.WARNING(
                    "MPESA_USE_STUB=True. Returning the registration payload without sending."
                )
            )
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
            return

        try:
            payload = MpesaClient().register_c2b_urls()
        except Exception as exc:  # noqa: BLE001
            raise CommandError(f"Failed to register C2B URLs: {exc}") from exc

        self.stdout.write(self.style.SUCCESS("C2B URL registration request sent."))
        self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
