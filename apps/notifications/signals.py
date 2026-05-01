"""
Startup signal to verify notification credentials.

This module verifies that all required notification credentials
are properly configured when Django starts up.
"""
import logging

from django.conf import settings
from django.core.checks import Error, Tags, register

logger = logging.getLogger(__name__)


@register(Tags.security)
def check_notification_credentials(app_configs, **kwargs):
    """
    Check that notification credentials are properly configured.
    
    This function is registered as a Django system check and runs
    when Django starts up or when checks are explicitly run.
    
    Returns:
        List of Error objects if there are configuration issues
    """
    errors = []
    
    # Skip all checks if DEBUG=True and mock delivery is allowed
    debug = getattr(settings, "DEBUG", False)
    allow_mock = getattr(settings, "OTP_ALLOW_MOCK_DELIVERY", False)
    app_env = getattr(settings, "APP_ENV", "development")
    
    if debug and allow_mock:
        logger.info(
            "Running in DEBUG mode with mock delivery enabled - "
            "skipping credential verification"
        )
        return errors
    
    # Check email credentials
    email_backend = getattr(settings, "EMAIL_BACKEND", None)
    required_email_settings = [
        "EMAIL_HOST",
        "EMAIL_PORT",
        "EMAIL_HOST_USER",
        "EMAIL_HOST_PASSWORD",
        "DEFAULT_FROM_EMAIL",
    ]
    
    if email_backend and "smtp" in email_backend.lower():
        missing_email = []
        for setting in required_email_settings:
            value = getattr(settings, setting, None)
            if not value:
                missing_email.append(setting)
        
        if missing_email:
            errors.append(
                Error(
                    f"Missing required email settings: {', '.join(missing_email)}",
                    id="notifications.E001",
                    msg=f"The following email settings are not configured: {', '.join(missing_email)}",
                    hint="Configure these settings in your environment or .env file",
                )
            )
    
    # Check SMS credentials
    sms_provider = getattr(settings, "SMS_PROVIDER", None)
    required_sms_settings = [
        "AFRICAS_TALKING_API_KEY",
        "AFRICAS_TALKING_USERNAME",
    ]
    
    if sms_provider == "africastalking":
        missing_sms = []
        for setting in required_sms_settings:
            value = getattr(settings, setting, None)
            if not value:
                missing_sms.append(setting)
        
        if missing_sms:
            errors.append(
                Error(
                    f"Missing required SMS settings: {', '.join(missing_sms)}",
                    id="notifications.E002",
                    msg=f"The following SMS settings are not configured: {', '.join(missing_sms)}",
                    hint="Configure these settings in your environment or .env file",
                )
            )
    
    # Check production safety
    if app_env == "production":
        # In production, certain settings must be properly configured
        if debug:
            errors.append(
                Error(
                    "DEBUG is True in production",
                    id="notifications.E003",
                    msg="DEBUG must be False in production",
                    hint="Set DEBUG=False in production environment",
                )
            )
        
        if allow_mock:
            errors.append(
                Error(
                    "OTP_ALLOW_MOCK_DELIVERY is True in production",
                    id="notifications.E004",
                    msg="OTP_ALLOW_MOCK_DELIVERY must be False in production",
                    hint="Set OTP_ALLOW_MOCK_DELIVERY=False in production environment",
                )
            )
        
        # Check email uses TLS
        if not getattr(settings, "EMAIL_USE_TLS", True):
            errors.append(
                Error(
                    "EMAIL_USE_TLS is disabled in production",
                    id="notifications.E005",
                    msg="EMAIL_USE_TLS must be True in production",
                    hint="Set EMAIL_USE_TLS=True in production environment",
                )
            )
        
        # Check secure cookies
        if not getattr(settings, "SESSION_COOKIE_SECURE", True):
            errors.append(
                Error(
                    "SESSION_COOKIE_SECURE is False in production",
                    id="notifications.E006",
                    msg="SESSION_COOKIE_SECURE must be True in production",
                    hint="Set SESSION_COOKIE_SECURE=True in production environment",
                )
            )
    
    return errors


def verify_credentials_on_startup():
    """
    Verify credentials on Django startup.
    
    This function can be called from AppConfig.ready() to
    verify credentials when the application starts.
    """
    from apps.notifications.services import (
        EmailConfigurationError,
        EmailService,
        SMSConfigurationError,
        SMSService,
    )
    
    debug = getattr(settings, "DEBUG", False)
    allow_mock = getattr(settings, "OTP_ALLOW_MOCK_DELIVERY", False)
    app_env = getattr(settings, "APP_ENV", "development")
    
    if debug and allow_mock:
        logger.info("Skipping credential verification in DEBUG mode with mock enabled")
        return
    
    # Verify email
    try:
        EmailService.validate_configuration()
        logger.info("Email credentials verified successfully")
    except EmailConfigurationError as e:
        logger.error(f"Email configuration error: {e}")
        if app_env == "production":
            raise
    
    # Verify SMS
    try:
        SMSService.validate_configuration()
        logger.info("SMS credentials verified successfully")
    except SMSConfigurationError as e:
        logger.error(f"SMS configuration error: {e}")
        if app_env == "production":
            raise
