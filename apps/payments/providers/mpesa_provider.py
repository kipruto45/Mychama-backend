"""
M-Pesa Payment Provider Implementation.

Implements the payment provider interface for M-Pesa (Safaricom).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import uuid
from decimal import Decimal
from typing import Any

import requests
from django.conf import settings

from core.safe_http import OutboundPolicy, UnsafeOutboundRequest, safe_request

from .unified_base import (
    PaymentIntent,
    PaymentProvider,
    PaymentProviderAuthenticationError,
    PaymentProviderError,
    PaymentProviderInvalidRequestError,
    PaymentProviderNetworkError,
    PaymentResult,
    WebhookVerificationResult,
)

logger = logging.getLogger(__name__)


class MpesaProvider(PaymentProvider):
    """M-Pesa payment provider implementation."""

    def __init__(self):
        self.environment = getattr(settings, "MPESA_ENVIRONMENT", "sandbox")
        self.use_stub = bool(getattr(settings, "MPESA_USE_STUB", True))
        self.consumer_key = getattr(settings, "MPESA_CONSUMER_KEY", "")
        self.consumer_secret = getattr(settings, "MPESA_CONSUMER_SECRET", "")
        self.passkey = getattr(settings, "MPESA_PASSKEY", "")
        self.shortcode = getattr(settings, "MPESA_SHORTCODE", "")
        self.callback_url = getattr(settings, "MPESA_CALLBACK_URL", "")
        self.signature_header = getattr(settings, "MPESA_CALLBACK_SIGNATURE_HEADER", "X-MPESA-SIGNATURE")

        if not self.use_stub and (not self.consumer_key or not self.consumer_secret):
            raise PaymentProviderError("M-Pesa credentials not configured")

    @property
    def provider_name(self) -> str:
        return "safaricom"

    @property
    def payment_method(self) -> str:
        return "mpesa"

    @property
    def supported_currencies(self) -> list[str]:
        return ["KES"]

    @property
    def _base_url(self) -> str:
        # Default Safaricom endpoints (allow env override by defining MPESA_BASE_URL in settings).
        override = getattr(settings, "MPESA_BASE_URL", "") or ""
        if override.strip():
            return override.strip().rstrip("/")
        env = str(self.environment or "sandbox").strip().lower()
        return ("https://api.safaricom.co.ke" if env == "production" else "https://sandbox.safaricom.co.ke").rstrip("/")

    def _access_token(self) -> str:
        if self.use_stub:
            return "stub-token"

        policy = OutboundPolicy(
            allowed_hosts={
                "api.safaricom.co.ke",
                "sandbox.safaricom.co.ke",
                (self._base_url.split("://", 1)[-1].split("/", 1)[0] or "").strip(),
            }
        )

        url = f"{self._base_url}/oauth/v1/generate?grant_type=client_credentials"
        try:
            response = safe_request(
                "GET",
                url,
                auth=(self.consumer_key, self.consumer_secret),
                policy=policy,
                timeout=15,
            )
        except (requests.RequestException, UnsafeOutboundRequest) as exc:  # pragma: no cover
            raise PaymentProviderNetworkError("Failed to connect to M-Pesa OAuth endpoint") from exc

        if response.status_code != 200:
            raise PaymentProviderAuthenticationError(
                "Failed to authenticate with M-Pesa",
                provider_error_code=str(response.status_code),
                provider_error_message=response.text[:300],
            )

        payload = response.json()
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise PaymentProviderAuthenticationError("M-Pesa OAuth token missing from response")
        return token

    def _password_and_timestamp(self) -> tuple[str, str]:
        from django.utils import timezone

        timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
        raw = f"{self.shortcode}{self.passkey}{timestamp}".encode()
        return base64.b64encode(raw).decode("utf-8"), timestamp

    def _transaction_type(self) -> str:
        return str(getattr(settings, "MPESA_TRANSACTION_TYPE", "") or "CustomerPayBillOnline")

    def create_payment(
        self,
        amount: Decimal,
        currency: str,
        reference: str,
        description: str,
        payer_phone: str | None = None,
        payer_email: str | None = None,
        metadata: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> PaymentIntent:
        """Create M-Pesa STK push payment."""
        if not self.validate_currency(currency):
            raise PaymentProviderError(f"Currency {currency} not supported by M-Pesa")

        if not payer_phone:
            raise PaymentProviderError("Phone number is required for M-Pesa payments")

        try:
            # Normalize phone number
            phone = self._normalize_phone(payer_phone)
            provider_token = (idempotency_key or uuid.uuid4().hex)[:20]

            if self.use_stub:
                checkout_request_id = f"ws_CO_{provider_token}"
                logger.info("M-Pesa STK push (stub) initiated: %s for reference %s", checkout_request_id, reference)
                return PaymentIntent(
                    provider_intent_id=checkout_request_id,
                    status="pending",
                    amount=amount,
                    currency=currency.upper(),
                    metadata={
                        "phone": phone,
                        "reference": reference,
                        **(metadata or {}),
                    },
                )

            if not self.callback_url:
                raise PaymentProviderInvalidRequestError("MPESA_CALLBACK_URL is not configured")
            if not self.shortcode or not self.passkey:
                raise PaymentProviderInvalidRequestError("M-Pesa shortcode/passkey is not configured")

            token = self._access_token()
            password, timestamp = self._password_and_timestamp()
            url = f"{self._base_url}/mpesa/stkpush/v1/processrequest"
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            payload = {
                "BusinessShortCode": self.shortcode,
                "Password": password,
                "Timestamp": timestamp,
                "TransactionType": self._transaction_type(),
                "Amount": int(Decimal(amount)),
                "PartyA": phone,
                "PartyB": self.shortcode,
                "PhoneNumber": phone,
                "CallBackURL": self.callback_url,
                "AccountReference": reference[:20],
                "TransactionDesc": (description or reference)[:40],
            }
            response = safe_request(
                "POST",
                url,
                json=payload,
                headers=headers,
                policy=OutboundPolicy(
                    allowed_hosts={
                        "api.safaricom.co.ke",
                        "sandbox.safaricom.co.ke",
                        (self._base_url.split("://", 1)[-1].split("/", 1)[0] or "").strip(),
                    }
                ),
                timeout=30,
            )
            data = response.json() if response.content else {}
            if response.status_code != 200 or str(data.get("ResponseCode")) != "0":
                raise PaymentProviderError(
                    "M-Pesa STK push request failed",
                    provider_error_code=str(data.get("ResponseCode") or response.status_code),
                    provider_error_message=str(data.get("errorMessage") or data.get("ResponseDescription") or response.text)[:300],
                )

            checkout_request_id = str(data.get("CheckoutRequestID") or "").strip()
            merchant_request_id = str(data.get("MerchantRequestID") or "").strip()
            if not checkout_request_id:
                raise PaymentProviderError("M-Pesa STK push response missing CheckoutRequestID")

            logger.info("M-Pesa STK push initiated: %s for reference %s", checkout_request_id, reference)
            return PaymentIntent(
                provider_intent_id=checkout_request_id,
                status="pending",
                amount=amount,
                currency=currency.upper(),
                metadata={
                    "phone": phone,
                    "reference": reference,
                    "merchant_request_id": merchant_request_id,
                    "response_description": data.get("ResponseDescription"),
                    **(metadata or {}),
                },
            )

        except PaymentProviderError:
            raise
        except Exception as e:
            logger.error("M-Pesa payment creation failed: %s", e)
            raise PaymentProviderError(f"M-Pesa payment creation failed: {e}")

    def _normalize_phone(self, phone: str) -> str:
        """Normalize phone number to M-Pesa format."""
        raw_phone = phone.strip()
        digits = "".join(filter(str.isdigit, raw_phone))

        # Convert to 254 format
        if raw_phone.startswith("+254") and len(digits) == 12:
            return digits
        if digits.startswith('0') and len(digits) == 10:
            return f"254{digits[1:]}"
        elif digits.startswith('254') and len(digits) == 12:
            return digits
        else:
            return digits

    def verify_payment(self, provider_reference: str) -> PaymentResult:
        """Verify M-Pesa payment status."""
        try:
            if self.use_stub:
                return PaymentResult(
                    provider_reference=provider_reference,
                    status="success",
                    amount=Decimal("0.00"),
                    currency="KES",
                    payer_reference="",
                    provider_metadata={
                        "mpesa_receipt_number": f"QHJ{provider_reference[:10]}",
                        "transaction_date": "20240101120000",
                    },
                )

            token = self._access_token()
            password, timestamp = self._password_and_timestamp()
            url = f"{self._base_url}/mpesa/stkpushquery/v1/query"
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            payload = {
                "BusinessShortCode": self.shortcode,
                "Password": password,
                "Timestamp": timestamp,
                "CheckoutRequestID": provider_reference,
            }
            response = safe_request(
                "POST",
                url,
                json=payload,
                headers=headers,
                policy=OutboundPolicy(
                    allowed_hosts={
                        "api.safaricom.co.ke",
                        "sandbox.safaricom.co.ke",
                        (self._base_url.split("://", 1)[-1].split("/", 1)[0] or "").strip(),
                    }
                ),
                timeout=20,
            )
            data = response.json() if response.content else {}
            if response.status_code != 200 or str(data.get("ResponseCode")) != "0":
                raise PaymentProviderError(
                    "M-Pesa verification failed",
                    provider_error_code=str(data.get("ResponseCode") or response.status_code),
                    provider_error_message=str(data.get("errorMessage") or data.get("ResponseDescription") or response.text)[:300],
                    is_retryable=True,
                )
            result_code = data.get("ResultCode")
            result_desc = str(data.get("ResultDesc") or "")
            if str(result_code) == "0":
                status = "success"
            elif str(result_code) in {"1032"}:
                status = "cancelled"
            elif str(result_code) in {"1037", "1"}:
                status = "pending"
            else:
                status = "failed"

            return PaymentResult(
                provider_reference=provider_reference,
                status=status,
                amount=Decimal("0.00"),
                currency="KES",
                payer_reference=None,
                provider_metadata={
                    "result_code": result_code,
                    "result_desc": result_desc,
                },
                failure_reason=result_desc if status in {"failed", "cancelled"} else None,
            )

        except PaymentProviderError:
            raise
        except Exception as e:
            logger.error("M-Pesa payment verification failed: %s", e)
            raise PaymentProviderError(f"M-Pesa payment verification failed: {e}")

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str | None,
        headers: dict[str, str] | None = None,
    ) -> WebhookVerificationResult:
        """Verify M-Pesa callback signature."""
        import json

        try:
            data = json.loads(payload)

            # Basic validation
            callback = data.get("Body", {}).get("stkCallback", {})
            checkout_request_id = callback.get("CheckoutRequestID")
            if not checkout_request_id:
                return WebhookVerificationResult(
                    is_valid=False,
                    error="Invalid M-Pesa callback structure",
                )

            headers = headers or {}
            source_ip = headers.get("REMOTE_ADDR") or None
            forwarded = headers.get("HTTP_X_FORWARDED_FOR") or headers.get("X_FORWARDED_FOR") or ""
            if forwarded and not source_ip:
                source_ip = str(forwarded).split(",")[0].strip()

            allowlist = [
                item.strip()
                for item in getattr(settings, "MPESA_CALLBACK_IP_ALLOWLIST", []) or []
                if str(item).strip()
            ]
            if getattr(settings, "PAYMENTS_CALLBACK_REQUIRE_IP_ALLOWLIST", False) and not allowlist:
                return WebhookVerificationResult(is_valid=False, error="Callback IP allowlist not configured")
            if allowlist and (not source_ip or source_ip not in set(allowlist)):
                return WebhookVerificationResult(is_valid=False, error="Source IP not allowlisted")

            secret = str(getattr(settings, "MPESA_CALLBACK_SECRET", "") or "").strip()
            if getattr(settings, "PAYMENTS_CALLBACK_REQUIRE_SIGNATURE", False) and not secret:
                return WebhookVerificationResult(is_valid=False, error="Callback signature secret not configured")
            if secret:
                if not signature:
                    return WebhookVerificationResult(is_valid=False, error="Missing callback signature")
                expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(expected, str(signature).strip()):
                    return WebhookVerificationResult(is_valid=False, error="Invalid callback signature")

            return WebhookVerificationResult(
                is_valid=True,
                event_type="mpesa.callback",
                provider_reference=checkout_request_id,
                payload=data,
            )

        except json.JSONDecodeError as e:
            return WebhookVerificationResult(
                is_valid=False,
                error=f"Invalid JSON payload: {e}",
            )
        except Exception as e:
            return WebhookVerificationResult(
                is_valid=False,
                error=f"Verification error: {e}",
            )

    def parse_webhook_event(
        self,
        payload: dict[str, Any],
    ) -> tuple[str, str | None, dict[str, Any]]:
        """Parse M-Pesa callback event."""
        callback = payload.get("Body", {}).get("stkCallback", {})
        checkout_request_id = callback.get("CheckoutRequestID")
        result_code = callback.get("ResultCode")
        result_desc = callback.get("ResultDesc", "")

        if result_code == 0:
            # Success
            callback_metadata = callback.get("CallbackMetadata", {}).get("Item", [])
            mpesa_receipt = next(
                (item.get("Value") for item in callback_metadata if item.get("Name") == "MpesaReceiptNumber"),
                None
            )
            amount = next((item.get("Value") for item in callback_metadata if item.get("Name") == "Amount"), None)
            phone = next((item.get("Value") for item in callback_metadata if item.get("Name") == "PhoneNumber"), None)
            txn_date = next((item.get("Value") for item in callback_metadata if item.get("Name") == "TransactionDate"), None)
            return "mpesa.success", checkout_request_id, {
                "mpesa_receipt_number": mpesa_receipt,
                "result_code": result_code,
                "result_desc": result_desc,
                "amount": amount,
                "phone": phone,
                "transaction_date": txn_date,
            }
        else:
            # Failure
            return "mpesa.failed", checkout_request_id, {
                "result_code": result_code,
                "result_desc": result_desc,
            }

    def refund_payment(
        self,
        provider_reference: str,
        amount: Decimal | None = None,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Refund M-Pesa payment."""
        # M-Pesa refunds are handled via B2C API
        # This is a simplified implementation
        return {
            "refund_id": f"refund_{provider_reference}",
            "status": "pending",
            "amount": amount,
            "currency": "KES",
        }
