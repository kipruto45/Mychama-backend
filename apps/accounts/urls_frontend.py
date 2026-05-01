from django.urls import path

from . import views_frontend
from .views_frontend import home_view

app_name = "auth"

urlpatterns = [
    # Auth Templates
    path("", home_view, name="home"),
    path("login/", views_frontend.login_view, name="login"),
    path("register/", views_frontend.register_view, name="register"),
    path("verify-phone-otp/", views_frontend.verify_phone_otp_view, name="verify_phone_otp"),
    path(
        "forgot-password/", views_frontend.forgot_password_view, name="forgot_password"
    ),
    path("reset-password/", views_frontend.reset_password_view, name="reset_password"),
    path(
        "change-password/", views_frontend.change_password_view, name="change_password"
    ),
    path("profile/", views_frontend.profile_view, name="profile"),
    path(
        "terms-of-service/",
        views_frontend.terms_of_service_view,
        name="terms_of_service",
    ),
    path("privacy-policy/", views_frontend.privacy_policy_view, name="privacy_policy"),
    path("switch-chama/", views_frontend.switch_chama_view, name="switch_chama"),
    path(
        "security-center/", views_frontend.security_center_view, name="security_center"
    ),
    path("logout/", views_frontend.logout_view, name="logout"),
]
