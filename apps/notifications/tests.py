from django.contrib.auth import get_user_model
from django.core import signing
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.automations.models import NotificationLog
from apps.automations.services import AutomationService
from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus
from apps.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationEvent,
    NotificationInboxStatus,
    NotificationPriority,
    NotificationStatus,
    NotificationTarget,
    NotificationType,
)
from apps.notifications.services import NotificationService, create_notification
from apps.notifications.views import (
    AnnouncementFeedView,
    NotificationArchiveView,
    NotificationStreamTokenView,
)


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class NotificationWorkflowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(
            phone="+254700010001",
            password="testpass123",
            full_name="Admin User",
            email="admin@example.com",
        )
        self.treasurer = user_model.objects.create_user(
            phone="+254700010002",
            password="testpass123",
            full_name="Treasurer User",
            email="treasurer@example.com",
        )
        self.member = user_model.objects.create_user(
            phone="+254700010003",
            password="testpass123",
            full_name="Member User",
            email="member@example.com",
        )

        self.chama = Chama.objects.create(
            name="Notification Test Chama",
            created_by=self.admin,
            updated_by=self.admin,
        )
        Membership.objects.create(
            user=self.admin,
            chama=self.chama,
            role=MembershipRole.CHAMA_ADMIN,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            approved_by=self.admin,
            approved_at=timezone.now(),
            created_by=self.admin,
            updated_by=self.admin,
        )
        Membership.objects.create(
            user=self.treasurer,
            chama=self.chama,
            role=MembershipRole.TREASURER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            approved_by=self.admin,
            approved_at=timezone.now(),
            created_by=self.admin,
            updated_by=self.admin,
        )
        Membership.objects.create(
            user=self.member,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            approved_by=self.admin,
            approved_at=timezone.now(),
            created_by=self.admin,
            updated_by=self.admin,
        )
        self.factory = APIRequestFactory()

    def test_publish_event_is_idempotent_by_event_key(self):
        first = NotificationService.publish_event(
            chama=self.chama,
            event_key="notif:event:payment:1",
            event_type=NotificationType.PAYMENT_CONFIRMATION,
            target=NotificationTarget.USER,
            target_user_ids=[str(self.member.id)],
            channels=["in_app"],
            subject="Payment received",
            message="Your contribution was received.",
            category=NotificationCategory.PAYMENTS,
            priority=NotificationPriority.NORMAL,
            actor=self.admin,
        )
        second = NotificationService.publish_event(
            chama=self.chama,
            event_key="notif:event:payment:1",
            event_type=NotificationType.PAYMENT_CONFIRMATION,
            target=NotificationTarget.USER,
            target_user_ids=[str(self.member.id)],
            channels=["in_app"],
            subject="Payment received",
            message="Your contribution was received.",
            category=NotificationCategory.PAYMENTS,
            priority=NotificationPriority.NORMAL,
            actor=self.admin,
        )

        self.assertEqual(first.id, second.id)
        self.assertEqual(NotificationEvent.objects.count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.member).count(), 1)

    def test_publish_event_routes_only_to_requested_role(self):
        event = NotificationService.publish_event(
            chama=self.chama,
            event_key="notif:event:loan:role",
            event_type=NotificationType.LOAN_UPDATE,
            target=NotificationTarget.ROLE,
            target_roles=[MembershipRole.TREASURER],
            channels=["in_app"],
            subject="Loan review needed",
            message="A loan needs your review.",
            category=NotificationCategory.LOANS,
            priority=NotificationPriority.HIGH,
            actor=self.admin,
        )

        self.assertEqual(event.recipient_count, 1)
        self.assertEqual(
            Notification.objects.filter(recipient=self.treasurer).count(),
            1,
        )
        self.assertEqual(
            Notification.objects.filter(recipient=self.member).count(),
            0,
        )

    def test_mark_notification_failure_uses_fixed_retry_schedule(self):
        notification = Notification.objects.create(
            chama=self.chama,
            recipient=self.member,
            type=NotificationType.SYSTEM,
            category=NotificationCategory.SYSTEM,
            priority=NotificationPriority.NORMAL,
            status=NotificationStatus.PENDING,
            message="Retry me",
            max_retries=4,
            created_by=self.admin,
            updated_by=self.admin,
        )

        expected_delays = [120, 600, 3600]
        for index, expected_delay in enumerate(expected_delays, start=1):
            NotificationService._mark_notification_failure(notification, [f"failure-{index}"])
            notification.refresh_from_db()
            self.assertEqual(notification.retry_count, index)
            self.assertIsNotNone(notification.next_retry_at)
            delay = int((notification.next_retry_at - timezone.now()).total_seconds())
            self.assertGreaterEqual(delay, expected_delay - 5)
            self.assertLessEqual(delay, expected_delay + 5)

        NotificationService._mark_notification_failure(notification, ["failure-4"])
        notification.refresh_from_db()
        self.assertEqual(notification.retry_count, 4)
        self.assertIsNone(notification.next_retry_at)

    def test_archive_view_marks_notification_as_archived(self):
        notification = Notification.objects.create(
            chama=self.chama,
            recipient=self.member,
            type=NotificationType.SYSTEM,
            category=NotificationCategory.SYSTEM,
            priority=NotificationPriority.NORMAL,
            status=NotificationStatus.SENT,
            inbox_status=NotificationInboxStatus.UNREAD,
            message="Archive me",
            created_by=self.admin,
            updated_by=self.admin,
        )

        request = self.factory.post(
            f"/api/v1/notifications/{notification.id}/archive",
            {},
            format="json",
        )
        force_authenticate(request, user=self.member)

        response = NotificationArchiveView.as_view()(request, id=notification.id)

        self.assertEqual(response.status_code, 200)
        notification.refresh_from_db()
        self.assertEqual(notification.inbox_status, NotificationInboxStatus.ARCHIVED)

    def test_create_notification_uses_event_router(self):
        event = create_notification(
            recipient=self.member,
            chama=self.chama,
            notification_type=NotificationType.MEMBERSHIP_UPDATE,
            title="Membership update",
            message="Your role changed.",
            category=NotificationCategory.MEMBERSHIP,
            send_email=False,
            send_sms=False,
        )

        self.assertIsNotNone(event)
        self.assertEqual(NotificationEvent.objects.count(), 1)
        self.assertEqual(
            Notification.objects.filter(recipient=self.member).count(),
            1,
        )

    def test_stream_token_view_returns_signed_token(self):
        request = self.factory.get(
            "/api/v1/notifications/stream/token",
            {"chama_id": str(self.chama.id)},
        )
        force_authenticate(request, user=self.member)

        response = NotificationStreamTokenView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        payload = signing.loads(
            response.data["stream_token"],
            salt="notifications.stream",
            max_age=21600,
        )
        self.assertEqual(payload["user_id"], str(self.member.id))

    def test_announcements_feed_returns_only_recipient_items(self):
        Notification.objects.create(
            chama=self.chama,
            recipient=self.member,
            type=NotificationType.GENERAL_ANNOUNCEMENT,
            category=NotificationCategory.SYSTEM,
            priority=NotificationPriority.NORMAL,
            status=NotificationStatus.SENT,
            inbox_status=NotificationInboxStatus.UNREAD,
            subject="Member update",
            message="Hello member",
            created_by=self.admin,
            updated_by=self.admin,
        )
        Notification.objects.create(
            chama=self.chama,
            recipient=self.treasurer,
            type=NotificationType.GENERAL_ANNOUNCEMENT,
            category=NotificationCategory.SYSTEM,
            priority=NotificationPriority.NORMAL,
            status=NotificationStatus.SENT,
            inbox_status=NotificationInboxStatus.UNREAD,
            subject="Treasurer update",
            message="Hello treasurer",
            created_by=self.admin,
            updated_by=self.admin,
        )

        request = self.factory.get(
            "/api/v1/notifications/announcements/feed",
            {"chama_id": str(self.chama.id)},
        )
        force_authenticate(request, user=self.member)
        response = AnnouncementFeedView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["title"], "Member update")

    def test_announcements_feed_can_filter_unread(self):
        Notification.objects.create(
            chama=self.chama,
            recipient=self.member,
            type=NotificationType.GENERAL_ANNOUNCEMENT,
            category=NotificationCategory.SYSTEM,
            priority=NotificationPriority.NORMAL,
            status=NotificationStatus.SENT,
            inbox_status=NotificationInboxStatus.READ,
            subject="Already read",
            message="Seen",
            read_at=timezone.now(),
            created_by=self.admin,
            updated_by=self.admin,
        )
        Notification.objects.create(
            chama=self.chama,
            recipient=self.member,
            type=NotificationType.GENERAL_ANNOUNCEMENT,
            category=NotificationCategory.SYSTEM,
            priority=NotificationPriority.NORMAL,
            status=NotificationStatus.SENT,
            inbox_status=NotificationInboxStatus.UNREAD,
            subject="Unread",
            message="New",
            created_by=self.admin,
            updated_by=self.admin,
        )

        request = self.factory.get(
            "/api/v1/notifications/announcements/feed",
            {"chama_id": str(self.chama.id), "unread": "true"},
        )
        force_authenticate(request, user=self.member)
        response = AnnouncementFeedView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Unread")
        self.assertEqual(response.data["results"][0]["chama_id"], str(self.chama.id))

    def test_automation_policy_send_creates_one_notification_per_allowed_channel(self):
        result = AutomationService.send_notification_with_policy(
            user=self.member,
            chama=self.chama,
            message="Automation policy test",
            channels=["in_app", "email"],
            subject="Policy send",
            notification_type=NotificationType.SYSTEM,
            idempotency_key="automation-policy-test",
            actor=self.admin,
        )

        notifications = Notification.objects.filter(
            recipient=self.member,
            subject="Policy send",
        ).order_by("idempotency_key")
        logs = NotificationLog.objects.filter(
            user=self.member,
            chama=self.chama,
            message="Automation policy test",
        )

        self.assertEqual(result["sent"], 2)
        self.assertEqual(notifications.count(), 2)
        self.assertEqual(logs.count(), 2)
        self.assertSetEqual(
            {notification.idempotency_key for notification in notifications},
            {
                "automation-policy-test:in_app",
                "automation-policy-test:email",
            },
        )

    def test_general_announcement_email_channel_is_blocked_when_not_critical(self):
        notification = NotificationService.send_notification(
            user=self.member,
            chama=self.chama,
            message="Normal announcement",
            channels=["in_app", "email"],
            subject="Community update",
            notification_type=NotificationType.GENERAL_ANNOUNCEMENT,
            priority=NotificationPriority.NORMAL,
            actor=self.admin,
        )

        self.assertFalse(notification.send_email)
        self.assertTrue(notification.send_push)

    def test_general_announcement_email_channel_allowed_when_critical(self):
        notification = NotificationService.send_notification(
            user=self.member,
            chama=self.chama,
            message="Critical announcement",
            channels=["in_app", "email"],
            subject="Urgent security notice",
            notification_type=NotificationType.GENERAL_ANNOUNCEMENT,
            priority=NotificationPriority.CRITICAL,
            actor=self.admin,
        )

        self.assertTrue(notification.send_email)
