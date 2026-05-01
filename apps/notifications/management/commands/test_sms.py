"""
Management command to test SMS sending.

Usage:
    python manage.py test_sms <phone>
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.notifications.sms import send_sms_message


class Command(BaseCommand):
    """Command to test SMS sending."""

    help = "Test SMS sending to a specified phone number"

    def add_arguments(self, parser):
        """Add command arguments."""
        parser.add_argument(
            "phone",
            type=str,
            help="Phone number to send test SMS to (e.g., +254712345678)",
        )
        parser.add_argument(
            "--message",
            type=str,
            default="This is a test SMS from Digital Chama. If you receive this, your SMS configuration is working correctly.",
            help="Message body of the test SMS",
        )

    def handle(self, *args, **options):
        """Handle the command."""
        phone = options["phone"]
        message = options["message"]

        self.stdout.write(f"Sending test SMS to: {phone}")

        # Show mock mode status
        allow_mock = getattr(settings, "OTP_ALLOW_MOCK_DELIVERY", False)
        app_env = getattr(settings, "APP_ENV", "development")

        if allow_mock and app_env != "production":
            self.stdout.write(self.style.WARNING(
                "⚠ Running in MOCK mode (OTP_ALLOW_MOCK_DELIVERY=True). "
                "No actual SMS will be sent."
            ))

        # Send test SMS
        self.stdout.write("Sending test SMS...")

        try:
            result = send_sms_message(
                phone_number=phone,
                message=message,
            )

            if result.ok:
                self.stdout.write(self.style.SUCCESS("✓ SMS sent successfully!"))
                self.stdout.write(f"  Provider: {result.provider}")
                self.stdout.write(f"  Message ID: {result.provider_message_id}")
                self.stdout.write(f"  Recipient: {phone}")
                self.stdout.write(f"  Message: {message}")

                if result.provider == "console":
                    self.stdout.write(self.style.WARNING(
                        "  (This was a mock delivery - no actual SMS sent)"
                    ))
            else:
                self.stdout.write(self.style.ERROR("✗ Failed to send SMS"))
                self.stdout.write(f"  Error: {result.raw_response}")
                raise CommandError("SMS sending failed")

        except Exception as e:
            raise CommandError(f"Error sending SMS: {e}")

        self.stdout.write(self.style.SUCCESS("\nTest SMS completed successfully!"))
