from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus
from apps.issues.models import (
    Issue,
    IssueCategory,
    IssuePriority,
    IssueSourceType,
    IssueStatus,
)
from apps.issues.services import (
    IssueServiceError,
    add_comment,
    assign_issue,
    change_issue_status,
    create_issue,
    propose_resolution,
    rate_issue,
    reopen_issue,
    request_clarification,
    escalate_issue,
    chairperson_approve_resolution,
)
from apps.issues.views import (
    IssueListCreateView,
    IssueDetailView,
    IssueCommentCreateView,
    IssueStatsView,
)


def mock_notification(*args, **kwargs):
    pass


@patch("apps.notifications.services.NotificationService.send_notification", mock_notification)
class IssueWorkflowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.factory = APIRequestFactory()
        self.admin = user_model.objects.create_user(
            phone="+254733000001",
            password="password123",
            full_name="Issue Admin",
        )
        self.secretary = user_model.objects.create_user(
            phone="+254733000002",
            password="password123",
            full_name="Issue Secretary",
        )
        self.treasurer = user_model.objects.create_user(
            phone="+254733000003",
            password="password123",
            full_name="Issue Treasurer",
        )
        self.member = user_model.objects.create_user(
            phone="+254733000004",
            password="password123",
            full_name="Issue Member",
        )
        self.chairperson = user_model.objects.create_user(
            phone="+254733000005",
            password="password123",
            full_name="Issue Chairperson",
        )
        self.chama = Chama.objects.create(
            name="Issue Workflow Chama",
            created_by=self.admin,
            updated_by=self.admin,
        )
        for user, role in [
            (self.admin, MembershipRole.CHAMA_ADMIN),
            (self.secretary, MembershipRole.SECRETARY),
            (self.treasurer, MembershipRole.TREASURER),
            (self.member, MembershipRole.MEMBER),
            (self.chairperson, MembershipRole.CHAMA_ADMIN),
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

    def test_member_can_create_issue(self):
        issue = create_issue(
            chama=self.chama,
            title="Payment Dispute Issue",
            description="My contribution was not recorded",
            category=IssueCategory.PAYMENT_DISPUTE,
            severity=IssuePriority.MEDIUM,
            raised_by=self.member,
            source_type=IssueSourceType.MEMBER,
            issue_scope="personal",
        )
        self.assertEqual(issue.status, IssueStatus.OPEN)
        self.assertTrue(issue.issue_code.startswith("ISS-"))
        self.assertEqual(issue.reopened_count, 0)

    def test_member_can_view_own_issues(self):
        issue = create_issue(
            chama=self.chama,
            title="Member Issue",
            description="Test issue",
            category=IssueCategory.OPERATIONAL,
            severity=IssuePriority.LOW,
            raised_by=self.member,
        )
        request = self.factory.get(
            f"/api/v1/issues/?chama_id={self.chama.id}",
            HTTP_X_CHAMA_ID=str(self.chama.id),
        )
        force_authenticate(request, user=self.member)
        response = IssueListCreateView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        titles = {row["title"] for row in response.data["results"]}
        self.assertIn("Member Issue", titles)

    def test_handler_can_assign_issue(self):
        issue = create_issue(
            chama=self.chama,
            title="Issue to Assign",
            description="Test issue",
            category=IssueCategory.OPERATIONAL,
            severity=IssuePriority.MEDIUM,
            raised_by=self.member,
        )
        updated_issue = assign_issue(
            issue,
            self.treasurer,
            self.admin,
            assigned_role="treasurer",
            note="Please handle this",
        )
        self.assertEqual(updated_issue.assigned_to_id, self.treasurer.id)
        self.assertEqual(updated_issue.status, IssueStatus.ASSIGNED)

    def test_clarification_request_workflow(self):
        issue = create_issue(
            chama=self.chama,
            title="Issue for Clarification",
            description="Test issue",
            category=IssueCategory.OPERATIONAL,
            severity=IssuePriority.MEDIUM,
            raised_by=self.member,
        )
        assign_issue(issue, self.treasurer, self.admin)
        
        comment = request_clarification(
            issue,
            self.treasurer,
            "Please provide more details about the transaction.",
        )
        self.assertEqual(comment.comment_type, "clarification")
        self.assertEqual(issue.status, IssueStatus.CLARIFICATION_REQUESTED)

    def test_resolution_proposal_workflow(self):
        issue = create_issue(
            chama=self.chama,
            title="Issue for Resolution",
            description="Test issue",
            category=IssueCategory.OPERATIONAL,
            severity=IssuePriority.MEDIUM,
            raised_by=self.member,
        )
        assign_issue(issue, self.treasurer, self.admin)
        change_issue_status(issue, IssueStatus.UNDER_INVESTIGATION, self.treasurer)
        
        resolution = propose_resolution(
            issue,
            self.treasurer,
            resolution_type="ledger_adjustment",
            summary="We will adjust the ledger",
            detailed_action_taken="Added missing contribution",
            financial_adjustment_amount=5000,
        )
        self.assertEqual(resolution.resolution_type, "ledger_adjustment")
        self.assertEqual(issue.status, IssueStatus.RESOLUTION_PROPOSED)

    def test_chairperson_approval_workflow(self):
        issue = create_issue(
            chama=self.chama,
            title="Issue for Approval",
            description="Test issue",
            category=IssueCategory.FINANCIAL,
            severity=IssuePriority.HIGH,
            raised_by=self.member,
        )
        assign_issue(issue, self.treasurer, self.admin)
        change_issue_status(issue, IssueStatus.UNDER_INVESTIGATION, self.treasurer)
        
        resolution = propose_resolution(
            issue,
            self.treasurer,
            resolution_type="refund",
            summary="Refund will be processed",
        )
        
        issue.status = IssueStatus.AWAITING_CHAIRPERSON_APPROVAL
        issue.save()
        
        updated_issue = chairperson_approve_resolution(issue, self.chairperson)
        self.assertEqual(updated_issue.status, IssueStatus.RESOLVED)
        self.assertTrue(updated_issue.chairperson_approved)

    def test_reopen_workflow(self):
        issue = create_issue(
            chama=self.chama,
            title="Issue to Reopen",
            description="Test issue",
            category=IssueCategory.OPERATIONAL,
            severity=IssuePriority.MEDIUM,
            raised_by=self.member,
        )
        
        change_issue_status(issue, IssueStatus.CLOSED, self.admin)
        
        reopened_issue = reopen_issue(
            issue,
            self.member,
            reason="Not satisfied with the resolution",
        )
        self.assertEqual(reopened_issue.status, IssueStatus.REOPENED)
        self.assertEqual(reopened_issue.reopened_count, 1)

    def test_rating_workflow(self):
        issue = create_issue(
            chama=self.chama,
            title="Issue to Rate",
            description="Test issue",
            category=IssueCategory.OPERATIONAL,
            severity=IssuePriority.MEDIUM,
            raised_by=self.member,
        )
        
        change_issue_status(issue, IssueStatus.RESOLVED, self.admin)
        
        rating = rate_issue(issue, self.member, score=4, feedback="Good resolution")
        self.assertEqual(rating.score, 4)
        self.assertEqual(rating.feedback, "Good resolution")

    def test_escalation_workflow(self):
        issue = create_issue(
            chama=self.chama,
            title="Issue to Escalate",
            description="Test issue",
            category=IssueCategory.GOVERNANCE,
            severity=IssuePriority.HIGH,
            raised_by=self.member,
        )
        
        escalated_issue = escalate_issue(
            issue,
            self.admin,
            escalation_type="committee",
            reason="Requires committee review",
        )
        self.assertEqual(escalated_issue.status, IssueStatus.ESCALATED)
        self.assertEqual(escalated_issue.escalation_type, "committee")

    def test_comment_visibility(self):
        issue = create_issue(
            chama=self.chama,
            title="Issue for Comments",
            description="Test issue",
            category=IssueCategory.OPERATIONAL,
            severity=IssuePriority.MEDIUM,
            raised_by=self.member,
        )
        
        public_comment = add_comment(
            issue,
            self.member,
            "This is a public comment",
            comment_type="public_update",
            visibility="member_visible",
        )
        self.assertEqual(public_comment.visibility, "member_visible")
        
        internal_comment = add_comment(
            issue,
            self.treasurer,
            "This is an internal note",
            comment_type="internal_note",
            visibility="internal_only",
        )
        self.assertEqual(internal_comment.visibility, "internal_only")

    def test_status_transition_validation(self):
        issue = create_issue(
            chama=self.chama,
            title="Issue for Validation",
            description="Test issue",
            category=IssueCategory.OPERATIONAL,
            severity=IssuePriority.MEDIUM,
            raised_by=self.member,
        )

        change_issue_status(issue, IssueStatus.CLOSED, self.admin, force=False)
        self.assertEqual(issue.status, IssueStatus.CLOSED)

        with self.assertRaises(IssueServiceError):
            change_issue_status(issue, IssueStatus.ASSIGNED, self.admin, force=False)

        change_issue_status(issue, IssueStatus.ASSIGNED, self.admin, force=True)
        self.assertEqual(issue.status, IssueStatus.ASSIGNED)

    def test_financial_issue_scope(self):
        issue = create_issue(
            chama=self.chama,
            title="Financial Issue",
            description="Test issue",
            category=IssueCategory.FINANCIAL,
            severity=IssuePriority.HIGH,
            raised_by=self.treasurer,
            source_type=IssueSourceType.TREASURER,
            issue_scope="financial",
        )
        self.assertEqual(issue.category, IssueCategory.FINANCIAL)
        self.assertEqual(issue.source_type, IssueSourceType.TREASURER)

    def test_system_issue_creation(self):
        from apps.issues.services import create_system_issue
        
        issue = create_system_issue(
            chama=self.chama,
            trigger_type="missed_payment",
            title="Auto-raised Issue",
            description="Member missed payment",
            category=IssueCategory.PAYMENT_DISPUTE,
            severity=IssuePriority.HIGH,
            linked_object_type="Contribution",
            linked_object_id=str(self.member.id),
            metadata={"amount": 5000},
        )
        self.assertEqual(issue.source_type, IssueSourceType.SYSTEM)
        self.assertEqual(issue.status, IssueStatus.OPEN)


@patch("apps.notifications.services.NotificationService.send_notification", mock_notification)
class IssueRBACTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.factory = APIRequestFactory()
        self.admin = user_model.objects.create_user(
            phone="+254733000101",
            password="password123",
            full_name="Issue RBAC Admin",
        )
        self.secretary = user_model.objects.create_user(
            phone="+254733000102",
            password="password123",
            full_name="Issue RBAC Secretary",
        )
        self.treasurer = user_model.objects.create_user(
            phone="+254733000103",
            password="password123",
            full_name="Issue RBAC Treasurer",
        )
        self.member = user_model.objects.create_user(
            phone="+254733000104",
            password="password123",
            full_name="Issue RBAC Member",
        )
        self.chama = Chama.objects.create(
            name="Issue RBAC Chama",
            created_by=self.admin,
            updated_by=self.admin,
        )
        for user, role in [
            (self.admin, MembershipRole.CHAMA_ADMIN),
            (self.secretary, MembershipRole.SECRETARY),
            (self.treasurer, MembershipRole.TREASURER),
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

    def test_secretary_cannot_see_financial_cases(self):
        payment_issue = create_issue(
            chama=self.chama,
            title="Finance issue",
            description="Contribution mismatch",
            category=IssueCategory.FINANCIAL,
            severity=IssuePriority.HIGH,
            raised_by=self.member,
        )
        meeting_issue = create_issue(
            chama=self.chama,
            title="Meeting issue",
            description="Agenda was missing",
            category=IssueCategory.OPERATIONAL,
            severity=IssuePriority.MEDIUM,
            raised_by=self.member,
        )
        
        request = self.factory.get(
            f"/api/v1/issues/?chama_id={self.chama.id}",
            HTTP_X_CHAMA_ID=str(self.chama.id),
        )
        force_authenticate(request, user=self.secretary)
        response = IssueListCreateView.as_view()(request)
        
        self.assertEqual(response.status_code, 200)
        titles = {row["title"] for row in response.data["results"]}
        self.assertIn("Meeting issue", titles)
        self.assertNotIn("Finance issue", titles)

    def test_treasurer_cannot_see_meeting_cases(self):
        payment_issue = create_issue(
            chama=self.chama,
            title="Finance issue",
            description="Contribution mismatch",
            category=IssueCategory.FINANCIAL,
            severity=IssuePriority.HIGH,
            raised_by=self.member,
        )
        meeting_issue = create_issue(
            chama=self.chama,
            title="Meeting issue",
            description="Agenda was missing",
            category=IssueCategory.OPERATIONAL,
            severity=IssuePriority.MEDIUM,
            raised_by=self.member,
        )
        
        request = self.factory.get(
            f"/api/v1/issues/?chama_id={self.chama.id}",
            HTTP_X_CHAMA_ID=str(self.chama.id),
        )
        force_authenticate(request, user=self.treasurer)
        response = IssueListCreateView.as_view()(request)
        
        self.assertEqual(response.status_code, 200)
        titles = {row["title"] for row in response.data["results"]}
        self.assertIn("Finance issue", titles)
        self.assertNotIn("Meeting issue", titles)

    def test_member_cannot_assign_issues(self):
        from apps.issues.permissions import can_assign_issue

        issue = create_issue(
            chama=self.chama,
            title="Member Issue",
            description="Test",
            category=IssueCategory.OPERATIONAL,
            severity=IssuePriority.LOW,
            raised_by=self.member,
        )
        member_membership = Membership.objects.get(user=self.member, chama=self.chama)
        admin_membership = Membership.objects.get(user=self.admin, chama=self.chama)

        self.assertFalse(can_assign_issue(self.member, member_membership, issue))
        self.assertTrue(can_assign_issue(self.admin, admin_membership, issue))


@patch("apps.notifications.services.NotificationService.send_notification", mock_notification)
@patch("apps.notifications.services.NotificationService.send_notification", mock_notification)
class IssueResolutionTypesTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(
            phone="+254733000201",
            password="password123",
            full_name="Resolution Admin",
        )
        self.chama = Chama.objects.create(
            name="Resolution Test Chama",
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

    def test_ledger_adjustment_resolution(self):
        issue = create_issue(
            chama=self.chama,
            title="Ledger Adjustment Issue",
            description="Test",
            category=IssueCategory.FINANCIAL,
            severity=IssuePriority.HIGH,
            raised_by=self.admin,
        )
        
        resolution = propose_resolution(
            issue,
            self.admin,
            resolution_type="ledger_adjustment",
            summary="Ledger adjusted",
            detailed_action_taken="Added missing contribution",
            financial_adjustment_amount=5000,
        )
        self.assertEqual(resolution.resolution_type, "ledger_adjustment")
        self.assertEqual(resolution.financial_adjustment_amount, 5000)

    def test_warning_resolution(self):
        from apps.issues.models import IssueResolutionType
        
        issue = create_issue(
            chama=self.chama,
            title="Warning Issue",
            description="Test",
            category=IssueCategory.MEMBER_CONDUCT,
            severity=IssuePriority.MEDIUM,
            raised_by=self.admin,
        )
        
        resolution = propose_resolution(
            issue,
            self.admin,
            resolution_type="warning",
            summary="Warning issued",
        )
        self.assertEqual(resolution.resolution_type, IssueResolutionType.WARNING)

    def test_suspension_resolution(self):
        from apps.issues.models import IssueResolutionType
        
        issue = create_issue(
            chama=self.chama,
            title="Suspension Issue",
            description="Test",
            category=IssueCategory.MEMBER_CONDUCT,
            severity=IssuePriority.HIGH,
            raised_by=self.admin,
        )
        
        resolution = propose_resolution(
            issue,
            self.admin,
            resolution_type="suspension",
            summary="Member suspended",
            detailed_action_taken="30 day suspension",
        )
        self.assertEqual(resolution.resolution_type, IssueResolutionType.SUSPENSION)

    def test_dismissal_resolution(self):
        from apps.issues.models import IssueResolutionType
        
        issue = create_issue(
            chama=self.chama,
            title="Dismiss Issue",
            description="Test",
            category=IssueCategory.MEMBER_CONDUCT,
            severity=IssuePriority.LOW,
            raised_by=self.admin,
        )
        
        resolution = propose_resolution(
            issue,
            self.admin,
            resolution_type="dismissal",
            summary="Issue dismissed",
            detailed_action_taken="Insufficient evidence",
        )
        self.assertEqual(resolution.resolution_type, IssueResolutionType.DISMISSAL)
