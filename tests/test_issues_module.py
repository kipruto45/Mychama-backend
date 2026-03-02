from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chama.models import Chama, Membership, MembershipRole
from apps.finance.models import Loan, LoanInterestType, LoanStatus
from apps.issues.models import (
    Issue,
    IssueActivityAction,
    IssueAttachment,
    IssueCategory,
    IssuePriority,
    IssueStatus,
    Suspension,
    Warning,
)
from apps.notifications.services import NotificationService

pytestmark = pytest.mark.django_db


def create_user(phone: str, full_name: str) -> User:
    return User.objects.create_user(
        phone=phone,
        password="SecurePass123!",
        full_name=full_name,
        email=f"{phone[1:]}@example.com",
    )


def auth_client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def create_chama_with_roles():
    admin = create_user("+254706000001", "Issues Admin")
    secretary = create_user("+254706000002", "Issues Secretary")
    treasurer = create_user("+254706000003", "Issues Treasurer")
    auditor = create_user("+254706000004", "Issues Auditor")
    member_a = create_user("+254706000005", "Issues Member A")
    member_b = create_user("+254706000006", "Issues Member B")

    chama = Chama.objects.create(
        name="Issues Test Chama",
        description="Issues module tests",
        county="Nairobi",
        subcounty="Kasarani",
        created_by=admin,
        updated_by=admin,
    )

    role_map = {
        admin: MembershipRole.CHAMA_ADMIN,
        secretary: MembershipRole.SECRETARY,
        treasurer: MembershipRole.TREASURER,
        auditor: MembershipRole.AUDITOR,
        member_a: MembershipRole.MEMBER,
        member_b: MembershipRole.MEMBER,
    }
    for user, role in role_map.items():
        Membership.objects.create(
            chama=chama,
            user=user,
            role=role,
            is_active=True,
            is_approved=True,
            approved_by=admin,
            created_by=admin,
            updated_by=admin,
        )

    return chama, admin, secretary, treasurer, auditor, member_a, member_b


def test_member_cannot_view_others_issues():
    chama, _, _, _, _, member_a, member_b = create_chama_with_roles()
    issue = Issue.objects.create(
        chama=chama,
        title="Missing minutes",
        description="Minutes not uploaded",
        category=IssueCategory.MEETING,
        priority=IssuePriority.MEDIUM,
        status=IssueStatus.OPEN,
        created_by=member_a,
        updated_by=member_a,
    )

    response = auth_client(member_b).get(f"/api/v1/issues/{issue.id}/")
    assert response.status_code == 403


def test_issue_update_rejects_stale_conditional_header():
    chama, _, _, _, _, member_a, _ = create_chama_with_roles()
    issue = Issue.objects.create(
        chama=chama,
        title="Editable issue",
        description="Original description",
        category=IssueCategory.OTHER,
        priority=IssuePriority.MEDIUM,
        status=IssueStatus.OPEN,
        created_by=member_a,
        updated_by=member_a,
    )

    client = auth_client(member_a)
    stale_value = (issue.updated_at - timedelta(seconds=5)).isoformat()
    stale_response = client.patch(
        f"/api/v1/issues/{issue.id}/",
        {"description": "Attempt stale update"},
        format="json",
        HTTP_IF_UNMODIFIED_SINCE=stale_value,
    )
    assert stale_response.status_code == 412

    fresh_response = client.patch(
        f"/api/v1/issues/{issue.id}/",
        {"description": "Fresh update"},
        format="json",
        HTTP_IF_UNMODIFIED_SINCE=issue.updated_at.isoformat(),
    )
    assert fresh_response.status_code == 200
    issue.refresh_from_db()
    assert issue.description == "Fresh update"


def test_admin_can_view_all_in_chama():
    chama, admin, _, _, _, member_a, member_b = create_chama_with_roles()

    Issue.objects.create(
        chama=chama,
        title="Issue A",
        description="Issue A description",
        category=IssueCategory.BEHAVIOR,
        priority=IssuePriority.HIGH,
        status=IssueStatus.OPEN,
        created_by=member_a,
        updated_by=member_a,
    )
    Issue.objects.create(
        chama=chama,
        title="Issue B",
        description="Issue B description",
        category=IssueCategory.FINANCE,
        priority=IssuePriority.MEDIUM,
        status=IssueStatus.IN_REVIEW,
        created_by=member_b,
        updated_by=member_b,
    )

    response = auth_client(admin).get(f"/api/v1/issues/?chama_id={chama.id}")
    assert response.status_code == 200
    assert response.json()["count"] == 2


def test_warning_creates_warning_activity_and_notification_call(monkeypatch):
    chama, admin, secretary, _, _, member_a, member_b = create_chama_with_roles()
    issue = Issue.objects.create(
        chama=chama,
        title="Harassment report",
        description="Behavior violation",
        category=IssueCategory.BEHAVIOR,
        priority=IssuePriority.HIGH,
        status=IssueStatus.OPEN,
        created_by=member_a,
        updated_by=member_a,
        reported_user=member_b,
    )

    calls = []

    def fake_send_notification(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return None

    monkeypatch.setattr(
        NotificationService, "send_notification", fake_send_notification
    )

    response = auth_client(secretary).post(
        f"/api/v1/issues/{issue.id}/warn",
        {
            "reason": "Use of abusive language",
            "severity": "high",
            "message_to_user": "You have been warned for misconduct.",
            "channels": ["sms", "email"],
        },
        format="json",
    )

    assert response.status_code == 201
    assert Warning.objects.filter(issue=issue, user=member_b).exists()
    assert issue.activity_logs.filter(action=IssueActivityAction.WARNED).exists()
    assert len(calls) == 1
    assert calls[0]["kwargs"]["user"].id == member_b.id

    # Admin can also warn.
    response_admin = auth_client(admin).post(
        f"/api/v1/issues/{issue.id}/warn",
        {
            "reason": "Repeated misconduct",
            "severity": "medium",
            "message_to_user": "Final warning",
        },
        format="json",
    )
    assert response_admin.status_code == 201


def test_suspension_updates_membership_creates_records_and_notification(monkeypatch):
    chama, _, secretary, _, _, member_a, member_b = create_chama_with_roles()
    issue = Issue.objects.create(
        chama=chama,
        title="Fraud allegation",
        description="Potential fake receipts",
        category=IssueCategory.FINANCE,
        priority=IssuePriority.URGENT,
        status=IssueStatus.OPEN,
        created_by=member_a,
        updated_by=member_a,
        reported_user=member_b,
    )

    calls = []

    def fake_send_notification(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return None

    monkeypatch.setattr(
        NotificationService, "send_notification", fake_send_notification
    )

    response = auth_client(secretary).post(
        f"/api/v1/issues/{issue.id}/suspend",
        {
            "reason": "Confirmed fraud pattern",
            "message_to_user": "You are suspended pending final review.",
            "channels": ["sms", "email"],
        },
        format="json",
    )

    assert response.status_code == 201
    assert Suspension.objects.filter(
        issue=issue, user=member_b, is_active=True
    ).exists()
    assert issue.activity_logs.filter(action=IssueActivityAction.SUSPENDED).exists()

    membership = Membership.objects.get(chama=chama, user=member_b)
    assert membership.is_active is False

    assert len(calls) == 1
    assert calls[0]["kwargs"]["user"].id == member_b.id


def test_issue_filters_and_search_work():
    chama, admin, _, _, _, member_a, member_b = create_chama_with_roles()
    loan = Loan.objects.create(
        chama=chama,
        member=member_a,
        principal="8000.00",
        interest_type=LoanInterestType.FLAT,
        interest_rate="12.00",
        duration_months=6,
        status=LoanStatus.ACTIVE,
        created_by=admin,
        updated_by=admin,
    )

    Issue.objects.create(
        chama=chama,
        title="Loan dispute",
        description="Member disputes the loan amount",
        category=IssueCategory.LOAN,
        priority=IssuePriority.HIGH,
        status=IssueStatus.OPEN,
        loan=loan,
        created_by=member_a,
        updated_by=member_a,
    )
    Issue.objects.create(
        chama=chama,
        title="Technical portal outage",
        description="Portal inaccessible",
        category=IssueCategory.TECHNICAL,
        priority=IssuePriority.MEDIUM,
        status=IssueStatus.IN_REVIEW,
        created_by=member_b,
        updated_by=member_b,
    )

    client = auth_client(admin)

    by_status = client.get(f"/api/v1/issues/?chama_id={chama.id}&status=open")
    assert by_status.status_code == 200
    assert by_status.json()["count"] == 1

    by_search = client.get(f"/api/v1/issues/?chama_id={chama.id}&search=portal")
    assert by_search.status_code == 200
    assert by_search.json()["count"] == 1

    by_category = client.get(f"/api/v1/issues/?chama_id={chama.id}&category=loan")
    assert by_category.status_code == 200
    assert by_category.json()["count"] == 1

    by_loan = client.get(f"/api/v1/issues/?chama_id={chama.id}&loan_id={loan.id}")
    assert by_loan.status_code == 200
    assert by_loan.json()["count"] == 1


def test_attachment_upload_works(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path

    chama, _, _, _, _, member_a, member_b = create_chama_with_roles()
    issue = Issue.objects.create(
        chama=chama,
        title="Evidence upload test",
        description="Attach supporting document",
        category=IssueCategory.OTHER,
        priority=IssuePriority.MEDIUM,
        status=IssueStatus.OPEN,
        created_by=member_a,
        updated_by=member_a,
        reported_user=member_b,
    )

    evidence = SimpleUploadedFile(
        "evidence.pdf",
        b"%PDF-1.4 test evidence content",
        content_type="application/pdf",
    )

    response = auth_client(member_a).post(
        f"/api/v1/issues/{issue.id}/attachments",
        {"file": evidence},
        format="multipart",
    )

    assert response.status_code == 201
    assert IssueAttachment.objects.filter(issue=issue).exists()
    attachment = IssueAttachment.objects.get(issue=issue)
    assert attachment.size > 0
