import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chama.models import Chama, Membership, MembershipRole
from apps.notifications.models import (
    Notification,
    NotificationInboxStatus,
    NotificationPreference,
    NotificationType,
)
from apps.payments.models import (
    MpesaSTKTransaction,
    PaymentIntent,
    PaymentIntentStatus,
    PaymentIntentType,
    PaymentPurpose,
)
from apps.security.models import AuditLog as SecurityAuditLog
from apps.security.models import DeviceSession, LoginAttempt
from apps.security.tasks import security_suspicious_activity_scan

pytestmark = pytest.mark.django_db


def create_user(phone: str, name: str) -> User:
    return User.objects.create_user(
        phone=phone,
        password="SecurePass123!",
        full_name=name,
        email=f"{phone.replace('+', '')}@example.com",
    )


def create_membership(user: User, chama: Chama, role: str, actor: User):
    return Membership.objects.create(
        user=user,
        chama=chama,
        role=role,
        is_active=True,
        is_approved=True,
        approved_by=actor,
        created_by=actor,
        updated_by=actor,
    )


def auth_client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_notifications_read_all_and_preferences_flow():
    admin = create_user("+254711111101", "Admin User")
    member = create_user("+254711111102", "Member User")

    chama = Chama.objects.create(
        name="Notif Security Chama A",
        description="Notifications test",
        county="Nairobi",
        subcounty="Westlands",
        created_by=admin,
        updated_by=admin,
    )
    create_membership(admin, chama, MembershipRole.CHAMA_ADMIN, admin)
    create_membership(member, chama, MembershipRole.MEMBER, admin)

    Notification.objects.create(
        chama=chama,
        recipient=member,
        type=NotificationType.SYSTEM,
        subject="System alert",
        message="Message 1",
        created_by=admin,
        updated_by=admin,
    )
    Notification.objects.create(
        chama=chama,
        recipient=member,
        type=NotificationType.LOAN_UPDATE,
        subject="Loan due",
        message="Message 2",
        created_by=admin,
        updated_by=admin,
    )

    client = auth_client(member)

    list_response = client.get(
        "/api/v1/notifications/",
        {"chama_id": str(chama.id)},
        format="json",
    )
    assert list_response.status_code == 200
    assert len(list_response.json()) == 2

    mark_all_response = client.post(
        "/api/v1/notifications/read-all",
        {"chama_id": str(chama.id)},
        format="json",
    )
    assert mark_all_response.status_code == 200
    assert mark_all_response.json()["updated"] == 2
    assert (
        Notification.objects.filter(
            chama=chama,
            recipient=member,
            inbox_status=NotificationInboxStatus.READ,
        ).count()
        == 2
    )

    pref_response = client.put(
        "/api/v1/notifications/preferences",
        {
            "chama_id": str(chama.id),
            "sms_enabled": False,
            "email_enabled": True,
            "language": "sw",
        },
        format="json",
    )
    assert pref_response.status_code == 200

    preference = NotificationPreference.objects.get(user=member, chama=chama)
    assert preference.sms_enabled is False
    assert preference.language == "sw"


def test_security_device_session_created_on_login_and_revoked():
    cache.clear()
    user = create_user("+254711111201", "Session User")

    login_client = APIClient()
    login_response = login_client.post(
        "/api/v1/auth/login",
        {
            "phone": "0711111201",
            "password": "SecurePass123!",
        },
        format="json",
        HTTP_USER_AGENT="pytest-device",
        HTTP_X_DEVICE_ID="pytest-device-id",
    )
    assert login_response.status_code == 200

    session = DeviceSession.objects.filter(user=user).order_by("-created_at").first()
    assert session is not None
    assert session.is_revoked is False

    client = auth_client(user)
    sessions_response = client.get("/api/v1/security/sessions", format="json")
    assert sessions_response.status_code == 200
    assert len(sessions_response.json()["results"]) >= 1

    revoke_response = client.post(
        f"/api/v1/security/sessions/{session.id}/revoke",
        {"reason": "logout from this device"},
        format="json",
    )
    assert revoke_response.status_code == 200

    session.refresh_from_db()
    assert session.is_revoked is True


def test_security_audit_endpoint_requires_admin_or_auditor():
    admin = create_user("+254711111301", "Admin User")
    auditor = create_user("+254711111302", "Auditor User")
    member = create_user("+254711111303", "Member User")

    chama = Chama.objects.create(
        name="Notif Security Chama B",
        description="Security audit test",
        county="Nairobi",
        subcounty="Westlands",
        created_by=admin,
        updated_by=admin,
    )

    create_membership(admin, chama, MembershipRole.CHAMA_ADMIN, admin)
    create_membership(auditor, chama, MembershipRole.AUDITOR, admin)
    create_membership(member, chama, MembershipRole.MEMBER, admin)

    SecurityAuditLog.objects.create(
        chama=chama,
        actor=admin,
        action_type="APPROVE_LOAN",
        target_type="Loan",
        target_id="loan-1",
        metadata={"amount": "1000.00"},
        created_by=admin,
        updated_by=admin,
    )

    member_response = auth_client(member).get(
        "/api/v1/security/audit",
        {"chama_id": str(chama.id)},
        format="json",
    )
    assert member_response.status_code == 403

    admin_response = auth_client(admin).get(
        "/api/v1/security/audit",
        {"chama_id": str(chama.id)},
        format="json",
    )
    assert admin_response.status_code == 200
    assert admin_response.json()["count"] >= 1

    export_response = auth_client(admin).get(
        "/api/v1/security/audit/export",
        {"chama_id": str(chama.id)},
        format="json",
    )
    assert export_response.status_code == 200
    assert export_response["Content-Type"].startswith("text/csv")
    assert "attachment;" in export_response["Content-Disposition"]
    assert "APPROVE_LOAN" in export_response.content.decode("utf-8")

    auditor_response = auth_client(auditor).get(
        "/api/v1/security/audit",
        {"chama_id": str(chama.id)},
        format="json",
    )
    assert auditor_response.status_code == 200


def test_security_suspicious_scan_flags_multiple_patterns(settings):
    settings.SECURITY_ANOMALY_WINDOW_MINUTES = 120
    settings.SECURITY_FAILED_LOGINS_IP_THRESHOLD = 3
    settings.SECURITY_STK_FAILURE_THRESHOLD = 1
    settings.SECURITY_RAPID_PAYOUT_THRESHOLD = 2
    settings.SECURITY_ROLE_CHANGE_THRESHOLD = 2

    admin = create_user("+254711111401", "Admin User")
    member = create_user("+254711111402", "Member User")
    chama = Chama.objects.create(
        name="Notif Security Chama C",
        description="Security anomalies",
        county="Nairobi",
        subcounty="Embakasi",
        created_by=admin,
        updated_by=admin,
    )
    create_membership(admin, chama, MembershipRole.CHAMA_ADMIN, admin)
    create_membership(member, chama, MembershipRole.MEMBER, admin)

    LoginAttempt.objects.bulk_create(
        [
            LoginAttempt(
                user_identifier=member.phone,
                ip_address="10.20.30.40",
                device_info="pytest",
                success=False,
            ),
            LoginAttempt(
                user_identifier=member.phone,
                ip_address="10.20.30.40",
                device_info="pytest",
                success=False,
            ),
            LoginAttempt(
                user_identifier=member.phone,
                ip_address="10.20.30.40",
                device_info="pytest",
                success=False,
            ),
        ]
    )

    payout_intent_1 = PaymentIntent.objects.create(
        chama=chama,
        created_by=admin,
        updated_by=admin,
        intent_type=PaymentIntentType.WITHDRAWAL,
        purpose=PaymentPurpose.OTHER,
        amount="1500.00",
        phone=member.phone,
        idempotency_key="scan-payout-1",
        currency="KES",
    )
    PaymentIntent.objects.create(
        chama=chama,
        created_by=admin,
        updated_by=admin,
        intent_type=PaymentIntentType.LOAN_DISBURSEMENT,
        purpose=PaymentPurpose.OTHER,
        amount="2000.00",
        phone=member.phone,
        idempotency_key="scan-payout-2",
        currency="KES",
    )

    MpesaSTKTransaction.objects.create(
        chama=chama,
        intent=payout_intent_1,
        phone=member.phone,
        amount="500.00",
        checkout_request_id="scan-stk-1",
        status=PaymentIntentStatus.FAILED,
    )
    MpesaSTKTransaction.objects.create(
        chama=chama,
        intent=payout_intent_1,
        phone=member.phone,
        amount="600.00",
        checkout_request_id="scan-stk-2",
        status=PaymentIntentStatus.FAILED,
    )

    SecurityAuditLog.objects.create(
        chama=chama,
        actor=admin,
        action_type="CHANGE_ROLE",
        target_type="Membership",
        target_id="m-1",
        metadata={},
        created_by=admin,
        updated_by=admin,
    )
    SecurityAuditLog.objects.create(
        chama=chama,
        actor=admin,
        action_type="CHANGE_ROLE",
        target_type="Membership",
        target_id="m-2",
        metadata={},
        created_by=admin,
        updated_by=admin,
    )

    result = security_suspicious_activity_scan()

    assert "failed_logins" in result["risk_flags"]
    assert "rapid_payouts" in result["risk_flags"]
    assert "rapid_role_changes" in result["risk_flags"]
    assert result["suspicious_ips"]["10.20.30.40"] >= 3
    assert result["rapid_payout_attempts"][str(chama.id)] >= 2
