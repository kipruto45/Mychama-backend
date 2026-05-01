from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

REDACTED = "[REDACTED]"

SENSITIVE_KEY_FRAGMENTS = {
    "password",
    "passcode",
    "pin",
    "otp",
    "secret",
    "api_key",
    "apikey",
    "token",
    "refresh",
    "access",
    "authorization",
    "signature",
    "private_key",
    "service_role",
    "client_secret",
    "session",
    "cookie",
}

_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+([A-Za-z0-9._~+/=-]{12,})")
_KV_RE = re.compile(
    r"(?i)\b(password|pin|otp|token|secret|api[_-]?key|authorization)\b\s*[:=]\s*([^\s,;]+)"
)


def _looks_sensitive_key(key: object) -> bool:
    if key is None:
        return False
    normalized = str(key).strip().lower()
    return any(fragment in normalized for fragment in SENSITIVE_KEY_FRAGMENTS)


def _redact_text(text: str) -> str:
    if not text:
        return text
    value = _BEARER_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    value = _KV_RE.sub(lambda m: f"{m.group(1)}={REDACTED}", value)
    return value


def redact_object(value):
    if value is None:
        return value

    if isinstance(value, str):
        return _redact_text(value)

    if isinstance(value, bytes):
        try:
            return _redact_text(value.decode("utf-8", errors="ignore"))
        except Exception:  # noqa: BLE001
            return REDACTED

    if isinstance(value, Mapping):
        redacted: dict = {}
        for key, item in value.items():
            if _looks_sensitive_key(key):
                redacted[key] = REDACTED
            else:
                redacted[key] = redact_object(item)
        return redacted

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_object(item) for item in value]

    return value


def patch_loguru_record(record: dict) -> dict:
    """
    Loguru patcher that redacts common secrets from message and extra fields.

    This intentionally errs on the side of redacting too much rather than leaking
    credentials into logs.
    """
    try:
        record["message"] = _redact_text(str(record.get("message", "")))
    except Exception:  # noqa: BLE001
        record["message"] = str(record.get("message", ""))

    extra = record.get("extra") or {}
    if isinstance(extra, dict):
        record["extra"] = redact_object(extra)

    return record

