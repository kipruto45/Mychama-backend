from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import uuid
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import resolve
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

import api.urls as api_urls
from apps.accounts.models import MemberKYC, MemberKYCStatus, ReferralReward
from apps.billing.models import BillingCredit
from apps.billing.services import get_latest_subscription
from apps.chama.models import (
    Chama,
    ChamaContributionSetting,
    ChamaSettings,
    Invite,
    InviteLink,
    LoanPolicy,
    Membership,
    MembershipRequest,
    MembershipRequestSource,
    MembershipRole,
    MemberStatus,
)
from apps.payouts.models import PayoutRotation
from apps.chama.views import (
    ChamaListCreateView,
    InviteAcceptAliasView,
    InviteAcceptView,
    InviteCodeAcceptView,
    InviteCodeValidateView,
    InviteJoinView,
    InviteLinkListCreateView,
    InviteLookupAliasView,
    JoinCodeEnableDisableView,
    JoinCodeJoinAliasView,
    JoinCodeJoinView,
    JoinCodeValidateAliasView,
    JoinCodeValidateView,
    MembershipRequestApproveView,
    MembershipRequestListView,
    MembershipRequestRejectView,
    MembershipListView,
    RequestJoinView,
)
from apps.chama.services import ChamaOnboardingService
from apps.chama.wizard_views import complete_wizard, group_setup


class ChamaModelTests(TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def test_new_chamas_get_unique_join_codes(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            phone="+254700000111",
            password="testpass123",
            full_name="Test User",
        )

        first = Chama.objects.create(
            name="Join Code Test Alpha",
            created_by=user,
            updated_by=user,
        )
        second = Chama.objects.create(
            name="Join Code Test Beta",
            created_by=user,
            updated_by=user,
        )

        self.assertTrue(first.join_code)
        self.assertTrue(second.join_code)
        self.assertNotEqual(first.join_code, second.join_code)
        self.assertIsNotNone(first.join_code_expires_at)
        self.assertIsNotNone(second.join_code_expires_at)

    def test_new_users_get_referral_codes(self):
        user_model = get_user_model()
        first = user_model.objects.create_user(
            phone="+254700000112",
            password="testpass123",
            full_name="Referral One",
        )
        second = user_model.objects.create_user(
            phone="+254700000113",
            password="testpass123",
            full_name="Referral Two",
        )

        self.assertTrue(first.referral_code)
        self.assertTrue(second.referral_code)
        self.assertNotEqual(first.referral_code, second.referral_code)

    def test_group_setup_accepts_valid_referral_code(self):
        user_model = get_user_model()
        referrer = user_model.objects.create_user(
            phone="+254700000114",
            password="testpass123",
            full_name="Referrer User",
        )
        creator = user_model.objects.create_user(
            phone="+254700000115",
            password="testpass123",
            full_name="Creator User",
        )

        request = APIRequestFactory().post(
            "/api/v1/chamas/wizard/group-setup",
            {
                "organization_name": "Referral Chama",
                "member_count": 12,
                "group_type": "savings",
                "user_role": "CHAMA_ADMIN",
                "country": "Kenya",
                "currency": "KES",
                "referral_enabled": True,
                "referral_code": referrer.referral_code,
            },
            format="json",
        )
        force_authenticate(request, user=creator)

        response = group_setup(request)

        self.assertEqual(response.status_code, 200)
        chama = Chama.objects.get(id=response.data["chama_id"])
        self.assertEqual(chama.referred_by_id, referrer.id)
        self.assertEqual(chama.referral_code_used, referrer.referral_code)
        self.assertIsNotNone(chama.referral_applied_at)

    def test_group_setup_rejects_invalid_referral_code(self):
        user_model = get_user_model()
        creator = user_model.objects.create_user(
            phone="+254700000116",
            password="testpass123",
            full_name="Creator User",
        )

        request = APIRequestFactory().post(
            "/api/v1/chamas/wizard/group-setup",
            {
                "organization_name": "Invalid Referral Chama",
                "member_count": 12,
                "group_type": "savings",
                "user_role": "CHAMA_ADMIN",
                "country": "Kenya",
                "currency": "KES",
                "referral_enabled": True,
                "referral_code": "BADCODE123",
            },
            format="json",
        )
        force_authenticate(request, user=creator)

        response = group_setup(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["detail"],
            "The referral code you entered is invalid.",
        )

    @override_settings(REFERRAL_REWARD_EXTENSION_DAYS=5)
    def test_complete_wizard_applies_referral_reward_to_referrer_subscription(self):
        user_model = get_user_model()
        referrer = user_model.objects.create_user(
            phone="+254700000121",
            password="testpass123",
            full_name="Reward Referrer",
        )
        creator = user_model.objects.create_user(
            phone="+254700000122",
            password="testpass123",
            full_name="Reward Creator",
        )

        referrer_chama = Chama.objects.create(
            name="Referrer Home Chama",
            created_by=referrer,
            updated_by=referrer,
        )
        Membership.objects.create(
            user=referrer,
            chama=referrer_chama,
            role=MembershipRole.CHAMA_ADMIN,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            approved_by=referrer,
            approved_at=referrer_chama.created_at,
            created_by=referrer,
            updated_by=referrer,
        )
        referrer_subscription = get_latest_subscription(referrer_chama)
        original_period_end = referrer_subscription.current_period_end

        group_setup_request = APIRequestFactory().post(
            "/api/v1/chamas/wizard/group-setup",
            {
                "organization_name": "Rewarded Referral Chama",
                "member_count": 8,
                "group_type": "savings",
                "user_role": "CHAMA_ADMIN",
                "country": "Kenya",
                "currency": "KES",
                "referral_enabled": True,
                "referral_code": referrer.referral_code,
            },
            format="json",
        )
        force_authenticate(group_setup_request, user=creator)
        response = group_setup(group_setup_request)
        self.assertEqual(response.status_code, 200)

        complete_request = APIRequestFactory().post(
            "/api/v1/chamas/wizard/complete",
            {},
            format="json",
        )
        force_authenticate(complete_request, user=creator)
        complete_response = complete_wizard(complete_request)

        self.assertEqual(complete_response.status_code, 200)

        reward = ReferralReward.objects.get(referred_chama_id=response.data["chama_id"])
        self.assertEqual(reward.status, ReferralReward.APPLIED)
        self.assertEqual(reward.reward_value, 5)
        self.assertEqual(reward.rewarded_chama_id, referrer_chama.id)

        referrer_subscription.refresh_from_db()
        self.assertEqual(
            referrer_subscription.current_period_end,
            original_period_end + timedelta(days=5),
        )

    @override_settings(
        REFERRAL_REWARD_TYPE=ReferralReward.BILLING_CREDIT,
        REFERRAL_REWARD_CREDIT_AMOUNT=1500,
    )
    def test_complete_wizard_issues_referral_billing_credit(self):
        user_model = get_user_model()
        referrer = user_model.objects.create_user(
            phone="+254700000123",
            password="testpass123",
            full_name="Credit Referrer",
        )
        creator = user_model.objects.create_user(
            phone="+254700000124",
            password="testpass123",
            full_name="Credit Creator",
        )

        referrer_chama = Chama.objects.create(
            name="Credit Referrer Chama",
            created_by=referrer,
            updated_by=referrer,
        )
        Membership.objects.create(
            user=referrer,
            chama=referrer_chama,
            role=MembershipRole.CHAMA_ADMIN,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            approved_by=referrer,
            approved_at=referrer_chama.created_at,
            created_by=referrer,
            updated_by=referrer,
        )

        group_setup_request = APIRequestFactory().post(
            "/api/v1/chamas/wizard/group-setup",
            {
                "organization_name": "Credit Rewarded Chama",
                "member_count": 8,
                "group_type": "savings",
                "user_role": "CHAMA_ADMIN",
                "country": "Kenya",
                "currency": "KES",
                "referral_enabled": True,
                "referral_code": referrer.referral_code,
            },
            format="json",
        )
        force_authenticate(group_setup_request, user=creator)
        response = group_setup(group_setup_request)
        self.assertEqual(response.status_code, 200)

        complete_request = APIRequestFactory().post(
            "/api/v1/chamas/wizard/complete",
            {},
            format="json",
        )
        force_authenticate(complete_request, user=creator)
        complete_response = complete_wizard(complete_request)
        self.assertEqual(complete_response.status_code, 200)

        reward = ReferralReward.objects.get(referred_chama_id=response.data["chama_id"])
        self.assertEqual(reward.status, ReferralReward.APPLIED)
        self.assertEqual(reward.reward_type, ReferralReward.BILLING_CREDIT)
        self.assertEqual(reward.reward_value, 1500)

        credit = BillingCredit.objects.get(chama=referrer_chama)
        self.assertEqual(credit.total_amount, 1500)
        self.assertEqual(credit.remaining_amount, 1500)

    def test_join_code_can_be_disabled_without_regenerating(self):
        user_model = get_user_model()
        admin = user_model.objects.create_user(
            phone="+254700000125",
            password="testpass123",
            full_name="Join Code Admin",
        )
        chama = Chama.objects.create(
            name="Disable Join Code Chama",
            created_by=admin,
            updated_by=admin,
        )
        Membership.objects.create(
            user=admin,
            chama=chama,
            role=MembershipRole.CHAMA_ADMIN,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            approved_by=admin,
            approved_at=chama.created_at,
            created_by=admin,
            updated_by=admin,
        )
        original_code = chama.join_code

        request = APIRequestFactory().delete(f"/api/v1/chamas/{chama.id}/join-code/")
        force_authenticate(request, user=admin)
        response = JoinCodeEnableDisableView.as_view()(request, id=chama.id)

        self.assertEqual(response.status_code, 200)
        chama.refresh_from_db()
        self.assertFalse(chama.join_enabled)
        self.assertEqual(chama.join_code, original_code)
        self.assertIsNone(chama.join_code_expires_at)

        validate_request = APIRequestFactory().get(
            f"/api/v1/chamas/join-codes/validate/{original_code}/"
        )
        validate_response = JoinCodeValidateView.as_view()(
            validate_request, code=original_code
        )
        self.assertEqual(validate_response.status_code, 404)

    def test_join_code_join_rejects_when_member_limit_is_reached(self):
        user_model = get_user_model()
        admin = user_model.objects.create_user(
            phone="+254700000126",
            password="testpass123",
            full_name="Capacity Admin",
        )
        joiner = user_model.objects.create_user(
            phone="+254700000127",
            password="testpass123",
            full_name="Capacity Joiner",
            phone_verified=True,
        )
        chama = Chama.objects.create(
            name="Capacity Limit Chama",
            join_mode="auto_join",
            max_members=1,
            created_by=admin,
            updated_by=admin,
        )
        Membership.objects.create(
            user=admin,
            chama=chama,
            role=MembershipRole.CHAMA_ADMIN,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            approved_by=admin,
            approved_at=chama.created_at,
            created_by=admin,
            updated_by=admin,
        )

        request = APIRequestFactory().post(
            f"/api/v1/chamas/join-codes/{chama.join_code}/join/",
            {},
            format="json",
        )
        force_authenticate(request, user=joiner)
        response = JoinCodeJoinView.as_view()(request, code=chama.join_code)

        self.assertEqual(response.status_code, 402)
        self.assertIn("member limit", response.data["detail"])

    def test_invite_join_records_request_source_and_invite_link(self):
        user_model = get_user_model()
        admin = user_model.objects.create_user(
            phone="+254700000128",
            password="testpass123",
            full_name="Invite Admin",
        )
        joiner = user_model.objects.create_user(
            phone="+254700000129",
            password="testpass123",
            full_name="Invite Joiner",
            phone_verified=True,
        )
        chama = Chama.objects.create(
            name="Invite Source Chama",
            created_by=admin,
            updated_by=admin,
        )
        Membership.objects.create(
            user=admin,
            chama=chama,
            role=MembershipRole.CHAMA_ADMIN,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            approved_by=admin,
            approved_at=chama.created_at,
            created_by=admin,
            updated_by=admin,
        )
        invite_link = InviteLink.objects.create(
            chama=chama,
            created_by=admin,
            approval_required=True,
            max_uses=1,
            expires_at=chama.created_at + timedelta(days=2),
            preassigned_role=MembershipRole.MEMBER,
            updated_by=admin,
        )

        request = APIRequestFactory().post(
            f"/api/v1/chamas/invites/{invite_link.build_presented_token()}/join/",
            {},
            format="json",
        )
        force_authenticate(request, user=joiner)
        response = InviteJoinView.as_view()(
            request,
            token=invite_link.build_presented_token(),
        )

        self.assertEqual(response.status_code, 201)
        membership_request = MembershipRequest.objects.get(user=joiner, chama=chama)
        self.assertEqual(membership_request.requested_via, MembershipRequestSource.INVITE_LINK)
        self.assertEqual(membership_request.invite_link_id, invite_link.id)

    def test_secretary_invite_to_privileged_role_defaults_to_member(self):
        user_model = get_user_model()
        owner = user_model.objects.create_user(
            phone="+254700000130",
            password="testpass123",
            full_name="Chama Owner",
        )
        secretary = user_model.objects.create_user(
            phone="+254700000131",
            password="testpass123",
            full_name="Chama Secretary",
        )
        chama = Chama.objects.create(
            name="Secretary Invite Chama",
            created_by=owner,
            updated_by=owner,
        )
        Membership.objects.create(
            user=owner,
            chama=chama,
            role=MembershipRole.CHAMA_ADMIN,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            approved_by=owner,
            approved_at=chama.created_at,
            created_by=owner,
            updated_by=owner,
        )
        Membership.objects.create(
            user=secretary,
            chama=chama,
            role=MembershipRole.SECRETARY,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            approved_by=owner,
            approved_at=chama.created_at,
            created_by=owner,
            updated_by=owner,
        )

        request = APIRequestFactory().post(
            f"/api/v1/chamas/{chama.id}/invite-links/",
            {
                "preassigned_role": MembershipRole.TREASURER,
                "approval_required": True,
                "expires_in_days": 2,
                "max_uses": 1,
            },
            format="json",
        )
        force_authenticate(request, user=secretary)
        response = InviteLinkListCreateView.as_view()(request, id=chama.id)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["role"], MembershipRole.MEMBER)
        self.assertIn(".", response.data["token"])
        created_link = InviteLink.objects.get(id=response.data["id"])
        self.assertTrue(created_link.requires_signature)
        self.assertNotEqual(response.data["token"], created_link.token)

    def test_signed_invite_link_rejects_raw_public_token(self):
        user_model = get_user_model()
        admin = user_model.objects.create_user(
            phone="+254700000132",
            password="testpass123",
            full_name="Signed Invite Admin",
        )
        chama = Chama.objects.create(
            name="Signed Invite Chama",
            created_by=admin,
            updated_by=admin,
        )
        invite_link = InviteLink.objects.create(
            chama=chama,
            created_by=admin,
            approval_required=True,
            expires_at=chama.created_at + timedelta(days=2),
            updated_by=admin,
        )

        raw_request = APIRequestFactory().get(
            f"/api/v1/invites/lookup?token={invite_link.token}"
        )
        raw_response = InviteLookupAliasView.as_view()(raw_request)
        self.assertEqual(raw_response.status_code, 404)

        signed_request = APIRequestFactory().get(
            f"/api/v1/invites/lookup?token={invite_link.build_presented_token()}"
        )
        signed_response = InviteLookupAliasView.as_view()(signed_request)
        self.assertEqual(signed_response.status_code, 200)
        self.assertEqual(signed_response.data["token"], invite_link.build_presented_token())

    @override_settings(JOIN_CODE_VALIDATE_RATE_LIMIT=(1, 60))
    def test_join_code_validate_is_rate_limited(self):
        user_model = get_user_model()
        admin = user_model.objects.create_user(
            phone="+254700000133",
            password="testpass123",
            full_name="Rate Limit Admin",
        )
        chama = Chama.objects.create(
            name="Rate Limited Join Code Chama",
            created_by=admin,
            updated_by=admin,
        )

        first_request = APIRequestFactory().get(
            f"/api/v1/chamas/join-codes/validate/{chama.join_code}/"
        )
        first_response = JoinCodeValidateView.as_view()(first_request, code=chama.join_code)
        self.assertEqual(first_response.status_code, 200)

        second_request = APIRequestFactory().get(
            f"/api/v1/chamas/join-codes/validate/{chama.join_code}/"
        )
        second_response = JoinCodeValidateView.as_view()(second_request, code=chama.join_code)
        self.assertEqual(second_response.status_code, 429)


class ChamaCreationPermissionTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user_model = get_user_model()

    def test_member_cannot_create_chama(self):
        owner = self.user_model.objects.create_user(
            phone="+254711111111",
            password="testpass123",
            full_name="Owner User",
        )
        member = self.user_model.objects.create_user(
            phone="+254722222222",
            password="testpass123",
            full_name="Member User",
        )
        chama = Chama.objects.create(
            name="Existing Chama",
            created_by=owner,
            updated_by=owner,
        )
        Membership.objects.create(
            user=member,
            chama=chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            approved_by=owner,
            approved_at=chama.created_at,
            created_by=owner,
            updated_by=owner,
        )

        request = self.factory.post("/api/v1/chamas/", {}, format="json")
        force_authenticate(request, user=member)
        response = ChamaListCreateView.as_view()(request)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "MEMBER_CREATE_CHAMA_FORBIDDEN")

    def test_non_member_roles_are_not_blocked_by_member_guard(self):
        owner = self.user_model.objects.create_user(
            phone="+254733333333",
            password="testpass123",
            full_name="Owner User",
        )
        treasurer = self.user_model.objects.create_user(
            phone="+254744444444",
            password="testpass123",
            full_name="Treasurer User",
        )
        chama = Chama.objects.create(
            name="Treasurer Chama",
            created_by=owner,
            updated_by=owner,
        )
        Membership.objects.create(
            user=treasurer,
            chama=chama,
            role=MembershipRole.TREASURER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            approved_by=owner,
            approved_at=chama.created_at,
            created_by=owner,
            updated_by=owner,
        )

        request = self.factory.post("/api/v1/chamas/", {}, format="json")
        force_authenticate(request, user=treasurer)
        response = ChamaListCreateView.as_view()(request)

        self.assertNotEqual(response.status_code, 403)
        self.assertNotEqual(response.data.get("code"), "MEMBER_CREATE_CHAMA_FORBIDDEN")

    def test_join_code_alias_and_invite_accept_alias_work(self):
        user_model = get_user_model()
        admin = user_model.objects.create_user(
            phone="+254700000134",
            password="testpass123",
            full_name="Alias Admin",
        )
        joiner = user_model.objects.create_user(
            phone="+254700000135",
            password="testpass123",
            full_name="Alias Joiner",
            phone_verified=True,
        )
        chama = Chama.objects.create(
            name="Alias Chama",
            join_mode="auto_join",
            created_by=admin,
            updated_by=admin,
        )
        Membership.objects.create(
            user=admin,
            chama=chama,
            role=MembershipRole.CHAMA_ADMIN,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            approved_by=admin,
            approved_at=chama.created_at,
            created_by=admin,
            updated_by=admin,
        )
        invite_link = InviteLink.objects.create(
            chama=chama,
            created_by=admin,
            approval_required=False,
            max_uses=1,
            expires_at=chama.created_at + timedelta(days=2),
            updated_by=admin,
        )

        validate_request = APIRequestFactory().post(
            "/api/v1/chamas/join-code/validate",
            {"join_code": chama.join_code},
            format="json",
        )
        validate_response = JoinCodeValidateAliasView.as_view()(validate_request)
        self.assertEqual(validate_response.status_code, 200)

        join_request = APIRequestFactory().post(
            "/api/v1/chamas/join",
            {"join_code": chama.join_code},
            format="json",
        )
        force_authenticate(join_request, user=joiner)
        join_response = JoinCodeJoinAliasView.as_view()(join_request)
        self.assertEqual(join_response.status_code, 201)

        another_joiner = user_model.objects.create_user(
            phone="+254700000136",
            password="testpass123",
            full_name="Alias Invite Joiner",
            phone_verified=True,
        )
        accept_request = APIRequestFactory().post(
            "/api/v1/invites/accept",
            {"token": invite_link.build_presented_token()},
            format="json",
        )
        force_authenticate(accept_request, user=another_joiner)
        accept_response = InviteAcceptAliasView.as_view()(accept_request)
        self.assertEqual(accept_response.status_code, 201)

    def test_create_chama_requires_approved_kyc(self):
        creator = self.user_model.objects.create_user(
            phone="+254755555551",
            password="testpass123",
            full_name="No KYC Creator",
        )
        request = self.factory.post("/api/v1/chamas/", {}, format="json")
        force_authenticate(request, user=creator)

        response = ChamaListCreateView.as_view()(request)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "KYC_REQUIRED_FOR_CHAMA_CREATION")

    def test_create_chama_with_approved_kyc_is_not_blocked_by_kyc_guard(self):
        creator = self.user_model.objects.create_user(
            phone="+254755555552",
            password="testpass123",
            full_name="Approved KYC Creator",
        )
        anchor_chama = Chama.objects.create(
            name="Creator KYC Anchor Chama",
            created_by=creator,
            updated_by=creator,
        )
        MemberKYC.objects.create(
            user=creator,
            chama=anchor_chama,
            id_number="12345678",
            status=MemberKYCStatus.APPROVED,
        )

        request = self.factory.post("/api/v1/chamas/", {}, format="json")
        force_authenticate(request, user=creator)
        response = ChamaListCreateView.as_view()(request)

        self.assertNotEqual(response.status_code, 403)
        self.assertNotEqual(response.data.get("code"), "KYC_REQUIRED_FOR_CHAMA_CREATION")


class ChamaAutomationRegressionTests(TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.factory = APIRequestFactory()
        self.user_model = get_user_model()
        self.admin = self.user_model.objects.create_user(
            phone="+254700000201",
            password="testpass123",
            full_name="Automation Admin",
        )
        self.requester = self.user_model.objects.create_user(
            phone="+254700000202",
            password="testpass123",
            full_name="Automation Requester",
            phone_verified=True,
        )

    @patch("apps.notifications.services.NotificationService.send_notification")
    def test_chama_creation_generates_default_invite_link(self, send_notification_mock):
        chama = ChamaOnboardingService.create_chama_with_defaults(
            payload={
                "name": "Automation Chama",
                "description": "Lifecycle automation coverage",
                "county": "Nairobi",
                "subcounty": "Westlands",
                "privacy": "invite_only",
                "chama_type": "savings",
                "contribution_setup": {
                    "amount": Decimal("1000.00"),
                    "frequency": "monthly",
                    "due_day": 5,
                    "grace_period_days": 3,
                    "late_fine_amount": Decimal("50.00"),
                },
                "finance_settings": {
                    "currency": "KES",
                    "payment_methods": ["mpesa"],
                    "loans_enabled": True,
                    "fines_enabled": True,
                    "approval_rule": "maker_checker",
                },
                "meeting_settings": {
                    "meeting_frequency": "monthly",
                    "quorum_percentage": 60,
                    "voting_enabled": True,
                },
                "membership_rules": {
                    "invite_only": True,
                    "approval_required": True,
                    "max_members": 12,
                },
                "notification_defaults": {
                    "member_join_alerts": True,
                    "payment_received_alerts": True,
                    "meeting_reminders": True,
                    "loan_updates": True,
                },
                "payout_rules": {
                    "rotation_order": "member_join_order",
                    "trigger_mode": "manual",
                    "payout_method": "mpesa",
                },
                "loan_rules": {
                    "loans_enabled": True,
                    "max_loan_amount": Decimal("25000.00"),
                    "interest_rate": Decimal("9.50"),
                    "repayment_period_months": 8,
                    "approval_layers": 3,
                },
                "governance_rules": {
                    "minimum_members_to_start": 5,
                    "quorum_percentage": 65,
                    "missed_payment_penalty_amount": Decimal("150.00"),
                    "constitution_summary": "Founding constitution for automation test.",
                },
            },
            actor=self.admin,
        )

        membership = Membership.objects.get(user=self.admin, chama=chama)
        invite_link = InviteLink.objects.filter(
            chama=chama,
            created_by=self.admin,
            preassigned_role=MembershipRole.MEMBER,
            is_active=True,
        ).first()

        self.assertEqual(membership.role, MembershipRole.CHAMA_ADMIN)
        self.assertIsNotNone(invite_link)
        self.assertTrue(invite_link.build_presented_token())
        self.assertEqual(invite_link.max_uses, 11)
        self.assertGreater(invite_link.expires_at, timezone.now())
        self.assertEqual(chama.max_members, 12)
        self.assertTrue(PayoutRotation.objects.filter(chama=chama).exists())
        settings = ChamaSettings.objects.get(chama=chama)
        self.assertEqual(settings.voting_quorum_percent, 65)
        self.assertEqual(settings.late_penalty_amount, Decimal("150.00"))
        loan_policy = LoanPolicy.objects.get(chama=chama)
        self.assertEqual(loan_policy.max_member_loan_amount, Decimal("25000.00"))
        self.assertEqual(loan_policy.interest_rate, Decimal("9.50"))
        self.assertEqual(loan_policy.max_repayment_period, 8)
        self.assertTrue(loan_policy.require_committee_vote)
        self.assertGreaterEqual(send_notification_mock.call_count, 1)

    @patch("apps.notifications.services.NotificationService.send_notification")
    def test_request_join_notifies_reviewers_with_push_channel(self, send_notification_mock):
        chama = Chama.objects.create(
            name="Join Request Automation Chama",
            allow_public_join=True,
            require_approval=True,
            join_mode="approval_required",
            created_by=self.admin,
            updated_by=self.admin,
        )
        Membership.objects.create(
            user=self.admin,
            chama=chama,
            role=MembershipRole.CHAMA_ADMIN,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            approved_by=self.admin,
            approved_at=timezone.now(),
            created_by=self.admin,
            updated_by=self.admin,
        )

        request = self.factory.post(
            f"/api/v1/chamas/{chama.id}/request-join/",
            {"request_note": "Please let me in"},
            format="json",
        )
        force_authenticate(request, user=self.requester)
        response = RequestJoinView.as_view()(request, id=chama.id)

        self.assertEqual(response.status_code, 201)
        reviewer_call = next(
            call
            for call in send_notification_mock.call_args_list
            if call.kwargs.get("user") == self.admin
        )
        self.assertIn("push", reviewer_call.kwargs["channels"])

    @patch("apps.notifications.services.NotificationService.send_notification")
    def test_membership_request_approval_sends_welcome_summary_with_push(self, send_notification_mock):
        chama = Chama.objects.create(
            name="Approval Automation Chama",
            created_by=self.admin,
            updated_by=self.admin,
        )
        Membership.objects.create(
            user=self.admin,
            chama=chama,
            role=MembershipRole.CHAMA_ADMIN,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            approved_by=self.admin,
            approved_at=timezone.now(),
            created_by=self.admin,
            updated_by=self.admin,
        )
        ChamaContributionSetting.objects.create(
            chama=chama,
            contribution_amount=Decimal("1200.00"),
            contribution_frequency="monthly",
            due_day=10,
            grace_period_days=3,
            late_fine_amount=Decimal("50.00"),
            created_by=self.admin,
            updated_by=self.admin,
        )
        request_record = MembershipRequest.objects.create(
            user=self.requester,
            chama=chama,
            status="pending",
            requested_via=MembershipRequestSource.PUBLIC_JOIN,
            expires_at=timezone.now() + timedelta(days=7),
            created_by=self.requester,
            updated_by=self.requester,
        )

        request = self.factory.post(
            f"/api/v1/chamas/{chama.id}/membership-requests/{request_record.id}/approve/",
            {"note": "Approved"},
            format="json",
        )
        force_authenticate(request, user=self.admin)
        response = MembershipRequestApproveView.as_view()(
            request,
            id=chama.id,
            request_id=request_record.id,
        )

        self.assertEqual(response.status_code, 200)
        member_call = next(
            call
            for call in send_notification_mock.call_args_list
            if call.kwargs.get("user") == self.requester
        )
        self.assertIn("push", member_call.kwargs["channels"])
        self.assertIn("sms", member_call.kwargs["channels"])
        self.assertIn("Contribution summary", member_call.kwargs["message"])

    @patch("apps.notifications.services.NotificationService.send_notification")
    def test_membership_request_rejection_notifies_member_with_reason_and_push(self, send_notification_mock):
        chama = Chama.objects.create(
            name="Rejection Automation Chama",
            created_by=self.admin,
            updated_by=self.admin,
        )
        Membership.objects.create(
            user=self.admin,
            chama=chama,
            role=MembershipRole.CHAMA_ADMIN,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            approved_by=self.admin,
            approved_at=timezone.now(),
            created_by=self.admin,
            updated_by=self.admin,
        )
        request_record = MembershipRequest.objects.create(
            user=self.requester,
            chama=chama,
            status="pending",
            requested_via=MembershipRequestSource.PUBLIC_JOIN,
            expires_at=timezone.now() + timedelta(days=7),
            created_by=self.requester,
            updated_by=self.requester,
        )

        request = self.factory.post(
            f"/api/v1/chamas/{chama.id}/membership-requests/{request_record.id}/reject/",
            {"note": "Member limit reached"},
            format="json",
        )
        force_authenticate(request, user=self.admin)
        response = MembershipRequestRejectView.as_view()(
            request,
            id=chama.id,
            request_id=request_record.id,
        )

        self.assertEqual(response.status_code, 200)
        member_call = next(
            call
            for call in send_notification_mock.call_args_list
            if call.kwargs.get("user") == self.requester
        )
        self.assertIn("push", member_call.kwargs["channels"])
        self.assertIn("Member limit reached", member_call.kwargs["message"])


class InviteUrlResolutionTests(TestCase):
    def test_api_urls_resolves_invite_code_accept_before_token_accept(self):
        match = resolve("/v1/invites/code/accept/", urlconf=api_urls)
        self.assertIs(match.func.view_class, InviteCodeAcceptView)
        self.assertEqual(match.kwargs, {})

        token_match = resolve("/v1/invites/abc123/accept/", urlconf=api_urls)
        self.assertIs(token_match.func.view_class, InviteAcceptView)
        self.assertEqual(token_match.kwargs.get("token"), "abc123")

    def test_api_urls_resolves_invite_code_validate_before_token_routes(self):
        match = resolve("/v1/invites/code/validate/", urlconf=api_urls)
        self.assertIs(match.func.view_class, InviteCodeValidateView)
        self.assertEqual(match.kwargs, {})

    def test_root_urls_resolves_invite_code_accept(self):
        match = resolve("/api/v1/invites/code/accept/")
        self.assertIs(match.func.view_class, InviteCodeAcceptView)
        self.assertEqual(match.kwargs, {})


class ChamaUrlResolutionCompatibilityTests(TestCase):
    def test_members_endpoints_resolve_with_and_without_trailing_slash(self):
        chama_id = uuid.uuid4()

        for path in (
            f"/api/v1/chamas/{chama_id}/members/",
            f"/api/v1/chamas/{chama_id}/members",
            f"/api/v1/chamas/{chama_id}/memberships/",
            f"/api/v1/chamas/{chama_id}/memberships",
        ):
            match = resolve(path)
            self.assertIs(match.func.view_class, MembershipListView)
            self.assertEqual(match.kwargs.get("id"), chama_id)

    def test_request_join_resolves_with_and_without_trailing_slash(self):
        chama_id = uuid.uuid4()

        for path in (
            f"/api/v1/chamas/{chama_id}/request-join",
            f"/api/v1/chamas/{chama_id}/request-join/",
        ):
            match = resolve(path)
            self.assertIs(match.func.view_class, RequestJoinView)
            self.assertEqual(match.kwargs.get("id"), chama_id)

    def test_membership_request_list_resolves_with_and_without_trailing_slash(self):
        chama_id = uuid.uuid4()

        for path in (
            f"/api/v1/chamas/{chama_id}/membership-requests",
            f"/api/v1/chamas/{chama_id}/membership-requests/",
        ):
            match = resolve(path)
            self.assertIs(match.func.view_class, MembershipRequestListView)
            self.assertEqual(match.kwargs.get("id"), chama_id)

    def test_membership_request_actions_resolve_with_and_without_trailing_slash(self):
        chama_id = uuid.uuid4()
        request_id = uuid.uuid4()

        approve_paths = (
            f"/api/v1/chamas/{chama_id}/membership-requests/{request_id}/approve",
            f"/api/v1/chamas/{chama_id}/membership-requests/{request_id}/approve/",
        )
        reject_paths = (
            f"/api/v1/chamas/{chama_id}/membership-requests/{request_id}/reject",
            f"/api/v1/chamas/{chama_id}/membership-requests/{request_id}/reject/",
        )

        for path in approve_paths:
            match = resolve(path)
            self.assertIs(match.func.view_class, MembershipRequestApproveView)
            self.assertEqual(match.kwargs.get("id"), chama_id)
            self.assertEqual(match.kwargs.get("request_id"), request_id)

        for path in reject_paths:
            match = resolve(path)
            self.assertIs(match.func.view_class, MembershipRequestRejectView)
            self.assertEqual(match.kwargs.get("id"), chama_id)
            self.assertEqual(match.kwargs.get("request_id"), request_id)


class InviteCodeAcceptFlowTests(TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.factory = APIRequestFactory()
        self.user_model = get_user_model()

    @patch("apps.automations.domain_services.send_user_notification")
    def test_accept_invite_code_creates_membership(self, _send_user_notification_mock):
        inviter = self.user_model.objects.create_user(
            phone="+254700009001",
            password="testpass123",
            full_name="Inviter",
            phone_verified=True,
        )
        invitee = self.user_model.objects.create_user(
            phone="+254700009002",
            password="testpass123",
            full_name="Invitee",
            phone_verified=True,
        )

        anchor_chama = Chama.objects.create(
            name="Invitee KYC Anchor",
            created_by=invitee,
            updated_by=invitee,
        )
        MemberKYC.objects.create(
            user=invitee,
            chama=anchor_chama,
            id_number="12345678",
            status=MemberKYCStatus.APPROVED,
        )

        chama = Chama.objects.create(
            name="Invite Accept Flow Chama",
            created_by=inviter,
            updated_by=inviter,
        )
        invite = Invite.objects.create(
            chama=chama,
            invited_by=inviter,
            invitee_phone=invitee.phone,
            identifier=invitee.phone,
            expires_at=timezone.now() + timedelta(days=2),
            created_by=inviter,
            updated_by=inviter,
        )

        request = self.factory.post(
            "/api/v1/invites/code/accept/",
            {"code": invite.code},
            format="json",
        )
        force_authenticate(request, user=invitee)
        response = InviteCodeAcceptView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.get("code"), "INVITE_ACCEPTED")
        self.assertTrue(
            Membership.objects.filter(
                user=invitee,
                chama=chama,
                status=MemberStatus.ACTIVE,
                is_active=True,
                is_approved=True,
                exited_at__isnull=True,
            ).exists()
        )
