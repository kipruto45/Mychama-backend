"""
Security helpers for billing metadata and webhook context.
"""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _billing_cipher() -> Fernet:
    seed = str(
        getattr(settings, 'BILLING_METADATA_ENCRYPTION_KEY', '') or settings.SECRET_KEY
    ).encode('utf-8')
    key = base64.urlsafe_b64encode(hashlib.sha256(seed).digest())
    return Fernet(key)


def encrypt_billing_metadata(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ''
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return _billing_cipher().encrypt(raw).decode('utf-8')


def decrypt_billing_metadata(token: str | None) -> dict[str, Any]:
    if not token:
        return {}
    try:
        raw = _billing_cipher().decrypt(str(token).encode('utf-8'))
    except (InvalidToken, ValueError, TypeError):
        return {}
    try:
        return json.loads(raw.decode('utf-8'))
    except json.JSONDecodeError:
        return {}
