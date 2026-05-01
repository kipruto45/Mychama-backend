from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus
from apps.governance.models import (
    ChamaRule,
    Motion,
    MotionVote,
    RoleChange,
    RoleChangeStatus,
    RoleChangeType,
    RuleAcknowledgment,
    RuleStatus,
)
from apps.governance.serializers import RoleChangeCreateSerializer
from apps.governance.tasks import apply_due_role_changes
from apps.governance.views import (
    ChamaRuleViewSet,
    MotionViewSet,
    RoleChangeViewSet,
    RuleAcknowledgmentViewSet,
)


class GovernanceRBACTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.factory = APIRequestFactory()
        self.admin = user_model.objects.create_user(
            phone="+254722000001",
            password="password123",
            full_name="Chama Admin",
        )
        self.secretary = user_model.objects.create_user(
            phone="+254722000002",
            password="password123",
            full_name="Secretary User",
        )
        self.treasurer = user_model.objects.create_user(
            phone="+254722000003",
            password="password123",
            full_name="Treasurer User",
        )
        self.auditor = user_model.objects.create_user(
            phone="+254722000004",
            password="password123",
            full_name="Auditor User",
        )
        self.member = user_model.objects.create_user(
            phone="+254722000005",
            password="password123",
            full_name="Member User",
        )
        self.chama = Chama.objects.create(
            name="Governance RBAC Chama",
            created_by=self.admin,
            updated_by=self.admin,
        )
        for user, role in [
            (self.admin, MembershipRole.CHAMA_ADMIN),
            (self.secretary, MembershipRole.SECRETARY),
            (self.treasurer, MembershipRole.TREASURER),
            (self.auditor, MembershipRole.AUDITOR),
            (self.member, MembershipRole.MEMBER),
        ]:
            Membership.objects.create(
                user=user,
                chama=self.chama,
                role=role,
                status=MemberStatus.ACTIVE,
                is_active=True,
                is_approved=True,
                approved_by=self.admin,
                approved_at=timezone.now(),
                created_by=self.admin,
                updated_by=self.admin,
            )

    def test_treasurer_cannot_create_motion(self):
        request = self.factory.post(
            "/api/v1/governance/motions/",
            {
                "chama": str(self.chama.id),
                "title": "Approve new project",
                "description": "Treasurer should not create this",
                "start_time": timezone.now().isoformat(),
                "end_time": (timezone.now() + timedelta(days=1)).isoformat(),
                "quorum_percent": 50,
                "eligible_roles": [MembershipRole.MEMBER],
            },
            format="json",
            HTTP_X_CHAMA_ID=str(self.chama.id),
        )
        force_authenticate(request, user=self.treasurer)

        response = MotionViewSet.as_view({"post": "create"})(request)

        self.assertEqual(response.status_code, 403)

    def test_auditor_cannot_vote_on_motion(self):
        motion = Motion.objects.create(
            chama=self.chama,
            title="Vote on budget",
            description="Auditor should not vote",
            created_by=self.admin,
            updated_by=self.admin,
            start_time=timezone.now() - timedelta(hours=1),
            end_time=timezone.now() + timedelta(hours=2),
        )
        request = self.factory.post(
            f"/api/v1/governance/motions/{motion.id}/cast_vote/",
            {"vote": "yes"},
            format="json",
            HTTP_X_CHAMA_ID=str(self.chama.id),
        )
        force_authenticate(request, user=self.auditor)

        response = MotionViewSet.as_view({"post": "cast_vote"})(request, pk=str(motion.id))

        self.assertEqual(response.status_code, 403)

    def test_special_motion_requires_two_thirds_yes_votes(self):
        motion = Motion.objects.create(
            chama=self.chama,
            title="Adopt special rule",
            description="Needs 2/3 yes",
            created_by=self.admin,
            updated_by=self.admin,
            start_time=timezone.now() - timedelta(days=2),
            end_time=timezone.now() - timedelta(hours=1),
            vote_type="special",
        )
        MotionVote.objects.create(motion=motion, user=self.admin, vote="yes")
        MotionVote.objects.create(motion=motion, user=self.secretary, vote="yes")
        MotionVote.objects.create(motion=motion, user=self.treasurer, vote="no")

        request = self.factory.post(
            f"/api/v1/governance/motions/{motion.id}/close/",
            {},
            format="json",
            HTTP_X_CHAMA_ID=str(self.chama.id),
        )
        force_authenticate(request, user=self.admin)
        response = MotionViewSet.as_view({"post": "close"})(request, pk=str(motion.id))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["result"]["passed"])

    def test_unanimous_motion_fails_with_abstain(self):
        motion = Motion.objects.create(
            chama=self.chama,
            title="Adopt unanimous constitution",
            description="All eligible voters must vote yes",
            created_by=self.admin,
            updated_by=self.admin,
            start_time=timezone.now() - timedelta(days=2),
            end_time=timezone.now() - timedelta(hours=1),
            vote_type="unanimous",
            eligible_roles=[MembershipRole.CHAMA_ADMIN, MembershipRole.SECRETARY],
        )
        MotionVote.objects.create(motion=motion, user=self.admin, vote="yes")
        MotionVote.objects.create(motion=motion, user=self.secretary, vote="abstain")

        request = self.factory.post(
            f"/api/v1/governance/motions/{motion.id}/close/",
            {},
            format="json",
            HTTP_X_CHAMA_ID=str(self.chama.id),
        )
        force_authenticate(request, user=self.admin)
        response = MotionViewSet.as_view({"post": "close"})(request, pk=str(motion.id))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["result"]["passed"])

    def test_member_acknowledgment_list_is_scoped_to_self(self):
        rule = ChamaRule.objects.create(
            chama=self.chama,
            category="membership",
            title="Rule One",
            content="All members must contribute.",
            status=RuleStatus.ACTIVE,
            created_by=self.admin,
            updated_by=self.admin,
        )
        RuleAcknowledgment.objects.create(rule=rule, member=self.member)
        RuleAcknowledgment.objects.create(rule=rule, member=self.secretary)

        request = self.factory.get(
            f"/api/v1/governance/acknowledgments/?chama_id={self.chama.id}",
            HTTP_X_CHAMA_ID=str(self.chama.id),
        )
        force_authenticate(request, user=self.member)

        response = RuleAcknowledgmentViewSet.as_view({"get": "list"})(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["member"], str(self.member.id))

    def test_member_rule_list_hides_draft_rules(self):
        ChamaRule.objects.create(
            chama=self.chama,
            category="membership",
            title="Active Rule",
            content="Visible to members.",
            status=RuleStatus.ACTIVE,
            created_by=self.admin,
            updated_by=self.admin,
        )
        ChamaRule.objects.create(
            chama=self.chama,
            category="membership",
            title="Draft Rule",
            content="Should stay hidden from members.",
            status=RuleStatus.DRAFT,
            created_by=self.admin,
            updated_by=self.admin,
        )

        request = self.factory.get(
            f"/api/v1/governance/rules/?chama_id={self.chama.id}",
            HTTP_X_CHAMA_ID=str(self.chama.id),
        )
        force_authenticate(request, user=self.member)

        response = ChamaRuleViewSet.as_view({"get": "list"})(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["status"], RuleStatus.ACTIVE)

    def test_role_change_serializer_blocks_conflicting_auditor_assignment(self):
        serializer = RoleChangeCreateSerializer(
            data={
                "chama": str(self.chama.id),
                "member": str(self.admin.id),
                "change_type": RoleChangeType.APPOINTMENT,
                "old_role": MembershipRole.CHAMA_ADMIN,
                "new_role": MembershipRole.AUDITOR,
                "effective_date": timezone.now().date().isoformat(),
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("new_role", serializer.errors)

    @patch("apps.notifications.services.NotificationService.send_notification")
    def test_make_effective_updates_membership_and_demotes_outgoing_holder(self, _send_notification):
        incoming = self.member
        role_change = RoleChange.objects.create(
            chama=self.chama,
            member=incoming,
            change_type=RoleChangeType.APPOINTMENT,
            old_role=MembershipRole.MEMBER,
            new_role=MembershipRole.SECRETARY,
            effective_date=timezone.now().date(),
            status=RoleChangeStatus.APPROVED,
            created_by=self.admin,
            updated_by=self.admin,
        )

        request = self.factory.post(
            f"/api/v1/governance/role-changes/{role_change.id}/make_effective/",
            {},
            format="json",
            HTTP_X_CHAMA_ID=str(self.chama.id),
        )
        force_authenticate(request, user=self.admin)

        response = RoleChangeViewSet.as_view({"post": "make_effective"})(
            request,
            pk=str(role_change.id),
        )

        self.assertEqual(response.status_code, 200)
        role_change.refresh_from_db()
        self.assertEqual(role_change.status, RoleChangeStatus.EFFECTIVE)
        incoming_membership = Membership.objects.get(user=incoming, chama=self.chama)
        outgoing_membership = Membership.objects.get(user=self.secretary, chama=self.chama)
        self.assertEqual(incoming_membership.role, MembershipRole.SECRETARY)
        self.assertEqual(outgoing_membership.role, MembershipRole.MEMBER)

    @patch("apps.notifications.services.NotificationService.send_notification")
    def test_scheduler_make_effective_uses_role_change_domain_service(self, send_notification_mock):
        role_change = RoleChange.objects.create(
            chama=self.chama,
            member=self.member,
            change_type=RoleChangeType.APPOINTMENT,
            old_role=MembershipRole.MEMBER,
            new_role=MembershipRole.SECRETARY,
            effective_date=timezone.now().date(),
            status=RoleChangeStatus.APPROVED,
            approved_by=self.admin,
            created_by=self.admin,
            updated_by=self.admin,
        )

        result = apply_due_role_changes()

        self.assertEqual(result["activated"], 1)
        self.assertEqual(result["activation_failures"], 0)
        role_change.refresh_from_db()
        self.assertEqual(role_change.status, RoleChangeStatus.EFFECTIVE)
        self.assertEqual(
            Membership.objects.get(user=self.member, chama=self.chama).role,
            MembershipRole.SECRETARY,
        )
        self.assertEqual(
            Membership.objects.get(user=self.secretary, chama=self.chama).role,
            MembershipRole.MEMBER,
        )
        self.assertGreaterEqual(send_notification_mock.call_count, 2)
