"""
Management command to view the latest development emails.
"""

from django.core.management.base import BaseCommand

from apps.core.email_backend import (
    clear_dev_emails,
    format_email_for_terminal,
    get_dev_emails,
    get_latest_invite_link,
    get_latest_otp,
    get_latest_password_reset_link,
)


class Command(BaseCommand):
    help = "View recent development emails captured by the dev email backend"

    def add_arguments(self, parser):
        parser.add_argument(
            "--latest",
            "-l",
            type=int,
            default=1,
            help="Number of latest emails to show (default: 1)",
        )
        parser.add_argument(
            "--all",
            "-a",
            action="store_true",
            help="Show all stored emails",
        )
        parser.add_argument(
            "--to",
            type=str,
            help="Filter by recipient email",
        )
        parser.add_argument(
            "--category",
            type=str,
            choices=["otp", "verification", "password_reset", "invite", "welcome", "announcement", "notification", "unknown"],
            help="Filter by email category",
        )
        parser.add_argument(
            "--otp",
            action="store_true",
            help="Show latest OTP code only",
        )
        parser.add_argument(
            "--invite",
            action="store_true",
            help="Show latest invite link only",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Show latest password reset link only",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear all stored development emails",
        )

    def handle(self, *args, **options):
        # Handle clear first
        if options.get("clear"):
            clear_dev_emails()
            self.stdout.write(self.style.SUCCESS("✓ All development emails cleared"))
            return
        
        # Handle special commands
        if options.get("otp"):
            code = get_latest_otp()
            if code:
                self.stdout.write(self.style.SUCCESS(f"\n🔑 Latest OTP Code: {code}\n"))
            else:
                self.stdout.write(self.style.WARNING("No OTP codes found"))
            return
        
        if options.get("invite"):
            link = get_latest_invite_link()
            if link:
                self.stdout.write(self.style.SUCCESS(f"\n📨 Latest Invite Link:\n{link}\n"))
            else:
                self.stdout.write(self.style.WARNING("No invite links found"))
            return
        
        if options.get("reset"):
            link = get_latest_password_reset_link()
            if link:
                self.stdout.write(self.style.SUCCESS(f"\n🔑 Latest Password Reset Link:\n{link}\n"))
            else:
                self.stdout.write(self.style.WARNING("No password reset links found"))
            return
        
        # Get emails
        limit = options.get("latest", 1) if not options.get("all") else 100
        recipient = options.get("to")
        category = options.get("category")
        
        emails = get_dev_emails(
            recipient=recipient,
            category=category,
            limit=limit
        )
        
        if not emails:
            self.stdout.write(self.style.WARNING("No development emails found"))
            self.stdout.write(self.style.INFO("\nTip: Send an email from the app to see it here."))
            return
        
        # Show emails
        for email in emails:
            self.stdout.write(format_email_for_terminal(email))
        
        self.stdout.write(f"\n✓ Showing {len(emails)} email(s)")


def print_dev_mail_help():
    """Print help for dev mail commands."""
    print("""
📧 Development Email Commands
==============================

Show latest emails:
  python manage.py dev_mail                    # Show 1 latest email
  python manage.py dev_mail -l 5               # Show 5 latest emails
  python manage.py dev_mail --all              # Show all emails

Filter emails:
  python manage.py dev_mail --to user@email.com
  python manage.py dev_mail --category otp
  python manage.py dev_mail --category verification

Quick access:
  python manage.py dev_mail --otp              # Show latest OTP code
  python manage.py dev_mail --invite           # Show latest invite link
  python manage.py dev_mail --reset            # Show latest reset link

Manage emails:
  python manage.py dev_mail --clear            # Clear all stored emails
""")