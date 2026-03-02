from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chama.models import Chama, Membership, MembershipRole
from apps.meetings.models import Meeting

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


def create_chama_with_admin(admin_user: User) -> Chama:
    chama = Chama.objects.create(
        name=f"Meetings Chama {admin_user.phone}",
        description="Meetings tests",
        county="Nairobi",
        subcounty="Westlands",
        created_by=admin_user,
        updated_by=admin_user,
    )
    Membership.objects.create(
        user=admin_user,
        chama=chama,
        role=MembershipRole.CHAMA_ADMIN,
        is_active=True,
        is_approved=True,
        approved_by=admin_user,
        created_by=admin_user,
        updated_by=admin_user,
    )
    return chama


def test_only_member_of_chama_can_view_meetings():
    admin = create_user("+254702000001", "Meetings Admin")
    outsider = create_user("+254702000002", "Meetings Outsider")
    chama = create_chama_with_admin(admin)

    Meeting.objects.create(
        chama=chama,
        title="Monthly Review",
        date=timezone.now() + timedelta(days=3),
        agenda="Review monthly performance",
        created_by=admin,
        updated_by=admin,
    )

    outsider_client = auth_client(outsider)
    forbidden = outsider_client.get(f"/api/v1/meetings/?chama_id={chama.id}")
    assert forbidden.status_code == 403

    admin_client = auth_client(admin)
    allowed = admin_client.get(f"/api/v1/meetings/?chama_id={chama.id}")
    assert allowed.status_code == 200
    assert len(allowed.json()) == 1


def test_only_secretary_or_admin_can_create_meeting():
    admin = create_user("+254702000003", "Meetings Admin 2")
    secretary = create_user("+254702000004", "Meetings Secretary")
    member = create_user("+254702000005", "Meetings Member")
    chama = create_chama_with_admin(admin)

    Membership.objects.create(
        user=secretary,
        chama=chama,
        role=MembershipRole.SECRETARY,
        is_active=True,
        is_approved=True,
        approved_by=admin,
        created_by=admin,
        updated_by=admin,
    )
    Membership.objects.create(
        user=member,
        chama=chama,
        role=MembershipRole.MEMBER,
        is_active=True,
        is_approved=True,
        approved_by=admin,
        created_by=admin,
        updated_by=admin,
    )

    payload = {
        "chama_id": str(chama.id),
        "title": "Budget Planning Meeting",
        "date": (timezone.now() + timedelta(days=2)).isoformat(),
        "agenda": "Plan next quarter budget",
    }

    member_client = auth_client(member)
    forbidden = member_client.post("/api/v1/meetings/", payload, format="json")
    assert forbidden.status_code == 403

    secretary_client = auth_client(secretary)
    secretary_created = secretary_client.post(
        "/api/v1/meetings/", payload, format="json"
    )
    assert secretary_created.status_code == 201

    admin_client = auth_client(admin)
    admin_created = admin_client.post(
        "/api/v1/meetings/",
        {
            **payload,
            "title": "Admin Meeting",
            "date": (timezone.now() + timedelta(days=3)).isoformat(),
        },
        format="json",
    )
    assert admin_created.status_code == 201


def test_meeting_agenda_vote_and_minutes_approval_flow():
    admin = create_user("+254702000006", "Meetings Admin 3")
    secretary = create_user("+254702000007", "Meetings Secretary 3")
    member = create_user("+254702000008", "Meetings Member 3")
    chama = create_chama_with_admin(admin)

    Membership.objects.create(
        user=secretary,
        chama=chama,
        role=MembershipRole.SECRETARY,
        is_active=True,
        is_approved=True,
        approved_by=admin,
        created_by=admin,
        updated_by=admin,
    )
    Membership.objects.create(
        user=member,
        chama=chama,
        role=MembershipRole.MEMBER,
        is_active=True,
        is_approved=True,
        approved_by=admin,
        created_by=admin,
        updated_by=admin,
    )

    meeting = Meeting.objects.create(
        chama=chama,
        title="Governance Meeting",
        date=timezone.now() + timedelta(days=1),
        agenda="Policy updates",
        created_by=admin,
        updated_by=admin,
    )

    member_client = auth_client(member)
    secretary_client = auth_client(secretary)
    admin_client = auth_client(admin)

    agenda_resp = member_client.post(
        f"/api/v1/meetings/{meeting.id}/agenda",
        {"title": "Adopt new savings rule", "description": "Rule text"},
        format="json",
    )
    assert agenda_resp.status_code == 201
    agenda_id = agenda_resp.json()["id"]

    approve_agenda = secretary_client.post(
        f"/api/v1/meetings/{meeting.id}/agenda/{agenda_id}/status",
        {"status": "approved"},
        format="json",
    )
    assert approve_agenda.status_code == 200

    vote_resp = member_client.post(
        f"/api/v1/meetings/{meeting.id}/votes",
        {"agenda_item_id": agenda_id, "choice": "yes"},
        format="json",
    )
    assert vote_resp.status_code == 200

    summary_resp = admin_client.get(
        f"/api/v1/meetings/{meeting.id}/votes/summary",
        {"agenda_item_id": agenda_id},
    )
    assert summary_resp.status_code == 200
    assert summary_resp.json()["votes"]["yes"] == 1

    upload_minutes = secretary_client.post(
        f"/api/v1/meetings/{meeting.id}/minutes/upload",
        {"minutes_text": "Minutes draft"},
        format="json",
    )
    assert upload_minutes.status_code == 200
    assert upload_minutes.json()["minutes_status"] == "pending_approval"

    approve_minutes = admin_client.post(
        f"/api/v1/meetings/{meeting.id}/minutes/approve",
        {"decision": "approved", "note": "Approved"},
        format="json",
    )
    assert approve_minutes.status_code == 201


def test_meeting_create_rejects_schedule_conflict(settings):
    settings.MEETING_DEFAULT_DURATION_MINUTES = 120

    admin = create_user("+254702000009", "Meetings Admin 4")
    chama = create_chama_with_admin(admin)
    admin_client = auth_client(admin)

    existing_start = (timezone.now() + timedelta(days=2)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    Meeting.objects.create(
        chama=chama,
        title="Existing Meeting",
        date=existing_start,
        agenda="Existing agenda",
        created_by=admin,
        updated_by=admin,
    )

    response = admin_client.post(
        "/api/v1/meetings/",
        {
            "chama_id": str(chama.id),
            "title": "Conflicting Meeting",
            "date": (existing_start + timedelta(minutes=45)).isoformat(),
            "agenda": "Should conflict",
        },
        format="json",
    )
    assert response.status_code == 400
    assert "conflict_meeting" in response.json()
