#!/usr/bin/env python
"""
Management command to test Firebase Cloud Messaging (FCM) push notifications.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.notifications.push import (
    FCMProvider,
    send_push_notification,
    send_push_to_topic,
)


class Command(BaseCommand):
    help = "Test Firebase Cloud Messaging (FCM) push notification"

    def add_arguments(self, parser):
        parser.add_argument(
            "--token",
            type=str,
            help="FCM device token to send notification to",
        )
        parser.add_argument(
            "--topic",
            type=str,
            help="Topic name to send notification to (e.g., chama_123)",
        )
        parser.add_argument(
            "--title",
            type=str,
            default="Test Notification",
            help="Notification title",
        )
        parser.add_argument(
            "--body",
            type=str,
            default="This is a test push notification from Digital Chama!",
            help="Notification body",
        )
        parser.add_argument(
            "--data",
            type=str,
            help="JSON data payload (optional)",
        )
        parser.add_argument(
            "--image",
            type=str,
            help="Image URL for notification (optional)",
        )

    def handle(self, *args, **options):
        token = options.get("token")
        topic = options.get("topic")
        title = options.get("title")
        body = options.get("body")
        data_str = options.get("data")
        image_url = options.get("image")

        self.stdout.write(self.style.NOTICE("=" * 60))
        self.stdout.write(self.style.NOTICE("FCM Push Notification Test"))
        self.stdout.write(self.style.NOTICE("=" * 60))

        # Check configuration
        provider = FCMProvider()
        
        self.stdout.write("\n[Config]")
        self.stdout.write(f"  FCM Enabled: {provider.enabled}")
        self.stdout.write(f"  API Key set: {bool(provider.api_key)}")
        self.stdout.write(f"  Project ID: {provider.project_id or 'Not set'}")
        
        if not provider.enabled:
            self.stdout.write(self.style.ERROR("\n⚠ FCM is not configured!"))
            self.stdout.write(self.style.ERROR("Set FCM_API_KEY or FIREBASE_SERVICE_ACCOUNT in your .env file"))
            self.stdout.write("\nExample .env configuration:")
            self.stdout.write("  FCM_API_KEY=your-server-key")
            self.stdout.write("  FCM_PROJECT_ID=your-project-id")
            self.stdout.write("  # OR use service account JSON:")
            self.stdout.write("  FIREBASE_SERVICE_ACCOUNT='{\"type\": \"service_account\", ...}'")
            return

        # Parse data payload if provided
        data = None
        if data_str:
            import json
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError as e:
                raise CommandError(f"Invalid JSON data: {e}")

        # Send to device token
        if token:
            self._send_to_token(provider, token, title, body, data, image_url)
        
        # Send to topic
        elif topic:
            self._send_to_topic(provider, topic, title, body, data, image_url)
        
        else:
            self.stdout.write(self.style.ERROR("\n⚠ Please provide either --token or --topic"))
            self.stdout.write(self.style.WARNING("Usage:"))
            self.stdout.write("  python manage.py test_fcm --token <device_token>")
            self.stdout.write("  python manage.py test_fcm --topic announcements")

    def _send_to_token(
        self,
        provider: FCMProvider,
        token: str,
        title: str,
        body: str,
        data: dict,
        image_url: str,
    ):
        """Send notification to a device token."""
        self.stdout.write("\n[Sending to device]")
        self.stdout.write(f"  Token: {token[:20]}...")

        result = send_push_notification(
            token=token,
            title=title,
            body=body,
            data=data,
            image_url=image_url,
            sound="default",
        )

        if result.success:
            self.stdout.write(self.style.SUCCESS("\n✓ Notification sent successfully!"))
            self.stdout.write(f"  Message ID: {result.message_id}")
        else:
            self.stdout.write(self.style.ERROR("\n✗ Failed to send notification"))
            self.stdout.write(f"  Error: {result.error}")

    def _send_to_topic(
        self,
        provider: FCMProvider,
        topic: str,
        title: str,
        body: str,
        data: dict,
        image_url: str,
    ):
        """Send notification to a topic."""
        self.stdout.write("\n[Sending to topic]")
        self.stdout.write(f"  Topic: {topic}")

        result = send_push_to_topic(
            topic=topic,
            title=title,
            body=body,
            data=data,
            image_url=image_url,
        )

        if result.success:
            self.stdout.write(self.style.SUCCESS("\n✓ Notification sent to topic!"))
            self.stdout.write(f"  Message ID: {result.message_id}")
        else:
            self.stdout.write(self.style.ERROR("\n✗ Failed to send to topic"))
            self.stdout.write(f"  Error: {result.error}")

        # Also test subscription
        self.stdout.write("\n[Testing topic subscription]")
        self.stdout.write("  Subscribing test token to topic...")
        
        # This would require a real device token - just show the capability
        self.stdout.write(self.style.WARNING("  Note: Topic subscription requires a valid device token"))
        self.stdout.write(f"  Use: provider.subscribe_to_topic(['token'], '{topic}')")
