from __future__ import annotations

import os
from dataclasses import dataclass

from django.conf import settings
from django.core.mail import get_connection
from django.core.mail import send_mail


@dataclass
class EmailDeliveryResult:
    ok: bool
    provider: str
    sent_count: int = 0
    provider_message_id: str = ""
    raw_response: dict | None = None


def _mock_delivery_allowed() -> bool:
    return bool(
        getattr(settings, "DEBUG", False)
        or getattr(settings, "OTP_ALLOW_MOCK_DELIVERY", False)
        or os.environ.get("PYTEST_CURRENT_TEST")
    )


def _running_pytest() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


class BaseEmailProvider:
    provider_name = "base"

    def send(
        self,
        *,
        subject: str,
        recipient_list: list[str],
        body: str,
        html_body: str = "",
    ) -> EmailDeliveryResult:
        raise NotImplementedError


class DjangoEmailProvider(BaseEmailProvider):
    provider_name = "django_email_backend"

    def send(
        self,
        *,
        subject: str,
        recipient_list: list[str],
        body: str,
        html_body: str = "",
    ) -> EmailDeliveryResult:
        email_backend = getattr(settings, "EMAIL_BACKEND", "")
        if (
            email_backend == "django.core.mail.backends.console.EmailBackend"
            and not _mock_delivery_allowed()
        ):
            raise RuntimeError(
                "Console email backend is disabled outside development and tests."
            )

        sent_count = send_mail(
            subject=subject or "Notification",
            message=body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"),
            recipient_list=recipient_list,
            html_message=html_body or None,
            connection=(
                get_connection("django.core.mail.backends.locmem.EmailBackend")
                if _running_pytest()
                else None
            ),
            fail_silently=False,
        )
        return EmailDeliveryResult(
            ok=sent_count > 0,
            provider=self.provider_name,
            sent_count=sent_count,
            raw_response={"sent_count": sent_count},
        )


class SendGridEmailProvider(BaseEmailProvider):
    provider_name = "sendgrid"

    def __init__(self):
        self.api_key = getattr(settings, "SENDGRID_API_KEY", "")
        self.from_email = getattr(
            settings,
            "SENDGRID_FROM_EMAIL",
            getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"),
        )

    def send(
        self,
        *,
        subject: str,
        recipient_list: list[str],
        body: str,
        html_body: str = "",
    ) -> EmailDeliveryResult:
        if not self.api_key:
            raise RuntimeError("SendGrid credentials are not configured.")

        try:
            from sendgrid import SendGridAPIClient
            from sendgrid.helpers.mail import Mail
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("sendgrid package is not available.") from exc

        message = Mail(
            from_email=self.from_email,
            to_emails=recipient_list,
            subject=subject or "Notification",
            plain_text_content=body,
        )
        if html_body:
            message.html_content = html_body
        response = SendGridAPIClient(self.api_key).send(message)
        if not 200 <= response.status_code < 300:
            raise RuntimeError(
                f"SendGrid delivery failed with status {response.status_code}."
            )

        provider_message_id = (
            response.headers.get("X-Message-Id", "")
            or response.headers.get("X-Message-ID", "")
        )
        return EmailDeliveryResult(
            ok=True,
            provider=self.provider_name,
            sent_count=len(recipient_list),
            provider_message_id=provider_message_id,
            raw_response={"status_code": response.status_code},
        )


class MailgunEmailProvider(BaseEmailProvider):
    """Mailgun SMTP email provider."""
    provider_name = "mailgun"

    def __init__(self):
        self.api_key = getattr(settings, "MAILGUN_API_KEY", "")
        self.domain = getattr(settings, "MAILGUN_DOMAIN", "")
        self.from_email = getattr(
            settings,
            "MAILGUN_FROM_EMAIL",
            getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"),
        )
        # SMTP configuration
        self.smtp_host = getattr(settings, "MAILGUN_SMTP_HOST", "smtp.mailgun.org")
        self.smtp_port = getattr(settings, "MAILGUN_SMTP_PORT", 587)
        self.smtp_username = getattr(settings, "MAILGUN_SMTP_USERNAME", "")
        self.smtp_password = getattr(settings, "MAILGUN_SMTP_PASSWORD", "")

    def send(
        self,
        *,
        subject: str,
        recipient_list: list[str],
        body: str,
        html_body: str = "",
    ) -> EmailDeliveryResult:
        if not self.smtp_username or not self.smtp_password:
            raise RuntimeError("Mailgun SMTP credentials are not configured.")

        try:
            from django.core.mail.backends.smtp import EmailBackend
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("SMTP backend not available.") from exc

        # Create SMTP connection
        connection = EmailBackend(
            host=self.smtp_host,
            port=self.smtp_port,
            username=self.smtp_username,
            password=self.smtp_password,
            use_tls=True,
            fail_silently=False,
        )

        sent_count = send_mail(
            subject=subject or "Notification",
            message=body,
            from_email=self.from_email,
            recipient_list=recipient_list,
            html_message=html_body or None,
            connection=connection,
            fail_silently=False,
        )

        return EmailDeliveryResult(
            ok=sent_count > 0,
            provider=self.provider_name,
            sent_count=sent_count,
            raw_response={"sent_count": sent_count},
        )


def get_email_provider() -> BaseEmailProvider:
    if _running_pytest():
        return DjangoEmailProvider()
    provider_name = getattr(settings, "EMAIL_PROVIDER", "django").lower()
    if provider_name == "sendgrid":
        return SendGridEmailProvider()
    if provider_name == "mailgun":
        return MailgunEmailProvider()
    return DjangoEmailProvider()


def send_email_message(
    *,
    subject: str,
    recipient_list: list[str],
    body: str,
    html_body: str = "",
) -> EmailDeliveryResult:
    provider = get_email_provider()
    return provider.send(
        subject=subject,
        recipient_list=recipient_list,
        body=body,
        html_body=html_body,
    )
