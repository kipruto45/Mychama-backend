"""
Management command to send bulk notifications via CSV import.

CSV Format:
    phone,email,chama_id,channel,subject,message,scheduled_at
    +254712345678,,,sms,,Hello member!
    ,user@example.com,,email,Hello,Your contribution is due
    +254798765432,user2@example.com,123,whatsapp,Meeting,Weekly meeting at 6pm,

Supported channels: sms, email, whatsapp, telegram, in_app, push
"""

import csv
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.models import User
from apps.notifications.models import (
    Notification,
    NotificationChannel,
    NotificationType,
)
from apps.notifications.services import NotificationService


class Command(BaseCommand):
    help = "Send bulk notifications from a CSV file"

    def add_arguments(self, parser):
        parser.add_argument(
            "file_path",
            type=str,
            help="Path to CSV file containing notifications",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview notifications without sending",
        )
        parser.add_argument(
            "--default-chama",
            type=str,
            help="Default chama ID if not specified in CSV",
        )

    def handle(self, *args, **options):
        file_path = options["file_path"]
        dry_run = options.get("dry_run", False)
        default_chama_id = options.get("default_chama")

        # Read CSV file
        try:
            with open(file_path) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
        except FileNotFoundError:
            raise CommandError(f"File not found: {file_path}")
        except Exception as e:
            raise CommandError(f"Error reading CSV: {e}")

        if not rows:
            raise CommandError("CSV file is empty")

        # Validate headers
        required_headers = ["message"]
        
        reader = csv.DictReader(f)
        headers = reader.fieldnames if hasattr(reader, 'fieldnames') else []
        
        # Re-open and read properly
        with open(file_path) as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
        
        for header in required_headers:
            if header not in headers:
                raise CommandError(f"Missing required header: {header}")

        self.stdout.write(f"Processing {len(rows)} notifications...")

        notifications_created = 0
        errors = []

        for idx, row in enumerate(rows, start=1):
            try:
                # Extract fields
                phone = row.get("phone", "").strip()
                email = row.get("email", "").strip()
                row.get("chama_id", "").strip() or default_chama_id
                channel = row.get("channel", "in_app").strip().lower()
                subject = row.get("subject", "").strip()
                message = row.get("message", "").strip()
                scheduled_at = row.get("scheduled_at", "").strip()

                # Validate message
                if not message:
                    errors.append(f"Row {idx}: Message is required")
                    continue

                # Validate channel
                valid_channels = [c[0] for c in NotificationChannel.choices]
                if channel and channel not in valid_channels:
                    errors.append(f"Row {idx}: Invalid channel '{channel}'")
                    continue

                # Resolve recipients
                recipients = []
                if email:
                    users = User.objects.filter(email__iexact=email)
                    if users.exists():
                        recipients.extend(users)
                    else:
                        errors.append(f"Row {idx}: User with email {email} not found")
                        continue

                if phone:
                    # Try to find user by phone
                    users = User.objects.filter(phone_number=phone)
                    if users.exists():
                        for user in users:
                            if user not in recipients:
                                recipients.append(user)
                    # If no user found, still add for SMS/WhatsApp
                    if not users.exists() and channel in ["sms", "whatsapp"]:
                        # We'll handle this in notification creation
                        pass

                # If no recipients found, use in_app for current user lookup
                if not recipients:
                    if email:
                        errors.append(f"Row {idx}: User with email {email} not found")
                        continue
                    if phone:
                        errors.append(f"Row {idx}: User with phone {phone} not found")
                        continue

                # Parse scheduled_at
                scheduled_time = None
                if scheduled_at:
                    try:
                        scheduled_time = datetime.fromisoformat(scheduled_at)
                        scheduled_time = timezone.make_aware(scheduled_time)
                    except ValueError:
                        errors.append(f"Row {idx}: Invalid scheduled_at format")
                        continue

                # Create notification data
                notification_data = {
                    "subject": subject or "Notification",
                    "message": message,
                    "channel": channel or NotificationChannel.IN_APP,
                    "notification_type": NotificationType.GENERAL_ANNOUNCEMENT,
                }

                if dry_run:
                    self.stdout.write(
                        f"  [DRY RUN] Row {idx}: {channel} to {email or phone} - {message[:50]}..."
                    )
                    continue

                # Create and queue notifications
                for user in recipients:
                    notification = Notification.objects.create(
                        user=user,
                        subject=notification_data["subject"],
                        message=notification_data["message"],
                        channel=notification_data["channel"],
                        notification_type=notification_data["notification_type"],
                        scheduled_at=scheduled_time,
                    )
                    NotificationService.queue_notification(notification)
                    notifications_created += 1

                # Handle SMS/WhatsApp to phone numbers without user account
                if channel in ["sms", "whatsapp"] and phone and not recipients:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  Row {idx}: SMS/WhatsApp to {phone} requires user account - skipped"
                        )
                    )

            except Exception as e:
                errors.append(f"Row {idx}: {str(e)}")

        # Print results
        self.stdout.write("\n" + "=" * 50)
        if dry_run:
            self.stdout.write(self.style.SUCCESS(f"✓ Dry run complete - {len(rows)} notifications would be sent"))
        else:
            self.stdout.write(self.style.SUCCESS(f"✓ Created {notifications_created} notifications"))

        if errors:
            self.stdout.write(self.style.ERROR(f"\n{len(errors)} errors:"))
            for error in errors[:10]:  # Show first 10 errors
                self.stdout.write(f"  - {error}")
            if len(errors) > 10:
                self.stdout.write(f"  ... and {len(errors) - 10} more errors")
