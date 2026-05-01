from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus
from apps.security.audit_chain import ImmutableAuditService
from apps.security.models import AuditChainCheckpoint, AuditLog, TrustedDevice
from apps.security.pin_service import PinService, PinType
from apps.security.rbac import (
    build_role_catalog,
    build_user_access_snapshot,
    get_role_permission_codes,
)
from apps.security.services import SecurityService
from core.exceptions import custom_exception_handler
from core.permissions import IsTreasurerOrAdmin


class _DummyView:
    def __init__(self, chama_id):
        self.kwargs = {"chama_id": str(chama_id)}


class RBACRegistryTests(TestCase):
    def test_role_catalog_contains_expected_roles(self):
        catalog = build_role_catalog()
        codes = {item["code"] for item in catalog}
        self.assertIn("chairperson", codes)
        self.assertIn("treasurer", codes)
        self.assertIn("member", codes)

    def test_treasurer_permission_matrix(self):
        permissions = get_role_permission_codes("treasurer")
        self.assertIn("can_manage_finance", permissions)
        self.assertIn("can_record_payments", permissions)
        self.assertNotIn("can_assign_roles", permissions)


class RBACPermissionTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.chama = Chama.objects.create(name="Security Test Chama")
        self.member_user = User.objects.create_user(
            phone="+254700100001",
            password="testpass123",
        )
        self.treasurer_user = User.objects.create_user(
            phone="+254700100002",
            password="testpass123",
        )
        Membership.objects.create(
            user=self.member_user,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
        )
        Membership.objects.create(
            user=self.treasurer_user,
            chama=self.chama,
            role=MembershipRole.TREASURER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
        )

    def _request_for(self, user):
        raw_request = self.factory.get(f"/api/v1/security/rbac/access?chama_id={self.chama.id}")
        request = APIView().initialize_request(raw_request)
        request.user = user
        return request

    def test_member_cannot_manage_finance(self):
        request = self._request_for(self.member_user)
        allowed = IsTreasurerOrAdmin().has_permission(request, _DummyView(self.chama.id))
        self.assertFalse(allowed)

    def test_treasurer_can_manage_finance(self):
        request = self._request_for(self.treasurer_user)
        allowed = IsTreasurerOrAdmin().has_permission(request, _DummyView(self.chama.id))
        self.assertTrue(allowed)

    def test_access_snapshot_contains_permissions(self):
        snapshot = build_user_access_snapshot(
            user=self.treasurer_user,
            chama_id=str(self.chama.id),
        )
        self.assertEqual(snapshot["role"], "treasurer")
        self.assertIn("can_manage_finance", snapshot["permissions"])


class ExceptionEnvelopeTests(TestCase):
    def test_validation_errors_are_normalized(self):
        response = custom_exception_handler(
            ValidationError({"phone": ["This field is required."]}),
            {"view": None},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["success"], False)
        self.assertEqual(response.data["message"], "Validation failed.")
        self.assertIn("phone", response.data["errors"])


class TrustedDeviceEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            phone="+254700200001",
            password="testpass123",
            full_name="Trusted Device User",
        )
        self.client.force_authenticate(user=self.user)

    def test_trusted_device_lifecycle(self):
        create_response = self.client.post(
            "/api/v1/security/trusted-devices",
            {
                "fingerprint": "device_abc123",
                "device_name": "Pixel 8",
                "device_type": "mobile",
                "user_agent": "MyChama Mobile",
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 200)
        self.assertTrue(create_response.data["is_trusted"])

        list_response = self.client.get("/api/v1/security/trusted-devices")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.data), 1)

        check_response = self.client.get(
            "/api/v1/security/trusted-devices/check",
            {"fingerprint": "device_abc123"},
        )
        self.assertEqual(check_response.status_code, 200)
        self.assertTrue(check_response.data["trusted"])

        revoke_response = self.client.post("/api/v1/security/trusted-devices/revoke-all")
        self.assertEqual(revoke_response.status_code, 200)
        self.assertEqual(revoke_response.data["data"]["revoked"], 1)

        self.assertFalse(
            TrustedDevice.objects.get(user=self.user, fingerprint="device_abc123").is_trusted
        )


class ApiInventoryAccessTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            phone="+254700300001",
            password="testpass123",
            full_name="Normal User",
        )
        self.admin = User.objects.create_user(
            phone="+254700300002",
            password="testpass123",
            full_name="Admin User",
        )
        self.admin.is_staff = True
        self.admin.save(update_fields=["is_staff"])

    def test_api_inventory_requires_admin(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get("/api/v1/security/api-inventory")
        self.assertEqual(response.status_code, 403)

    def test_api_inventory_admin_can_access(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/v1/security/api-inventory")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.get("success"))
        payload = response.data.get("data") or {}
        self.assertIn("items", payload)
        self.assertGreaterEqual(payload.get("count", 0), 1)

    def test_schema_requires_admin_in_tests(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get("/api/schema/")
        self.assertEqual(response.status_code, 403)

        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/schema/")
        self.assertEqual(response.status_code, 200)


class PinServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone="+254700300001",
            password="testpass123",
            full_name="PIN User",
        )

    def test_pin_is_hashed_with_bcrypt_and_verifies(self):
        PinService.set_pin(self.user, "1234", PinType.TRANSACTION)

        secret = self.user.pin_secrets.get(pin_type="transaction")
        self.assertTrue(secret.pin_hash.startswith("bcrypt_sha256$"))

        valid, _message = PinService.verify_pin(
            self.user,
            "1234",
            PinType.TRANSACTION,
        )
        self.assertTrue(valid)

    def test_pin_lockout_escalates_at_configured_thresholds(self):
        PinService.set_pin(self.user, "1234", PinType.WITHDRAWAL)

        for _ in range(5):
            valid, _message = PinService.verify_pin(
                self.user,
                "9999",
                PinType.WITHDRAWAL,
            )
            self.assertFalse(valid)

        secret = self.user.pin_secrets.get(pin_type="withdrawal")
        self.assertEqual(secret.lockout_level, 1)
        self.assertTrue(secret.is_locked)
        self.assertIsNotNone(secret.locked_until)


class PinEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            phone="+254700300002",
            password="testpass123",
            full_name="PIN Endpoint User",
        )
        self.client.force_authenticate(user=self.user)

    def test_pin_endpoints_support_set_change_and_verify(self):
        status_response = self.client.get("/api/v1/security/pins")
        self.assertEqual(status_response.status_code, 200)
        self.assertFalse(status_response.data["data"]["has_transaction_pin"])

        create_response = self.client.post(
            "/api/v1/security/pins/set",
            {
                "pin_type": "transaction",
                "pin": "1234",
                "confirm_pin": "1234",
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 200)

        verify_response = self.client.post(
            "/api/v1/security/pins/verify",
            {
                "pin_type": "transaction",
                "pin": "1234",
                "action": "withdraw",
                "risk_score": 65,
            },
            format="json",
        )
        self.assertEqual(verify_response.status_code, 200)
        self.assertTrue(verify_response.data["data"]["verified"])

        change_response = self.client.post(
            "/api/v1/security/pins/set",
            {
                "pin_type": "transaction",
                "current_pin": "1234",
                "pin": "5678",
                "confirm_pin": "5678",
            },
            format="json",
        )
        self.assertEqual(change_response.status_code, 200)

        verify_new_response = self.client.post(
            "/api/v1/security/pins/verify",
            {
                "pin_type": "transaction",
                "pin": "5678",
            },
            format="json",
        )
        self.assertEqual(verify_new_response.status_code, 200)


class AuditChainTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone="+254700300003",
            password="testpass123",
            full_name="Audit User",
        )
        self.chama = Chama.objects.create(name="Audit Chain Chama")

    def test_security_audit_log_builds_hash_chain_and_checkpoint(self):
        first = SecurityService.create_audit_log(
            action_type="TEST_EVENT_ONE",
            target_type="User",
            target_id=str(self.user.id),
            actor=self.user,
            chama=self.chama,
            metadata={"step": 1},
        )
        second = SecurityService.create_audit_log(
            action_type="TEST_EVENT_TWO",
            target_type="User",
            target_id=str(self.user.id),
            actor=self.user,
            chama=self.chama,
            metadata={"step": 2},
        )

        self.assertEqual(first.chain_index, 1)
        self.assertEqual(second.chain_index, 2)
        self.assertEqual(second.prev_hash, first.event_hash)

        valid, message = ImmutableAuditService.verify_chain_integrity()
        self.assertTrue(valid, message)

        checkpoint = SecurityService.create_audit_checkpoint()
        self.assertIsInstance(checkpoint, AuditChainCheckpoint)
        self.assertEqual(checkpoint.last_chain_index, 2)
        self.assertEqual(checkpoint.record_count, 2)

    def test_security_audit_log_is_append_only(self):
        entry = SecurityService.create_audit_log(
            action_type="IMMUTABLE_TEST",
            target_type="User",
            target_id=str(self.user.id),
            actor=self.user,
            metadata={"immutable": True},
        )

        entry.metadata = {"immutable": False}
        with self.assertRaises(DjangoValidationError):
            entry.save()

        with self.assertRaises(DjangoValidationError):
            entry.delete()
