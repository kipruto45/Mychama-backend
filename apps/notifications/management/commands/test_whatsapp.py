"""
Management command to test WhatsApp message delivery.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.notifications.whatsapp import send_whatsapp_message


class Command(BaseCommand):
    help = "Test WhatsApp message delivery to a phone number"

    def add_arguments(self, parser):
        parser.add_argument(
            "phone_number",
            type=str,
            help="Phone number to send test message to (e.g., +254712345678)",
        )
        parser.add_argument(
            "--message",
            type=str,
            default="Hello from Digital Chama! This is a test WhatsApp notification.",
            help="Message to send",
        )
        parser.add_argument(
            "--template",
            type=str,
            help="Template name to send (instead of text message)",
        )

    def handle(self, *args, **options):
        phone_number = options["phone_number"]
        message = options["message"]
        template_name = options.get("template")

        self.stdout.write(f"Sending WhatsApp to {phone_number}...")

        try:
            result = send_whatsapp_message(
                phone_number=phone_number,
                message=message,
                template_name=template_name,
            )

            if result.ok:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✓ WhatsApp message sent successfully via {result.provider}"
                    )
                )
                if result.provider_message_id:
                    self.stdout.write(f"  Message ID: {result.provider_message_id}")
            else:
                self.stdout.write(
                    self.style.ERROR(f"✗ Failed to send WhatsApp message")
                )
                if result.raw_response:
                    self.stdout.write(f"  Error: {result.raw_response}")
                raise CommandError("WhatsApp delivery failed")

        except RuntimeError as e:
            raise CommandError(f"Configuration error: {e}")
        except Exception as e:
            raise CommandError(f"Error sending WhatsApp: {e}")
