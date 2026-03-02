"""
Telegram Bot Notification Service

This module handles sending messages via Telegram Bot API.
Popular alternative to WhatsApp for automated notifications.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

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
class TelegramDeliveryResult:
    ok: bool
    provider: str
    provider_message_id: str = ""
    raw_response: dict | None = None


class BaseTelegramProvider:
    provider_name = "base"

    def send_message(
        self,
        chat_id: str,
        text: str,
        parse_mode: str = None,
        reply_markup: dict = None,
    ) -> TelegramDeliveryResult:
        raise NotImplementedError


class ConsoleTelegramProvider(BaseTelegramProvider):
    """Console provider for development/testing."""

    provider_name = "console_telegram"

    def send_message(
        self,
        chat_id: str,
        text: str,
        parse_mode: str = None,
        reply_markup: dict = None,
    ) -> TelegramDeliveryResult:
        if not _mock_delivery_allowed():
            raise RuntimeError(
                "Console Telegram provider is disabled outside development and tests."
            )
        logger.info(
            "Telegram[%s] chat_id=%s text=%s parse_mode=%s",
            self.provider_name,
            chat_id,
            text,
            parse_mode,
        )
        return TelegramDeliveryResult(
            ok=True,
            provider=self.provider_name,
            raw_response={},
        )


class TelegramBotAPIProvider(BaseTelegramProvider):
    """
    Telegram Bot API provider.
    
    Requires:
    - TELEGRAM_BOT_TOKEN
    
    To get a bot token:
    1. Start a chat with @BotFather on Telegram
    2. Use /newbot command to create a new bot
    3. Copy the provided bot token
    
    Documentation: https://core.telegram.org/bots/api
    """

    provider_name = "telegram_bot_api"

    def __init__(self):
        self.token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
        self.api_url = f"https://api.telegram.org/bot{self.token}"

    def send_message(
        self,
        chat_id: str,
        text: str,
        parse_mode: str = "Markdown",
        reply_markup: dict = None,
    ) -> TelegramDeliveryResult:
        if not self.token:
            raise RuntimeError("Telegram bot token is not configured.")

        import requests

        url = f"{self.api_url}/sendMessage"
        
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            response = requests.post(url, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            if not data.get("ok"):
                return TelegramDeliveryResult(
                    ok=False,
                    provider=self.provider_name,
                    raw_response=data,
                )

            message_id = data.get("result", {}).get("message_id", "")

            return TelegramDeliveryResult(
                ok=True,
                provider=self.provider_name,
                provider_message_id=str(message_id),
                raw_response=data,
            )
        except requests.exceptions.RequestException as e:
            logger.error(f"Telegram API error: {e}")
            return TelegramDeliveryResult(
                ok=False,
                provider=self.provider_name,
                raw_response={"error": str(e)},
            )

    def send_html_message(self, chat_id: str, html: str) -> TelegramDeliveryResult:
        """Send HTML formatted message."""
        return self.send_message(chat_id=chat_id, text=html, parse_mode="HTML")

    def send_markdown_message(self, chat_id: str, markdown: str) -> TelegramDeliveryResult:
        """Send Markdown formatted message."""
        return self.send_message(chat_id=chat_id, text=markdown, parse_mode="Markdown")

    def send_photo(
        self,
        chat_id: str,
        photo_url: str,
        caption: str = None,
    ) -> TelegramDeliveryResult:
        """Send a photo with optional caption."""
        if not self.token:
            raise RuntimeError("Telegram bot token is not configured.")

        import requests

        url = f"{self.api_url}/sendPhoto"
        
        payload = {
            "chat_id": chat_id,
            "photo": photo_url,
        }
        
        if caption:
            payload["caption"] = caption

        try:
            response = requests.post(url, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            if not data.get("ok"):
                return TelegramDeliveryResult(
                    ok=False,
                    provider=self.provider_name,
                    raw_response=data,
                )

            message_id = data.get("result", {}).get("message_id", "")

            return TelegramDeliveryResult(
                ok=True,
                provider=self.provider_name,
                provider_message_id=str(message_id),
                raw_response=data,
            )
        except requests.exceptions.RequestException as e:
            logger.error(f"Telegram API error: {e}")
            return TelegramDeliveryResult(
                ok=False,
                provider=self.provider_name,
                raw_response={"error": str(e)},
            )

    def send_document(
        self,
        chat_id: str,
        document_url: str,
        caption: str = None,
    ) -> TelegramDeliveryResult:
        """Send a document with optional caption."""
        if not self.token:
            raise RuntimeError("Telegram bot token is not configured.")

        import requests

        url = f"{self.api_url}/sendDocument"
        
        payload = {
            "chat_id": chat_id,
            "document": document_url,
        }
        
        if caption:
            payload["caption"] = caption

        try:
            response = requests.post(url, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            if not data.get("ok"):
                return TelegramDeliveryResult(
                    ok=False,
                    provider=self.provider_name,
                    raw_response=data,
                )

            message_id = data.get("result", {}).get("message_id", "")

            return TelegramDeliveryResult(
                ok=True,
                provider=self.provider_name,
                provider_message_id=str(message_id),
                raw_response=data,
            )
        except requests.exceptions.RequestException as e:
            logger.error(f"Telegram API error: {e}")
            return TelegramDeliveryResult(
                ok=False,
                provider=self.provider_name,
                raw_response={"error": str(e)},
            )

    def create_inline_keyboard(
        self,
        buttons: list[list[dict]],
    ) -> dict:
        """
        Create inline keyboard markup.
        
        Args:
            buttons: List of button rows, each row is a list of button dicts
                    with 'text' and 'url' or 'callback_data' keys
        
        Example:
            [
                [{"text": "Approve", "callback_data": "approve_123"}, {"text": "Reject", "callback_data": "reject_123"}],
                [{"text": "View Details", "url": "https://example.com/123"}]
            ]
        """
        keyboard = []
        for row in buttons:
            keyboard_row = []
            for button in row:
                keyboard_row.append({
                    "text": button["text"],
                    **{k: v for k, v in button.items() if k != "text"}
                })
            keyboard.append(keyboard_row)
        
        return {"inline_keyboard": keyboard}


def get_telegram_provider() -> BaseTelegramProvider:
    """Get the configured Telegram provider."""
    if _running_pytest():
        return ConsoleTelegramProvider()
    
    provider_name = getattr(settings, "TELEGRAM_PROVIDER", "console").lower()
    if provider_name == "telegram_bot_api":
        return TelegramBotAPIProvider()
    
    return ConsoleTelegramProvider()


def send_telegram_message(
    chat_id: str,
    text: str,
    parse_mode: str = "Markdown",
) -> TelegramDeliveryResult:
    """Send a Telegram message."""
    provider = get_telegram_provider()
    return provider.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)


def send_telegram_photo(
    chat_id: str,
    photo_url: str,
    caption: str = None,
) -> TelegramDeliveryResult:
    """Send a photo via Telegram."""
    provider = get_telegram_provider()
    return provider.send_photo(chat_id=chat_id, photo_url=photo_url, caption=caption)
