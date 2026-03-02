from copy import deepcopy

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403,F401

DEBUG = False

DATABASES = deepcopy(DATABASES)
default_db = DATABASES["default"]
if default_db["ENGINE"] != "django.db.backends.postgresql":
    raise ImproperlyConfigured(
        "config.settings.test requires PostgreSQL. Set DATABASE_URL to a PostgreSQL database."
    )

default_db["CONN_MAX_AGE"] = 0
default_db.setdefault("TEST", {})
default_db["TEST"].setdefault(
    "NAME",
    env("TEST_DATABASE_NAME", default=f'test_{default_db["NAME"]}'),
)

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
OTP_ALLOW_MOCK_DELIVERY = True
SMS_PROVIDER = "console"

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
