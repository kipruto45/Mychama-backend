import re
from datetime import timedelta

import pytest
from django.core import mail
from django.test import Client, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import (
    OTPDeliveryChannel,
    OTPDeliveryLog,
    OTPDeliveryStatus,
    OTPPurpose,
    OTPToken,
    User,
)
from apps.accounts.services import OTPService
from apps.chama.models import (
    Chama,
    InviteLink,
    MemberStatus,
    Membership,
    MembershipRequest,
    MembershipRequestStatus,
    MembershipRole,
)

pytestmark = pytest.mark.django_db


def create_user(phone: str, full_name: str, *, verified: bool = False) -> User:
    user = User.objects.create_user(
        phone=phone,
        password="SecurePass123!",
        full_name=full_name,
        email=f"{phone.replace('+', '')}@example.com",
    )
    if verified:
        user.phone_verified = True
        user.save(update_fields=["phone_verified"])
    return user


def create_chama_with_admin(admin_user: User, *, allow_public_join: bool = True) -> Chama:
    chama = Chama.objects.create(
        name=f"Workflow {admin_user.phone[-4:]}",
        description="Workflow tests",
        county="Nairobi",
        subcounty="Westlands",
        allow_public_join=allow_public_join,
        created_by=admin_user,
        updated_by=admin_user,
    )
    Membership.objects.create(
        user=admin_user,
        chama=chama,
        role=MembershipRole.CHAMA_ADMIN,
        is_active=True,
        is_approved=True,
        status=MemberStatus.ACTIVE,
        approved_by=admin_user,
        created_by=admin_user,
        updated_by=admin_user,
    )
    return chama


def auth_client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_register_and_verify_phone_otp():
    mail.outbox = []
    response = APIClient().post(
        "/api/v1/auth/register",
        {
            "phone": "0712000011",
            "full_name": "New Member",
            "email": "member@example.com",
            "password": "SecurePass123!",
            "password_confirm": "SecurePass123!",
            "otp_delivery_method": "sms",
        },
        format="json",
    )
    assert response.status_code == 201
    user = User.objects.get(phone="+254712000011")
    assert user.phone_verified is False

    otp = OTPToken.objects.filter(
        user=user,
        purpose=OTPPurpose.VERIFY_PHONE,
        is_used=False,
    ).order_by("-created_at").first()
    assert otp is not None
    assert otp.code == ""
    otp.refresh_from_db()
    assert otp.sent_count == 2
    assert OTPDeliveryLog.objects.filter(
        otp_token=otp,
        channel=OTPDeliveryChannel.SMS,
        status="sent",
    ).exists()
    assert OTPDeliveryLog.objects.filter(
        otp_token=otp,
        channel=OTPDeliveryChannel.EMAIL,
        status="sent",
    ).exists()
    assert len(mail.outbox) == 1
    code_match = re.search(r"\b(\d{6})\b", mail.outbox[-1].body)
    assert code_match is not None

    verify_response = APIClient().post(
        "/api/v1/auth/otp/confirm",
        {
            "phone": user.phone,
            "purpose": OTPPurpose.VERIFY_PHONE,
            "code": code_match.group(1),
        },
        format="json",
    )
    assert verify_response.status_code == 200
    user.refresh_from_db()
    assert user.phone_verified is True


def test_otp_verification_locks_user_after_max_attempts():
    user = create_user("+254712000012", "OTP Lock User", verified=False)
    otp, plain_code = OTPService.generate_otp(
        phone=user.phone,
        user=user,
        purpose=OTPPurpose.VERIFY_PHONE,
        delivery_method="sms",
    )
    wrong_code = "000000" if plain_code != "000000" else "999999"

    last_message = ""
    for _ in range(otp.max_attempts):
        verified, last_message = OTPService.verify_otp(
            phone=user.phone,
            code=wrong_code,
            purpose=OTPPurpose.VERIFY_PHONE,
            user=user,
        )
        assert verified is False

    user.refresh_from_db()
    assert user.is_locked() is True
    assert "Too many failed attempts" in last_message


def test_sms_otp_callback_marks_delivery_delivered():
    user = create_user("+254712000013", "SMS Callback User", verified=False)
    otp, _ = OTPService.generate_otp(
        phone=user.phone,
        user=user,
        purpose=OTPPurpose.VERIFY_PHONE,
        delivery_method="sms",
    )
    delivery_log = OTPDeliveryLog.objects.create(
        otp_token=otp,
        user=user,
        channel=OTPDeliveryChannel.SMS,
        provider_name="africastalking",
        provider_message_id="sms-message-123",
        status=OTPDeliveryStatus.SENT,
        destination=user.phone,
    )

    response = APIClient().post(
        "/api/v1/notifications/callbacks/otp/sms",
        {
            "id": "sms-message-123",
            "status": "Success",
            "phoneNumber": user.phone,
        },
        format="json",
    )

    assert response.status_code == 200
    delivery_log.refresh_from_db()
    assert delivery_log.status == OTPDeliveryStatus.DELIVERED
    assert (
        delivery_log.provider_response["delivery_callback"]["status"]
        == "Success"
    )


def test_email_otp_callback_marks_delivery_delivered():
    user = create_user("+254712000014", "Email Callback User", verified=False)
    otp, _ = OTPService.generate_otp(
        phone=user.phone,
        user=user,
        purpose=OTPPurpose.VERIFY_PHONE,
        delivery_method="email",
    )
    delivery_log = OTPDeliveryLog.objects.create(
        otp_token=otp,
        user=user,
        channel=OTPDeliveryChannel.EMAIL,
        provider_name="sendgrid",
        provider_message_id="email-message-123",
        status=OTPDeliveryStatus.SENT,
        destination=user.email,
    )

    response = APIClient().post(
        "/api/v1/notifications/callbacks/otp/email",
        [
            {
                "event": "delivered",
                "email": user.email,
                "sg_message_id": "email-message-123.filter0001",
            }
        ],
        format="json",
    )

    assert response.status_code == 200
    delivery_log.refresh_from_db()
    assert delivery_log.status == OTPDeliveryStatus.DELIVERED
    assert (
        delivery_log.provider_response["delivery_callback"]["event"]
        == "delivered"
    )


@override_settings(OTP_SMS_CALLBACK_TOKEN="otp-callback-secret")
def test_sms_otp_callback_requires_shared_token_when_configured():
    user = create_user("+254712000015", "SMS Token User", verified=False)
    otp, _ = OTPService.generate_otp(
        phone=user.phone,
        user=user,
        purpose=OTPPurpose.VERIFY_PHONE,
        delivery_method="sms",
    )
    OTPDeliveryLog.objects.create(
        otp_token=otp,
        user=user,
        channel=OTPDeliveryChannel.SMS,
        provider_name="africastalking",
        provider_message_id="sms-message-124",
        status=OTPDeliveryStatus.SENT,
        destination=user.phone,
    )

    client = APIClient()
    forbidden = client.post(
        "/api/v1/notifications/callbacks/otp/sms",
        {
            "id": "sms-message-124",
            "status": "Success",
            "phoneNumber": user.phone,
        },
        format="json",
    )
    assert forbidden.status_code == 403

    allowed = client.post(
        "/api/v1/notifications/callbacks/otp/sms",
        {
            "id": "sms-message-124",
            "status": "Success",
            "phoneNumber": user.phone,
        },
        format="json",
        HTTP_X_OTP_CALLBACK_TOKEN="otp-callback-secret",
    )
    assert allowed.status_code == 200


def test_join_request_creates_membership_request_not_active_membership():
    admin = create_user("+254712000021", "Admin User", verified=True)
    member = create_user("+254712000022", "Member User")
    chama = create_chama_with_admin(admin, allow_public_join=True)

    response = auth_client(member).post(
        f"/api/v1/chamas/{chama.id}/request-join",
        {"request_note": "Please approve my membership."},
        format="json",
    )
    assert response.status_code == 201

    membership_request = MembershipRequest.objects.get(
        user=member,
        chama=chama,
    )
    assert membership_request.status == MembershipRequestStatus.PENDING
    assert not Membership.objects.filter(
        user=member,
        chama=chama,
        is_active=True,
        is_approved=True,
    ).exists()


def test_membership_request_approval_requires_phone_verification():
    admin = create_user("+254712000031", "Admin User", verified=True)
    member = create_user("+254712000032", "Member User", verified=False)
    chama = create_chama_with_admin(admin, allow_public_join=True)
    request_obj = MembershipRequest.objects.create(
        user=member,
        chama=chama,
        status=MembershipRequestStatus.PENDING,
        expires_at=chama.created_at + timedelta(days=7),
        created_by=member,
        updated_by=member,
    )

    response = auth_client(admin).post(
        f"/api/v1/chamas/{chama.id}/membership-requests/{request_obj.id}/approve",
        {},
        format="json",
    )
    assert response.status_code == 400
    assert "Phone verification is required" in response.json()["detail"]


def test_membership_request_approval_sets_active_membership_and_status():
    admin = create_user("+254712000041", "Admin User", verified=True)
    member = create_user("+254712000042", "Member User", verified=True)
    chama = create_chama_with_admin(admin, allow_public_join=True)
    request_obj = MembershipRequest.objects.create(
        user=member,
        chama=chama,
        status=MembershipRequestStatus.PENDING,
        expires_at=chama.created_at + timedelta(days=7),
        created_by=member,
        updated_by=member,
    )

    response = auth_client(admin).post(
        f"/api/v1/chamas/{chama.id}/membership-requests/{request_obj.id}/approve",
        {"note": "Approved."},
        format="json",
    )
    assert response.status_code == 200

    request_obj.refresh_from_db()
    assert request_obj.status == MembershipRequestStatus.APPROVED

    membership = Membership.objects.get(user=member, chama=chama)
    assert membership.is_active is True
    assert membership.is_approved is True
    assert membership.status == MemberStatus.ACTIVE
    assert membership.role == MembershipRole.MEMBER


def test_membership_status_endpoint_returns_pending_redirect():
    admin = create_user("+254712000051", "Admin User", verified=True)
    member = create_user("+254712000052", "Member User", verified=True)
    chama = create_chama_with_admin(admin, allow_public_join=True)
    MembershipRequest.objects.create(
        user=member,
        chama=chama,
        status=MembershipRequestStatus.PENDING,
        expires_at=chama.created_at + timedelta(days=7),
        created_by=member,
        updated_by=member,
    )

    response = auth_client(member).get(
        "/api/v1/auth/membership-status",
        {"chama_id": str(chama.id)},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == MembershipRequestStatus.PENDING
    assert body["can_access"] is False
    assert body["redirect_to"] == "/chama/join/pending/"


def test_join_request_rejects_invite_token_restricted_to_other_phone():
    admin = create_user("+254712000061", "Admin User", verified=True)
    member = create_user("+254712000062", "Member User", verified=True)
    chama = create_chama_with_admin(admin, allow_public_join=False)
    invite = InviteLink.objects.create(
        chama=chama,
        token=InviteLink.generate_token(),
        created_by=admin,
        restricted_phone="+254799999999",
        expires_at=chama.created_at + timedelta(days=7),
        updated_by=admin,
    )

    response = auth_client(member).post(
        f"/api/v1/chamas/{chama.id}/request-join",
        {"invite_token": invite.token},
        format="json",
    )
    assert response.status_code == 400
    assert "invite_token" in response.json()


def test_duplicate_pending_request_returns_existing_request_without_creating_new_one():
    admin = create_user("+254712000071", "Admin User", verified=True)
    member = create_user("+254712000072", "Member User", verified=True)
    chama = create_chama_with_admin(admin, allow_public_join=True)

    first_response = auth_client(member).post(
        f"/api/v1/chamas/{chama.id}/request-join",
        {"request_note": "First request"},
        format="json",
    )
    assert first_response.status_code == 201

    second_response = auth_client(member).post(
        f"/api/v1/chamas/{chama.id}/request-join",
        {"request_note": "Duplicate request"},
        format="json",
    )
    assert second_response.status_code == 200
    assert "pending" in second_response.json()["detail"].lower()
    assert (
        MembershipRequest.objects.filter(
            user=member,
            chama=chama,
            status=MembershipRequestStatus.PENDING,
        ).count()
        == 1
    )


def test_expired_pending_request_is_rotated_to_new_pending_request():
    admin = create_user("+254712000081", "Admin User", verified=True)
    member = create_user("+254712000082", "Member User", verified=True)
    chama = create_chama_with_admin(admin, allow_public_join=True)
    old_request = MembershipRequest.objects.create(
        user=member,
        chama=chama,
        status=MembershipRequestStatus.PENDING,
        expires_at=chama.created_at - timedelta(days=1),
        created_by=member,
        updated_by=member,
    )

    response = auth_client(member).post(
        f"/api/v1/chamas/{chama.id}/request-join",
        {"request_note": "Fresh request after expiry"},
        format="json",
    )
    assert response.status_code == 201

    old_request.refresh_from_db()
    assert old_request.status == MembershipRequestStatus.EXPIRED
    assert (
        MembershipRequest.objects.filter(
            user=member,
            chama=chama,
            status=MembershipRequestStatus.PENDING,
        ).count()
        == 1
    )


def test_authenticated_home_redirects_to_pending_join_status_when_request_exists():
    admin = create_user("+254712000091", "Admin User", verified=True)
    member = create_user("+254712000092", "Member User", verified=True)
    chama = create_chama_with_admin(admin, allow_public_join=True)
    MembershipRequest.objects.create(
        user=member,
        chama=chama,
        status=MembershipRequestStatus.PENDING,
        expires_at=chama.created_at + timedelta(days=7),
        created_by=member,
        updated_by=member,
    )

    client = Client()
    client.force_login(member)
    response = client.get("/")
    assert response.status_code == 302
    assert response.url.endswith("/chama/join/pending/")


def test_authenticated_home_redirects_to_join_page_when_no_membership_or_request():
    user = create_user("+254712000101", "No Membership User", verified=True)
    client = Client()
    client.force_login(user)
    response = client.get("/")
    assert response.status_code == 302
    assert response.url.endswith("/chama/join/")


def test_invite_validate_returns_public_invite_metadata():
    admin = create_user("+254712000111", "Admin User", verified=True)
    chama = create_chama_with_admin(admin, allow_public_join=False)
    invite = InviteLink.objects.create(
        chama=chama,
        token=InviteLink.generate_token(),
        created_by=admin,
        preassigned_role=MembershipRole.TREASURER,
        approval_required=False,
        expires_at=chama.created_at + timedelta(days=7),
        updated_by=admin,
    )

    response = APIClient().get(f"/api/v1/chamas/invites/validate/{invite.token}/")
    assert response.status_code == 200

    body = response.json()
    assert body["token"] == invite.token
    assert body["chama_id"] == str(chama.id)
    assert body["chama_name"] == chama.name
    assert body["role"] == MembershipRole.TREASURER
    assert body["role_display"] == "Treasurer"
    assert body["approval_required"] is False
    assert body["is_valid"] is True


def test_invite_join_auto_approves_membership_with_preassigned_role():
    admin = create_user("+254712000121", "Admin User", verified=True)
    member = create_user("+254712000122", "Member User", verified=True)
    chama = create_chama_with_admin(admin, allow_public_join=False)
    invite = InviteLink.objects.create(
        chama=chama,
        token=InviteLink.generate_token(),
        created_by=admin,
        preassigned_role=MembershipRole.TREASURER,
        approval_required=False,
        expires_at=chama.created_at + timedelta(days=7),
        updated_by=admin,
    )

    response = auth_client(member).post(
        f"/api/v1/chamas/invites/{invite.token}/join/",
        {},
        format="json",
    )
    assert response.status_code == 201

    membership = Membership.objects.get(user=member, chama=chama)
    assert membership.status == MemberStatus.ACTIVE
    assert membership.is_active is True
    assert membership.is_approved is True
    assert membership.role == MembershipRole.TREASURER

    invite.refresh_from_db()
    assert invite.current_uses == 1


def test_invite_join_can_create_pending_request_when_approval_is_required():
    admin = create_user("+254712000131", "Admin User", verified=True)
    member = create_user("+254712000132", "Member User", verified=True)
    chama = create_chama_with_admin(admin, allow_public_join=False)
    invite = InviteLink.objects.create(
        chama=chama,
        token=InviteLink.generate_token(),
        created_by=admin,
        preassigned_role=MembershipRole.AUDITOR,
        approval_required=True,
        expires_at=chama.created_at + timedelta(days=7),
        updated_by=admin,
    )

    response = auth_client(member).post(
        f"/api/v1/chamas/invites/{invite.token}/join/",
        {"request_note": "Please approve my invite join."},
        format="json",
    )
    assert response.status_code == 201

    membership_request = MembershipRequest.objects.get(user=member, chama=chama)
    assert membership_request.status == MembershipRequestStatus.PENDING

    membership = Membership.objects.get(user=member, chama=chama)
    assert membership.status == MemberStatus.PENDING
    assert membership.is_active is False
    assert membership.is_approved is False
    assert membership.role == MembershipRole.AUDITOR

    invite.refresh_from_db()
    assert invite.current_uses == 1
