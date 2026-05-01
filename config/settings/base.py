"""
Django base settings for Digital Chama project.
"""

import importlib.util
import os
from datetime import timedelta
from pathlib import Path
import base64

import environ

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Initialize environ
env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
)

# Read .env file
environ.Env.read_env(os.path.join(BASE_DIR, ".env"), overwrite=False)

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env("SECRET_KEY", default="django-insecure-dev-key-change-in-production")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env.bool("DEBUG", default=False)

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "0.0.0.0"])

# Application definition
DJANGO_APPS = [
    "jazzmin",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    "corsheaders",
    "django_filters",
    "drf_spectacular",
]
if importlib.util.find_spec("rest_framework_simplejwt.token_blacklist") is not None:
    THIRD_PARTY_APPS.append("rest_framework_simplejwt.token_blacklist")
if importlib.util.find_spec("django_celery_beat") is not None:
    THIRD_PARTY_APPS.append("django_celery_beat")

LOCAL_APPS = [
    "apps.accounts",
    "apps.app",
    "apps.chama",
    "apps.finance",
    "apps.fines",
    "apps.payments",
    "apps.payouts",
    "apps.meetings",
    "apps.notifications",
    "apps.governance",
    "apps.messaging",
    "apps.documents",
    "apps.reports",
    "apps.ai",
    "apps.search",
    "apps.settings",
    "apps.audit",
    "apps.admin_management",
    "apps.analytics",
    "apps.reconciliation",
    "apps.policy",
    "apps.support",
    "apps.deeplinks",
    "core",
    "apps.security",
    "apps.billing",
    "apps.exports",
    "apps.automations",
    "apps.issues",
    "apps.investments",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

JAZZMIN_SETTINGS = {
    "site_title": "MyChama Admin",
    "site_header": "MyChama Admin Console",
    "site_brand": "MyChama Admin Console",
    "site_logo": "branding/logo.png",
    "login_logo": "branding/logo.png",
    "login_logo_dark": "branding/logo.png",
    "site_icon": "branding/favicon.png",
    "site_logo_classes": "img-circle elevation-3",
    "login_logo_classes": "img-circle elevation-3",
    "welcome_sign": "MyChama API and Operations Console",
    "copyright": "MyChama",
    "default_theme_mode": "light",
    "custom_css": "admin/css/mychama_admin.css",
    "search_model": "accounts.User",
    "user_avatar": "avatar",
    "show_sidebar": True,
    "navigation_expanded": True,
    "show_ui_builder": False,
    "related_modal_active": True,
    "changeform_format": "horizontal_tabs",
    "topmenu_links": [
        {"name": "Dashboard", "url": "admin:index", "permissions": ["auth.view_user"]},
        {"name": "API Docs", "url": "swagger-ui"},
        {"name": "Schema", "url": "schema"},
        {"app": "accounts"},
        {"app": "payments"},
        {"app": "reports"},
    ],
    "order_with_respect_to": [
        "accounts",
        "chama",
        "finance",
        "payments",
        "meetings",
        "notifications",
        "reports",
        "security",
        "audit",
        "analytics",
        "ai",
    ],
    "icons": {
        "auth": "fas fa-users-cog",
        "accounts": "fas fa-user-shield",
        "accounts.User": "fas fa-user",
        "accounts.OTPToken": "fas fa-key",
        "chama": "fas fa-people-group",
        "chama.Chama": "fas fa-people-roof",
        "chama.Membership": "fas fa-id-card",
        "finance": "fas fa-wallet",
        "finance.Contribution": "fas fa-hand-holding-dollar",
        "finance.Loan": "fas fa-money-bill-wave",
        "payments": "fas fa-credit-card",
        "payments.PaymentIntent": "fas fa-receipt",
        "payments.PaymentTransaction": "fas fa-money-check-dollar",
        "meetings": "fas fa-calendar-check",
        "notifications": "fas fa-bell",
        "reports": "fas fa-chart-column",
        "audit": "fas fa-clipboard-check",
        "analytics": "fas fa-chart-line",
        "security": "fas fa-shield-halved",
        "documents": "fas fa-folder-open",
        "governance": "fas fa-gavel",
        "billing": "fas fa-file-invoice-dollar",
        "support": "fas fa-life-ring",
        "ai": "fas fa-robot",
        "search": "fas fa-magnifying-glass",
        "settings": "fas fa-sliders",
        "exports": "fas fa-file-export",
        "investments": "fas fa-piggy-bank",
    },
}

JAZZMIN_UI_TWEAKS = {
    "theme": "flatly",
    "dark_mode_theme": None,
    "navbar": "navbar-white navbar-light",
    "accent": "accent-primary",
    "sidebar": "sidebar-light-primary",
    "brand_colour": "navbar-primary",
    "navbar_small_text": False,
    "footer_small_text": False,
    "body_small_text": False,
    "brand_small_text": False,
    "sidebar_nav_small_text": False,
    "sidebar_disable_expand": False,
    "sidebar_nav_child_indent": True,
    "sidebar_nav_compact_style": False,
    "sidebar_nav_legacy_style": False,
    "sidebar_nav_flat_style": True,
    "sidebar_fixed": True,
    "layout_boxed": False,
    "footer_fixed": False,
    "actions_sticky_top": True,
    "navbar_fixed": True,
}

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.CorrelationIdMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database
DATABASE_POOL_URL = env("DATABASE_POOL_URL", default="")
DATABASE_URL = env("DATABASE_URL", default="")
ACTIVE_DATABASE_URL = DATABASE_POOL_URL or DATABASE_URL
if ACTIVE_DATABASE_URL:
    DATABASES = {
        "default": env.db_url_config(ACTIVE_DATABASE_URL),
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB", default="postgres"),
            "USER": env("POSTGRES_USER", default="postgres"),
            "PASSWORD": env(
                "POSTGRES_PASSWORD", default=env("DB_PASSWORD", default="")
            ),
            "HOST": env("DB_HOST", default="db.bkkftnavcqufaxrskdwo.supabase.co"),
            "PORT": env("DB_PORT", default="5432"),
        }
    }

db_host = str(DATABASES["default"].get("HOST", "") or "")
db_engine = DATABASES["default"].get("ENGINE", "")
db_options = dict(DATABASES["default"].get("OPTIONS", {}) or {})
if db_engine == "django.db.backends.postgresql":
    is_supabase_host = any(
        host_fragment in db_host for host_fragment in ("supabase.co", "supabase.com")
    )
    db_sslmode = env("DB_SSLMODE", default="require" if is_supabase_host else "prefer")
    if db_sslmode and "sslmode" not in db_options:
        db_options["sslmode"] = db_sslmode
    db_sslrootcert = env("DB_SSLROOTCERT", default="")
    if db_sslrootcert and "sslrootcert" not in db_options:
        db_options["sslrootcert"] = db_sslrootcert
elif db_options:
    # sqlite/mysql backends may receive SSL options from inherited env vars.
    # Strip Postgres-specific keys to avoid invalid connection kwargs.
    db_options.pop("sslmode", None)
    db_options.pop("sslrootcert", None)
if db_options:
    DATABASES["default"]["OPTIONS"] = db_options
else:
    DATABASES["default"].pop("OPTIONS", None)

# Supabase Configuration
SUPABASE_URL = env("SUPABASE_URL", default="https://bkkftnavcqufaxrskdwo.supabase.co")
SUPABASE_ANON_KEY = env("SUPABASE_ANON_KEY", default="")
SUPABASE_SERVICE_ROLE_KEY = env("SUPABASE_SERVICE_ROLE_KEY", default="")
SUPABASE_JWT_SECRET = env("SUPABASE_JWT_SECRET", default="")
SUPABASE_REQUEST_TIMEOUT = env.int("SUPABASE_REQUEST_TIMEOUT", default=5)
SUPABASE_DATABASE_HOST = DATABASES["default"].get("HOST", "")
SUPABASE_STORAGE_BUCKET = env("SUPABASE_STORAGE_BUCKET", default="")
SUPABASE_STORAGE_PUBLIC = env.bool("SUPABASE_STORAGE_PUBLIC", default=False)
SUPABASE_STORAGE_SIGNED_URL_TTL = env.int(
    "SUPABASE_STORAGE_SIGNED_URL_TTL", default=3600
)
SUPABASE_USE_STORAGE = bool(
    SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY and SUPABASE_STORAGE_BUCKET
)

# Field-level encryption (AES-256-GCM via core.encryption)
# Expected format: urlsafe-base64 key bytes (Fernet-compatible).
_field_encryption_key_raw = env("FIELD_ENCRYPTION_KEY", default="").strip()
FIELD_ENCRYPTION_KEY = b""
if _field_encryption_key_raw:
    try:
        FIELD_ENCRYPTION_KEY = base64.urlsafe_b64decode(_field_encryption_key_raw.encode("utf-8"))
    except Exception:  # noqa: BLE001
        # Preserve backward compatibility when key is provided as raw bytes-like string.
        FIELD_ENCRYPTION_KEY = _field_encryption_key_raw.encode("utf-8")

# Public web/app link configuration
SITE_URL = env("SITE_URL", default="https://mychama.app").rstrip("/")
DEEP_LINK_SCHEME = env("DEEP_LINK_SCHEME", default="mychama")
DEEP_LINK_DOMAIN = env("DEEP_LINK_DOMAIN", default="mychama.app")
PLAY_STORE_URL = env(
    "PLAY_STORE_URL",
    default="https://play.google.com/store/apps/details?id=com.mychama.app",
)
APP_STORE_URL = env(
    "APP_STORE_URL",
    default="https://apps.apple.com/app/id0000000000",
)
IOS_BUNDLE_ID = env("IOS_BUNDLE_ID", default="com.mychama.app")
IOS_ASSOCIATED_APP_ID = env("IOS_ASSOCIATED_APP_ID", default="")
ANDROID_APPLICATION_ID = env("ANDROID_APPLICATION_ID", default="com.mychama.app")
ANDROID_SHA256_CERT_FINGERPRINTS = [
    value.strip()
    for value in env.list("ANDROID_SHA256_CERT_FINGERPRINTS", default=[])
    if str(value).strip()
]

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
PASSWORD_HASHERS = [
    # Use bcrypt for new password hashes (backward compatible with legacy hashers).
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
]

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True

# Authentication backends - support both username and phone-based auth
AUTHENTICATION_BACKENDS = [
    "apps.accounts.backends.PhoneAuthenticationBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# Static files (CSS, JavaScript, Images)
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Media files
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
if SUPABASE_USE_STORAGE and SUPABASE_STORAGE_PUBLIC:
    MEDIA_URL = f"{SUPABASE_URL.rstrip('/')}/storage/v1/object/public/{SUPABASE_STORAGE_BUCKET}/"

STORAGES = {
    "default": {
        "BACKEND": (
            "core.storage.SupabaseStorage"
            if SUPABASE_USE_STORAGE
            else "django.core.files.storage.FileSystemStorage"
        ),
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Custom user model
AUTH_USER_MODEL = "accounts.User"

# REST Framework
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "core.authentication.MyChamaJWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "core.throttles.DefaultAnonThrottle",
        "core.throttles.DefaultUserThrottle",
    ],
    "DEFAULT_PAGINATION_CLASS": "core.pagination.StandardResultsSetPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_SCHEMA_CLASS": "core.openapi.MyChamaAutoSchema",
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/minute",
        "user": env("DRF_THROTTLE_USER", default="1000/hour"),
        "login": "10/minute",
        "login_identifier": "5/minute",
        "register": "10/hour",
        "register_identifier": "3/hour",
        "password_reset": "10/hour",
        "password_reset_identifier": "5/hour",
        "otp_request": env("DRF_THROTTLE_OTP_REQUEST", default="3/10m"),
        "otp_identifier": env("DRF_THROTTLE_OTP_IDENTIFIER", default="3/10m"),
        "otp_verify": env("DRF_THROTTLE_OTP_VERIFY", default="10/minute"),
        "otp_verify_identifier": env(
            "DRF_THROTTLE_OTP_VERIFY_IDENTIFIER", default="10/minute"
        ),
        "payment_initiation": env(
            "DRF_THROTTLE_PAYMENT_INITIATION", default="10/minute"
        ),
        "mpesa_callback": "120/minute",
        "notification_dispatch": "60/hour",
        "report_export": "30/hour",
        "issue_create": "20/hour",
        "issue_moderation": "60/hour",
        "ai_chat": "120/hour",
        "ai_action": "60/hour",
    },
    "EXCEPTION_HANDLER": "core.exceptions.custom_exception_handler",
}

# JWT Settings
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

# CORS
CORS_ALLOW_ALL_ORIGINS = env.bool("CORS_ALLOW_ALL_ORIGINS", default=False)
CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=[
        "http://localhost:3000",
        "http://localhost:8081",
        "http://localhost:19006",
    ],
)
CORS_ALLOWED_ORIGIN_REGEXES = env.list("CORS_ALLOWED_ORIGIN_REGEXES", default=[])
CORS_ALLOW_CREDENTIALS = True

# Email
EMAIL_PROVIDER = env("EMAIL_PROVIDER", default="django").lower()
SENDGRID_API_KEY = env("SENDGRID_API_KEY", default="")
SENDGRID_FROM_EMAIL = env("SENDGRID_FROM_EMAIL", default="")
GMAIL_EMAIL = env("GMAIL_EMAIL", default="")
GMAIL_APP_PASSWORD = env("GMAIL_APP_PASSWORD", default="")
GMAIL_FROM_EMAIL = env("GMAIL_FROM_EMAIL", default=GMAIL_EMAIL)
MAILGUN_API_KEY = env("MAILGUN_API_KEY", default="")
MAILGUN_DOMAIN = env("MAILGUN_DOMAIN", default="")
MAILGUN_FROM_EMAIL = env("MAILGUN_FROM_EMAIL", default="")
MAILGUN_SMTP_HOST = env("MAILGUN_SMTP_HOST", default="smtp.mailgun.org")
MAILGUN_SMTP_PORT = env.int("MAILGUN_SMTP_PORT", default=587)
MAILGUN_SMTP_USERNAME = env("MAILGUN_SMTP_USERNAME", default="")
MAILGUN_SMTP_PASSWORD = env("MAILGUN_SMTP_PASSWORD", default="")
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default=(
        "django.core.mail.backends.smtp.EmailBackend"
        if EMAIL_PROVIDER in {"gmail", "mailgun"}
        else "django.core.mail.backends.console.EmailBackend"
    ),
)
EMAIL_HOST = env(
    "EMAIL_HOST",
    default="smtp.gmail.com" if EMAIL_PROVIDER == "gmail" else MAILGUN_SMTP_HOST,
)
EMAIL_PORT = env.int(
    "EMAIL_PORT",
    default=587,
)
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_HOST_USER = env(
    "EMAIL_HOST_USER",
    default=GMAIL_EMAIL if EMAIL_PROVIDER == "gmail" else MAILGUN_SMTP_USERNAME,
)
EMAIL_HOST_PASSWORD = env(
    "EMAIL_HOST_PASSWORD",
    default=GMAIL_APP_PASSWORD if EMAIL_PROVIDER == "gmail" else MAILGUN_SMTP_PASSWORD,
)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="noreply@digitalchama.com")
EMAIL_FROM_NAME = env("EMAIL_FROM_NAME", default="MyChama")

# Logging configuration
# Note: Actual logging setup is done in core/logging_config.py using Loguru
# This is a fallback for Django's default logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": env("LOG_LEVEL", default="INFO"),
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": env("DJANGO_LOG_LEVEL", default="INFO"),
            "propagate": False,
        },
    },
}

# Loguru configuration
LOGURU_LOG_LEVEL = env("LOG_LEVEL", default="INFO")

# SMS (Africa's Talking)
SMS_PROVIDER = env("SMS_PROVIDER", default="console").lower()
AFRICAS_TALKING_USERNAME = env("AFRICAS_TALKING_USERNAME", default="")
AFRICAS_TALKING_API_KEY = env("AFRICAS_TALKING_API_KEY", default="")
AFRICAS_TALKING_SENDER_ID = env(
    "AFRICAS_TALKING_SENDER_ID",
    default=env("SMS_SENDER_ID", default="MYCHAMA"),
)
SMS_SENDER_ID = AFRICAS_TALKING_SENDER_ID

# OTP Configuration
OTP_EXPIRY_MINUTES = env.int("OTP_EXPIRY_MINUTES", default=5)
OTP_MAX_ATTEMPTS = env.int("OTP_MAX_ATTEMPTS", default=5)
OTP_LENGTH = env.int("OTP_LENGTH", default=6)
OTP_COOLDOWN_SECONDS = env.int("OTP_COOLDOWN_SECONDS", default=60)
OTP_RESEND_WINDOW_SECONDS = env.int("OTP_RESEND_WINDOW_SECONDS", default=600)
OTP_MAX_RESENDS_PER_WINDOW = env.int("OTP_MAX_RESENDS_PER_WINDOW", default=3)
OTP_DELIVERY_RETRY_LIMIT = env.int("OTP_DELIVERY_RETRY_LIMIT", default=2)
OTP_LOCKOUT_SECONDS = env.int("OTP_LOCKOUT_SECONDS", default=1800)
OTP_ALLOW_MOCK_DELIVERY = env.bool("OTP_ALLOW_MOCK_DELIVERY", default=False)
PRINT_OTP_IN_CONSOLE = env.bool("PRINT_OTP_IN_CONSOLE", default=False)
ENABLE_DEV_OTP_ENDPOINT = env.bool("ENABLE_DEV_OTP_ENDPOINT", default=False)
ENABLE_DEV_ENDPOINTS = env.bool("ENABLE_DEV_ENDPOINTS", default=False)
OTP_CALLBACK_TOKEN_HEADER = env(
    "OTP_CALLBACK_TOKEN_HEADER", default="X-OTP-Callback-Token"
)
OTP_SMS_CALLBACK_TOKEN = env("OTP_SMS_CALLBACK_TOKEN", default="")
OTP_EMAIL_CALLBACK_TOKEN = env("OTP_EMAIL_CALLBACK_TOKEN", default="")
KYC_AUTO_PROVIDER = env("KYC_AUTO_PROVIDER", default="auto").lower()
ONFIDO_API_KEY = env("ONFIDO_API_KEY", default="")
ONFIDO_WORKFLOW_ID = env("ONFIDO_WORKFLOW_ID", default="")
ONFIDO_API_URL = env("ONFIDO_API_URL", default="https://api.onfido.com/v3.6")
KYC_CALLBACK_SIGNATURE_HEADER = env(
    "KYC_CALLBACK_SIGNATURE_HEADER", default="X-KYC-Signature"
)
KYC_WEBHOOK_SECRET = env("KYC_WEBHOOK_SECRET", default="")
SMILE_WEBHOOK_SECRET = env("SMILE_WEBHOOK_SECRET", default="")
ONFIDO_WEBHOOK_SECRET = env("ONFIDO_WEBHOOK_SECRET", default="")
MAX_ACTIVE_SESSIONS_PER_USER = env.int("MAX_ACTIVE_SESSIONS_PER_USER", default=3)
SESSION_INACTIVITY_TIMEOUT_SECONDS = env.int(
    "SESSION_INACTIVITY_TIMEOUT_SECONDS", default=300
)
PASSWORD_MIN_ENTROPY_BITS = env.float("PASSWORD_MIN_ENTROPY_BITS", default=45)
HIBP_PASSWORD_CHECK_ENABLED = env.bool("HIBP_PASSWORD_CHECK_ENABLED", default=True)
HIBP_PASSWORD_CHECK_TIMEOUT_SECONDS = env.float(
    "HIBP_PASSWORD_CHECK_TIMEOUT_SECONDS", default=5
)
HIBP_PASSWORD_CHECK_FAIL_CLOSED = env.bool(
    "HIBP_PASSWORD_CHECK_FAIL_CLOSED", default=False
)
HIBP_PASSWORD_BREACH_MIN_COUNT = env.int("HIBP_PASSWORD_BREACH_MIN_COUNT", default=1)
TRANSACTION_PIN_BCRYPT_ROUNDS = env.int("TRANSACTION_PIN_BCRYPT_ROUNDS", default=12)
WITHDRAWAL_PIN_REQUIRED = env.bool("WITHDRAWAL_PIN_REQUIRED", default=True)
PIN_MAX_ATTEMPTS = env.int("PIN_MAX_ATTEMPTS", default=5)
PIN_LOCKOUT_STAGE_1_SECONDS = env.int("PIN_LOCKOUT_STAGE_1_SECONDS", default=300)
PIN_LOCKOUT_STAGE_2_SECONDS = env.int("PIN_LOCKOUT_STAGE_2_SECONDS", default=1800)
PIN_LOCKOUT_STAGE_3_SECONDS = env.int("PIN_LOCKOUT_STAGE_3_SECONDS", default=86400)
PIN_LOCKOUT_FREEZE_ON_ESCALATION = env.bool(
    "PIN_LOCKOUT_FREEZE_ON_ESCALATION",
    default=True,
)
LOGIN_CAPTCHA_AFTER_FAILURES = env.int("LOGIN_CAPTCHA_AFTER_FAILURES", default=5)
LOGIN_FREEZE_AFTER_SUSPICIOUS_EVENTS = env.int(
    "LOGIN_FREEZE_AFTER_SUSPICIOUS_EVENTS",
    default=3,
)
LOGIN_GEO_VELOCITY_KM_PER_HOUR = env.int(
    "LOGIN_GEO_VELOCITY_KM_PER_HOUR",
    default=900,
)
SECRET_ROTATION_DAYS = env.int("SECRET_ROTATION_DAYS", default=90)
FIELD_ENCRYPTION_ENABLED = env.bool("FIELD_ENCRYPTION_ENABLED", default=False)
ENCRYPTION_KMS_PROVIDER = env("ENCRYPTION_KMS_PROVIDER", default="local").lower()
ENCRYPTION_KEY_RING = env("ENCRYPTION_KEY_RING", default="mychama-security")
PII_ENCRYPTION_KEY_ID = env("PII_ENCRYPTION_KEY_ID", default="")
KYC_ENCRYPTION_KEY_ID = env("KYC_ENCRYPTION_KEY_ID", default="")
AUDIT_SIGNING_KEY_ID = env("AUDIT_SIGNING_KEY_ID", default="")
BACKUP_ENCRYPTION_KEY_ID = env("BACKUP_ENCRYPTION_KEY_ID", default="")
ENCRYPTION_ACTIVE_KEY_VERSION = env("ENCRYPTION_ACTIVE_KEY_VERSION", default="v1")

# Redis
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")

# Celery
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/0")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TIME_LIMIT = env.int("CELERY_TASK_TIME_LIMIT", default=300)
CELERY_BEAT_SCHEDULER = env(
    "CELERY_BEAT_SCHEDULER",
    default=(
        "django_celery_beat.schedulers:DatabaseScheduler"
        if importlib.util.find_spec("django_celery_beat") is not None
        else "celery.beat:PersistentScheduler"
    ),
)

try:
    from config.celery_schedule import (
        CELERY_BEAT_SCHEDULE as PROJECT_CELERY_BEAT_SCHEDULE,
    )
except Exception:  # pragma: no cover - keep settings import resilient
    PROJECT_CELERY_BEAT_SCHEDULE = {}

CELERY_BEAT_SCHEDULE = PROJECT_CELERY_BEAT_SCHEDULE

# Cache
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

# M-Pesa
MPESA_CONSUMER_KEY = env("MPESA_CONSUMER_KEY", default="")
MPESA_CONSUMER_SECRET = env("MPESA_CONSUMER_SECRET", default="")
MPESA_SHORTCODE = env("MPESA_SHORTCODE", default="")
MPESA_PASSKEY = env("MPESA_PASSKEY", default="")
MPESA_CALLBACK_URL = env("MPESA_CALLBACK_URL", default="")
MPESA_ENVIRONMENT = env("MPESA_ENVIRONMENT", default="sandbox")
MPESA_CALLBACK_IP_ALLOWLIST = env.list("MPESA_CALLBACK_IP_ALLOWLIST", default=[])
MPESA_CALLBACK_SIGNATURE_HEADER = env(
    "MPESA_CALLBACK_SIGNATURE_HEADER",
    default="X-MPESA-SIGNATURE",
)
MPESA_CALLBACK_SECRET = env("MPESA_CALLBACK_SECRET", default="")
PAYMENTS_CALLBACK_REQUIRE_IP_ALLOWLIST = env.bool(
    "PAYMENTS_CALLBACK_REQUIRE_IP_ALLOWLIST",
    default=False,
)
PAYMENTS_CALLBACK_REQUIRE_SIGNATURE = env.bool(
    "PAYMENTS_CALLBACK_REQUIRE_SIGNATURE",
    default=False,
)
MPESA_USE_STUB = env.bool("MPESA_USE_STUB", default=DEBUG)

# Stripe
STRIPE_PUBLIC_KEY = env("STRIPE_PUBLIC_KEY", default="")
STRIPE_SECRET_KEY = env("STRIPE_SECRET_KEY", default="")
STRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET", default="")

# OpenAI Configuration
OPENAI_API_KEY = env("OPENAI_API_KEY", default="")
OPENAI_PROJECT = env("OPENAI_PROJECT", default="")
OPENAI_MODEL = env("OPENAI_MODEL", default="gpt-4-turbo-preview")
AI_MODEL = env("AI_MODEL", default=OPENAI_MODEL)
AI_CHAT_MODEL = env("AI_CHAT_MODEL", default=AI_MODEL)
AI_MODERATION_MODEL = env("AI_MODERATION_MODEL", default="text-moderation-latest")
AI_EMBEDDING_MODEL = env("AI_EMBEDDING_MODEL", default="text-embedding-3-small")
AI_MAX_TOKENS = env.int("AI_MAX_TOKENS", default=2000)
OPENAI_TIMEOUT_SECONDS = env.int("OPENAI_TIMEOUT_SECONDS", default=30)

# Analytics
POSTHOG_ENABLED = env.bool(
    "POSTHOG_ENABLED", default=bool(env("POSTHOG_API_KEY", default=""))
)
POSTHOG_API_KEY = env("POSTHOG_API_KEY", default="")
POSTHOG_HOST = env("POSTHOG_HOST", default="https://app.posthog.com")
POSTHOG_PROJECT_ID = env("POSTHOG_PROJECT_ID", default="")

# Firebase Analytics
FIREBASE_ANALYTICS_ENABLED = env.bool("FIREBASE_ANALYTICS_ENABLED", default=False)
FIREBASE_SERVICE_ACCOUNT = env("FIREBASE_SERVICE_ACCOUNT", default="")
FIREBASE_SERVICE_ACCOUNT_FILE = env(
    "FIREBASE_SERVICE_ACCOUNT_FILE",
    default=env("FIREBASE_SERVICE_ACCOUNT_PATH", default=""),
)
FCM_API_KEY = env("FCM_API_KEY", default="")
FCM_PROJECT_ID = env("FCM_PROJECT_ID", default="")
EXPO_PUSH_ENABLED = env.bool("EXPO_PUSH_ENABLED", default=True)
EXPO_PUSH_ACCESS_TOKEN = env("EXPO_PUSH_ACCESS_TOKEN", default="")
BACKUP_DIR = env("BACKUP_DIR", default=str(BASE_DIR / "backups"))
TEMP_FILE_ROOT = env("TEMP_FILE_ROOT", default="/tmp")
BACKUP_INTEGRITY_CHECK_ENABLED = env.bool(
    "BACKUP_INTEGRITY_CHECK_ENABLED", default=True
)
PRIVATE_MEDIA_STORAGE_REQUIRED = env.bool(
    "PRIVATE_MEDIA_STORAGE_REQUIRED", default=True
)
KYC_SIGNED_URL_TTL_SECONDS = env.int("KYC_SIGNED_URL_TTL_SECONDS", default=900)
CLAMAV_HOST = env("CLAMAV_HOST", default="clamav")
CLAMAV_PORT = env.int("CLAMAV_PORT", default=3310)
CLAMAV_SCAN_TIMEOUT_SECONDS = env.int("CLAMAV_SCAN_TIMEOUT_SECONDS", default=10)
PRIVACY_POLICY_VERSION = env("PRIVACY_POLICY_VERSION", default="2026.04")
RETENTION_FINANCIAL_DAYS = env.int("RETENTION_FINANCIAL_DAYS", default=2555)
RETENTION_KYC_DAYS = env.int("RETENTION_KYC_DAYS", default=1825)
RETENTION_SECURITY_LOG_DAYS = env.int("RETENTION_SECURITY_LOG_DAYS", default=365)
RETENTION_CONSENT_DAYS = env.int("RETENTION_CONSENT_DAYS", default=730)
DATA_BREACH_NOTIFY_WINDOW_HOURS = env.int(
    "DATA_BREACH_NOTIFY_WINDOW_HOURS",
    default=72,
)
FRAUD_STEP_UP_SCORE_THRESHOLD = env.int("FRAUD_STEP_UP_SCORE_THRESHOLD", default=61)
FRAUD_FREEZE_SCORE_THRESHOLD = env.int("FRAUD_FREEZE_SCORE_THRESHOLD", default=81)
FRAUD_REVIEW_SLA_HOURS = env.int("FRAUD_REVIEW_SLA_HOURS", default=24)
FRAUD_LARGE_TRANSACTION_HOLD_KES = env.int(
    "FRAUD_LARGE_TRANSACTION_HOLD_KES",
    default=100000,
)
AML_SINGLE_TRANSACTION_THRESHOLD_KES = env.int(
    "AML_SINGLE_TRANSACTION_THRESHOLD_KES",
    default=1000000,
)
AML_STRUCTURING_THRESHOLD_KES = env.int(
    "AML_STRUCTURING_THRESHOLD_KES",
    default=1000000,
)
AML_STRUCTURING_WINDOW_HOURS = env.int("AML_STRUCTURING_WINDOW_HOURS", default=24)

# Monitoring
SENTRY_DSN = env("SENTRY_DSN", default="")
SENTRY_ENVIRONMENT = env("SENTRY_ENVIRONMENT", default="development")
SENTRY_RELEASE = env("SENTRY_RELEASE", default="")
SENTRY_TRACES_SAMPLE_RATE = env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.1)
SENTRY_PROFILES_SAMPLE_RATE = env.float("SENTRY_PROFILES_SAMPLE_RATE", default=0.0)
PAGERDUTY_ROUTING_KEY = env("PAGERDUTY_ROUTING_KEY", default="")
ALERT_FAILED_LOGIN_SPIKE_THRESHOLD = env.int(
    "ALERT_FAILED_LOGIN_SPIKE_THRESHOLD",
    default=25,
)
ALERT_FRAUD_SPIKE_THRESHOLD = env.int("ALERT_FRAUD_SPIKE_THRESHOLD", default=10)
ALERT_KYC_FAILURE_THRESHOLD = env.int("ALERT_KYC_FAILURE_THRESHOLD", default=20)
ALERT_API_P95_SECONDS = env.float("ALERT_API_P95_SECONDS", default=1.5)

# File upload
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # 10MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # 10MB

# Spectacular settings
SPECTACULAR_SETTINGS = {
    "TITLE": "Digital Chama API",
    "DESCRIPTION": "API for Digital Chama - Savings Group Management Platform",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": r"/api/v[0-9]",
    "SWAGGER_UI_SETTINGS": {
        "deepLinking": True,
        "displayRequestDuration": True,
        "persistAuthorization": True,
    },
    "CONTACT": {
        "name": "MyChama Engineering",
        "url": "https://mychama.app",
        "email": "support@mychama.app",
    },
    "PREPROCESSING_HOOKS": [
        "core.openapi.deduplicate_schema_endpoints",
    ],
    "ENUM_NAME_OVERRIDES": {
        "ChamaStatusEnum": "apps.chama.models.ChamaStatus.choices",
        "ChamaTypeEnum": "apps.chama.models.ChamaType.choices",
        "MemberStatusEnum": "apps.chama.models.MemberStatus.choices",
        "MembershipRoleEnum": "apps.chama.models.MembershipRole.choices",
        "InviteStatusEnum": "apps.chama.models.InviteStatus.choices",
        "MembershipRequestStatusEnum": "apps.chama.models.MembershipRequestStatus.choices",
        "LoanStatusEnum": "apps.finance.models.LoanStatus.choices",
        "FineStatusEnum": "apps.fines.models.FineStatus.choices",
        "PaymentIntentStatusEnum": "apps.payments.models.PaymentIntentStatus.choices",
        "PaymentDisputeStatusEnum": "apps.payments.models.PaymentDisputeStatus.choices",
        "PaymentRefundStatusEnum": "apps.payments.models.PaymentRefundStatus.choices",
        "MpesaTransactionStatusEnum": "apps.payments.models.MpesaTransactionStatus.choices",
        "ReconciliationRunStatusEnum": "apps.payments.models.ReconciliationRunStatus.choices",
        "PaymentAllocationStrategyEnum": "apps.payments.models.PaymentAllocationStrategy.choices",
        "LegacyPaymentPurposeEnum": "apps.payments.models.PaymentPurpose.choices",
        "MpesaPurposeEnum": "apps.payments.models.MpesaPurpose.choices",
        "UnifiedPaymentStatusEnum": "apps.payments.unified_models.PaymentStatus.choices",
        "UnifiedPaymentMethodEnum": "apps.payments.unified_models.PaymentMethod.choices",
        "UnifiedPaymentPurposeEnum": "apps.payments.unified_models.PaymentPurpose.choices",
        "CardPaymentStatusEnum": "apps.payments.card_models.CardPaymentStatus.choices",
        "CardPaymentPurposeEnum": "apps.payments.card_models.CardPaymentPurpose.choices",
        "FineCategoryEnum": "apps.fines.models.FineCategory.choices",
        "IssueCategoryEnum": "apps.issues.models.IssueCategory.choices",
        "RuleCategoryEnum": "apps.governance.models.RuleCategory.choices",
        "RuleStatusEnum": "apps.governance.models.RuleStatus.choices",
        "AcknowledgmentStatusEnum": "apps.governance.models.AcknowledgmentStatus.choices",
        "GovernanceApprovalStatusEnum": "apps.governance.models.ApprovalStatus.choices",
        "NotificationCategoryEnum": "apps.notifications.models.NotificationCategory.choices",
        "NotificationStatusEnum": "apps.notifications.models.NotificationStatus.choices",
        "NotificationInboxStatusEnum": "apps.notifications.models.NotificationInboxStatus.choices",
        "NotificationDeliveryStatusEnum": "apps.notifications.models.NotificationDeliveryStatus.choices",
        "NotificationEventStatusEnum": "apps.notifications.models.NotificationEventStatus.choices",
        "NotificationTargetEnum": "apps.notifications.models.NotificationTarget.choices",
        "BroadcastTargetEnum": "apps.notifications.models.BroadcastTarget.choices",
        "BroadcastAnnouncementStatusEnum": "apps.notifications.models.BroadcastAnnouncementStatus.choices",
        "PaymentDisputeCategoryEnum": "apps.payments.models.PaymentDisputeCategory.choices",
        "ApprovalLevelEnum": "apps.governance.models.ApprovalLevel.choices",
        "InvestmentStatusEnum": "apps.investments.models.InvestmentStatus.choices",
        "InvestmentApprovalStatusEnum": "apps.investments.models.ApprovalStatus.choices",
        "OTPDeliveryStatusEnum": "apps.accounts.models.OTPDeliveryStatus.choices",
        "CurrencyChoicesEnum": "core.constants.CurrencyChoices.choices",
        "SimpleCurrencyCodeEnum": [
            ("KES", "KES"),
            ("USD", "USD"),
            ("EUR", "EUR"),
            ("GBP", "GBP"),
        ],
        "RoleChoicesEnum": "core.constants.RoleChoices.choices",
        "SplitPaymentStrategyEnum": [
            ("repayment_first", "repayment_first"),
            ("welfare_first", "welfare_first"),
            ("ratio", "ratio"),
            ("auto", "auto"),
            ("custom", "custom"),
        ],
        "MpesaOnlyPaymentMethodEnum": [
            ("mpesa", "M-Pesa"),
        ],
        "OTPPurposeEnum": "apps.accounts.models.OTPPurpose.choices",
        "NotificationChannelEnum": "apps.notifications.models.NotificationChannel.choices",
        "OTPDeliveryChannelEnum": "apps.accounts.models.OTPDeliveryChannel.choices",
        # Additional status enums to prevent naming collisions
        "PayoutStatusEnum": "apps.payouts.models.PayoutStatus.choices",
        "PayoutTriggerTypeEnum": "apps.payouts.models.PayoutTriggerType.choices",
        "EligibilityStatusEnum": "apps.payouts.models.EligibilityStatus.choices",
        "ContributionGoalStatusEnum": "apps.finance.models.ContributionGoalStatus.choices",
        "ContributionScheduleStatusEnum": "apps.finance.models.ContributionScheduleStatus.choices",
        "InstallmentStatusEnum": "apps.finance.models.InstallmentStatus.choices",
        "ExpenseStatusEnum": "apps.finance.models.ExpenseStatus.choices",
        "PenaltyStatusEnum": "apps.finance.models.PenaltyStatus.choices",
        "LoanApplicationStatusEnum": "apps.finance.models.LoanApplicationStatus.choices",
        "LoanGuarantorStatusEnum": "apps.finance.models.LoanGuarantorStatus.choices",
        "LoanTopUpStatusEnum": "apps.finance.models.LoanTopUpStatus.choices",
        "LoanRestructureStatusEnum": "apps.finance.models.LoanRestructureStatus.choices",
        "IssueStatusEnum": "apps.issues.models.IssueStatus.choices",
        "AppealStatusEnum": "apps.issues.models.AppealStatus.choices",
        "WarningStatusEnum": "apps.issues.models.WarningStatus.choices",
        "IssueResolutionStatusEnum": "apps.issues.models.IssueResolutionStatus.choices",
        "IncidentStatusEnum": "apps.security.incident_response.IncidentStatus.choices",
        "FraudCaseStatusEnum": "apps.security.models.FraudCase.CaseStatus.choices",
        "MotionStatusEnum": "apps.governance.models.MotionStatus.choices",
        "ReportStatusEnum": "apps.reports.models.ReportStatus.choices",
        "ReportRunStatusEnum": "apps.reports.models.ReportRunStatus.choices",
        "ExportStatusEnum": "apps.exports.models.ExportStatus.choices",
        "InvestmentProductStatusEnum": "apps.investments.models.InvestmentProductStatus.choices",
        "MemberInvestmentPositionStatusEnum": "apps.investments.models.MemberInvestmentPositionStatus.choices",
        "InvestmentRequestStatusEnum": "apps.investments.models.InvestmentRequestStatus.choices",
        "InvestmentReturnLedgerStatusEnum": "apps.investments.models.InvestmentReturnLedgerStatus.choices",
        "TransactionStatusEnum": "apps.payments.unified_models.TransactionStatus.choices",
        "StatementImportStatusEnum": "apps.payments.unified_models.StatementImportStatus.choices",
        "ScheduledAnnouncementStatusEnum": "apps.notifications.models.ScheduledAnnouncementStatus.choices",
        "MpesaB2CStatusEnum": "apps.payments.models.MpesaB2CStatus.choices",
    },
    "EXTENSIONS_TO_SKIP": [],
    "COMPONENT_SPLIT_REQUEST": True,
}

# Frontend URL
FRONTEND_URL = env("FRONTEND_URL", default="http://localhost:8081")

# Security
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
