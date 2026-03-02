from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.accounts.models import ReferralReward
from apps.accounts.views import ReferralLeaderboardView, ReferralSummaryView
from apps.chama.models import Chama


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
