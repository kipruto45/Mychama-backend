import hashlib
import re

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


class FlexibleRateThrottleMixin:
    EXTENDED_RATE_RE = re.compile(
        r"^\s*(?P<count>\d+)\s*/\s*(?:(?P<window>\d+)\s*)?(?P<unit>s|sec|second|seconds|m|min|minute|minutes|h|hr|hour|hours|d|day|days)\s*$",
        re.IGNORECASE,
    )
    UNIT_SECONDS = {
        "s": 1,
        "sec": 1,
        "second": 1,
        "seconds": 1,
        "m": 60,
        "min": 60,
        "minute": 60,
        "minutes": 60,
        "h": 3600,
        "hr": 3600,
        "hour": 3600,
        "hours": 3600,
        "d": 86400,
        "day": 86400,
        "days": 86400,
    }

    def parse_rate(self, rate):
        try:
            return super().parse_rate(rate)
        except (KeyError, ValueError, TypeError):
            if not rate:
                return (None, None)

            match = self.EXTENDED_RATE_RE.match(str(rate))
            if not match:
                raise

            count = int(match.group("count"))
            window = int(match.group("window") or 1)
            unit = match.group("unit").lower()
            return count, window * self.UNIT_SECONDS[unit]


class DefaultAnonThrottle(FlexibleRateThrottleMixin, AnonRateThrottle):
    scope = "anon"


class DefaultUserThrottle(FlexibleRateThrottleMixin, UserRateThrottle):
    scope = "user"


class LoginRateThrottle(FlexibleRateThrottleMixin, AnonRateThrottle):
    scope = "login"


class RegisterRateThrottle(FlexibleRateThrottleMixin, AnonRateThrottle):
    scope = "register"


class BaseIdentifierRateThrottle(FlexibleRateThrottleMixin, SimpleRateThrottle):
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


class PasswordResetRateThrottle(FlexibleRateThrottleMixin, AnonRateThrottle):
    scope = "password_reset"


class PasswordResetIdentifierRateThrottle(BaseIdentifierRateThrottle):
    scope = "password_reset_identifier"
    identifier_field = "identifier"


class OTPRequestRateThrottle(FlexibleRateThrottleMixin, AnonRateThrottle):
    scope = "otp_request"


class OTPIdentifierRateThrottle(BaseIdentifierRateThrottle):
    scope = "otp_identifier"
    identifier_field = "identifier"


class OTPVerifyRateThrottle(FlexibleRateThrottleMixin, AnonRateThrottle):
    scope = "otp_verify"


class OTPVerifyIdentifierRateThrottle(BaseIdentifierRateThrottle):
    scope = "otp_verify_identifier"
    identifier_field = "identifier"


class PaymentInitiationRateThrottle(FlexibleRateThrottleMixin, UserRateThrottle):
    scope = "payment_initiation"


class MpesaCallbackRateThrottle(FlexibleRateThrottleMixin, AnonRateThrottle):
    scope = "mpesa_callback"


class NotificationDispatchRateThrottle(FlexibleRateThrottleMixin, UserRateThrottle):
    scope = "notification_dispatch"


class ReportExportRateThrottle(FlexibleRateThrottleMixin, UserRateThrottle):
    scope = "report_export"


class IssueCreateRateThrottle(FlexibleRateThrottleMixin, UserRateThrottle):
    scope = "issue_create"


class IssueModerationRateThrottle(FlexibleRateThrottleMixin, UserRateThrottle):
    scope = "issue_moderation"


class AIChatRateThrottle(FlexibleRateThrottleMixin, UserRateThrottle):
    scope = "ai_chat"


class AIActionRateThrottle(FlexibleRateThrottleMixin, UserRateThrottle):
    scope = "ai_action"


# Backward-compatible aliases.
LoginThrottle = LoginRateThrottle
APIThrottle = DefaultUserThrottle
PasswordResetThrottle = PasswordResetRateThrottle
