"""
Transaction PIN Service

Manages transaction PIN and withdrawal PIN with lockout escalation.
"""

import logging
from datetime import timedelta
from enum import IntEnum

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User

logger = logging.getLogger(__name__)


class PinType(IntEnum):
    TRANSACTION = 1
    WITHDRAWAL = 2


class PinLockoutLevel(IntEnum):
    NONE = 0
    FIVE_MINUTES = 1
    THIRTY_MINUTES = 2
    TWENTY_FOUR_HOURS = 3
    FROZEN = 4


PIN_LOCKOUT_CONFIG = {
    PinLockoutLevel.NONE: None,
    PinLockoutLevel.FIVE_MINUTES: lambda: timedelta(
        seconds=max(60, int(getattr(settings, "PIN_LOCKOUT_STAGE_1_SECONDS", 300)))
    ),
    PinLockoutLevel.THIRTY_MINUTES: lambda: timedelta(
        seconds=max(60, int(getattr(settings, "PIN_LOCKOUT_STAGE_2_SECONDS", 1800)))
    ),
    PinLockoutLevel.TWENTY_FOUR_HOURS: lambda: timedelta(
        seconds=max(60, int(getattr(settings, "PIN_LOCKOUT_STAGE_3_SECONDS", 86400)))
    ),
    PinLockoutLevel.FROZEN: None,
}

def hash_pin(pin: str) -> str:
    """Hash PIN using bcrypt_sha256 via Django's password hashers."""
    return make_password(pin, hasher="bcrypt_sha256")


class PinService:
    """Service for managing transaction and withdrawal PINs."""

    @staticmethod
    @transaction.atomic
    def set_pin(user: User, pin: str, pin_type: PinType) -> bool:
        """Set a new PIN for user."""
        from .models import MemberPinSecret

        if not PinService._validate_pin_format(pin):
            raise ValueError("PIN must be 4-6 digits")

        pin_hash = hash_pin(pin)

        MemberPinSecret.objects.update_or_create(
            user=user,
            pin_type=PinService._pin_type_value(pin_type),
            defaults={
                "pin_hash": pin_hash,
                "salt": "",
                "is_locked": False,
                "failed_attempts": 0,
                "lockout_level": PinLockoutLevel.NONE,
                "locked_until": None,
                "rotated_at": timezone.now(),
            },
        )

        logger.info(f"PIN set for user {user.id}, type: {pin_type.name}")
        return True

    @staticmethod
    def verify_pin(user: User, pin: str, pin_type: PinType) -> tuple[bool, str]:
        """Verify a PIN and handle lockout logic."""
        from .models import MemberPinSecret

        try:
            pin_secret = MemberPinSecret.objects.get(
                user=user,
                pin_type=PinService._pin_type_value(pin_type),
            )
        except MemberPinSecret.DoesNotExist:
            return False, "PIN not set"

        if pin_secret.is_locked_out:
            if pin_secret.locked_until and timezone.now() < pin_secret.locked_until:
                remaining = (pin_secret.locked_until - timezone.now()).seconds // 60
                return False, f"Account locked. Try again in {remaining} minutes"
            elif pin_secret.lockout_level >= PinLockoutLevel.FROZEN:
                return False, "Account frozen. Contact support."
        elif pin_secret.is_locked and pin_secret.locked_until and timezone.now() >= pin_secret.locked_until:
            pin_secret.is_locked = False
            pin_secret.locked_until = None
            pin_secret.save(update_fields=["is_locked", "locked_until", "updated_at"])

        if not pin_secret.pin_hash:
            return False, "PIN not set"

        valid = check_password(pin, pin_secret.pin_hash)
        legacy_valid = False
        if not valid and len(pin_secret.pin_hash) == 64 and pin_secret.salt:
            legacy_valid = PinService._verify_legacy_pin(
                pin=pin,
                stored_hash=pin_secret.pin_hash,
                salt=pin_secret.salt,
            )
            valid = legacy_valid

        if valid:
            pin_secret.failed_attempts = 0
            pin_secret.lockout_level = PinLockoutLevel.NONE
            pin_secret.locked_until = None
            pin_secret.is_locked = False
            update_fields = [
                "failed_attempts",
                "lockout_level",
                "locked_until",
                "is_locked",
                "updated_at",
            ]
            if legacy_valid:
                pin_secret.pin_hash = hash_pin(pin)
                pin_secret.salt = ""
                update_fields.extend(["pin_hash", "salt"])
            pin_secret.save(update_fields=update_fields)
            return True, "OK"

        pin_secret.failed_attempts += 1

        new_level = PinService._calculate_lockout_level(pin_secret.failed_attempts)

        if new_level > pin_secret.lockout_level:
            pin_secret.lockout_level = new_level
            lockout_duration = PIN_LOCKOUT_CONFIG.get(new_level)
            if lockout_duration:
                pin_secret.locked_until = timezone.now() + lockout_duration()
                pin_secret.is_locked = True
            if new_level >= PinLockoutLevel.FROZEN:
                pin_secret.is_locked = True
                pin_secret.locked_until = None
                PinService._freeze_account_for_pin_abuse(user=user)

        pin_secret.save(
            update_fields=[
                "failed_attempts",
                "lockout_level",
                "locked_until",
                "is_locked",
                "updated_at",
            ]
        )

        logger.warning(
            f"PIN verification failed for user {user.id}, type: {pin_type.name}, "
            f"attempts: {pin_secret.failed_attempts}, new_level: {pin_secret.lockout_level}"
        )

        if pin_secret.lockout_level >= PinLockoutLevel.FROZEN:
            return False, "Account frozen due to too many failed attempts. Contact support."
        elif pin_secret.locked_until:
            remaining = (pin_secret.locked_until - timezone.now()).seconds // 60
            return False, f"Too many attempts. Try again in {remaining} minutes"
        else:
            remaining = max(
                0,
                PinService._threshold_for_level(pin_secret.lockout_level + 1)
                - pin_secret.failed_attempts,
            )
            return False, f"Incorrect PIN. {remaining} attempts remaining"

    @staticmethod
    def change_pin(user: User, old_pin: str, new_pin: str, pin_type: PinType) -> tuple[bool, str]:
        """Change PIN with old PIN verification."""
        valid, msg = PinService.verify_pin(user, old_pin, pin_type)
        if not valid:
            return False, msg

        return PinService.set_pin(user, new_pin, pin_type), "PIN changed successfully"

    @staticmethod
    def reset_pin(user: User, pin_type: PinType, otp_verified: bool = False) -> bool:
        """Reset PIN (requires OTP verification)."""
        from .models import MemberPinSecret

        if not otp_verified:
            raise PermissionError("OTP verification required for PIN reset")

        MemberPinSecret.objects.filter(
            user=user,
            pin_type=PinService._pin_type_value(pin_type),
        ).update(
            pin_hash="",
            salt="",
            failed_attempts=0,
            lockout_level=PinLockoutLevel.NONE,
            locked_until=None,
            is_locked=False,
            rotated_at=timezone.now(),
        )

        logger.info(f"PIN reset for user {user.id}, type: {pin_type.name}")
        return True

    @staticmethod
    def unlock_pin(user: User, pin_type: PinType, admin_user: User = None) -> bool:
        """Unlock PIN (admin function)."""
        from .models import MemberPinSecret

        updated = MemberPinSecret.objects.filter(
            user=user,
            pin_type=PinService._pin_type_value(pin_type),
        ).update(
            failed_attempts=0,
            lockout_level=PinLockoutLevel.NONE,
            locked_until=None,
            is_locked=False,
            updated_by=admin_user,
        )
        if updated and user.locked_until and user.locked_until > timezone.now():
            user.locked_until = None
            user.save(update_fields=["locked_until"])

        if updated:
            logger.warning(f"PIN unlocked for user {user.id}, type: {pin_type.name}, by: {admin_user.id if admin_user else 'system'}")

        return bool(updated)

    @staticmethod
    def has_pin(user: User, pin_type: PinType) -> bool:
        """Check if user has set a PIN."""
        from .models import MemberPinSecret

        return MemberPinSecret.objects.filter(
            user=user,
            pin_type=PinService._pin_type_value(pin_type),
            pin_hash__isnull=False,
        ).exclude(pin_hash="").exists()

    @staticmethod
    def _validate_pin_format(pin: str) -> bool:
        """Validate PIN format (4-6 digits)."""
        return bool(pin and pin.isdigit() and 4 <= len(pin) <= 6)

    @staticmethod
    def _calculate_lockout_level(failed_attempts: int) -> PinLockoutLevel:
        """Calculate lockout level based on failed attempts."""
        if failed_attempts >= PinService._threshold_for_level(PinLockoutLevel.FROZEN):
            return PinLockoutLevel.FROZEN
        elif failed_attempts >= PinService._threshold_for_level(PinLockoutLevel.TWENTY_FOUR_HOURS):
            return PinLockoutLevel.TWENTY_FOUR_HOURS
        elif failed_attempts >= PinService._threshold_for_level(PinLockoutLevel.THIRTY_MINUTES):
            return PinLockoutLevel.THIRTY_MINUTES
        elif failed_attempts >= PinService._threshold_for_level(PinLockoutLevel.FIVE_MINUTES):
            return PinLockoutLevel.FIVE_MINUTES
        else:
            return PinLockoutLevel.NONE

    @staticmethod
    def _threshold_for_level(level: int | PinLockoutLevel) -> int:
        base = max(1, int(getattr(settings, "PIN_MAX_ATTEMPTS", 5)))
        normalized = int(level)
        if normalized <= 0:
            return base
        return base * normalized

    @staticmethod
    def _pin_type_value(pin_type: PinType | str) -> str:
        if isinstance(pin_type, PinType):
            return pin_type.name.lower()
        return str(pin_type).strip().lower()

    @staticmethod
    def _verify_legacy_pin(*, pin: str, stored_hash: str, salt: str) -> bool:
        import hashlib

        combined = f"{pin}:{salt}:{settings.SECRET_KEY}"
        legacy_hash = hashlib.pbkdf2_hmac(
            "sha256",
            combined.encode(),
            b"pin_salt",
            100000,
        ).hex()
        return legacy_hash == stored_hash

    @staticmethod
    def _freeze_account_for_pin_abuse(*, user: User) -> None:
        if not getattr(settings, "PIN_LOCKOUT_FREEZE_ON_ESCALATION", True):
            return
        user.locked_until = timezone.now() + timedelta(days=3650)
        user.save(update_fields=["locked_until"])


class StepUpAuthService:
    """Service for step-up authentication."""

    @staticmethod
    def requires_step_up(user: User, action: str, risk_score: int = 0) -> tuple[bool, str]:
        """Determine if action requires step-up authentication."""
        if risk_score >= 60:
            return True, "pin_required"

        high_risk_actions = ["withdraw", "payout", "link_bank", "change_phone"]

        if action in high_risk_actions:
            return True, "pin_required"

        return False, ""

    @staticmethod
    def verify_step_up(user: User, pin: str, pin_type: PinType = PinType.TRANSACTION) -> tuple[bool, str]:
        """Verify step-up authentication."""
        return PinService.verify_pin(user, pin, pin_type)


__all__ = [
    "PinService",
    "StepUpAuthService",
    "PinType",
    "PinLockoutLevel",
]
