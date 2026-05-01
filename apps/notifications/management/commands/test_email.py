"""
Management command to test email sending.

Usage:
    python manage.py test_email <email>
"""
from django.core.management.base import BaseCommand, CommandError

from apps.notifications.email import send_email_message


class Command(BaseCommand):
    """Command to test email sending."""

    help = "Test email sending to a specified email address"

    def add_arguments(self, parser):
        """Add command arguments."""
        parser.add_argument(
            "email",
            type=str,
            help="Email address to send test email to",
        )
        parser.add_argument(
            "--subject",
            type=str,
            default="Test Email from Digital Chama",
            help="Subject of the test email",
        )
        parser.add_argument(
            "--message",
            type=str,
            default="This is a test email from Digital Chama. If you receive this, your email configuration is working correctly.",
            help="Message body of the test email",
        )

    def handle(self, *args, **options):
        """Handle the command."""
        email = options["email"]
        subject = options["subject"]
        message = options["message"]

        self.stdout.write(f"Sending test email to: {email}")

        # Send test email
        self.stdout.write("Sending test email...")

        try:
            result = send_email_message(
                subject=subject,
                recipient_list=[email],
                body=message,
                html_body=f"<p>{message}</p>",
            )

            if result.ok:
                self.stdout.write(self.style.SUCCESS("✓ Email sent successfully!"))
                self.stdout.write(f"  Provider: {result.provider}")
                self.stdout.write(f"  Message ID: {result.provider_message_id}")
                self.stdout.write(f"  Recipient: {email}")
                self.stdout.write(f"  Subject: {subject}")
            else:
                self.stdout.write(self.style.ERROR("✗ Failed to send email"))
                self.stdout.write(f"  Error: {result.raw_response}")
                raise CommandError("Email sending failed")

        except Exception as e:
            raise CommandError(f"Error sending email: {e}")

        self.stdout.write(self.style.SUCCESS("\nTest email completed successfully!"))
