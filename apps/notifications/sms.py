from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger(__name__)


def _mock_delivery_allowed() -> bool:
    return bool(
        getattr(settings, "DEBUG", False)
        or getattr(settings, "OTP_ALLOW_MOCK_DELIVERY", False)
        or os.environ.get("PYTEST_CURRENT_TEST")
    )


def _running_pytest() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


@dataclass
class SMSDeliveryResult:
    ok: bool
    provider: str
    provider_message_id: str = ""
    raw_response: dict | None = None


class BaseSMSProvider:
    provider_name = "base"

    def send(self, phone_number: str, message: str) -> SMSDeliveryResult:
        raise NotImplementedError


class ConsoleSMSProvider(BaseSMSProvider):
    provider_name = "console"

    def send(self, phone_number: str, message: str) -> SMSDeliveryResult:
        if not _mock_delivery_allowed():
            raise RuntimeError(
                "Console SMS provider is disabled outside development and tests."
            )
        logger.info(
            "SMS[%s] to=%s message=%s", self.provider_name, phone_number, message
        )
        return SMSDeliveryResult(ok=True, provider=self.provider_name, raw_response={})


class AfricasTalkingSMSProvider(BaseSMSProvider):
    provider_name = "africastalking"

    def __init__(self):
        self.username = getattr(settings, "AFRICAS_TALKING_USERNAME", "")
        self.api_key = getattr(settings, "AFRICAS_TALKING_API_KEY", "")
        self.sender_id = getattr(
            settings,
            "AFRICAS_TALKING_SENDER_ID",
            getattr(settings, "SMS_SENDER_ID", ""),
        )

    def send(self, phone_number: str, message: str) -> SMSDeliveryResult:
        if not self.username or not self.api_key:
            raise RuntimeError("Africa's Talking credentials are not configured.")

        try:
            import africastalking
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("africastalking package is not available.") from exc

        africastalking.initialize(self.username, self.api_key)
        sms = africastalking.SMS
        response = sms.send(
            message=message,
            recipients=[phone_number],
            sender_id=self.sender_id or None,
        )
        provider_message_id = ""
        recipients = response.get("SMSMessageData", {}).get("Recipients", [])
        if recipients:
            provider_message_id = recipients[0].get("messageId", "") or ""

        return SMSDeliveryResult(
            ok=True,
            provider=self.provider_name,
            provider_message_id=provider_message_id,
            raw_response=response,
        )


def get_sms_provider() -> BaseSMSProvider:
    if _running_pytest():
        return ConsoleSMSProvider()
    provider_name = getattr(settings, "SMS_PROVIDER", "console").lower().replace("_", "")
    if provider_name == "africastalking":
        return AfricasTalkingSMSProvider()
    return ConsoleSMSProvider()


def send_sms_message(phone_number: str, message: str) -> SMSDeliveryResult:
    provider = get_sms_provider()
    return provider.send(phone_number=phone_number, message=message)


def send_sms(phone_number: str, message: str) -> SMSDeliveryResult:
    """
    Backwards-compatible alias used by older management commands.
    """
    return send_sms_message(phone_number=phone_number, message=message)
