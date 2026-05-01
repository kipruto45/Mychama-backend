"""
WhatsApp Business API Notification Service

This module handles sending WhatsApp messages via the WhatsApp Business API.
Very popular in Kenya for business communications.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import requests
from django.conf import settings

from core.safe_http import OutboundPolicy, UnsafeOutboundRequest, safe_request

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
class WhatsAppDeliveryResult:
    ok: bool
    provider: str
    provider_message_id: str = ""
    raw_response: dict | None = None


class BaseWhatsAppProvider:
    provider_name = "base"

    def send(self, phone_number: str, message: str, template_name: str = None) -> WhatsAppDeliveryResult:
        raise NotImplementedError


class ConsoleWhatsAppProvider(BaseWhatsAppProvider):
    """Console provider for development/testing."""

    provider_name = "console_whatsapp"

    def send(self, phone_number: str, message: str, template_name: str = None) -> WhatsAppDeliveryResult:
        if not _mock_delivery_allowed():
            raise RuntimeError(
                "Console WhatsApp provider is disabled outside development and tests."
            )
        logger.info(
            "WhatsApp[%s] to=%s message=%s template=%s",
            self.provider_name,
            phone_number,
            message,
            template_name,
        )
        return WhatsAppDeliveryResult(
            ok=True,
            provider=self.provider_name,
            raw_response={},
        )


class WhatsAppBusinessAPIProvider(BaseWhatsAppProvider):
    """
    WhatsApp Business API provider using the official Meta API.
    
    Requires:
    - WHATSAPP_BUSINESS_ACCOUNT_ID
    - WHATSAPP_PHONE_NUMBER_ID
    - WHATSAPP_ACCESS_TOKEN
    
    Documentation: https://developers.facebook.com/docs/whatsapp
    """

    provider_name = "whatsapp_business_api"

    def __init__(self):
        self.account_id = getattr(settings, "WHATSAPP_BUSINESS_ACCOUNT_ID", "")
        self.phone_number_id = getattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "")
        self.access_token = getattr(settings, "WHATSAPP_ACCESS_TOKEN", "")
        self.api_version = getattr(settings, "WHATSAPP_API_VERSION", "v18.0")
        self.base_url = f"https://graph.facebook.com/{self.api_version}"

    def _format_phone_number(self, phone_number: str) -> str:
        """Format phone number for WhatsApp API (must include country code)."""
        # Remove any whitespace or special characters
        cleaned = "".join(c for c in phone_number if c.isdigit() or c == "+")
        
        # Add country code if not present (assume Kenya +254)
        if not cleaned.startswith("+"):
            if cleaned.startswith("0"):
                cleaned = "+254" + cleaned[1:]
            elif cleaned.startswith("7") or cleaned.startswith("1"):
                cleaned = "+254" + cleaned
        
        return cleaned

    def send(
        self,
        phone_number: str,
        message: str,
        template_name: str = None,
    ) -> WhatsAppDeliveryResult:
        if not self.access_token or not self.phone_number_id:
            raise RuntimeError("WhatsApp Business API credentials are not configured.")


        formatted_phone = self._format_phone_number(phone_number)
        
        # If template_name is provided, send template message
        if template_name:
            return self._send_template_message(formatted_phone, template_name)
        
        # Otherwise send text message
        return self._send_text_message(formatted_phone, message)

    def _send_text_message(self, phone_number: str, message: str) -> WhatsAppDeliveryResult:
        """Send a text message via WhatsApp API."""
        url = f"{self.base_url}/{self.phone_number_id}/messages"
        
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "text",
            "text": {"body": message},
        }

        try:
            response = safe_request(
                "POST",
                url,
                json=payload,
                headers=headers,
                policy=OutboundPolicy(allowed_hosts={"graph.facebook.com"}),
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            
            message_id = ""
            if "messages" in data and len(data["messages"]) > 0:
                message_id = data["messages"][0].get("id", "")

            return WhatsAppDeliveryResult(
                ok=True,
                provider=self.provider_name,
                provider_message_id=message_id,
                raw_response=data,
            )
        except (requests.exceptions.RequestException, UnsafeOutboundRequest):
            logger.warning("WhatsApp delivery failed")
            return WhatsAppDeliveryResult(
                ok=False,
                provider=self.provider_name,
                raw_response={"error": "delivery_failed"},
            )

    def _send_template_message(
        self,
        phone_number: str,
        template_name: str,
    ) -> WhatsAppDeliveryResult:
        """Send a pre-approved template message via WhatsApp API."""
        url = f"{self.base_url}/{self.phone_number_id}/messages"
        
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": "en_US"},
            },
        }

        try:
            response = safe_request(
                "POST",
                url,
                json=payload,
                headers=headers,
                policy=OutboundPolicy(allowed_hosts={"graph.facebook.com"}),
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            
            message_id = ""
            if "messages" in data and len(data["messages"]) > 0:
                message_id = data["messages"][0].get("id", "")

            return WhatsAppDeliveryResult(
                ok=True,
                provider=self.provider_name,
                provider_message_id=message_id,
                raw_response=data,
            )
        except (requests.exceptions.RequestException, UnsafeOutboundRequest):
            logger.warning("WhatsApp template delivery failed")
            return WhatsAppDeliveryResult(
                ok=False,
                provider=self.provider_name,
                raw_response={"error": "delivery_failed"},
            )

    def send_template_with_variables(
        self,
        phone_number: str,
        template_name: str,
        variables: list[str],
    ) -> WhatsAppDeliveryResult:
        """Send a template message with variable parameters."""
        formatted_phone = self._format_phone_number(phone_number)
        
        url = f"{self.base_url}/{self.phone_number_id}/messages"
        
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        
        # Build components for template with variables
        components = []
        if variables:
            component_params = []
            for _i, var in enumerate(variables):
                component_params.append({"type": "parameter", "parameter": "text", "text": var})
            
            components.append({
                "type": "body",
                "parameters": component_params,
            })
        
        payload = {
            "messaging_product": "whatsapp",
            "to": formatted_phone,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": "en_US"},
            },
        }
        
        if components:
            payload["template"]["components"] = components

        try:
            response = safe_request(
                "POST",
                url,
                json=payload,
                headers=headers,
                policy=OutboundPolicy(allowed_hosts={"graph.facebook.com"}),
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            
            message_id = ""
            if "messages" in data and len(data["messages"]) > 0:
                message_id = data["messages"][0].get("id", "")

            return WhatsAppDeliveryResult(
                ok=True,
                provider=self.provider_name,
                provider_message_id=message_id,
                raw_response=data,
            )
        except (requests.exceptions.RequestException, UnsafeOutboundRequest):
            logger.warning("WhatsApp template delivery failed")
            return WhatsAppDeliveryResult(
                ok=False,
                provider=self.provider_name,
                raw_response={"error": "delivery_failed"},
            )


def get_whatsapp_provider() -> BaseWhatsAppProvider:
    """Get the configured WhatsApp provider."""
    if _running_pytest():
        return ConsoleWhatsAppProvider()
    
    provider_name = getattr(settings, "WHATSAPP_PROVIDER", "console").lower()
    if provider_name == "whatsapp_business_api":
        return WhatsAppBusinessAPIProvider()
    
    return ConsoleWhatsAppProvider()


def send_whatsapp_message(
    phone_number: str,
    message: str,
    template_name: str = None,
) -> WhatsAppDeliveryResult:
    """Send a WhatsApp message."""
    provider = get_whatsapp_provider()
    return provider.send(phone_number=phone_number, message=message, template_name=template_name)
