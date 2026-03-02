import hashlib

from rest_framework.throttling import (
    AnonRateThrottle,
    SimpleRateThrottle,
    UserRateThrottle,
)

from core.utils import normalize_kenyan_phone


def _normalize_identifier(raw_value: str | None) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    if "@" in value:
        return value.lower()
    try:
        return normalize_kenyan_phone(value)
    except ValueError:
        return value.lower()


class DefaultAnonThrottle(AnonRateThrottle):
    scope = "anon"


class DefaultUserThrottle(UserRateThrottle):
    scope = "user"


class LoginRateThrottle(AnonRateThrottle):
    scope = "login"


class RegisterRateThrottle(AnonRateThrottle):
    scope = "register"


class BaseIdentifierRateThrottle(SimpleRateThrottle):
    identifier_field = ""
    cache_format = "throttle_%(scope)s_%(ident)s"

    def get_identifier(self, request):
        if not self.identifier_field:
            return ""
        return _normalize_identifier(request.data.get(self.identifier_field))

    def get_cache_key(self, request, view):
        identifier = self.get_identifier(request)
        if not identifier:
            return None
        identifier_hash = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
        return self.cache_format % {
            "scope": self.scope,
            "ident": identifier_hash,
        }


class LoginIdentifierRateThrottle(BaseIdentifierRateThrottle):
    scope = "login_identifier"
    identifier_field = "phone"


class RegisterIdentifierRateThrottle(BaseIdentifierRateThrottle):
    scope = "register_identifier"
    identifier_field = "phone"


class PasswordResetRateThrottle(AnonRateThrottle):
    scope = "password_reset"


class PasswordResetIdentifierRateThrottle(BaseIdentifierRateThrottle):
    scope = "password_reset_identifier"
    identifier_field = "identifier"


class OTPRequestRateThrottle(AnonRateThrottle):
    scope = "otp_request"


class OTPIdentifierRateThrottle(BaseIdentifierRateThrottle):
    scope = "otp_identifier"
    identifier_field = "phone"


class PaymentInitiationRateThrottle(UserRateThrottle):
    scope = "payment_initiation"


class MpesaCallbackRateThrottle(AnonRateThrottle):
    scope = "mpesa_callback"


class NotificationDispatchRateThrottle(UserRateThrottle):
    scope = "notification_dispatch"


class ReportExportRateThrottle(UserRateThrottle):
    scope = "report_export"


class IssueCreateRateThrottle(UserRateThrottle):
    scope = "issue_create"


class IssueModerationRateThrottle(UserRateThrottle):
    scope = "issue_moderation"


class AIChatRateThrottle(UserRateThrottle):
    scope = "ai_chat"


class AIActionRateThrottle(UserRateThrottle):
    scope = "ai_action"


# Backward-compatible aliases.
LoginThrottle = LoginRateThrottle
APIThrottle = DefaultUserThrottle
PasswordResetThrottle = PasswordResetRateThrottle
