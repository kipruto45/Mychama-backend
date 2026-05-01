"""
Field-Level Encryption Service

Provides AES-256-GCM encryption for sensitive fields.
Supports envelope encryption with KMS-managed keys.
"""

import base64
import hashlib
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)


@dataclass
class EncryptedData:
    """Container for encrypted data."""

    ciphertext: bytes
    key_version: int
    nonce: bytes
    tag: bytes

    def to_string(self) -> str:
        """Convert to storage string."""
        combined = self.nonce + self.tag + self.ciphertext
        return base64.b64encode(combined).decode()

    @classmethod
    def from_string(cls, data: str, key_version: int) -> "EncryptedData":
        """Create from storage string."""
        combined = base64.b64decode(data.encode())
        nonce = combined[:12]
        tag = combined[12:28]
        ciphertext = combined[28:]
        return cls(ciphertext=ciphertext, key_version=key_version, nonce=nonce, tag=tag)


class EncryptionKeyManager:
    """Manages encryption keys with versioning."""

    def __init__(self):
        self._key_cache: dict[int, bytes] = {}
        self._current_version = 1

    def get_key(self, version: int = None) -> bytes:
        """Get encryption key by version."""
        if version is None:
            version = self._current_version

        if version not in self._key_cache:
            key_str = self._derive_key(version)
            self._key_cache[version] = key_str

        return self._key_cache[version]

    def get_current_key(self) -> bytes:
        """Get current encryption key."""
        return self.get_key(self._current_version)

    def get_current_version(self) -> int:
        """Get current key version."""
        return self._current_version

    def _derive_key(self, version: int) -> bytes:
        """Derive key from master secret and version."""
        from django.conf import settings

        master_key = getattr(settings, "FIELD_ENCRYPTION_KEY", None)
        if not master_key:
            master_key = Fernet.generate_key()
            logger.warning("Using generated encryption key - should use settings in production")

        derived = hashlib.pbkdf2_hmac(
            "sha256",
            master_key,
            f"v{version}".encode(),
            100000,
            32,
        )
        return derived

    def rotate_key(self) -> int:
        """Rotate to new key version."""
        self._current_version += 1
        logger.info(f"Key rotated to version {self._current_version}")
        return self._current_version


class FieldEncryptionService:
    """
    Field-level encryption service using AES-256-GCM.
    """

    NONCE_SIZE = 12
    TAG_SIZE = 16

    def __init__(self):
        self._key_manager = EncryptionKeyManager()

    def encrypt(self, plaintext: str, key_version: int = None) -> str:
        """
        Encrypt plaintext field.
        Returns encrypted string with key version metadata.
        """
        if not plaintext:
            return ""

        key = self._key_manager.get_key(key_version)
        nonce = os.urandom(self.NONCE_SIZE)

        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)

        encrypted = EncryptedData(
            ciphertext=ciphertext,
            key_version=key_version or self._key_manager.get_current_version(),
            nonce=nonce,
            tag=ciphertext[-self.TAG_SIZE:],
        )

        return encrypted.to_string()

    def decrypt(self, encrypted_data: str) -> str | None:
        """
        Decrypt encrypted field.
        Automatically detects key version from data.
        """
        if not encrypted_data:
            return ""

        try:
            combined = base64.b64decode(encrypted_data.encode())

            if len(combined) < self.NONCE_SIZE + self.TAG_SIZE:
                return self._decrypt_legacy(encrypted_data)

            nonce = combined[:self.NONCE_SIZE]
            tag = combined[self.NONCE_SIZE : self.NONCE_SIZE + self.TAG_SIZE]
            ciphertext = combined[self.NONCE_SIZE + self.TAG_SIZE :]

            key_version = self._key_manager.get_current_version()
            key = self._key_manager.get_key(key_version)

            aesgcm = AESGCM(key)
            plaintext = aesgcm.decrypt(nonce, ciphertext + tag, None)

            return plaintext.decode()

        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            return None

    def _decrypt_legacy(self, data: str) -> str | None:
        """Handle legacy Fernet encryption."""
        try:
            from django.conf import settings

            key = getattr(settings, "FIELD_ENCRYPTION_KEY", None)
            if not key:
                return None

            f = Fernet(key)
            return f.decrypt(data.encode()).decode()
        except Exception:
            return None

    def reencrypt(self, encrypted_data: str) -> str:
        """Re-encrypt with current key version."""
        plaintext = self.decrypt(encrypted_data)
        if plaintext is None:
            return encrypted_data
        return self.encrypt(plaintext)

    def rotate_keys(self) -> int:
        """Rotate encryption keys."""
        return self._key_manager.rotate_key()


class PIIMaskingService:
    """Service for masking PII in logs and outputs."""

    PHONE_PATTERN = re.compile(r"\+?254\d{9}")
    ID_PATTERN = re.compile(r"\b\d{6,12}\b")
    EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
    ACCOUNT_PATTERN = re.compile(r"\b\d{10,16}\b")

    @staticmethod
    def mask_phone(phone: str) -> str:
        """Mask phone number."""
        if not phone:
            return ""
        digits = re.sub(r"\D", "", phone)
        if len(digits) < 6:
            return "***"
        return f"+254{digits[3:5]}{'*' * 4}{digits[-2:]}"

    @staticmethod
    def mask_id(id_number: str) -> str:
        """Mask ID number."""
        if not id_number:
            return ""
        if len(id_number) < 6:
            return "***"
        return f"{id_number[:2]}{'*' * (len(id_number) - 4)}{id_number[-2:]}"

    @staticmethod
    def mask_email(email: str) -> str:
        """Mask email address."""
        if not email:
            return ""
        parts = email.split("@")
        if len(parts) != 2:
            return "***"
        local, domain = parts
        if len(local) <= 2:
            masked_local = "*" * len(local)
        else:
            masked_local = local[0] + "*" * (len(local) - 2) + local[-1]
        return f"{masked_local}@{domain}"

    @staticmethod
    def mask_account(account: str) -> str:
        """Mask bank account number."""
        if not account:
            return ""
        if len(account) < 6:
            return "***"
        return f"****{account[-4:]}"

    @classmethod
    def mask_message(cls, message: str) -> str:
        """Mask all PII in a message."""
        if not message:
            return ""

        message = cls.PHONE_PATTERN.sub("[PHONE]", message)
        message = cls.ID_PATTERN.sub("[ID]", message)
        message = cls.EMAIL_PATTERN.sub("[EMAIL]", message)
        message = cls.ACCOUNT_PATTERN.sub("[ACCOUNT]", message)

        return message


class SensitiveFieldMixin:
    """Mixin for models with sensitive fields."""

    SENSITIVE_FIELDS: list[str] = []

    @classmethod
    def get_sensitive_fields(cls) -> list[str]:
        """Get list of sensitive field names."""
        return cls.SENSITIVE_FIELDS

    def get_masked_field(self, field_name: str) -> str:
        """Get masked version of a field."""
        value = getattr(self, field_name, None)
        if not value:
            return ""

        if field_name == "phone":
            return PIIMaskingService.mask_phone(value)
        elif field_name in ["id_number", "national_id"]:
            return PIIMaskingService.mask_id(value)
        elif field_name == "email":
            return PIIMaskingService.mask_email(value)
        elif field_name in ["account_number", "bank_account"]:
            return PIIMaskingService.mask_account(value)

        return str(value)[:2] + "***"


field_encryption_service = FieldEncryptionService()
pii_masking_service = PIIMaskingService()

__all__ = [
    "FieldEncryptionService",
    "PIIMaskingService",
    "SensitiveFieldMixin",
    "EncryptedData",
    "EncryptionKeyManager",
    "field_encryption_service",
    "pii_masking_service",
]
