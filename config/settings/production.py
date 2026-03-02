from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403,F401

DEBUG = False

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
PAYMENTS_CALLBACK_REQUIRE_IP_ALLOWLIST = env.bool(
    "PAYMENTS_CALLBACK_REQUIRE_IP_ALLOWLIST", default=True
)
PAYMENTS_CALLBACK_REQUIRE_SIGNATURE = env.bool(
    "PAYMENTS_CALLBACK_REQUIRE_SIGNATURE", default=True
)
MPESA_USE_STUB = env.bool("MPESA_USE_STUB", default=False)

STATICFILES_STORAGE = "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
]


def _assert_production_safety():
    insecure_secret = (
        not SECRET_KEY
        or SECRET_KEY == "django-insecure-change-me"
        or SECRET_KEY.startswith("django-insecure-")
    )
    if insecure_secret:
        raise ImproperlyConfigured(
            "Production SECRET_KEY is insecure. Set a strong SECRET_KEY via environment."
        )

    if not ALLOWED_HOSTS:
        raise ImproperlyConfigured("ALLOWED_HOSTS must be set in production.")

    if CORS_ALLOW_ALL_ORIGINS:
        raise ImproperlyConfigured(
            "CORS_ALLOW_ALL_ORIGINS must be False in production."
        )

    if not SECURE_SSL_REDIRECT:
        raise ImproperlyConfigured("SECURE_SSL_REDIRECT must be enabled in production.")

    if not PAYMENTS_CALLBACK_REQUIRE_IP_ALLOWLIST:
        raise ImproperlyConfigured(
            "PAYMENTS_CALLBACK_REQUIRE_IP_ALLOWLIST must be True in production."
        )
    if not MPESA_CALLBACK_IP_ALLOWLIST:
        raise ImproperlyConfigured(
            "MPESA_CALLBACK_IP_ALLOWLIST must be configured in production."
        )

    if not PAYMENTS_CALLBACK_REQUIRE_SIGNATURE:
        raise ImproperlyConfigured(
            "PAYMENTS_CALLBACK_REQUIRE_SIGNATURE must be True in production."
        )
    if not MPESA_CALLBACK_SECRET:
        raise ImproperlyConfigured(
            "MPESA_CALLBACK_SECRET must be configured in production."
        )

    if OTP_ALLOW_MOCK_DELIVERY:
        raise ImproperlyConfigured(
            "OTP_ALLOW_MOCK_DELIVERY must be False in production."
        )

    # Disable development-only OTP features in production
    if getattr(settings, 'PRINT_OTP_IN_CONSOLE', False):
        raise ImproperlyConfigured(
            "PRINT_OTP_IN_CONSOLE must be False in production."
        )
    if getattr(settings, 'ENABLE_DEV_OTP_ENDPOINT', False):
        raise ImproperlyConfigured(
            "ENABLE_DEV_OTP_ENDPOINT must be False in production."
        )
    if getattr(settings, 'ENABLE_DEV_ENDPOINTS', False):
        raise ImproperlyConfigured(
            "ENABLE_DEV_ENDPOINTS must be False in production."
        )
    if not getattr(settings, 'FRONTEND_URL', None):
        raise ImproperlyConfigured(
            "FRONTEND_URL must be configured in production."
        )

    if SMS_PROVIDER == "console":
        raise ImproperlyConfigured(
            "SMS_PROVIDER must use a real provider in production."
        )
    if SMS_PROVIDER == "africastalking" and (
        not AFRICAS_TALKING_USERNAME or not AFRICAS_TALKING_API_KEY
    ):
        raise ImproperlyConfigured(
            "Africa's Talking credentials must be configured in production."
        )
    if SMS_PROVIDER == "africastalking" and not OTP_SMS_CALLBACK_TOKEN:
        raise ImproperlyConfigured(
            "OTP_SMS_CALLBACK_TOKEN must be configured for SMS delivery callbacks."
        )

    if EMAIL_PROVIDER == "sendgrid" and not SENDGRID_API_KEY:
        raise ImproperlyConfigured(
            "SENDGRID_API_KEY must be configured when EMAIL_PROVIDER=sendgrid."
        )
    if EMAIL_PROVIDER == "sendgrid" and not OTP_EMAIL_CALLBACK_TOKEN:
        raise ImproperlyConfigured(
            "OTP_EMAIL_CALLBACK_TOKEN must be configured for email delivery callbacks."
        )
    if (
        EMAIL_PROVIDER == "django"
        and EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend"
    ):
        raise ImproperlyConfigured(
            "Console email backend must not be used in production."
        )


_assert_production_safety()
