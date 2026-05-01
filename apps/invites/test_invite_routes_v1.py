from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chama.models import Chama, Invite, InviteStatus, MembershipRole


@pytest.mark.django_db
def test_v1_invites_code_validate_route_exists_and_returns_preview():
    inviter = User.objects.create_user(
        phone="+254700111222",
        full_name="Inviter",
        password="Passw0rd!123",
        phone_verified=True,
    )
    chama = Chama.objects.create(
        name="Route Test Chama",
        description="Test",
        county="Nairobi",
        subcounty="Westlands",
        created_by=inviter,
        updated_by=inviter,
        max_members=10,
        setup_completed=True,
        setup_step=6,
    )
    invite = Invite.objects.create(
        chama=chama,
        invited_by=inviter,
        invitee_phone="+254700999888",
        role_to_assign=MembershipRole.MEMBER,
        role=MembershipRole.MEMBER,
        status=InviteStatus.PENDING,
        expires_at=timezone.now() + timedelta(days=7),
        max_uses=1,
        use_count=0,
        created_by=inviter,
        updated_by=inviter,
        code="MDPQXRJC",
    )

    client = APIClient()
    res = client.post("/api/v1/invites/code/validate/", {"code": invite.code}, format="json")

    assert res.status_code == 200
    assert res.data["code"] == "INVITE_PREVIEW_READY"
    assert res.data["chama_name"] == "Route Test Chama"
    assert res.data["invited_by_name"] == "Inviter"


@pytest.mark.django_db
def test_v1_invites_code_validate_returns_404_for_unknown_code():
    client = APIClient()
    res = client.post("/api/v1/invites/code/validate/", {"code": "DOESNOTEXIST"}, format="json")
    assert res.status_code == 404

