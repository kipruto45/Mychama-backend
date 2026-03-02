from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.accounts.models import LoginEvent, PasswordResetToken, User

pytestmark = pytest.mark.django_db


def _create_user(phone: str = "+254712345678", password: str = "SecurePass123!"):
    return User.objects.create_user(
        phone=phone,
        password=password,
        full_name="Test User",
        email="test@example.com",
    )


def test_cannot_login_with_wrong_password(client):
    _create_user()

    response = client.post(
        "/api/v1/auth/login",
        {"phone": "0712345678", "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"
    assert LoginEvent.objects.filter(success=False).count() == 1


def test_login_throttle_basic(client):
    cache.clear()
    _create_user(phone="+254712345679")

    for _ in range(5):
        response = client.post(
            "/api/v1/auth/login",
            {"phone": "0712345679", "password": "wrong-password"},
        )
        assert response.status_code == 401

    throttled_response = client.post(
        "/api/v1/auth/login",
        {"phone": "0712345679", "password": "wrong-password"},
    )

    assert throttled_response.status_code == 429


def test_login_lockout_applies_across_ips_for_same_identifier(client, settings):
    cache.clear()
    _create_user(phone="+254712345681")
    settings.LOGIN_LOCKOUT_FAILURE_LIMIT = 3
    settings.LOGIN_LOCKOUT_COOLDOWN_SECONDS = 600

    for ip_addr in ["10.0.0.1", "10.0.0.2", "10.0.0.3"]:
        response = client.post(
            "/api/v1/auth/login",
            {"phone": "0712345681", "password": "wrong-password"},
            REMOTE_ADDR=ip_addr,
        )
        assert response.status_code == 401

    throttled_response = client.post(
        "/api/v1/auth/login",
        {"phone": "0712345681", "password": "wrong-password"},
        REMOTE_ADDR="10.0.0.4",
    )

    assert throttled_response.status_code == 429
    assert throttled_response.json()["code"] == "account_locked"


def test_login_lockout_after_repeated_failures(client, settings):
    cache.clear()
    _create_user(phone="+254712345682")
    settings.LOGIN_LOCKOUT_FAILURE_LIMIT = 3
    settings.LOGIN_LOCKOUT_COOLDOWN_SECONDS = 600
    throttle_rates = {
        **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
        "login": "100/minute",
        "login_identifier": "100/minute",
    }
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": throttle_rates,
    }

    for _ in range(3):
        response = client.post(
            "/api/v1/auth/login",
            {"phone": "0712345682", "password": "wrong-password"},
        )
        assert response.status_code == 401

    locked_response = client.post(
        "/api/v1/auth/login",
        {"phone": "0712345682", "password": "wrong-password"},
    )

    assert locked_response.status_code == 429
    payload = locked_response.json()
    assert payload["code"] == "account_locked"


def test_password_reset_token_expires_properly(client):
    user = _create_user(phone="+254712345680")

    raw_token = "expired-reset-token"
    token = PasswordResetToken.objects.create(
        user=user,
        token_hash=PasswordResetToken.hash_token(raw_token),
        expires_at=timezone.now() - timedelta(minutes=1),
    )

    response = client.post(
        "/api/v1/auth/password-reset/confirm",
        {"token": raw_token, "new_password": "NewSecurePass123!"},
    )

    assert token.is_expired is True
    assert response.status_code == 400

    user.refresh_from_db()
    assert user.check_password("SecurePass123!") is True
