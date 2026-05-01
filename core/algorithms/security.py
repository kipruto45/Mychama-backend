from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class TokenBucketState:
    tokens: Decimal
    last_refill_at: datetime


def consume_from_token_bucket(
    *,
    state: TokenBucketState,
    now: datetime,
    capacity: Decimal,
    refill_per_second: Decimal,
    cost: Decimal = Decimal("1"),
) -> tuple[TokenBucketState, bool]:
    elapsed_seconds = max(
        Decimal("0"), Decimal(str((now - state.last_refill_at).total_seconds()))
    )
    refilled = state.tokens + (elapsed_seconds * refill_per_second)
    available = min(capacity, refilled)
    allowed = available >= cost
    remaining = available - cost if allowed else available
    return TokenBucketState(tokens=remaining, last_refill_at=now), allowed


def sliding_window_failures(
    *,
    failure_timestamps: list[datetime],
    now: datetime,
    window_seconds: int,
) -> int:
    if window_seconds <= 0:
        return len(failure_timestamps)
    window_start = now - timedelta(seconds=window_seconds)
    return sum(1 for stamp in failure_timestamps if stamp >= window_start)


def compute_lock_expiry(*, now: datetime, cooldown_seconds: int) -> datetime:
    return now + timedelta(seconds=max(1, cooldown_seconds))


def generate_otp_code(length: int = 6) -> str:
    length = max(4, min(10, int(length)))
    upper_bound = 10**length
    value = secrets.randbelow(upper_bound)
    return f"{value:0{length}d}"


def mask_phone_number(phone: str) -> str:
    value = str(phone or "").strip()
    if len(value) <= 7:
        return value
    return f"{value[:5]}****{value[-3:]}"


def redact_sensitive_values(
    payload: Any,
    *,
    sensitive_keys: set[str] | None = None,
) -> Any:
    keys = sensitive_keys or {
        "password",
        "token",
        "access",
        "refresh",
        "secret",
        "pin",
        "otp",
        "email",
        "phone",
    }
    if isinstance(payload, dict):
        redacted: dict[str, Any] = {}
        for key, value in payload.items():
            if key.lower() in keys:
                redacted[key] = "***"
            else:
                redacted[key] = redact_sensitive_values(
                    value,
                    sensitive_keys=keys,
                )
        return redacted
    if isinstance(payload, list):
        return [redact_sensitive_values(item, sensitive_keys=keys) for item in payload]
    return payload
