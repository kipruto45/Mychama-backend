from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403,F401

# Firebase Cloud Messaging Configuration
FCM_PROJECT_ID = env("FCM_PROJECT_ID", default="")
FCM_API_KEY = env("FCM_API_KEY", default="")
PUSH_NOTIFICATION_ENABLED = env.bool("PUSH_NOTIFICATION_ENABLED", default=True)

# Firebase Service Account - try file first, then JSON string
_firebase_service_file = env("FIREBASE_CREDENTIALS_FILE", default="")
if _firebase_service_file and os.path.isfile(_firebase_service_file):
    with open(_firebase_service_file) as f:
        import json
        FIREBASE_SERVICE_ACCOUNT = json.load(f)
else:
    _firebase_service_json = env("FIREBASE_SERVICE_ACCOUNT", default="")
    if _firebase_service_json:
        import json
        try:
            FIREBASE_SERVICE_ACCOUNT = json.loads(_firebase_service_json)
        except json.JSONDecodeError:
            FIREBASE_SERVICE_ACCOUNT = None
    else:
        FIREBASE_SERVICE_ACCOUNT = None

# Sentry Configuration
SENTRY_DSN = env("SENTRY_DSN", default="")
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.redis import RedisIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[
            DjangoIntegration(
                transaction_style="url",
                middleware_spans=True,
                signals_spans=True,
            ),
            CeleryIntegration(
                monitor_beat_tasks=True,
                propagate_traces=True,
            ),
            RedisIntegration(),
        ],
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.1),
        profiles_sample_rate=env.float("SENTRY_PROFILES_SAMPLE_RATE", default=0.1),
        send_default_pii=True,
        environment=env("SENTRY_ENVIRONMENT", default="production"),
        release=env("SENTRY_RELEASE", default=""),
    )

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
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
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

    if PRIVATE_MEDIA_STORAGE_REQUIRED:
        if not SUPABASE_USE_STORAGE:
            raise ImproperlyConfigured(
                "PRIVATE_MEDIA_STORAGE_REQUIRED=True requires Supabase Storage to be configured "
                "(set SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_STORAGE_BUCKET)."
            )
        if SUPABASE_STORAGE_PUBLIC:
            raise ImproperlyConfigured(
                "SUPABASE_STORAGE_PUBLIC must be False when PRIVATE_MEDIA_STORAGE_REQUIRED=True."
            )

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
