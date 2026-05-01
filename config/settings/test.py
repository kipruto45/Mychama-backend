from copy import deepcopy

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403,F401

DEBUG = False

USE_SQLITE_FOR_TESTS = env.bool("USE_SQLITE_FOR_TESTS", default=True)
if USE_SQLITE_FOR_TESTS:
    test_sqlite_name = env("TEST_SQLITE_NAME", default=":memory:")
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": test_sqlite_name,
            "TEST": {"NAME": test_sqlite_name},
        }
    }
else:
    DATABASES = deepcopy(DATABASES)
    default_db = DATABASES["default"]
    if default_db["ENGINE"] != "django.db.backends.postgresql":
        raise ImproperlyConfigured(
            "config.settings.test requires PostgreSQL when USE_SQLITE_FOR_TESTS is false."
        )

    default_db["CONN_MAX_AGE"] = 0
    default_db.setdefault("TEST", {})
    default_db["TEST"].setdefault(
        "NAME",
        env("TEST_DATABASE_NAME", default=f'test_{default_db["NAME"]}'),
    )

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
OTP_ALLOW_MOCK_DELIVERY = True
PRINT_OTP_IN_CONSOLE = False
SMS_PROVIDER = "console"
PUSH_NOTIFICATION_ENABLED = False
LOGIN_NEW_DEVICE_ALERT_ENABLED = False
HIBP_PASSWORD_CHECK_ENABLED = False
MPESA_USE_STUB = env.bool("MPESA_USE_STUB", default=True)

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "digital-chama-test",
    }
}

# Keep tests isolated from external infra and make async calls deterministic.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"

# Keep tests offline and filesystem-only for file uploads.
SUPABASE_USE_STORAGE = False
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
