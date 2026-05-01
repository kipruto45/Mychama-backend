from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

import requests
from django.conf import settings
from django.core.exceptions import ValidationError

from core.safe_http import OutboundPolicy, safe_request


@dataclass(frozen=True)
class PasswordBreachResult:
    breached: bool
    count: int = 0


def estimate_password_entropy_bits(password: str) -> float:
    value = str(password or "")
    if not value:
        return 0.0

    pool_size = 0
    if any(char.islower() for char in value):
        pool_size += 26
    if any(char.isupper() for char in value):
        pool_size += 26
    if any(char.isdigit() for char in value):
        pool_size += 10
    if any(not char.isalnum() for char in value):
        pool_size += 33

    if pool_size == 0:
        return 0.0

    return round(len(value) * math.log2(pool_size), 2)


def validate_password_entropy(password: str) -> None:
    minimum_bits = float(getattr(settings, "PASSWORD_MIN_ENTROPY_BITS", 45))
    entropy = estimate_password_entropy_bits(password)
    if entropy < minimum_bits:
        raise ValidationError(
            "Password is too weak. Use a longer passphrase with mixed character types."
        )


def check_password_breach(password: str) -> PasswordBreachResult:
    if not getattr(settings, "HIBP_PASSWORD_CHECK_ENABLED", True):
        return PasswordBreachResult(breached=False, count=0)

    sha1_hash = hashlib.sha1(str(password or "").encode("utf-8")).hexdigest().upper()
    prefix, suffix = sha1_hash[:5], sha1_hash[5:]

    try:
        response = safe_request(
            "GET",
            f"https://api.pwnedpasswords.com/range/{prefix}",
            policy=OutboundPolicy(allowed_hosts={"api.pwnedpasswords.com"}),
            headers={
                "Add-Padding": "true",
                "User-Agent": "MyChama-Password-Security",
            },
            timeout=float(getattr(settings, "HIBP_PASSWORD_CHECK_TIMEOUT_SECONDS", 5)),
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        if getattr(settings, "HIBP_PASSWORD_CHECK_FAIL_CLOSED", False):
            raise ValidationError(
                "Password breach screening is temporarily unavailable. Please try again."
            ) from exc
        return PasswordBreachResult(breached=False, count=0)

    for line in response.text.splitlines():
        hash_suffix, _, count = line.partition(":")
        if hash_suffix.strip().upper() != suffix:
            continue
        try:
            breach_count = int(count.strip() or "0")
        except ValueError:
            breach_count = 1
        return PasswordBreachResult(breached=breach_count > 0, count=breach_count)

    return PasswordBreachResult(breached=False, count=0)


def validate_password_security(password: str) -> None:
    validate_password_entropy(password)
    breach_result = check_password_breach(password)
    threshold = int(getattr(settings, "HIBP_PASSWORD_BREACH_MIN_COUNT", 1))
    if breach_result.breached and breach_result.count >= threshold:
        raise ValidationError(
            "This password has appeared in known breaches. Choose a different password."
        )
