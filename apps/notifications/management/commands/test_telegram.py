"""
Management command to test Telegram message delivery.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.notifications.telegram import send_telegram_message


class Command(BaseCommand):
    help = "Test Telegram message delivery to a chat ID"

    def add_arguments(self, parser):
        parser.add_argument(
            "chat_id",
            type=str,
            help="Telegram chat ID to send test message to",
        )
        parser.add_argument(
            "--message",
            type=str,
            default="Hello from Digital Chama! This is a test Telegram notification.",
            help="Message to send",
        )
        parser.add_argument(
            "--html",
            action="store_true",
            help="Send as HTML instead of Markdown",
        )

    def handle(self, *args, **options):
        chat_id = options["chat_id"]
        message = options["message"]
        use_html = options["html"]

        self.stdout.write(f"Sending Telegram message to {chat_id}...")

        try:
            parse_mode = "HTML" if use_html else "Markdown"
            result = send_telegram_message(
                chat_id=chat_id,
                text=message,
                parse_mode=parse_mode,
            )

            if result.ok:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✓ Telegram message sent successfully via {result.provider}"
                    )
                )
                if result.provider_message_id:
                    self.stdout.write(f"  Message ID: {result.provider_message_id}")
            else:
                self.stdout.write(
                    self.style.ERROR("✗ Failed to send Telegram message")
                )
                if result.raw_response:
                    self.stdout.write(f"  Error: {result.raw_response}")
                raise CommandError("Telegram delivery failed")

        except RuntimeError as e:
            raise CommandError(f"Configuration error: {e}")
        except Exception as e:
            raise CommandError(f"Error sending Telegram: {e}")
