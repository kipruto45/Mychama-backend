from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus
from apps.meetings.models import Attendance, AttendanceStatus, Meeting
from apps.meetings.tasks import meetings_reminder_1h


class MeetingAutomationTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(
            phone="+254733100001",
            password="password123",
            full_name="Meeting Admin",
        )
        self.rsvp_member = user_model.objects.create_user(
            phone="+254733100002",
            password="password123",
            full_name="RSVP Member",
        )
        self.non_rsvp_member = user_model.objects.create_user(
            phone="+254733100003",
            password="password123",
            full_name="Non RSVP Member",
        )
        self.chama = Chama.objects.create(name="Meeting Automation Chama")
        for user, role in [
            (self.admin, MembershipRole.CHAMA_ADMIN),
            (self.rsvp_member, MembershipRole.MEMBER),
            (self.non_rsvp_member, MembershipRole.MEMBER),
        ]:
            Membership.objects.create(
                user=user,
                chama=self.chama,
                role=role,
                status=MemberStatus.ACTIVE,
                is_active=True,
                is_approved=True,
                joined_at=timezone.now() - timedelta(days=60),
            )
        self.meeting = Meeting.objects.create(
            chama=self.chama,
            title="Weekly Review",
            date=timezone.now() + timedelta(hours=1),
            created_by=self.admin,
            updated_by=self.admin,
        )
        Attendance.objects.create(
            meeting=self.meeting,
            member=self.rsvp_member,
            status=AttendanceStatus.PRESENT,
            created_by=self.admin,
            updated_by=self.admin,
        )

    @patch("apps.notifications.services.NotificationService.send_notification")
    def test_one_hour_reminder_targets_only_rsvpd_members(self, send_notification_mock):
        result = meetings_reminder_1h()
        payload = result.get("result", result)

        self.assertEqual(payload["notifications"], 1)
        recipients = {str(call.kwargs["user"].id) for call in send_notification_mock.call_args_list}
        self.assertIn(str(self.rsvp_member.id), recipients)
        self.assertNotIn(str(self.non_rsvp_member.id), recipients)
