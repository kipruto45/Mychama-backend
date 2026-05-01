from __future__ import annotations

import base64
import hashlib
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.core.files.base import ContentFile, File
from django.core.files.storage import Storage, storages
from django.utils.deconstruct import deconstructible


_KYC_FILE_MAGIC = b"MYCHAMA_KYC_ENC_V1\0"
_KYC_NONCE_SIZE = 12


def _read_content_bytes(content: Any) -> bytes:
    if isinstance(content, (bytes, bytearray)):
        return bytes(content)
    if hasattr(content, "chunks"):
        return b"".join(content.chunks())
    if hasattr(content, "read"):
        data = content.read()
        return data if isinstance(data, bytes) else str(data).encode("utf-8")
    raise TypeError("Unsupported file content object for KYC storage upload")


def _kyc_file_key() -> bytes:
    """
    Derive a stable 32-byte AES key for KYC file encryption.

    Uses FIELD_ENCRYPTION_KEY as master input. In production this must be set.
    """
    master = getattr(settings, "FIELD_ENCRYPTION_KEY", b"") or b""
    if not isinstance(master, (bytes, bytearray)):
        master = str(master).encode("utf-8")
    if not master:
        # Fall back to SECRET_KEY-derived bytes for non-production/dev environments.
        master = hashlib.sha256(str(getattr(settings, "SECRET_KEY", "")).encode("utf-8")).digest()
    return hashlib.pbkdf2_hmac("sha256", bytes(master), b"kyc-file-encryption", 200_000, 32)


def encrypt_kyc_file(*, plaintext: bytes, aad: bytes) -> bytes:
    aesgcm = AESGCM(_kyc_file_key())
    nonce = os.urandom(_KYC_NONCE_SIZE)
    ciphertext = aesgcm.encrypt(nonce, plaintext, aad)
    return _KYC_FILE_MAGIC + nonce + ciphertext


def decrypt_kyc_file(*, payload: bytes, aad: bytes) -> bytes:
    if not payload.startswith(_KYC_FILE_MAGIC):
        return payload
    offset = len(_KYC_FILE_MAGIC)
    nonce = payload[offset : offset + _KYC_NONCE_SIZE]
    ciphertext = payload[offset + _KYC_NONCE_SIZE :]
    aesgcm = AESGCM(_kyc_file_key())
    return aesgcm.decrypt(nonce, ciphertext, aad)


@deconstructible
class KYCPrivateEncryptedStorage(Storage):
    """
    Encrypts KYC uploads at rest while delegating persistence to the configured default storage.

    - Works with FileSystemStorage in dev/test.
    - Works with SupabaseStorage in production when configured.
    """

    def __init__(self):
        self._backend = storages["default"]

    def _open(self, name: str, mode: str = "rb") -> File:
        file_obj = self._backend.open(name, mode=mode)
        raw = file_obj.read()
        plaintext = decrypt_kyc_file(payload=raw, aad=name.encode("utf-8"))
        return ContentFile(plaintext, name=name)

    def _save(self, name: str, content: Any) -> str:
        plaintext = _read_content_bytes(content)
        encrypted = encrypt_kyc_file(plaintext=plaintext, aad=name.encode("utf-8"))
        wrapped = ContentFile(encrypted, name=name)
        return self._backend.save(name, wrapped)

    def delete(self, name: str) -> None:
        return self._backend.delete(name)

    def exists(self, name: str) -> bool:
        try:
            return self._backend.exists(name)
        except Exception as exc:
            # If there's any error checking existence (e.g., network issue), 
            # assume it doesn't exist to avoid blocking the upload
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Error checking if KYC file exists: {name}. Error: {exc}")
            return False

    def size(self, name: str) -> int:
        return self._backend.size(name)

    def url(self, name: str) -> str:
        return self._backend.url(name)

    def get_modified_time(self, name: str):
        return self._backend.get_modified_time(name)


def export_kyc_file_key_fingerprint() -> str:
    """
    Safe fingerprint for audit/debug (never returns the key).
    """
    return base64.urlsafe_b64encode(hashlib.sha256(_kyc_file_key()).digest()[:12]).decode("utf-8")

