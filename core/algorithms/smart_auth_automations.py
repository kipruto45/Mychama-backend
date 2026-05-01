"""
OTP & Authentication Automations

Production-grade automations for:
- OTP generation (6-digit, 5-minute expiry)
- OTP cooldown enforcer (60 seconds, max 3 resends)
- OTP max attempts locker (5 failures = 30-min lock)
- Account unlock after timeout
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from apps.accounts.models import User


@dataclass
class OTPResult:
    """OTP generation result."""
    code: str
    user_id: str
    expires_at: str
    cooldown_remaining: int
    resends_remaining: int
    attempts_remaining: int
    delivery_method: str


@dataclass
class OTPVerificationResult:
    """OTP verification result."""
    is_valid: bool
    attempts_remaining: int
    error: str | None


@dataclass
class AccountLockResult:
    """Account lock result."""
    is_locked: bool
    locked_until: str | None
    attempts_remaining: int
    unlock_in_minutes: int


def generate_otp(user: "User", delivery_method: str = "sms") -> OTPResult:
    """Generate 6-digit OTP with expiry and rate limiting."""
    user_id = str(user.id)
    now = timezone.now()
    
    cooldown_seconds = int(getattr(settings, "OTP_COOLDOWN_SECONDS", 60))
    max_resends = int(getattr(settings, "OTP_MAX_RESENDS_PER_WINDOW", 3))
    expiry_minutes = int(getattr(settings, "OTP_EXPIRY_MINUTES", 5))
    max_attempts = int(getattr(settings, "OTP_MAX_ATTEMPTS", 5))
    
    cooldown_key = f"otp:cooldown:{user_id}"
    cooldown_remaining = cache.get(cooldown_key, 0)
    
    resend_key = f"otp:resends:{user_id}"
    resend_count = cache.get(resend_key, 0)
    resends_remaining = max(0, max_resends - resend_count)
    
    if cooldown_remaining > 0 and resends_remaining <= 0:
        raise PermissionError(
            f"OTP cooldown active. Wait {cooldown_remaining}s. "
            f"Resends exhausted ({max_resends}/{max_resends})."
        )
    
    code = secrets.randbelow(900000) + 100000
    code_str = str(code)
    
    otp_data = {
        "code": code_str,
        "user_id": user_id,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=expiry_minutes)).isoformat(),
        "attempts": 0,
        "max_attempts": max_attempts,
        "delivery_method": delivery_method,
    }
    
    cache.set(f"otp:data:{user_id}", otp_data, timeout=expiry_minutes * 60 + 30)
    cache.set(cooldown_key, cooldown_seconds, timeout=cooldown_seconds)
    
    if resend_count < max_resends:
        cache.set(resend_key, resend_count + 1, timeout=600)
    
    logger.info("OTP generated for user %s via %s", user_id, delivery_method)
    
    return OTPResult(
        code=code_str,
        user_id=user_id,
        expires_at=otp_data["expires_at"],
        cooldown_remaining=cooldown_remaining,
        resends_remaining=resends_remaining - 1,
        attempts_remaining=max_attempts,
        delivery_method=delivery_method,
    )


def verify_otp(user: "User", code: str) -> OTPVerificationResult:
    """Verify OTP with attempt tracking and locking."""
    user_id = str(user.id)
    max_attempts = int(getattr(settings, "OTP_MAX_ATTEMPTS", 5))
    lockout_seconds = int(getattr(settings, "OTP_LOCKOUT_SECONDS", 1800))
    
    lock_key = f"otp:lockout:{user_id}"
    if cache.get(lock_key):
        return OTPVerificationResult(
            is_valid=False,
            attempts_remaining=0,
            error="Account temporarily locked due to too many failed attempts.",
        )
    
    otp_data = cache.get(f"otp:data:{user_id}")
    
    if not otp_data:
        return OTPVerificationResult(
            is_valid=False,
            attempts_remaining=0,
            error="Invalid or expired OTP.",
        )
    
    attempts_key = f"otp:attempts:{user_id}"
    current_attempts = int(cache.get(attempts_key, 0))
    
    if current_attempts >= max_attempts:
        cache.set(lock_key, True, timeout=lockout_seconds)
        cache.delete(f"otp:data:{user_id}")
        logger.warning("Account locked due to max OTP attempts: %s", user_id)
        return OTPVerificationResult(
            is_valid=False,
            attempts_remaining=0,
            error="Max verification attempts exceeded.",
        )
    
    if code != otp_data["code"]:
        new_attempts = current_attempts + 1
        remaining = max(0, max_attempts - new_attempts)
        cache.set(attempts_key, new_attempts, timeout=lockout_seconds)
        
        logger.warning(
            "Invalid OTP attempt for %s: attempt %s/%s",
            user_id,
            new_attempts,
            max_attempts,
        )
        
        return OTPVerificationResult(
            is_valid=False,
            attempts_remaining=remaining,
            error=f"Invalid OTP. {remaining} attempts remaining.",
        )
    
    cache.delete(f"otp:data:{user_id}")
    cache.delete(attempts_key)
    logger.info("OTP verified successfully for %s", user_id)
    
    return OTPVerificationResult(
        is_valid=True,
        attempts_remaining=max_attempts,
        error=None,
    )


def check_account_lock(user: "User") -> AccountLockResult:
    """Check if account is locked due to OTP failures."""
    user_id = str(user.id)
    max_attempts = int(getattr(settings, "OTP_MAX_ATTEMPTS", 5))
    lockout_seconds = int(getattr(settings, "OTP_LOCKOUT_SECONDS", 1800))
    
    lock_key = f"otp:lockout:{user_id}"
    locked_until_ts = cache.get(f"{lock_key}:until")
    
    if not locked_until_ts:
        return AccountLockResult(
            is_locked=False,
            locked_until=None,
            attempts_remaining=max_attempts,
            unlock_in_minutes=0,
        )
    
    unlock_minutes = max(0, int((locked_until_ts - timezone.now().timestamp()) / 60))
    
    return AccountLockResult(
        is_locked=True,
        locked_until=timezone.datetime.fromtimestamp(locked_until_ts).isoformat(),
        attempts_remaining=0,
        unlock_in_minutes=unlock_minutes,
    )


def is_locked_out(user: "User") -> bool:
    """Check if user is currently locked out."""
    user_id = str(user.id)
    return bool(cache.get(f"otp:lockout:{user_id}"))


def clear_lockout(user: "User") -> None:
    """Clear lockout for user (admin or after timeout)."""
    user_id = str(user.id)
    cache.delete(f"otp:lockout:{user_id}")
    cache.delete(f"otp:attempts:{user_id}")
    logger.info("Lockout cleared for %s", user_id)


def get_otp_status(user: "User") -> dict:
    """Get full OTP status for a user."""
    user_id = str(user.id)
    cooldown_seconds = int(getattr(settings, "OTP_COOLDOWN_SECONDS", 60))
    max_resends = int(getattr(settings, "OTP_MAX_RESENDS_PER_WINDOW", 3))
    max_attempts = int(getattr(settings, "OTP_MAX_ATTEMPTS", 5))
    
    cooldown_key = f"otp:cooldown:{user_id}"
    resend_key = f"otp:resends:{user_id}"
    attempts_key = f"otp:attempts:{user_id}"
    
    cooldown_remaining = cache.get(cooldown_key, 0)
    resend_count = cache.get(resend_key, 0)
    attempts = cache.get(attempts_key, 0)
    
    return {
        "user_id": user_id,
        "cooldown_remaining": cooldown_remaining,
        "cooldown_seconds": cooldown_seconds,
        "resends_used": resend_count,
        "resends_max": max_resends,
        "resends_remaining": max(0, max_resends - resend_count),
        "attempts": attempts,
        "attempts_max": max_attempts,
        "attempts_remaining": max(0, max_attempts - attempts),
        "is_locked": is_locked_out(user),
    }