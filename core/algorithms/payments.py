from __future__ import annotations

import hashlib
import random
from decimal import Decimal

PAYMENT_STATUS_TRANSITIONS = {
    "INITIATED": {"PENDING", "CANCELLED", "EXPIRED", "FAILED"},
    "PENDING": {"SUCCESS", "FAILED", "EXPIRED", "CANCELLED", "TIMEOUT"},
    "SUCCESS": set(),
    "FAILED": set(),
    "EXPIRED": set(),
    "CANCELLED": set(),
    "TIMEOUT": {"PENDING", "FAILED"},
}


def can_transition_payment_status(*, current: str, target: str) -> bool:
    current_key = str(current or "").upper()
    target_key = str(target or "").upper()
    if current_key == target_key:
        return True
    return target_key in PAYMENT_STATUS_TRANSITIONS.get(current_key, set())


def callback_is_duplicate(
    *, callback_reference: str, seen_references: set[str]
) -> bool:
    normalized = str(callback_reference or "").strip().upper()
    if not normalized:
        return False
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    if digest in seen_references:
        return True
    seen_references.add(digest)
    return False


def exponential_backoff_seconds(
    *,
    attempt: int,
    base_seconds: int = 2,
    max_seconds: int = 300,
    jitter_ratio: Decimal = Decimal("0.20"),
) -> int:
    normalized_attempt = max(0, int(attempt))
    baseline = min(max_seconds, base_seconds * (2**normalized_attempt))
    jitter = Decimal(str(random.uniform(0, float(jitter_ratio))))
    with_jitter = Decimal(str(baseline)) * (Decimal("1.0") + jitter)
    return int(min(Decimal(str(max_seconds)), with_jitter).to_integral_value())
