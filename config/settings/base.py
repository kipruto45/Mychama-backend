from datetime import timedelta
from pathlib import Path

import environ
from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parents[2]

env = environ.Env(DEBUG=(bool, False), DJANGO_READ_DOT_ENV_FILE=(bool, True))
if env.bool("DJANGO_READ_DOT_ENV_FILE", default=True):
    environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="django-insecure-change-me")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "corsheaders",
    "drf_spectacular",
    "core",
    "apps.accounts",
    "apps.chama",
    "apps.finance",
    "apps.meetings",
    "apps.issues",
    "apps.payments",
    "apps.notifications",
    "apps.security",
    "apps.reports",
    "apps.ai",
    "apps.automations",
    "apps.app",
    "apps.admin_management",
    "apps.billing",
    "apps.fines",
    "apps.messaging",
    "apps.investments",
    "apps.exports",
    "apps.governance",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "core.middleware.CorrelationIdMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
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
                "core.context_processors.ui_preferences",
            ]
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgresql://digital_chama:digital_chama@localhost:5432/digital_chama",
    )
}
CACHES = {
    "default": env.cache(
        "CACHE_URL",
        default=env("REDIS_URL", default="redis://localhost:6379/1"),
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/dashboards/"
LOGOUT_REDIRECT_URL = "/login/"

API_ENABLE_SESSION_AUTH = env.bool("API_ENABLE_SESSION_AUTH", default=False)
API_ENABLE_BASIC_AUTH = env.bool("API_ENABLE_BASIC_AUTH", default=False)
DEFAULT_AUTHENTICATION_CLASSES = [
    "rest_framework_simplejwt.authentication.JWTAuthentication",
]
if API_ENABLE_SESSION_AUTH:
    DEFAULT_AUTHENTICATION_CLASSES.append(
        "rest_framework.authentication.SessionAuthentication"
    )
if API_ENABLE_BASIC_AUTH:
    DEFAULT_AUTHENTICATION_CLASSES.append(
        "rest_framework.authentication.BasicAuthentication"
    )

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": DEFAULT_AUTHENTICATION_CLASSES,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
    ],
    "DEFAULT_PAGINATION_CLASS": "core.pagination.DefaultPagination",
    "DEFAULT_THROTTLE_CLASSES": [
        "core.throttles.DefaultAnonThrottle",
        "core.throttles.DefaultUserThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": env("DRF_THROTTLE_ANON", default="60/minute"),
        "user": env("DRF_THROTTLE_USER", default="120/minute"),
        "login": env("DRF_THROTTLE_LOGIN", default="5/minute"),
        "login_identifier": env("DRF_THROTTLE_LOGIN_IDENTIFIER", default="6/minute"),
        "register": env("DRF_THROTTLE_REGISTER", default="5/minute"),
        "register_identifier": env(
            "DRF_THROTTLE_REGISTER_IDENTIFIER",
            default="5/hour",
        ),
        "otp_request": env("DRF_THROTTLE_OTP_REQUEST", default="6/hour"),
        "otp_identifier": env("DRF_THROTTLE_OTP_IDENTIFIER", default="6/hour"),
        "password_reset": env("DRF_THROTTLE_PASSWORD_RESET", default="3/minute"),
        "password_reset_identifier": env(
            "DRF_THROTTLE_PASSWORD_RESET_IDENTIFIER",
            default="5/hour",
        ),
        "payment_initiation": env(
            "DRF_THROTTLE_PAYMENT_INITIATION",
            default="10/minute",
        ),
        "mpesa_callback": env("DRF_THROTTLE_MPESA_CALLBACK", default="60/minute"),
        "notification_dispatch": env(
            "DRF_THROTTLE_NOTIFICATION_DISPATCH",
            default="20/minute",
        ),
        "report_export": env("DRF_THROTTLE_REPORT_EXPORT", default="15/minute"),
        "issue_create": env("DRF_THROTTLE_ISSUE_CREATE", default="30/hour"),
        "issue_moderation": env("DRF_THROTTLE_ISSUE_MODERATION", default="60/hour"),
        "ai_chat": env("DRF_THROTTLE_AI_CHAT", default="30/hour"),
        "ai_action": env("DRF_THROTTLE_AI_ACTION", default="20/hour"),
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": False,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Digital Chama Management System API",
    "DESCRIPTION": "Foundation API surface for the Digital Chama platform.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOW_ALL_ORIGINS = env.bool("CORS_ALLOW_ALL_ORIGINS", default=False)
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
ENABLE_FRONTEND_ROUTES = env.bool("ENABLE_FRONTEND_ROUTES", default=False)
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = env.bool("CSRF_COOKIE_HTTPONLY", default=True)
SESSION_COOKIE_SAMESITE = env("SESSION_COOKIE_SAMESITE", default="Lax")
CSRF_COOKIE_SAMESITE = env("CSRF_COOKIE_SAMESITE", default="Lax")
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=not DEBUG)
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=not DEBUG)
X_FRAME_OPTIONS = env("X_FRAME_OPTIONS", default="DENY")
SECURE_REFERRER_POLICY = env(
    "SECURE_REFERRER_POLICY",
    default="strict-origin-when-cross-origin",
)
SECURE_CONTENT_TYPE_NOSNIFF = env.bool("SECURE_CONTENT_TYPE_NOSNIFF", default=True)
CONTENT_SECURITY_POLICY = env(
    "CONTENT_SECURITY_POLICY",
    default=(
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com data:; "
        "img-src 'self' data: https:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
)
PERMISSIONS_POLICY = env(
    "PERMISSIONS_POLICY",
    default="camera=(), geolocation=(), microphone=()",
)
LOGIN_LOCKOUT_FAILURE_LIMIT = env.int("LOGIN_LOCKOUT_FAILURE_LIMIT", default=5)
LOGIN_LOCKOUT_COOLDOWN_SECONDS = env.int(
    "LOGIN_LOCKOUT_COOLDOWN_SECONDS",
    default=900,
)
LOGIN_NEW_DEVICE_ALERT_ENABLED = env.bool(
    "LOGIN_NEW_DEVICE_ALERT_ENABLED",
    default=False,
)
LOGIN_NEW_DEVICE_ALERT_CHANNELS = env.list(
    "LOGIN_NEW_DEVICE_ALERT_CHANNELS",
    default=["email"],
)
SECURITY_ANOMALY_WINDOW_MINUTES = env.int(
    "SECURITY_ANOMALY_WINDOW_MINUTES",
    default=60,
)
SECURITY_FAILED_LOGINS_IP_THRESHOLD = env.int(
    "SECURITY_FAILED_LOGINS_IP_THRESHOLD",
    default=5,
)
SECURITY_STK_FAILURE_THRESHOLD = env.int(
    "SECURITY_STK_FAILURE_THRESHOLD",
    default=5,
)
SECURITY_RAPID_PAYOUT_THRESHOLD = env.int(
    "SECURITY_RAPID_PAYOUT_THRESHOLD",
    default=5,
)
SECURITY_ROLE_CHANGE_THRESHOLD = env.int(
    "SECURITY_ROLE_CHANGE_THRESHOLD",
    default=4,
)
OTP_EXPIRY_MINUTES = env.int("OTP_EXPIRY_MINUTES", default=5)
OTP_MAX_ATTEMPTS = env.int("OTP_MAX_ATTEMPTS", default=5)
OTP_COOLDOWN_SECONDS = env.int("OTP_COOLDOWN_SECONDS", default=60)
OTP_RESEND_WINDOW_SECONDS = env.int("OTP_RESEND_WINDOW_SECONDS", default=600)
OTP_MAX_RESENDS_PER_WINDOW = env.int("OTP_MAX_RESENDS_PER_WINDOW", default=3)
OTP_DELIVERY_RETRY_LIMIT = env.int("OTP_DELIVERY_RETRY_LIMIT", default=2)
OTP_LOCKOUT_SECONDS = env.int("OTP_LOCKOUT_SECONDS", default=600)
OTP_ALLOW_MOCK_DELIVERY = env.bool("OTP_ALLOW_MOCK_DELIVERY", default=False)
# Default OTP delivery method: 'sms', 'email', or 'both'
OTP_DEFAULT_DELIVERY_METHOD = env("OTP_DEFAULT_DELIVERY_METHOD", default="sms")
OTP_CALLBACK_TOKEN_HEADER = env(
    "OTP_CALLBACK_TOKEN_HEADER",
    default="X-OTP-Callback-Token",
)
OTP_SMS_CALLBACK_TOKEN = env("OTP_SMS_CALLBACK_TOKEN", default="")
OTP_EMAIL_CALLBACK_TOKEN = env("OTP_EMAIL_CALLBACK_TOKEN", default="")

SMS_PROVIDER = env("SMS_PROVIDER", default="console")
AFRICAS_TALKING_USERNAME = env("AFRICAS_TALKING_USERNAME", default="")
AFRICAS_TALKING_API_KEY = env("AFRICAS_TALKING_API_KEY", default="")
AFRICAS_TALKING_SENDER_ID = env("AFRICAS_TALKING_SENDER_ID", default="")
SMS_SENDER_ID = env("SMS_SENDER_ID", default="")

# WhatsApp Business API Configuration
WHATSAPP_PROVIDER = env("WHATSAPP_PROVIDER", default="console")
WHATSAPP_BUSINESS_ACCOUNT_ID = env("WHATSAPP_BUSINESS_ACCOUNT_ID", default="")
WHATSAPP_PHONE_NUMBER_ID = env("WHATSAPP_PHONE_NUMBER_ID", default="")
WHATSAPP_ACCESS_TOKEN = env("WHATSAPP_ACCESS_TOKEN", default="")
WHATSAPP_API_VERSION = env("WHATSAPP_API_VERSION", default="v18.0")
WHATSAPP_VERIFY_TOKEN = env("WHATSAPP_VERIFY_TOKEN", default="")
WHATSAPP_WEBHOOK_SECRET = env("WHATSAPP_WEBHOOK_SECRET", default="")

# Firebase Cloud Messaging (FCM) Configuration
FCM_PROVIDER = env("FCM_PROVIDER", default="console")
FCM_API_KEY = env("FCM_API_KEY", default="")
FCM_PROJECT_ID = env("FCM_PROJECT_ID", default="")
FIREBASE_SERVICE_ACCOUNT = env("FIREBASE_SERVICE_ACCOUNT", default="")
FIREBASE_SERVICE_ACCOUNT_FILE = env("FIREBASE_SERVICE_ACCOUNT_FILE", default="")
FCM_VAPID_KEY = env("FCM_VAPID_KEY", default="")

# Webhook Secrets for Delivery Callbacks
SENDGRID_WEBHOOK_SECRET = env("SENDGRID_WEBHOOK_SECRET", default="")
MAILGUN_WEBHOOK_SECRET = env("MAILGUN_WEBHOOK_SECRET", default="")

# Telegram Bot Configuration
TELEGRAM_PROVIDER = env("TELEGRAM_PROVIDER", default="console")
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", default="")

EMAIL_PROVIDER = env("EMAIL_PROVIDER", default="django")
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = env("EMAIL_HOST", default="localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=False)
DEFAULT_FROM_EMAIL = env(
    "DEFAULT_FROM_EMAIL",
    default="no-reply@digitalchama.local",
)
SENDGRID_API_KEY = env("SENDGRID_API_KEY", default="")
SENDGRID_FROM_EMAIL = env("SENDGRID_FROM_EMAIL", default=DEFAULT_FROM_EMAIL)

# Mailgun SMTP settings
MAILGUN_API_KEY = env("MAILGUN_API_KEY", default="")
MAILGUN_DOMAIN = env("MAILGUN_DOMAIN", default="")
MAILGUN_FROM_EMAIL = env("MAILGUN_FROM_EMAIL", default=DEFAULT_FROM_EMAIL)
MAILGUN_SMTP_HOST = env("MAILGUN_SMTP_HOST", default="smtp.mailgun.org")
MAILGUN_SMTP_PORT = env.int("MAILGUN_SMTP_PORT", default=587)
MAILGUN_SMTP_USERNAME = env("MAILGUN_SMTP_USERNAME", default="")
MAILGUN_SMTP_PASSWORD = env("MAILGUN_SMTP_PASSWORD", default="")

CELERY_BROKER_URL = env(
    "CELERY_BROKER_URL", default=env("REDIS_URL", default="redis://localhost:6379/0")
)
CELERY_RESULT_BACKEND = env(
    "CELERY_RESULT_BACKEND",
    default=env("REDIS_URL", default="redis://localhost:6379/0"),
)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = env.int("CELERY_TASK_TIME_LIMIT", default=300)
MEETING_REMINDER_WINDOW_MINUTES = env.int("MEETING_REMINDER_WINDOW_MINUTES", default=30)
MEETING_DEFAULT_DURATION_MINUTES = env.int(
    "MEETING_DEFAULT_DURATION_MINUTES", default=120
)
MEETING_MINUTES_MAX_FILE_SIZE_MB = env.int(
    "MEETING_MINUTES_MAX_FILE_SIZE_MB", default=5
)
MEETING_MINUTES_ALLOWED_EXTENSIONS = env.list(
    "MEETING_MINUTES_ALLOWED_EXTENSIONS",
    default=[".pdf", ".doc", ".docx", ".txt"],
)
ISSUE_ATTACHMENT_MAX_FILE_SIZE_MB = env.int(
    "ISSUE_ATTACHMENT_MAX_FILE_SIZE_MB",
    default=10,
)
ISSUE_ATTACHMENT_ALLOWED_EXTENSIONS = env.list(
    "ISSUE_ATTACHMENT_ALLOWED_EXTENSIONS",
    default=[".jpg", ".jpeg", ".png", ".webp", ".pdf"],
)
OPENAI_API_KEY = env("OPENAI_API_KEY", default="")
AI_CHAT_MODEL = env("AI_CHAT_MODEL", default="gpt-5-mini")
AI_MODERATION_MODEL = env("AI_MODERATION_MODEL", default="omni-moderation-latest")
AI_EMBEDDING_MODEL = env("AI_EMBEDDING_MODEL", default="text-embedding-3-small")
AUTOMATION_QUIET_HOURS_START = env.int("AUTOMATION_QUIET_HOURS_START", default=21)
AUTOMATION_QUIET_HOURS_END = env.int("AUTOMATION_QUIET_HOURS_END", default=7)
AUTOMATION_NOTIFICATION_DAILY_LIMIT = env.int(
    "AUTOMATION_NOTIFICATION_DAILY_LIMIT",
    default=20,
)
NOTIFICATION_BEHAVIORAL_THRESHOLD = env.int(
    "NOTIFICATION_BEHAVIORAL_THRESHOLD",
    default=20,
)
LOAN_AUTO_PENALTY_CAP_PERCENT = env.int(
    "LOAN_AUTO_PENALTY_CAP_PERCENT",
    default=10,
)
AUTOMATION_JOB_LOCK_SECONDS = env.int("AUTOMATION_JOB_LOCK_SECONDS", default=600)
CELERY_BEAT_SCHEDULE = {
    "notifications-daily-due-reminders": {
        "task": "apps.automations.tasks.notifications_daily_due_reminders_job",
        "schedule": crontab(minute=0, hour=18),
    },
    "notifications-meeting-reminders": {
        "task": "apps.automations.tasks.notifications_meeting_reminders_job",
        "schedule": crontab(minute="*/30"),
    },
    "notifications-process-scheduled": {
        "task": "apps.automations.tasks.notifications_process_scheduled_job",
        "schedule": crontab(minute="*/5"),
    },
    "notifications-retry-failed": {
        "task": "apps.automations.tasks.notifications_retry_failed_job",
        "schedule": crontab(minute="*/10"),
    },
    "payments-expire-pending-stk": {
        "task": "apps.payments.tasks.payments_expire_pending_stk",
        "schedule": crontab(minute="*/5"),
    },
    "payments-daily-reconciliation": {
        "task": "apps.payments.tasks.payments_daily_reconciliation",
        "schedule": crontab(minute=0, hour=22),
    },
    "payments-advanced-reconciliation": {
        "task": "apps.payments.tasks.payments_advanced_reconciliation",
        "schedule": crontab(minute=30, hour=22),
    },
    "payments-fraud-pattern-detection": {
        "task": "apps.payments.tasks.payments_fraud_pattern_detection",
        "schedule": crontab(minute="*/15"),
    },
    "payments-escalate-pending-disbursements": {
        "task": "apps.payments.tasks.payments_escalate_pending_disbursements",
        "schedule": crontab(minute=0, hour="*"),
    },
    "membership-approved-welcome-sweep": {
        "task": "apps.finance.tasks.on_membership_approved_sweep",
        "schedule": crontab(minute="*/30"),
    },
    "memberships-expire-pending-requests": {
        "task": "apps.automations.tasks.memberships_expire_pending_requests_job",
        "schedule": crontab(minute=0, hour=1),
    },
    "memberships-pending-approval-reminders": {
        "task": "apps.automations.tasks.memberships_pending_approval_reminders_job",
        "schedule": crontab(minute=0, hour=9),
    },
    # Invite Link Automation Tasks
    "invites-expiring-reminders": {
        "task": "apps.chama.tasks.send_expiring_invite_reminders",
        "schedule": crontab(minute=0, hour=9),
    },
    "invites-cleanup-expired": {
        "task": "apps.chama.tasks.cleanup_expired_invite_links",
        "schedule": crontab(minute=0, hour=2),
    },
    "invites-unused-reminders": {
        "task": "apps.chama.tasks.send_unused_invite_reminders",
        "schedule": crontab(minute=0, hour=10, day_of_week='monday'),
    },
    "contributions-daily-reminder": {
        "task": "apps.finance.tasks.contributions_daily_reminder",
        "schedule": crontab(minute=0, hour=18),
    },
    "contributions-overdue-penalize": {
        "task": "apps.finance.tasks.contributions_mark_overdue_and_penalize",
        "schedule": crontab(minute=0, hour=20),
    },
    "contributions-monthly-statement": {
        "task": "apps.finance.tasks.contributions_monthly_statement",
        "schedule": crontab(minute=0, hour=7, day_of_month=1),
    },
    "loans-due-soon-reminder": {
        "task": "apps.finance.tasks.loans_due_soon_reminder",
        "schedule": crontab(minute=0, hour=8),
    },
    "loans-due-today-reminder": {
        "task": "apps.finance.tasks.loans_due_today_reminder",
        "schedule": crontab(minute=0, hour=7),
    },
    "loans-overdue-escalation": {
        "task": "apps.finance.tasks.loans_overdue_escalation",
        "schedule": crontab(minute=0, hour=9),
    },
    "loans-delinquency-monitor": {
        "task": "apps.finance.tasks.loans_delinquency_monitor",
        "schedule": crontab(minute=10, hour=9),
    },
    "loans-auto-penalty-calculator": {
        "task": "apps.finance.tasks.loans_auto_penalty_calculator",
        "schedule": crontab(minute=15, hour=20),
    },
    "loans-auto-close-paid": {
        "task": "apps.finance.tasks.loans_auto_close_when_paid",
        "schedule": crontab(minute=0, hour="*"),
    },
    "ledger-daily-integrity-audit": {
        "task": "apps.finance.tasks.ledger_daily_integrity_audit",
        "schedule": crontab(minute=0, hour=2),
    },
    "memberships-inactivity-monitor": {
        "task": "apps.finance.tasks.memberships_inactivity_monitor",
        "schedule": crontab(minute=0, hour=6),
    },
    "payouts-timeout-monitor": {
        "task": "apps.payments.tasks.payouts_timeout_monitor",
        "schedule": crontab(minute="*/10"),
    },
    "payouts-escalate-stuck-pending": {
        "task": "apps.payments.tasks.payouts_escalate_stuck_pending",
        "schedule": crontab(minute=0, hour="*"),
    },
    "issues-escalate-old-open": {
        "task": "apps.issues.tasks.issues_escalate_old_open",
        "schedule": crontab(minute=30, hour=6),
    },
    "issues-nightly-ai-triage": {
        "task": "apps.issues.tasks.issues_auto_triage_ai",
        "schedule": crontab(minute=0, hour=1),
    },
    "meetings-reminder-24h": {
        "task": "apps.meetings.tasks.meetings_reminder_24h",
        "schedule": crontab(minute=0, hour="*"),
    },
    "meetings-reminder-48h": {
        "task": "apps.meetings.tasks.meetings_reminder_48h",
        "schedule": crontab(minute=10, hour="*"),
    },
    "meetings-reminder-4h": {
        "task": "apps.meetings.tasks.meetings_reminder_4h",
        "schedule": crontab(minute=20, hour="*"),
    },
    "meetings-reminder-30m": {
        "task": "apps.meetings.tasks.meetings_reminder_30m",
        "schedule": crontab(minute="*/15"),
    },
    "meetings-reminder-2h": {
        "task": "apps.meetings.tasks.meetings_reminder_2h",
        "schedule": crontab(minute=15, hour="*"),
    },
    "meetings-minutes-compliance-monitor": {
        "task": "apps.meetings.tasks.meetings_minutes_compliance_monitor",
        "schedule": crontab(minute=45, hour=7),
    },
    "notifications-behavioral-throttle": {
        "task": "apps.notifications.tasks.behavioral_notification_throttle",
        "schedule": crontab(minute=0, hour="*"),
    },
    "notifications-weekly-smart-digest": {
        "task": "apps.notifications.tasks.weekly_smart_digest",
        "schedule": crontab(minute=15, hour=7, day_of_week=1),
    },
    "notifications-reconciliation-summary": {
        "task": "apps.notifications.tasks.reconciliation_summary_to_treasurer",
        "schedule": crontab(minute=40, hour=22),
    },
    "ai-weekly-insights-report": {
        "task": "apps.ai.tasks.ai_weekly_insights_report_task",
        "schedule": crontab(minute=30, hour=7, day_of_week=1),
    },
    "ai-membership-risk-scoring": {
        "task": "apps.ai.tasks.ai_membership_risk_scoring_task",
        "schedule": crontab(minute=0, hour=3),
    },
    "ai-loan-default-prediction": {
        "task": "apps.ai.tasks.ai_loan_default_prediction_task",
        "schedule": crontab(minute=15, hour=3),
    },
    "ai-contribution-behavior-forecast": {
        "task": "apps.ai.tasks.ai_contribution_behavior_forecast_task",
        "schedule": crontab(minute=30, hour=3),
    },
    "ai-governance-health-score": {
        "task": "apps.ai.tasks.ai_governance_health_score_task",
        "schedule": crontab(minute=45, hour=3),
    },
    "ai-executive-summary": {
        "task": "apps.ai.tasks.ai_executive_summary_task",
        "schedule": crontab(minute=0, hour=6, day_of_month=1),
    },
    "ai-nightly-kb-reindex": {
        "task": "apps.ai.tasks.ai_nightly_kb_reindex_task",
        "schedule": crontab(minute=0, hour=1),
    },
    "ai-anomaly-scan": {
        "task": "apps.ai.tasks.ai_anomaly_scan_task",
        "schedule": crontab(minute=0, hour=2),
    },
    "security-lockout-cleanup": {
        "task": "apps.automations.tasks.security_lockout_cleanup",
        "schedule": crontab(minute=0, hour="*"),
    },
    "security-suspicious-scan": {
        "task": "apps.automations.tasks.security_suspicious_activity_scan",
        "schedule": crontab(minute=30, hour="*"),
    },
    "security-failed-login-alerts": {
        "task": "apps.automations.tasks.security_failed_login_alerts_job",
        "schedule": crontab(minute=45, hour="*"),
    },
    "security-clear-old-login-attempts": {
        "task": "apps.security.tasks.security_clear_old_login_attempts",
        "schedule": crontab(minute=10, hour=3),
    },
    "billing-process-subscription-lifecycle": {
        "task": "apps.billing.tasks.billing_process_subscription_lifecycle",
        "schedule": crontab(minute=0, hour=1),
    },
    "billing-retry-payments": {
        "task": "apps.billing.tasks.billing_retry_payments",
        "schedule": crontab(minute=0, hour="*/6"),
    },
    "billing-send-renewal-reminders": {
        "task": "apps.billing.tasks.billing_send_renewal_reminders",
        "schedule": crontab(minute=0, hour=8),
    },
    "billing-send-failed-payment-reminders": {
        "task": "apps.billing.tasks.billing_send_failed_payment_reminders",
        "schedule": crontab(minute=0, hour="*/12"),
    },
    "billing-send-credit-expiry-reminders": {
        "task": "apps.billing.tasks.billing_send_credit_expiry_reminders",
        "schedule": crontab(minute=15, hour=8),
    },
    "billing-reset-usage": {
        "task": "apps.billing.tasks.billing_reset_usage",
        "schedule": crontab(minute=30, hour=0),
    },
    "billing-cleanup-credit-reservations": {
        "task": "apps.billing.tasks.billing_cleanup_credit_reservations",
        "schedule": crontab(minute=30, hour="*"),
    },
}
PASSWORD_RESET_TOKEN_MINUTES = env.int("PASSWORD_RESET_TOKEN_MINUTES", default=30)
MEMBERSHIP_REQUEST_EXPIRY_DAYS = env.int("MEMBERSHIP_REQUEST_EXPIRY_DAYS", default=7)
MEMBERSHIP_REVIEW_REMINDER_HOURS = env.int(
    "MEMBERSHIP_REVIEW_REMINDER_HOURS",
    default=24,
)
REPORT_CACHE_TTL_SECONDS = env.int("REPORT_CACHE_TTL_SECONDS", default=300)

SITE_URL = env("SITE_URL", default="http://localhost:8000")
MPESA_USE_STUB = env.bool("MPESA_USE_STUB", default=True)
MPESA_ENVIRONMENT = env("MPESA_ENVIRONMENT", default="sandbox")
MPESA_CONSUMER_KEY = env("MPESA_CONSUMER_KEY", default="")
MPESA_CONSUMER_SECRET = env("MPESA_CONSUMER_SECRET", default="")
MPESA_SHORTCODE = env("MPESA_SHORTCODE", default="")
MPESA_PASSKEY = env("MPESA_PASSKEY", default="")
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

# Daraja (C2B/STK/B2C) canonical settings.
DARAJA_CONSUMER_KEY = env("DARAJA_CONSUMER_KEY", default=MPESA_CONSUMER_KEY)
DARAJA_CONSUMER_SECRET = env("DARAJA_CONSUMER_SECRET", default=MPESA_CONSUMER_SECRET)
DARAJA_SHORTCODE = env("DARAJA_SHORTCODE", default=MPESA_SHORTCODE)
DARAJA_PASSKEY = env("DARAJA_PASSKEY", default=MPESA_PASSKEY)
DARAJA_CALLBACK_BASE_URL = env("DARAJA_CALLBACK_BASE_URL", default=SITE_URL)
DARAJA_B2C_INITIATOR_NAME = env("DARAJA_B2C_INITIATOR_NAME", default="")
DARAJA_B2C_INITIATOR_PASSWORD = env("DARAJA_B2C_INITIATOR_PASSWORD", default="")
DARAJA_CERT_PATH = env("DARAJA_CERT_PATH", default="")
DARAJA_SECURITY_CREDENTIAL = env("DARAJA_SECURITY_CREDENTIAL", default="")
DARAJA_B2C_SHORTCODE = env("DARAJA_B2C_SHORTCODE", default=DARAJA_SHORTCODE)
DARAJA_ENV = env("DARAJA_ENV", default=MPESA_ENVIRONMENT)

BILLING_DEFAULT_GRACE_DAYS = env.int("BILLING_DEFAULT_GRACE_DAYS", default=7)
BILLING_ENFORCEMENT_MODE = env(
    "BILLING_ENFORCEMENT_MODE",
    default="hard_lock",
)
BILLING_UPGRADE_APPROVAL_THRESHOLD = env(
    "BILLING_UPGRADE_APPROVAL_THRESHOLD",
    default="50000",
)
BILLING_AUTO_RENEW_ENABLED = env.bool("BILLING_AUTO_RENEW_ENABLED", default=True)
BILLING_PAYMENT_RETRY_SCHEDULE = env.list(
    "BILLING_PAYMENT_RETRY_SCHEDULE",
    default=[1, 3, 7],
)
BILLING_ALLOW_ENTERPRISE_OVERRIDES = env.bool(
    "BILLING_ALLOW_ENTERPRISE_OVERRIDES",
    default=True,
)
BILLING_TAX_RATE = env("BILLING_TAX_RATE", default="0.00")
BILLING_METADATA_ENCRYPTION_KEY = env(
    "BILLING_METADATA_ENCRYPTION_KEY",
    default="",
)
BILLING_CREDIT_EXPIRY_DAYS = env.int(
    "BILLING_CREDIT_EXPIRY_DAYS",
    default=90,
)
BILLING_CREDIT_RESERVATION_TIMEOUT_MINUTES = env.int(
    "BILLING_CREDIT_RESERVATION_TIMEOUT_MINUTES",
    default=30,
)
REFERRAL_REWARD_TYPE = env("REFERRAL_REWARD_TYPE", default="trial_extension")
REFERRAL_REWARD_EXTENSION_DAYS = env.int(
    "REFERRAL_REWARD_EXTENSION_DAYS",
    default=7,
)
REFERRAL_REWARD_CREDIT_AMOUNT = env.int(
    "REFERRAL_REWARD_CREDIT_AMOUNT",
    default=1000,
)

LOG_LEVEL = env("LOG_LEVEL", default="INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "correlation_id": {
            "()": "core.logging.CorrelationIdFilter",
        }
    },
    "formatters": {
        "structured": {
            "format": (
                '{"time":"%(asctime)s","level":"%(levelname)s",'
                '"logger":"%(name)s","trace_id":"%(correlation_id)s",'
                '"message":"%(message)s"}'
            )
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "structured",
            "filters": ["correlation_id"],
        }
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        }
    },
}

SENTRY_DSN = env("SENTRY_DSN", default="")
SENTRY_TRACES_SAMPLE_RATE = env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.1)
if SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.django import DjangoIntegration
        from sentry_sdk.integrations.redis import RedisIntegration

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[
                DjangoIntegration(),
                CeleryIntegration(),
                RedisIntegration(),
            ],
            traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
            send_default_pii=False,
            environment=env("APP_ENV", default="development"),
        )
    except Exception:  # noqa: BLE001
        pass
