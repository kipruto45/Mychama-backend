from .base import *  # noqa: F403,F401

DEBUG = env.bool("DEBUG", default=True)

# Use .env file settings - ALLOWED_HOSTS should come from env
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal", "web", "frontend", "localhost:8888"])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[
    "http://localhost:3000", 
    "http://localhost:8000", 
    "http://localhost:8888",
    "http://127.0.0.1:3000", 
    "http://127.0.0.1:8000",
    "http://127.0.0.1:8888"
])

CORS_ALLOW_ALL_ORIGINS = env.bool("CORS_ALLOW_ALL_ORIGINS", default=True)

# Use console email backend for local development - emails print to terminal
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")

ENABLE_FRONTEND_ROUTES = env.bool("ENABLE_FRONTEND_ROUTES", default=False)

# Database configuration - PostgreSQL default for local development
# Default connects to Docker postgres on port 5432 (matches docker-compose setup)
# For local testing without Docker, you may need to change 'postgres' to 'localhost'
# or set up a local PostgreSQL instance on port 5432
_db_url = env("DATABASE_URL", default="postgresql://digital_chama:change-this-production-db-password@localhost:5432/digital_chama")
DATABASES = {"default": env.db("DATABASE_URL", default=_db_url)}

if DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql":
    DATABASES["default"]["CONN_MAX_AGE"] = 600

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

# OTP configuration - default to sending via both SMS and email for local testing
OTP_DEFAULT_DELIVERY_METHOD = env("OTP_DEFAULT_DELIVERY_METHOD", default="both")
OTP_ALLOW_MOCK_DELIVERY = env.bool("OTP_ALLOW_MOCK_DELIVERY", default=True)

# Development-only OTP settings
# Print OTP to console in development (SECURE - only works when DEBUG=True)
PRINT_OTP_IN_CONSOLE = env.bool("PRINT_OTP_IN_CONSOLE", default=True)
# Enable debug endpoint to retrieve OTP (SECURE - only works when DEBUG=True)
ENABLE_DEV_OTP_ENDPOINT = env.bool("ENABLE_DEV_OTP_ENDPOINT", default=True)
# Secret token for dev OTP endpoint (should be changed in production)
DEV_OTP_SECRET_TOKEN = env("DEV_OTP_SECRET_TOKEN", default="super-secret-dev-token-12345")

# Celery configuration for development
# Use eager task execution (no async needed)
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=True)

# Logging - more verbose for development
LOGGING["root"]["level"] = "INFO"
LOGGING["loggers"]["django"]["level"] = "DEBUG"
