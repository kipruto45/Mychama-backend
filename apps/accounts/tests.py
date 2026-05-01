from unittest.mock import patch
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from apps.accounts.kyc_service import KYCService
from apps.accounts.kyc.providers.smile_identity import SmileProviderResult
from apps.accounts.kyc.services import KYCWorkflowService, sync_user_access_state
from apps.accounts.kyc.tasks import daily_sanctions_rescreen
from apps.accounts.models import MemberKYC, MemberKYCStatus, OTPPurpose, ReferralReward
from apps.accounts.tasks import (
    kyc_daily_sanctions_screening,
    kyc_renewal_and_expiry_reminders,
)
from apps.accounts.services import (
    OTPDeliveryError,
    OTPDispatchResult,
    OTPRateLimitError,
    OTPService,
)
from apps.accounts.views import ReferralLeaderboardView, ReferralSummaryView
from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus
from apps.notifications.models import Notification


class ReferralSummaryViewTests(TestCase):
    def test_referral_summary_returns_stats_and_history(self):
        user_model = get_user_model()
        referrer = user_model.objects.create_user(
            phone="+254700000117",
            password="testpass123",
            full_name="Referrer User",
        )

        Chama.objects.create(
            name="Referral Alpha",
            referred_by=referrer,
            referral_code_used=referrer.referral_code,
            setup_completed=True,
        )
        Chama.objects.create(
            name="Referral Beta",
            referred_by=referrer,
            referral_code_used=referrer.referral_code,
            setup_completed=False,
        )

        request = APIRequestFactory().get("/api/v1/auth/referrals")
        force_authenticate(request, user=referrer)

        response = ReferralSummaryView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["referral_code"], referrer.referral_code)
        self.assertEqual(response.data["stats"]["total_referrals"], 2)
        self.assertEqual(response.data["stats"]["completed_referrals"], 1)
        self.assertEqual(response.data["stats"]["pending_setup_referrals"], 1)
        self.assertEqual(response.data["stats"]["reward_eligible_referrals"], 1)
        self.assertIn("policy", response.data)
        self.assertIn("rewards", response.data)
        self.assertEqual(len(response.data["history"]), 2)

    def test_referral_leaderboard_returns_ranked_referrers_for_admin(self):
        user_model = get_user_model()
        admin = user_model.objects.create_user(
            phone="+254700000118",
            password="testpass123",
            full_name="Admin User",
            is_staff=True,
        )
        top_referrer = user_model.objects.create_user(
            phone="+254700000119",
            password="testpass123",
            full_name="Top Referrer",
        )
        other_referrer = user_model.objects.create_user(
            phone="+254700000120",
            password="testpass123",
            full_name="Other Referrer",
        )

        alpha = Chama.objects.create(
            name="Top Alpha",
            referred_by=top_referrer,
            referral_code_used=top_referrer.referral_code,
            setup_completed=True,
        )
        Chama.objects.create(
            name="Top Beta",
            referred_by=top_referrer,
            referral_code_used=top_referrer.referral_code,
            setup_completed=True,
        )
        Chama.objects.create(
            name="Other Alpha",
            referred_by=other_referrer,
            referral_code_used=other_referrer.referral_code,
            setup_completed=False,
        )
        ReferralReward.objects.create(
            referrer=top_referrer,
            referred_chama=alpha,
            reward_value=7,
            status=ReferralReward.APPLIED,
        )

        request = APIRequestFactory().get("/api/v1/auth/referrals/leaderboard")
        force_authenticate(request, user=admin)

        response = ReferralLeaderboardView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["leaderboard"][0]["user_id"], str(top_referrer.id))
        self.assertEqual(response.data["leaderboard"][0]["completed_referrals"], 2)
        self.assertEqual(response.data["leaderboard"][0]["reward_days_earned"], 7)


class AuthContractCompatibilityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user_model = get_user_model()
        self.user = self.user_model.objects.create_user(
            phone="+254700001010",
            password="OldPass123!",
            full_name="Auth Contract User",
        )

    @patch(
        "apps.accounts.services.OTPService.send_otp",
        return_value=OTPDispatchResult(
            requested_method="sms",
            sms_sent=True,
            masked_phone="+2547***010",
        ),
    )
    def test_login_returns_otp_challenge_for_password_step(self, _send_otp):
        response = self.client.post(
            "/api/v1/auth/login",
            {
                "phone": "0700001010",
                "password": "OldPass123!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["code"], "OTP_REQUIRED")
        self.assertEqual(response.data["identifier"], self.user.phone)
        self.assertEqual(response.data["purpose"], OTPPurpose.LOGIN_2FA)
        self.assertNotIn("access", response.data)
        self.assertNotIn("refresh", response.data)

    def test_logout_accepts_missing_refresh_for_backwards_compatibility(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post("/api/v1/auth/logout", {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_205_RESET_CONTENT)

    def test_password_reset_request_accepts_legacy_phone_payload(self):
        response = self.client.post(
            "/api/v1/auth/password-reset/request",
            {"phone": self.user.phone},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["code"], "PASSWORD_RESET_CODE_SENT")

    @patch("apps.accounts.services.OTPService.verify_otp", return_value=(True, "ok"))
    def test_password_reset_confirm_accepts_legacy_phone_code_payload(self, _verify_otp):
        response = self.client.post(
            "/api/v1/auth/password-reset/confirm",
            {
                "phone": self.user.phone,
                "code": "123456",
                "new_password": "NewPass123!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["code"], "PASSWORD_RESET_SUCCESS")

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewPass123!"))

    def test_me_response_includes_mobile_auth_state_fields(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get("/api/v1/auth/me")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("phone_verified", response.data)
        self.assertIn("profile_completed", response.data)
        self.assertIn("active_chama_id", response.data)
        self.assertFalse(response.data["phone_verified"])
        self.assertFalse(response.data["profile_completed"])

    def test_membership_options_alias_matches_supported_endpoint(self):
        self.client.force_authenticate(user=self.user)

        canonical = self.client.get("/api/v1/auth/chamas")
        alias = self.client.get("/api/v1/auth/membership-options")

        self.assertEqual(canonical.status_code, status.HTTP_200_OK)
        self.assertEqual(alias.status_code, status.HTTP_200_OK)
        self.assertEqual(alias.data, canonical.data)

    @patch("apps.accounts.services.OTPService.verify_otp", return_value=(True, "ok"))
    def test_public_otp_verify_returns_enriched_user_payload(self, _verify_otp):
        response = self.client.post(
            "/api/v1/auth/otp/confirm",
            {
                "phone": self.user.phone,
                "identifier": self.user.phone,
                "code": "123456",
                "purpose": "verify_phone",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["code"], "OTP_VERIFIED")
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertTrue(response.data["user"]["phone_verified"])
        self.assertIn("profile_completed", response.data["user"])
        self.assertIn("active_chama_id", response.data["user"])

    @patch("apps.accounts.services.OTPService.verify_otp", return_value=(True, "ok"))
    def test_refresh_rotates_refresh_token_and_rejects_reuse(self, _verify_otp):
        verify_response = self.client.post(
            "/api/v1/auth/otp/confirm",
            {
                "phone": self.user.phone,
                "identifier": self.user.phone,
                "code": "123456",
                "purpose": "login_2fa",
            },
            format="json",
            HTTP_X_DEVICE_ID="test-device-1",
            HTTP_X_DEVICE_NAME="Test Device",
        )
        self.assertEqual(verify_response.status_code, status.HTTP_200_OK)
        original_refresh = verify_response.data["refresh"]

        first_refresh = self.client.post(
            "/api/v1/auth/refresh",
            {"refresh": original_refresh},
            format="json",
            HTTP_X_DEVICE_ID="test-device-1",
            HTTP_X_DEVICE_NAME="Test Device",
        )
        self.assertEqual(first_refresh.status_code, status.HTTP_200_OK)
        self.assertIn("refresh", first_refresh.data)
        self.assertNotEqual(first_refresh.data["refresh"], original_refresh)

        reused_refresh = self.client.post(
            "/api/v1/auth/refresh",
            {"refresh": original_refresh},
            format="json",
            HTTP_X_DEVICE_ID="test-device-1",
            HTTP_X_DEVICE_NAME="Test Device",
        )
        self.assertEqual(reused_refresh.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(reused_refresh.data["code"], "TOKEN_INVALID")

    @patch(
        "apps.accounts.services.OTPService.send_otp",
        return_value=OTPDispatchResult(
            requested_method="email",
            email_sent=True,
            masked_email="a***@example.com",
        ),
    )
    def test_register_returns_email_verification_context_when_email_delivery_selected(self, _send_otp):
        response = self.client.post(
            "/api/v1/auth/register",
            {
                "phone": "0700001011",
                "full_name": "Email OTP User",
                "email": "email-otp@example.com",
                "password": "StrongPass123!",
                "password_confirm": "StrongPass123!",
                "otp_delivery_method": "email",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["code"], "REGISTER_SUCCESS_OTP_SENT")
        self.assertEqual(response.data["data"]["identifier"], "email-otp@example.com")
        self.assertEqual(response.data["data"]["purpose"], "verify_email")
        self.assertEqual(response.data["data"]["delivery_method"], "email")
        self.assertEqual(response.data["data"]["phone"], "+254700001011")

    @patch(
        "apps.accounts.services.OTPService.send_otp",
        side_effect=OTPDeliveryError("provider unavailable"),
    )
    def test_register_does_not_fake_otp_success_when_delivery_fails(self, _send_otp):
        response = self.client.post(
            "/api/v1/auth/register",
            {
                "phone": "0700001012",
                "full_name": "Delivery Failure User",
                "email": "delivery-failure@example.com",
                "password": "StrongPass123!",
                "password_confirm": "StrongPass123!",
                "otp_delivery_method": "email",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertFalse(response.data["success"])
        self.assertEqual(response.data["code"], "REGISTER_SUCCESS_OTP_FAILED")
        self.assertEqual(response.data["data"]["identifier"], "delivery-failure@example.com")
        self.assertEqual(response.data["data"]["purpose"], "verify_email")

    def test_register_rejects_weak_password_with_field_errors(self):
        response = self.client.post(
            "/api/v1/auth/register",
            {
                "phone": "0700001091",
                "full_name": "Weak Password User",
                "email": "weak-password@example.com",
                "password": "password",
                "password_confirm": "password",
                "otp_delivery_method": "sms",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        self.assertEqual(response.data["code"], "WEAK_PASSWORD")
        self.assertIn("password", response.data["errors"])
        self.assertTrue(len(response.data["errors"]["password"]) >= 1)

    def test_register_rejects_password_mismatch_with_field_error(self):
        response = self.client.post(
            "/api/v1/auth/register",
            {
                "phone": "0700001092",
                "full_name": "Password Mismatch User",
                "email": "password-mismatch@example.com",
                "password": "StrongPass123!",
                "password_confirm": "StrongPass123!x",
                "otp_delivery_method": "sms",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        self.assertEqual(response.data["code"], "PASSWORD_MISMATCH")
        self.assertIn("password_confirm", response.data["errors"])

    def test_register_rejects_duplicate_phone_with_specific_code(self):
        existing = self.user_model.objects.create_user(
            phone="+254700001099",
            password="StrongPass123!",
            full_name="Existing User",
        )

        response = self.client.post(
            "/api/v1/auth/register",
            {
                "phone": "0700001099",
                "full_name": "Duplicate Phone User",
                "email": "duplicate-phone@example.com",
                "password": "StrongPass123!",
                "password_confirm": "StrongPass123!",
                "otp_delivery_method": "sms",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        self.assertEqual(response.data["code"], "PHONE_ALREADY_EXISTS")
        self.assertIn("phone", response.data["errors"])

    def test_register_rejects_duplicate_email_with_specific_code(self):
        self.user.phone = "+254700001197"
        self.user.email = "duplicate-email@example.com"
        self.user.save(update_fields=["phone", "email"])

        response = self.client.post(
            "/api/v1/auth/register",
            {
                "phone": "0700001098",
                "full_name": "Duplicate Email User",
                "email": "duplicate-email@example.com",
                "password": "StrongPass123!",
                "password_confirm": "StrongPass123!",
                "otp_delivery_method": "email",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        self.assertEqual(response.data["code"], "EMAIL_ALREADY_EXISTS")
        self.assertIn("email", response.data["errors"])

    def test_public_otp_request_rejects_mixed_phone_purpose_with_email_delivery(self):
        response = self.client.post(
            "/api/v1/auth/otp/send",
            {
                "identifier": "+254700001010",
                "purpose": "verify_phone",
                "delivery_method": "email",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        self.assertEqual(response.data["code"], "INVALID_VERIFICATION_PURPOSE")
        self.assertIn("purpose", response.data["errors"])

    @patch("apps.accounts.services.OTPService.verify_otp", return_value=(True, "ok"))
    def test_public_email_otp_verify_accepts_email_identifier_and_marks_user_verified(self, _verify_otp):
        self.user.email = "verified-by-email@example.com"
        self.user.save(update_fields=["email"])

        response = self.client.post(
            "/api/v1/auth/otp/confirm",
            {
                "identifier": "verified-by-email@example.com",
                "purpose": "verify_email",
                "code": "123456",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["code"], "OTP_VERIFIED")
        self.assertEqual(response.data["data"]["identifier"], "verified-by-email@example.com")
        self.assertEqual(response.data["data"]["purpose"], "verify_email")

        self.user.refresh_from_db()
        self.assertTrue(self.user.phone_verified)


class MemberKYCSubmissionContractTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user_model = get_user_model()
        self.admin = self.user_model.objects.create_user(
            phone="+254755000001",
            password="AdminPass123!",
            full_name="KYC Admin User",
        )
        self.member = self.user_model.objects.create_user(
            phone="+254755000002",
            password="MemberPass123!",
            full_name="KYC Member User",
        )
        self.chama = Chama.objects.create(
            name="KYC Test Chama",
            created_by=self.admin,
            updated_by=self.admin,
        )
        Membership.objects.create(
            user=self.member,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            approved_by=self.admin,
            approved_at=self.chama.created_at,
            created_by=self.admin,
            updated_by=self.admin,
        )
        self.client.force_authenticate(user=self.member)

    def test_submit_kyc_accepts_mpesa_name_and_location_fields(self):
        id_front_image = SimpleUploadedFile(
            "id-front.jpg",
            b"\xff\xd8\xff\xe0\x00\x10JFIF",
            content_type="image/jpeg",
        )
        id_back_image = SimpleUploadedFile(
            "id-back.jpg",
            b"\xff\xd8\xff\xe0\x00\x10JFIF",
            content_type="image/jpeg",
        )
        response = self.client.post(
            "/api/v1/auth/kyc",
            {
                "chama_id": str(self.chama.id),
                "document_type": "national_id",
                "id_number": "12345678",
                "mpesa_registered_name": "KYC Member User",
                "location_latitude": "-1.292100",
                "location_longitude": "36.821900",
                "id_front_image": id_front_image,
                "id_back_image": id_back_image,
            },
            format="multipart",
        )

        self.assertIn(response.status_code, [status.HTTP_201_CREATED, status.HTTP_200_OK])
        self.assertEqual(response.data["id_number"], "12345678")
        self.assertEqual(response.data["mpesa_registered_name"], "KYC Member User")
        self.assertEqual(response.data["document_type"], "national_id")
        self.assertIn("location_latitude", response.data)
        self.assertIn("location_longitude", response.data)

    def test_submit_kyc_requires_both_location_coordinates_when_provided(self):
        id_front_image = SimpleUploadedFile(
            "id-front.jpg",
            b"\xff\xd8\xff\xe0\x00\x10JFIF",
            content_type="image/jpeg",
        )
        id_back_image = SimpleUploadedFile(
            "id-back.jpg",
            b"\xff\xd8\xff\xe0\x00\x10JFIF",
            content_type="image/jpeg",
        )
        response = self.client.post(
            "/api/v1/auth/kyc",
            {
                "chama_id": str(self.chama.id),
                "document_type": "national_id",
                "id_number": "12345678",
                "location_latitude": "-1.292100",
                "id_front_image": id_front_image,
                "id_back_image": id_back_image,
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            "Both location_latitude and location_longitude are required",
            str(response.data),
        )

    def test_submit_kyc_rejects_duplicate_id_numbers(self):
        other_member = self.user_model.objects.create_user(
            phone="+254755000099",
            password="OtherPass123!",
            full_name="Other Member",
        )
        Membership.objects.create(
            user=other_member,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            approved_by=self.admin,
            approved_at=self.chama.created_at,
            created_by=self.admin,
            updated_by=self.admin,
        )
        MemberKYC.objects.create(
            user=other_member,
            chama=self.chama,
            document_type="national_id",
            id_number="12345678",
            status=MemberKYCStatus.APPROVED,
        )

        id_front_image = SimpleUploadedFile(
            "id-front.jpg",
            b"\xff\xd8\xff\xe0\x00\x10JFIF",
            content_type="image/jpeg",
        )
        id_back_image = SimpleUploadedFile(
            "id-back.jpg",
            b"\xff\xd8\xff\xe0\x00\x10JFIF",
            content_type="image/jpeg",
        )
        response = self.client.post(
            "/api/v1/auth/kyc",
            {
                "chama_id": str(self.chama.id),
                "document_type": "national_id",
                "id_number": "12345678",
                "mpesa_registered_name": "KYC Member User",
                "id_front_image": id_front_image,
                "id_back_image": id_back_image,
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "rejected")
        self.assertTrue(response.data["duplicate_id_detected"])

    def test_submit_kyc_allows_platform_submission_without_chama_membership(self):
        non_member = self.user_model.objects.create_user(
            phone="+254755000123",
            password="MemberPass123!",
            full_name="Platform KYC User",
        )
        self.client.force_authenticate(user=non_member)

        id_front_image = SimpleUploadedFile(
            "id-front.jpg",
            b"\xff\xd8\xff\xe0\x00\x10JFIF",
            content_type="image/jpeg",
        )
        id_back_image = SimpleUploadedFile(
            "id-back.jpg",
            b"\xff\xd8\xff\xe0\x00\x10JFIF",
            content_type="image/jpeg",
        )
        selfie_image = SimpleUploadedFile(
            "selfie.jpg",
            b"\xff\xd8\xff\xe0\x00\x10JFIF",
            content_type="image/jpeg",
        )
        response = self.client.post(
            "/api/v1/auth/kyc",
            {
                "document_type": "national_id",
                "id_number": "12345678",
                "mpesa_registered_name": "Platform KYC User",
                "id_front_image": id_front_image,
                "id_back_image": id_back_image,
                "selfie_image": selfie_image,
            },
            format="multipart",
        )

        self.assertIn(response.status_code, [status.HTTP_201_CREATED, status.HTTP_200_OK])
        self.assertEqual(response.data.get("code"), "KYC_SUBMITTED")
        self.assertEqual(response.data["id_number"], "12345678")
        self.assertIsNone(response.data.get("chama_id"))


class OTPAutomationTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.user = self.user_model.objects.create_user(
            phone="+254755100001",
            password="OtpPass123!",
            full_name="OTP User",
        )

    @override_settings(OTP_LOCKOUT_SECONDS=1800)
    def test_otp_lockout_last_for_thirty_minutes_after_max_attempts(self):
        otp_token, _ = OTPService.generate_otp(
            phone=self.user.phone,
            user=self.user,
            purpose=OTPPurpose.LOGIN_2FA,
        )

        for _ in range(5):
            verified, _message = OTPService.verify_otp(
                phone=self.user.phone,
                code="000000",
                purpose=OTPPurpose.LOGIN_2FA,
                user=self.user,
            )
            self.assertFalse(verified)

        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.locked_until)
        self.assertIsNotNone(self.user.account_locked_until)
        remaining = int((self.user.locked_until - timezone.now()).total_seconds())
        self.assertGreaterEqual(remaining, 1700)
        self.assertLessEqual(remaining, 1800)

        verified, message = OTPService.verify_otp(
            phone=self.user.phone,
            code="000000",
            purpose=OTPPurpose.LOGIN_2FA,
            user=self.user,
        )
        self.assertFalse(verified)
        self.assertIn("Too many failed attempts", message)

    @patch("apps.accounts.services.OTPService.verify_otp", return_value=(True, "ok"))
    def test_verify_phone_otp_alias_unlocks_tier_zero_access(self, _verify_otp):
        response = self.client.post(
            "/api/v1/auth/verify-phone-otp/",
            {
                "phone": self.user.phone,
                "identifier": self.user.phone,
                "code": "123456",
                "purpose": "verify_phone",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.phone_verified)
        self.assertTrue(self.user.otp_verified)
        self.assertEqual(self.user.tier_access, "tier_0_view_only")
        self.assertEqual(self.user.kyc_status, "not_started")
        self.assertFalse(self.user.financial_access_enabled)


class KYCWorkflowAutomationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user_model = get_user_model()
        self.member = self.user_model.objects.create_user(
            phone="+254755900001",
            password="KycFlow123!",
            full_name="KYC Flow Member",
        )
        self.member.phone_verified = True
        self.member.save(update_fields=["phone_verified"])
        sync_user_access_state(self.member)
        self.client.force_authenticate(user=self.member)

    def _image(self, name="document.jpg"):
        return SimpleUploadedFile(name, b"fake-image-bytes", content_type="image/jpeg")

    def _provider_result(self, *, face_match_score=94, liveness_passed=True):
        return SmileProviderResult(
            provider_reference="smile-ref-001",
            provider_payload={"provider": "smile_identity"},
            provider_result={"provider": "smile_identity", "reference_id": "smile-ref-001"},
            document_authentic=True,
            face_matched=True,
            liveness_passed=liveness_passed,
            name_match=True,
            dob_match=True,
            id_number_valid=True,
            iprs_match=True,
            face_match_score=face_match_score,
        )

    def test_create_chama_kyc_can_start_without_membership(self):
        response = self.client.post(
            "/api/v1/kyc/start/",
            {"onboarding_path": "create_chama"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["data"]["record"]["onboarding_path"], "create_chama")
        self.assertIsNone(response.data["data"]["record"]["chama"])

    @patch("apps.accounts.kyc.notifications.notify_member_status")
    @patch("apps.accounts.kyc.providers.smile_identity.SmileIdentityKYCProvider.submit_verification")
    @patch("apps.accounts.services.KYCService.run_screening_checks")
    def test_score_based_auto_approve_unlocks_full_access(
        self,
        screening_mock,
        provider_mock,
        _notify_member_status,
    ):
        screening_mock.return_value = {
            "pep_match": False,
            "blacklist_match": False,
            "sanctions_match": False,
        }
        provider_mock.return_value = self._provider_result(face_match_score=98)

        kyc = KYCWorkflowService.start_session(user=self.member, onboarding_path="create_chama")
        kyc = KYCWorkflowService.update_profile(
            kyc,
            payload={
                "legal_name": "KYC Flow Member",
                "date_of_birth": "1994-01-01",
                "gender": "male",
                "nationality": "Kenyan",
                "document_type": "national_id",
                "id_number": "12345678",
            },
        )
        kyc, _errors, _metrics = KYCWorkflowService.attach_document(kyc, field_name="id_front_image", upload=self._image("front.jpg"))
        kyc, _errors, _metrics = KYCWorkflowService.attach_document(kyc, field_name="id_back_image", upload=self._image("back.jpg"))
        kyc.selfie_image = self._image("selfie.jpg")
        kyc.liveness_passed = True
        kyc.save(update_fields=["selfie_image", "liveness_passed", "updated_at"])

        KYCWorkflowService.process_submission(kyc)

        kyc.refresh_from_db()
        self.member.refresh_from_db()
        self.assertEqual(kyc.status, MemberKYCStatus.APPROVED)
        self.assertEqual(self.member.tier_access, "tier_2_full")
        self.assertEqual(self.member.kyc_status, "approved")
        self.assertTrue(self.member.financial_access_enabled)

    @patch("apps.accounts.kyc.notifications.notify_member_status")
    @patch("apps.accounts.kyc.notifications.notify_system_admins")
    @patch("apps.accounts.kyc.providers.smile_identity.SmileIdentityKYCProvider.submit_verification")
    @patch("apps.accounts.services.KYCService.run_screening_checks")
    def test_sanctions_match_freezes_account(
        self,
        screening_mock,
        provider_mock,
        _notify_system_admins,
        _notify_member_status,
    ):
        screening_mock.return_value = {
            "pep_match": False,
            "blacklist_match": False,
            "sanctions_match": True,
        }
        provider_mock.return_value = self._provider_result()

        kyc = MemberKYC.objects.create(
            user=self.member,
            id_number="99887766",
            document_type="national_id",
            legal_name="KYC Flow Member",
            status=MemberKYCStatus.QUEUED,
            quality_front_passed=True,
            quality_back_passed=True,
            liveness_passed=True,
            id_front_image=self._image("front.jpg"),
            id_back_image=self._image("back.jpg"),
            selfie_image=self._image("selfie.jpg"),
        )

        KYCWorkflowService.process_submission(kyc)

        kyc.refresh_from_db()
        self.member.refresh_from_db()
        self.assertEqual(kyc.status, MemberKYCStatus.FROZEN)
        self.assertTrue(kyc.account_frozen_for_compliance)
        self.assertTrue(self.member.account_frozen)
        self.assertEqual(self.member.tier_access, "restricted")
        self.assertEqual(self.member.kyc_status, "frozen")

    @patch("apps.accounts.kyc.services.KYCWorkflowService.sanctions_rescreen_only")
    def test_daily_sanctions_rescreen_runs_for_approved_records(self, rescreen_mock):
        MemberKYC.objects.create(
            user=self.member,
            id_number="99887767",
            status=MemberKYCStatus.APPROVED,
        )
        result = daily_sanctions_rescreen()
        self.assertEqual(result["screened"], 1)
        self.assertEqual(rescreen_mock.call_count, 1)


class KYCAutomationTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.reviewer = self.user_model.objects.create_user(
            phone="+254755100002",
            password="ReviewerPass123!",
            full_name="Reviewer User",
        )
        self.system_admin = self.user_model.objects.create_user(
            phone="+254755100003",
            password="SystemAdminPass123!",
            full_name="System Admin",
            is_staff=True,
        )
        self.member = self.user_model.objects.create_user(
            phone="+254755100004",
            password="MemberPass123!",
            full_name="KYC Member",
        )
        self.chama = Chama.objects.create(
            name="KYC Automation Chama",
            created_by=self.reviewer,
            updated_by=self.reviewer,
        )
        self.membership = Membership.objects.create(
            user=self.member,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            can_request_loan=False,
            can_withdraw_savings=False,
            can_vote=False,
            restriction_reason="Awaiting KYC",
            approved_by=self.reviewer,
            approved_at=timezone.now(),
            created_by=self.reviewer,
            updated_by=self.reviewer,
        )
        self.kyc = MemberKYC.objects.create(
            user=self.member,
            chama=self.chama,
            id_number="12345678",
            status=MemberKYCStatus.PENDING,
        )

    @patch("apps.notifications.services.NotificationService.send_notification")
    def test_kyc_approval_unlocks_member_access(self, send_notification_mock):
        success, message = KYCService.approve_kyc(
            kyc_id=str(self.kyc.id),
            reviewer=self.system_admin,
            review_note="Verified",
        )

        self.assertTrue(success)
        self.assertEqual(message, "KYC approved successfully")
        self.member.refresh_from_db()
        self.membership.refresh_from_db()
        self.assertTrue(self.member.phone_verified)
        self.assertTrue(self.membership.can_request_loan)
        self.assertTrue(self.membership.can_withdraw_savings)
        self.assertTrue(self.membership.can_vote)
        self.assertEqual(send_notification_mock.call_count, 1)

    @patch("apps.notifications.services.NotificationService.send_notification")
    def test_kyc_third_rejection_escalates_to_system_admin(self, send_notification_mock):
        self.kyc.rejection_attempts = 2
        self.kyc.save(update_fields=["rejection_attempts", "updated_at"])

        success, message = KYCService.reject_kyc(
            kyc_id=str(self.kyc.id),
            reviewer=self.system_admin,
            review_note="Face mismatch",
        )

        self.assertTrue(success)
        self.assertEqual(message, "KYC rejected")
        self.kyc.refresh_from_db()
        self.assertEqual(self.kyc.rejection_attempts, 3)
        self.assertIsNotNone(self.kyc.escalated_to_system_admin_at)
        recipients = {
            str(call.kwargs["user"].id)
            for call in send_notification_mock.call_args_list
        }
        self.assertIn(str(self.member.id), recipients)
        self.assertIn(str(self.system_admin.id), recipients)


class KYCWebhookAutomationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user_model = get_user_model()
        self.admin = self.user_model.objects.create_user(
            phone="+254755100010",
            password="AdminPass123!",
            full_name="Webhook Admin",
        )
        self.member = self.user_model.objects.create_user(
            phone="+254755100011",
            password="MemberPass123!",
            full_name="Webhook Member",
        )
        self.chama = Chama.objects.create(
            name="Webhook Chama",
            created_by=self.admin,
            updated_by=self.admin,
        )
        self.membership = Membership.objects.create(
            user=self.member,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            can_request_loan=False,
            can_withdraw_savings=False,
            can_vote=False,
            restriction_reason="Awaiting KYC",
            approved_by=self.admin,
            approved_at=timezone.now(),
            created_by=self.admin,
            updated_by=self.admin,
        )
        self.kyc = MemberKYC.objects.create(
            user=self.member,
            chama=self.chama,
            id_number="87654321",
            status=MemberKYCStatus.PENDING,
            auto_verification_reference="kyc-ref-001",
        )

    @patch("apps.notifications.services.NotificationService.send_notification")
    def test_kyc_webhook_approval_unlocks_member_access(self, send_notification_mock):
        response = self.client.post(
            "/api/v1/auth/kyc/webhook?provider=smile",
            {
                "reference_id": "kyc-ref-001",
                "status": "approved",
                "message": "Verified",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.kyc.refresh_from_db()
        self.membership.refresh_from_db()
        self.member.refresh_from_db()
        self.assertEqual(self.kyc.status, MemberKYCStatus.APPROVED)
        self.assertEqual(self.kyc.auto_verification_provider, "smile")
        self.assertIsNotNone(self.kyc.auto_verified_at)
        self.assertTrue(self.membership.can_request_loan)
        self.assertTrue(self.membership.can_withdraw_savings)
        self.assertTrue(self.membership.can_vote)
        self.assertTrue(self.member.phone_verified)
        self.assertGreaterEqual(send_notification_mock.call_count, 1)

    @patch("apps.notifications.services.NotificationService.send_notification")
    def test_kyc_webhook_rejection_tracks_attempts_and_reason(self, send_notification_mock):
        response = self.client.post(
            "/api/v1/auth/kyc/webhook?provider=onfido",
            {
                "reference_id": "kyc-ref-001",
                "decision": "failed",
                "reason": "Face mismatch",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.kyc.refresh_from_db()
        self.assertEqual(self.kyc.status, MemberKYCStatus.REJECTED)
        self.assertEqual(self.kyc.auto_verification_provider, "onfido")
        self.assertEqual(self.kyc.last_rejection_reason, "Face mismatch")
        self.assertEqual(self.kyc.rejection_attempts, 1)
        self.assertGreaterEqual(send_notification_mock.call_count, 1)


class KYCMonitoringTasksTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(
            phone="+254755200001",
            password="password123",
            full_name="Monitoring Admin",
        )
        self.member = user_model.objects.create_user(
            phone="+254755200002",
            password="password123",
            full_name="Monitoring Member",
        )
        self.chama = Chama.objects.create(
            name="KYC Monitoring Chama",
            created_by=self.admin,
            updated_by=self.admin,
        )
        Membership.objects.create(
            user=self.member,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            approved_by=self.admin,
            approved_at=timezone.now(),
            created_by=self.admin,
            updated_by=self.admin,
        )
        self.kyc = MemberKYC.objects.create(
            user=self.member,
            chama=self.chama,
            id_number="12345678",
            status=MemberKYCStatus.APPROVED,
            review_note="Approved",
            id_expiry_date=timezone.localdate() + timedelta(days=10),
            reviewed_at=timezone.now() - timedelta(days=340),
        )

    @patch("apps.accounts.tasks.KYCService.run_screening_checks")
    @patch("apps.notifications.services.NotificationService.send_notification")
    def test_daily_sanctions_screening_flags_compliance(self, send_notification_mock, screening_mock):
        screening_mock.return_value = {
            "pep_match": False,
            "sanctions_match": True,
            "blacklist_match": False,
        }
        result = kyc_daily_sanctions_screening()
        self.assertEqual(result["flagged"], 1)
        self.kyc.refresh_from_db()
        self.assertTrue(self.kyc.account_frozen_for_compliance)
        self.assertTrue(self.kyc.requires_reverification)
        self.assertEqual(self.kyc.status, MemberKYCStatus.REJECTED)
        self.assertGreaterEqual(send_notification_mock.call_count, 1)

    @patch("apps.notifications.services.NotificationService.send_notification")
    def test_renewal_task_marks_reverification_for_expiry_and_annual_due(self, send_notification_mock):
        result = kyc_renewal_and_expiry_reminders()
        self.assertGreaterEqual(result["reminded"], 1)
        self.kyc.refresh_from_db()
        self.assertTrue(self.kyc.requires_reverification)
        self.assertTrue(self.kyc.reverification_reason)
        self.assertGreaterEqual(send_notification_mock.call_count, 1)
