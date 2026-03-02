import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chama.models import Chama, Membership, MembershipRole

pytestmark = pytest.mark.django_db


def create_user(phone: str, full_name: str) -> User:
    return User.objects.create_user(
        phone=phone,
        password="SecurePass123!",
        full_name=full_name,
        email=f"{phone[1:]}@example.com",
    )


def create_chama_with_admin(admin_user: User) -> Chama:
    chama = Chama.objects.create(
        name="Nairobi Savers",
        description="Tenant scoped chama",
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


def auth_client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_user_cannot_view_chama_they_dont_belong_to():
    admin = create_user("+254700000001", "Admin User")
    outsider = create_user("+254700000002", "Outsider User")
    chama = create_chama_with_admin(admin)

    client = auth_client(outsider)
    response = client.get(f"/api/v1/chamas/{chama.id}/")

    assert response.status_code == 403


def test_user_cannot_access_without_approval():
    admin = create_user("+254700000003", "Admin User")
    pending_user = create_user("+254700000004", "Pending User")
    chama = create_chama_with_admin(admin)

    Membership.objects.create(
        user=pending_user,
        chama=chama,
        role=MembershipRole.MEMBER,
        is_active=True,
        is_approved=False,
        created_by=admin,
        updated_by=admin,
    )

    client = auth_client(pending_user)
    response = client.get(f"/api/v1/chamas/{chama.id}/members")

    assert response.status_code == 403


def test_only_admin_can_approve_members_and_change_roles():
    admin = create_user("+254700000005", "Admin User")
    treasurer = create_user("+254700000006", "Treasurer User")
    target = create_user("+254700000007", "Target User")
    chama = create_chama_with_admin(admin)

    Membership.objects.create(
        user=treasurer,
        chama=chama,
        role=MembershipRole.TREASURER,
        is_active=True,
        is_approved=True,
        approved_by=admin,
        created_by=admin,
        updated_by=admin,
    )

    target_membership = Membership.objects.create(
        user=target,
        chama=chama,
        role=MembershipRole.MEMBER,
        is_active=True,
        is_approved=False,
        created_by=admin,
        updated_by=admin,
    )

    treasurer_client = auth_client(treasurer)
    admin_client = auth_client(admin)

    approve_url = f"/api/v1/chamas/{chama.id}/members/{target_membership.id}/approve"
    role_url = f"/api/v1/chamas/{chama.id}/members/{target_membership.id}/role"

    forbidden_approve = treasurer_client.post(approve_url)
    assert forbidden_approve.status_code == 403

    approved = admin_client.post(approve_url)
    assert approved.status_code == 200

    target_membership.refresh_from_db()
    assert target_membership.is_approved is True

    forbidden_role = treasurer_client.patch(role_url, {"role": MembershipRole.AUDITOR}, format="json")
    assert forbidden_role.status_code == 403

    changed_role = admin_client.patch(role_url, {"role": MembershipRole.AUDITOR}, format="json")
    assert changed_role.status_code == 200

    target_membership.refresh_from_db()
    assert target_membership.role == MembershipRole.AUDITOR
