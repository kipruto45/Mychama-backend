from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path

import requests
from django.conf import settings


class MpesaClientError(Exception):
    pass


class MpesaClient:
    def __init__(self):
        self.consumer_key = settings.DARAJA_CONSUMER_KEY
        self.consumer_secret = settings.DARAJA_CONSUMER_SECRET
        self.shortcode = settings.DARAJA_SHORTCODE
        self.passkey = settings.DARAJA_PASSKEY
        self.environment = settings.DARAJA_ENV
        self.callback_base_url = settings.DARAJA_CALLBACK_BASE_URL.rstrip("/")
        self.b2c_shortcode = settings.DARAJA_B2C_SHORTCODE
        self.initiator_name = settings.DARAJA_B2C_INITIATOR_NAME
        self.initiator_password = settings.DARAJA_B2C_INITIATOR_PASSWORD

        if self.environment == "production":
            self.base_url = "https://api.safaricom.co.ke"
        else:
            self.base_url = "https://sandbox.safaricom.co.ke"

    def _request(self, method: str, path: str, *, token: str, payload: dict) -> dict:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            response = requests.request(
                method,
                url,
                json=payload,
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            raise MpesaClientError(f"Daraja request failed for {path}.") from exc

    def get_access_token(self) -> str:
        api_url = f"{self.base_url}/oauth/v1/generate?grant_type=client_credentials"
        try:
            response = requests.get(
                api_url,
                auth=(self.consumer_key, self.consumer_secret),
                timeout=30,
            )
            response.raise_for_status()
            return response.json()["access_token"]
        except requests.RequestException as exc:
            raise MpesaClientError("Failed to get Daraja access token.") from exc

    @staticmethod
    def generate_stk_password(shortcode: str, passkey: str, timestamp: str) -> str:
        raw = f"{shortcode}{passkey}{timestamp}".encode("utf-8")
        return base64.b64encode(raw).decode("utf-8")

    @staticmethod
    def generate_security_credential(password: str, cert_path: str) -> str:
        """Encrypt initiator password with Daraja cert and return base64 cipher text.

        This uses `cryptography` when available. If unavailable, the caller should
        pre-configure DARAJA_SECURITY_CREDENTIAL.
        """
        try:
            from cryptography.hazmat.backends import default_backend
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding
        except Exception as exc:  # noqa: BLE001
            raise MpesaClientError(
                "cryptography package not available for security credential generation. "
                "Set DARAJA_SECURITY_CREDENTIAL in environment."
            ) from exc

        cert_file = Path(cert_path)
        if not cert_file.exists():
            raise MpesaClientError("Daraja certificate path does not exist.")

        public_key = serialization.load_pem_public_key(
            cert_file.read_bytes(),
            backend=default_backend(),
        )
        encrypted = public_key.encrypt(
            password.encode("utf-8"),
            padding.PKCS1v15(),
        )
        return base64.b64encode(encrypted).decode("utf-8")

    def get_security_credential(self) -> str:
        configured = str(settings.DARAJA_SECURITY_CREDENTIAL or "").strip()
        if configured:
            return configured

        cert_path = str(settings.DARAJA_CERT_PATH or "").strip()
        if not cert_path:
            raise MpesaClientError(
                "DARAJA_SECURITY_CREDENTIAL or DARAJA_CERT_PATH must be configured."
            )
        return self.generate_security_credential(self.initiator_password, cert_path)

    def initiate_stk_push(
        self,
        phone_number: str,
        amount: str,
        account_reference: str,
        transaction_desc: str,
        callback_url: str | None = None,
    ) -> dict:
        access_token = self.get_access_token()
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        password = self.generate_stk_password(self.shortcode, self.passkey, timestamp)

        payload = {
            "BusinessShortCode": self.shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": amount,
            "PartyA": phone_number,
            "PartyB": self.shortcode,
            "PhoneNumber": phone_number,
            "CallBackURL": callback_url or f"{self.callback_base_url}/api/v1/payments/callbacks/stk",
            "AccountReference": account_reference,
            "TransactionDesc": transaction_desc,
        }
        return self._request(
            "POST",
            "/mpesa/stkpush/v1/processrequest",
            token=access_token,
            payload=payload,
        )

    def query_stk_push_status(self, checkout_request_id: str) -> dict:
        access_token = self.get_access_token()
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        password = self.generate_stk_password(self.shortcode, self.passkey, timestamp)

        payload = {
            "BusinessShortCode": self.shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "CheckoutRequestID": checkout_request_id,
        }
        return self._request(
            "POST",
            "/mpesa/stkpushquery/v1/query",
            token=access_token,
            payload=payload,
        )

    def register_c2b_urls(self) -> dict:
        access_token = self.get_access_token()
        payload = {
            "ShortCode": self.shortcode,
            "ResponseType": "Completed",
            "ConfirmationURL": (
                f"{self.callback_base_url}/api/v1/payments/callbacks/c2b/confirmation"
            ),
            "ValidationURL": (
                f"{self.callback_base_url}/api/v1/payments/callbacks/c2b/validation"
            ),
        }
        return self._request(
            "POST",
            "/mpesa/c2b/v1/registerurl",
            token=access_token,
            payload=payload,
        )

    def send_b2c_payment(
        self,
        *,
        phone_number: str,
        amount: str,
        command_id: str,
        remarks: str,
        occasion: str = "",
    ) -> dict:
        access_token = self.get_access_token()
        payload = {
            "InitiatorName": self.initiator_name,
            "SecurityCredential": self.get_security_credential(),
            "CommandID": command_id,
            "Amount": amount,
            "PartyA": self.b2c_shortcode,
            "PartyB": phone_number,
            "Remarks": remarks,
            "QueueTimeOutURL": (
                f"{self.callback_base_url}/api/v1/payments/callbacks/b2c/timeout"
            ),
            "ResultURL": (
                f"{self.callback_base_url}/api/v1/payments/callbacks/b2c/result"
            ),
            "Occasion": occasion,
        }
        return self._request(
            "POST",
            "/mpesa/b2c/v1/paymentrequest",
            token=access_token,
            payload=payload,
        )
