"""
Management command to test OTP sending.

Usage:
    python manage.py test_otp --phone +2547xxxxxxx
    python manage.py test_otp --email user@example.com
    python manage.py test_otp --phone +2547... --email user@example.com --purpose login --channel both
"""
import os
from django.core.management.base import BaseCommand, CommandError

from apps.notifications.sms import send_sms
from apps.notifications.email import send_email_message


class Command(BaseCommand):
    """Command to test OTP sending."""

    help = "Test OTP sending via SMS, Email, or Both"

    def add_arguments(self, parser):
        """Add command arguments."""
        parser.add_argument(
            "--phone",
            type=str,
            help="Phone number to send OTP to (E.164 format, e.g., +254712345678)",
        )
        parser.add_argument(
            "--email",
            type=str,
            help="Email address to send OTP to",
        )
        parser.add_argument(
            "--purpose",
            type=str,
            default="login",
            choices=["login", "signup", "password_reset", "action_approval"],
            help="Purpose of the OTP",
        )
        parser.add_argument(
            "--channel",
            type=str,
            default="sms",
            choices=["sms", "email", "both"],
            help="Channel to send OTP via",
        )
        parser.add_argument(
            "--user-id",
            type=int,
            help="User ID to associate with the OTP (optional)",
        )

    def handle(self, *args, **options):
        """Handle the command."""
        phone = options.get("phone")
        email = options.get("email")
        purpose = options.get("purpose")
        channel = options.get("channel")
        user_id = options.get("user_id")

        # Validate at least one destination is provided
        if not phone and not email:
            raise CommandError("Please provide at least --phone or --email")

        # For 'both' channel, we need at least one of each
        if channel == "both" and not (phone and email):
            raise CommandError("Channel 'both' requires both --phone and --email")

        # In DEBUG mode, allow printing OTP code for testing
        print_otp = os.environ.get("PRINT_OTP_IN_CONSOLE", "False").lower() == "true"
        debug_mode = os.environ.get("DEBUG", "False").lower() == "true"

        # Generate a random 6-digit OTP
        import random
        otp_code = "".join([str(random.randint(0, 9)) for _ in range(6)])

        # Send via requested channel(s)
        results = []

        if channel in ("sms", "both") and phone:
            self.stdout.write(f"Sending OTP via SMS to: {phone}")
            try:
                message = f"Your Digital Chama verification code is: {otp_code}. Valid for 5 minutes."
                result = send_sms(phone, message)
                if result.ok:
                    self.stdout.write(self.style.SUCCESS("✓ SMS sent successfully!"))
                    self.stdout.write(f"  Provider: {result.provider}")
                    self.stdout.write(f"  Message ID: {result.provider_message_id}")
                    results.append(("sms", True))
                else:
                    self.stdout.write(self.style.ERROR("✗ Failed to send SMS"))
                    self.stdout.write(f"  Error: {result.raw_response}")
                    results.append(("sms", False))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"✗ Error sending SMS: {e}"))
                results.append(("sms", False))

        if channel in ("email", "both") and email:
            self.stdout.write(f"Sending OTP via Email to: {email}")
            try:
                subject = f"Digital Chama - {purpose.title()} Verification Code"
                body = f"""
Your verification code is: {otp_code}

This code is valid for 5 minutes. Please do not share this code with anyone.

If you did not request this code, please ignore this email.
"""
                html_body = f"""
<p>Your verification code is: <strong>{otp_code}</strong></p>
<p>This code is valid for 5 minutes. Please do not share this code with anyone.</p>
<p>If you did not request this code, please ignore this email.</p>
"""
                result = send_email_message(
                    subject=subject,
                    recipient_list=[email],
                    body=body,
                    html_body=html_body,
                )
                if result.ok:
                    self.stdout.write(self.style.SUCCESS("✓ Email sent successfully!"))
                    self.stdout.write(f"  Provider: {result.provider}")
                    self.stdout.write(f"  Message ID: {result.provider_message_id}")
                    results.append(("email", True))
                else:
                    self.stdout.write(self.style.ERROR("✗ Failed to send Email"))
                    self.stdout.write(f"  Error: {result.raw_response}")
                    results.append(("email", False))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"✗ Error sending Email: {e}"))
                results.append(("email", False))

        # Print OTP code in debug mode if enabled
        if print_otp and debug_mode:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(f"OTP Code (DEBUG ONLY): {otp_code}"))
            self.stdout.write(self.style.WARNING("This code will NOT be shown in production!"))
        elif debug_mode:
            self.stdout.write("")
            self.stdout.write(f"OTP Code generated but not printed (set PRINT_OTP_IN_CONSOLE=True to see it)")

        # Summary
        self.stdout.write("")
        self.stdout.write("Summary:")
        for ch, success in results:
            status = self.style.SUCCESS("✓") if success else self.style.ERROR("✗")
            self.stdout.write(f"  {status} {ch.upper()}")

        # Check if all succeeded
        if all(success for _, success in results):
            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS("All OTP requests completed successfully!"))
        else:
            self.stdout.write("")
            self.stdout.write(self.style.ERROR("Some OTP requests failed. Check errors above."))
            raise CommandError("OTP test failed")
