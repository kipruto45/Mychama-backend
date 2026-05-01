from django.urls import path
from django.http import JsonResponse
from django.conf import settings

from apps.accounts.views import (
    ChangePasswordView,
    DevOTPView,
    LoginView,
    LogoutView,
    MemberCardView,
    MemberKYCReviewView,
    MemberKYCReverificationTriggerView,
    MemberKYCView,
    MembershipOptionsView,
    MembershipStatusView,
    MeView,
    OTPRequestView,
    OTPVerifyView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    PublicOTPRequestView,
    PublicOTPVerifyView,
    ReferralLeaderboardView,
    ReferralSummaryView,
    RefreshView,
    RegisterView,
    SecurityCenterView,
    SwitchChamaView,
    UserPreferenceView,
)
from apps.accounts.webhooks import MemberKYCWebhookView

# Sentry test view
def sentry_debug(request):
    """Test endpoint to trigger an error for Sentry debugging."""
    if not settings.DEBUG:
        return JsonResponse({"error": "Not available in production"}, status=403)
    
    # Trigger a division by zero error
    division_by_zero = 1 / 0  # noqa: F841
    return JsonResponse({"message": "This should not be reached"})

app_name = "accounts"

urlpatterns = [
    path("sentry-debug/", sentry_debug, name="sentry-debug"),
    path("register", RegisterView.as_view(), name="register"),
    path("register", RegisterView.as_view(), name="user-register"),
    path("login", LoginView.as_view(), name="login"),
    path("login", LoginView.as_view(), name="user-login"),
    path("refresh", RefreshView.as_view(), name="refresh"),
    path("logout", LogoutView.as_view(), name="logout"),
    path("otp/send", PublicOTPRequestView.as_view(), name="otp-send"),
    path("otp/confirm", PublicOTPVerifyView.as_view(), name="otp-confirm"),
    path("verify-phone-otp/", PublicOTPVerifyView.as_view(), name="verify-phone-otp"),
    path("resend-otp/", PublicOTPRequestView.as_view(), name="resend-otp"),
    path("otp/request", OTPRequestView.as_view(), name="otp-request"),
    path("otp/verify", OTPVerifyView.as_view(), name="otp-verify"),
    # Dev-only OTP endpoint (must be behind secret token)
    path("dev/otp/latest", DevOTPView.as_view(), name="dev-otp-latest"),
    path("me", MeView.as_view(), name="me"),
    path("referrals", ReferralSummaryView.as_view(), name="referrals"),
    path("referrals/leaderboard", ReferralLeaderboardView.as_view(), name="referrals-leaderboard"),
    path("membership-status", MembershipStatusView.as_view(), name="membership-status"),
    path("chamas", MembershipOptionsView.as_view(), name="chama-memberships"),
    path("membership-options", MembershipOptionsView.as_view(), name="membership-options"),
    path("switch-chama", SwitchChamaView.as_view(), name="switch-chama"),
    path("preferences", UserPreferenceView.as_view(), name="preferences"),
    path("kyc", MemberKYCView.as_view(), name="kyc"),
    path("kyc/<uuid:id>/review", MemberKYCReviewView.as_view(), name="kyc-review"),
    path("kyc/<uuid:id>/trigger-reverification", MemberKYCReverificationTriggerView.as_view(), name="kyc-trigger-reverification"),
    path("kyc/webhook", MemberKYCWebhookView.as_view(), name="kyc-webhook"),
    path("member-card", MemberCardView.as_view(), name="member-card"),
    path("security-center", SecurityCenterView.as_view(), name="security-center"),
    path(
        "password-reset/request",
        PasswordResetRequestView.as_view(),
        name="password-reset-request",
    ),
    path(
        "password-reset/confirm",
        PasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),
    path("change-password", ChangePasswordView.as_view(), name="change-password"),
]
