from .base import *  # noqa: F403,F401

# Force DEBUG=True for development - override any system env
DEBUG = True

DEFAULT_DEV_ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "testserver",
    "192.168.0.103",
    "192.168.0.104",
    "192.168.0.105",
    "192.168.0.106",
    "10.0.2.2",  # Android emulator
    "host.docker.internal",
    "web",
    "frontend",
    "localhost:8888",
]

DEFAULT_DEV_CSRF_TRUSTED_ORIGINS = [
    "http://localhost:3000", 
    "http://localhost:8000", 
    "http://localhost:8888",
    "http://127.0.0.1:3000", 
    "http://127.0.0.1:8000",
    "http://127.0.0.1:8888",
    "http://0.0.0.0:8000",
    "http://192.168.0.103:8000",
    "http://192.168.0.104:8000",
    "http://192.168.0.105:8000",
    "http://192.168.0.106:8000",
    "http://10.0.2.2:8000",  # Android emulator
    # Expo/React Native dev servers
    "http://192.168.0.103:8081",
    "http://192.168.0.103:19000",
    "http://192.168.0.103:19001",
    "http://192.168.0.103:19002",
]

# Keep any explicit env values, but always allow the common local dev hosts.
ALLOWED_HOSTS = list(
    dict.fromkeys(
        env.list("ALLOWED_HOSTS", default=DEFAULT_DEV_ALLOWED_HOSTS)
        + DEFAULT_DEV_ALLOWED_HOSTS
    )
)
CSRF_TRUSTED_ORIGINS = list(
    dict.fromkeys(
        env.list("CSRF_TRUSTED_ORIGINS", default=DEFAULT_DEV_CSRF_TRUSTED_ORIGINS)
        + DEFAULT_DEV_CSRF_TRUSTED_ORIGINS
    )
)

# In local development, avoid host-header lockouts when the app is opened via
# LAN IP, 0.0.0.0, test clients, or container aliases.
if "*" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append("*")

CORS_ALLOW_ALL_ORIGINS = env.bool("CORS_ALLOW_ALL_ORIGINS", default=True)

# Use SMTP email backend for development if provider is configured
# Otherwise use custom dev email backend for local testing
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend" if env("EMAIL_PROVIDER", default="") == "smtp" else "apps.core.email_backend.DevEmailBackend")

# OTP Configuration - prefer email in development
OTP_DEV_PREFERS_EMAIL = env.bool("OTP_DEV_PREFERS_EMAIL", default=True)

ENABLE_FRONTEND_ROUTES = env.bool("ENABLE_FRONTEND_ROUTES", default=False)

# Database configuration - prefer the Supabase session pooler when present.
_dev_use_sqlite = env.bool("DEV_USE_SQLITE", default=True)
_db_url = (
    f"sqlite:///{BASE_DIR / 'db.sqlite3'}"
    if _dev_use_sqlite
    else (
        env("DATABASE_POOL_URL", default="")
        or env(
            "DATABASE_URL",
            default="postgresql://digital_chama:change-this-production-db-password@localhost:5432/digital_chama",
        )
    )
)
DATABASES = {"default": env.db_url_config(_db_url)}

if DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql":
    DATABASES["default"]["CONN_MAX_AGE"] = 600

_db_host = str(DATABASES["default"].get("HOST", "") or "")
_db_options = dict(DATABASES["default"].get("OPTIONS", {}) or {})
if DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql":
    _is_supabase_host = any(
        host_fragment in _db_host for host_fragment in ("supabase.co", "supabase.com")
    )
    _db_sslmode = env("DB_SSLMODE", default="require" if _is_supabase_host else "prefer")
    if _db_sslmode and "sslmode" not in _db_options:
        _db_options["sslmode"] = _db_sslmode
    _db_sslrootcert = env("DB_SSLROOTCERT", default="")
    if _db_sslrootcert and "sslrootcert" not in _db_options:
        _db_options["sslrootcert"] = _db_sslrootcert
else:
    _db_options.pop("sslmode", None)
    _db_options.pop("sslrootcert", None)
if _db_options:
    DATABASES["default"]["OPTIONS"] = _db_options
else:
    DATABASES["default"].pop("OPTIONS", None)

# Use local memory cache for development
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "digital-chama-dev",
    }
}

# Disable some security features for local development
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0

# MPESA configuration for local development
# Set MPESA_USE_STUB=True to avoid actual M-Pesa API calls during testing
MPESA_USE_STUB = env.bool("MPESA_USE_STUB", default=True)
MPESA_ENVIRONMENT = env("MPESA_ENVIRONMENT", default="sandbox")

# OTP configuration - default to SMS for local testing.
OTP_DEFAULT_DELIVERY_METHOD = env("OTP_DEFAULT_DELIVERY_METHOD", default="sms")
OTP_ALLOW_MOCK_DELIVERY = env.bool("OTP_ALLOW_MOCK_DELIVERY", default=True)

# Development-only OTP settings
# Print OTP to console in development (SECURE - only works when DEBUG=True)
PRINT_OTP_IN_CONSOLE = env.bool("PRINT_OTP_IN_CONSOLE", default=True)
# Enable debug endpoint to retrieve OTP (SECURE - only works when DEBUG=True)
ENABLE_DEV_OTP_ENDPOINT = env.bool("ENABLE_DEV_OTP_ENDPOINT", default=True)
# Secret token for dev OTP endpoint (should be changed in production)
DEV_OTP_SECRET_TOKEN = env("DEV_OTP_SECRET_TOKEN", default="super-secret-dev-token-12345")
# Skip 2FA in development - return tokens directly after password auth
OTP_REQUIRED_IN_DEV = env.bool("OTP_REQUIRED_IN_DEV", default=False)

# Celery configuration for development
# Use eager task execution (no async needed)
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=True)

# Logging - more verbose for development
LOGGING["root"]["level"] = "INFO"
LOGGING["loggers"]["django"]["level"] = "DEBUG"

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

# Sentry Configuration for Development
SENTRY_DSN = env("SENTRY_DSN", default="")
if SENTRY_DSN and env.bool("SENTRY_ENABLED", default=True):
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
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=1.0),
        profiles_sample_rate=env.float("SENTRY_PROFILE_SESSION_SAMPLE_RATE", default=1.0),
        send_default_pii=env.bool("SENTRY_SEND_DEFAULT_PII", default=True),
        environment="development",
    )

# =============================================================================
# DEVELOPMENT SERVER INSTRUCTIONS
# =============================================================================
# To run Django accessible from other devices on the LAN:
#
#   python manage.py runserver 0.0.0.0:8000
#
# Then access from:
# - Physical device: http://<LAPTOP_IP>:8000 (find with: hostname -I)
# - Android emulator: http://10.0.2.2:8000
# - iOS simulator: http://localhost:8000
#
# Important: Ensure port 8000 is allowed through your firewall!
