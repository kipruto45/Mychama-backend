"""
Account management services including OTP generation, verification, and KYC validation.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.accounts.models import (
    OTPDeliveryChannel,
    OTPDeliveryLog,
    OTPDeliveryMethod,
    OTPDeliveryStatus,
    OTPPurpose,
    OTPToken,
    User,
)
from apps.notifications.email import send_email_message
from apps.notifications.sms import send_sms_message
from core.audit import create_audit_log

logger = logging.getLogger(__name__)

# OTP Errors
class OTPError(Exception):
    """Custom exception for OTP operations"""
    pass


class OTPRateLimitError(OTPError):
    """Raised when OTP request limits are exceeded."""


class OTPDeliveryError(OTPError):
    """Raised when OTP delivery fails across all available channels."""


@dataclass
class OTPDispatchResult:
    requested_method: str
    sms_sent: bool = False
    email_sent: bool = False
    failed_channels: list[str] = field(default_factory=list)
    masked_phone: str = ""
    masked_email: str = ""

    @property
    def success(self) -> bool:
        return self.sms_sent or self.email_sent

    @property
    def channels_sent(self) -> list[str]:
        channels: list[str] = []
        if self.sms_sent:
            channels.append(OTPDeliveryChannel.SMS)
        if self.email_sent:
            channels.append(OTPDeliveryChannel.EMAIL)
        return channels

    def __bool__(self) -> bool:
        return self.success

# KYC file validation constants
KYC_ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/jpg"}
KYC_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
KYC_MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB


class OTPService:
    """Service for managing One-Time Password tokens."""

    @staticmethod
    def otp_expiry_minutes() -> int:
        """Get OTP expiry time in minutes from settings (default 5)."""
        return int(getattr(settings, "OTP_EXPIRY_MINUTES", 5))

    @staticmethod
    def max_otp_attempts() -> int:
        """Get max OTP verification attempts from settings (default 5)."""
        return int(getattr(settings, "OTP_MAX_ATTEMPTS", 5))
    
    @staticmethod
    def cooldown_seconds() -> int:
        """Get cooldown between OTP requests in seconds (default 60)."""
        return int(getattr(settings, "OTP_COOLDOWN_SECONDS", 60))

    @staticmethod
    def resend_window_seconds() -> int:
        """Get rolling resend window in seconds (default 10 minutes)."""
        return int(getattr(settings, "OTP_RESEND_WINDOW_SECONDS", 600))

    @staticmethod
    def max_resends_per_window() -> int:
        """Get max OTP requests allowed in the resend window."""
        return int(getattr(settings, "OTP_MAX_RESENDS_PER_WINDOW", 3))

    @staticmethod
    def delivery_retry_limit() -> int:
        """Get additional retry attempts per channel after the first send."""
        return int(getattr(settings, "OTP_DELIVERY_RETRY_LIMIT", 2))

    @staticmethod
    def lockout_seconds() -> int:
        """Get account lockout duration after repeated OTP failures."""
        return int(getattr(settings, "OTP_LOCKOUT_SECONDS", 600))

    @staticmethod
    def _mask_phone(phone: str) -> str:
        cleaned = str(phone or "").strip()
        if len(cleaned) <= 6:
            return cleaned
        return f"{cleaned[:5]}***{cleaned[-3:]}"

    @staticmethod
    def _mask_email(email: str) -> str:
        cleaned = str(email or "").strip()
        if "@" not in cleaned:
            return cleaned
        local_part, domain = cleaned.split("@", 1)
        masked_local = (local_part[:1] + "***") if local_part else "***"
        return f"{masked_local}@{domain}"

    @staticmethod
    def _build_message(otp_token: OTPToken, plain_code: str) -> str:
        purpose_messages = {
            OTPPurpose.REGISTER: "Your Digital Chama registration code is:",
            OTPPurpose.LOGIN_2FA: "Your Digital Chama login code is:",
            OTPPurpose.PASSWORD_RESET: "Your Digital Chama password reset code is:",
            OTPPurpose.WITHDRAWAL_CONFIRM: "Your Digital Chama withdrawal confirmation code is:",
            OTPPurpose.VERIFY_PHONE: "Your Digital Chama verification code is:",
        }
        prefix = purpose_messages.get(otp_token.purpose, "Your Digital Chama code is:")
        return (
            f"{prefix} {plain_code}. "
            f"Valid for {OTPService.otp_expiry_minutes()} minutes. "
            "Do not share this code."
        )

    @staticmethod
    def _preferred_channels(
        requested_method: str,
        *,
        user: User | None,
    ) -> list[str]:
        channels: list[str] = []
        wants_email = bool(user and user.email)

        if requested_method == OTPDeliveryMethod.EMAIL and wants_email:
            channels.append(OTPDeliveryChannel.EMAIL)
        if requested_method in {OTPDeliveryMethod.SMS, OTPDeliveryMethod.BOTH}:
            channels.append(OTPDeliveryChannel.SMS)
        if requested_method == OTPDeliveryMethod.BOTH and wants_email:
            channels.append(OTPDeliveryChannel.EMAIL)
        # Always include email as fallback if user has email and SMS is being sent
        if requested_method == OTPDeliveryMethod.SMS and wants_email:
            channels.append(OTPDeliveryChannel.EMAIL)

        # Deduplicate while preserving order.
        return list(dict.fromkeys(channels))

    @staticmethod
    def _log_delivery_attempt(
        *,
        otp_token: OTPToken,
        user: User | None,
        channel: str,
        status: str,
        destination: str,
        attempt_number: int,
        provider_name: str = "",
        provider_message_id: str = "",
        error_message: str = "",
        provider_response: dict | None = None,
    ) -> None:
        OTPDeliveryLog.objects.create(
            otp_token=otp_token,
            user=user,
            channel=channel,
            provider_name=provider_name,
            provider_message_id=provider_message_id,
            status=status,
            destination=destination,
            attempt_number=attempt_number,
            error_message=error_message,
            provider_response=provider_response or {},
        )

    @staticmethod
    def _resolve_billing_chama(user: User | None):
        if not user or not getattr(user, "is_authenticated", False):
            return None

        from apps.chama.models import ChamaStatus, MemberStatus, Membership

        preferred_chama_id = None
        if hasattr(user, "preferences"):
            preferred_chama_id = getattr(user.preferences, "active_chama_id", None)

        if preferred_chama_id:
            preferred_membership = (
                Membership.objects.filter(
                    user=user,
                    chama_id=preferred_chama_id,
                    status=MemberStatus.ACTIVE,
                    is_active=True,
                    is_approved=True,
                    chama__status=ChamaStatus.ACTIVE,
                )
                .select_related("chama")
                .first()
            )
            if preferred_membership:
                return preferred_membership.chama

        membership = (
            Membership.objects.filter(
                user=user,
                status=MemberStatus.ACTIVE,
                is_active=True,
                is_approved=True,
                chama__status=ChamaStatus.ACTIVE,
            )
            .select_related("chama")
            .order_by("-created_at")
            .first()
        )
        return membership.chama if membership else None

    @staticmethod
    def _send_sms_channel(
        *,
        phone: str,
        message: str,
        otp_token: OTPToken,
        user: User | None,
    ) -> bool:
        billing_chama = OTPService._resolve_billing_chama(user or otp_token.user)
        if billing_chama:
            from apps.billing.metering import usage_within_limit
            from apps.billing.models import UsageMetric

            usage = usage_within_limit(billing_chama, UsageMetric.OTP_SMS, 1)
            if not usage["allowed"]:
                logger.warning(
                    "SMS OTP blocked by billing limit for user=%s chama=%s",
                    getattr(user or otp_token.user, "id", None),
                    billing_chama.id,
                )
                OTPService._log_delivery_attempt(
                    otp_token=otp_token,
                    user=user,
                    channel=OTPDeliveryChannel.SMS,
                    status=OTPDeliveryStatus.FAILED,
                    destination=phone,
                    attempt_number=1,
                    error_message=(
                        "SMS quota exceeded for the active subscription."
                    ),
                )
                return False

        for attempt_number in range(1, OTPService.delivery_retry_limit() + 2):
            try:
                result = send_sms_message(phone_number=phone, message=message)
                if billing_chama:
                    from apps.billing.metering import increment_usage
                    from apps.billing.models import UsageMetric

                    increment_usage(billing_chama, UsageMetric.OTP_SMS, 1)
                OTPService._log_delivery_attempt(
                    otp_token=otp_token,
                    user=user,
                    channel=OTPDeliveryChannel.SMS,
                    status=OTPDeliveryStatus.SENT,
                    destination=phone,
                    attempt_number=attempt_number,
                    provider_name=result.provider,
                    provider_message_id=result.provider_message_id,
                    provider_response=result.raw_response or {},
                )
                return True
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "SMS OTP delivery failed for %s on attempt %s",
                    phone,
                    attempt_number,
                )
                OTPService._log_delivery_attempt(
                    otp_token=otp_token,
                    user=user,
                    channel=OTPDeliveryChannel.SMS,
                    status=OTPDeliveryStatus.FAILED,
                    destination=phone,
                    attempt_number=attempt_number,
                    provider_name=getattr(getattr(exc, "__class__", None), "__name__", ""),
                    error_message=str(exc),
                )
        return False

    @staticmethod
    def _send_email_channel(
        *,
        user: User,
        message: str,
        otp_token: OTPToken,
    ) -> bool:
        if not user.email:
            return False

        for attempt_number in range(1, OTPService.delivery_retry_limit() + 2):
            try:
                result = send_email_message(
                    subject="Verification Code",
                    recipient_list=[user.email],
                    body=message,
                )
                OTPService._log_delivery_attempt(
                    otp_token=otp_token,
                    user=user,
                    channel=OTPDeliveryChannel.EMAIL,
                    status=OTPDeliveryStatus.SENT,
                    destination=user.email,
                    attempt_number=attempt_number,
                    provider_name=result.provider,
                    provider_message_id=result.provider_message_id,
                    provider_response=result.raw_response or {},
                )
                return True
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Email OTP delivery failed for %s on attempt %s",
                    user.email,
                    attempt_number,
                )
                OTPService._log_delivery_attempt(
                    otp_token=otp_token,
                    user=user,
                    channel=OTPDeliveryChannel.EMAIL,
                    status=OTPDeliveryStatus.FAILED,
                    destination=user.email,
                    attempt_number=attempt_number,
                    provider_name=getattr(getattr(exc, "__class__", None), "__name__", ""),
                    error_message=str(exc),
                )
        return False

    @staticmethod
    def _lock_user_for_otp_failures(user: User) -> None:
        user.locked_until = timezone.now() + timedelta(seconds=OTPService.lockout_seconds())
        user.save(update_fields=["locked_until"])

    @staticmethod
    def _normalize_provider_message_id(provider_message_id: str | None) -> str:
        raw_value = str(provider_message_id or "").strip()
        if not raw_value:
            return ""
        normalized = raw_value.strip("<>")
        return normalized.split(".", 1)[0]

    @staticmethod
    def record_delivery_callback(
        *,
        channel: str,
        status: str,
        provider_name: str = "",
        provider_message_id: str = "",
        destination: str = "",
        error_message: str = "",
        provider_payload: dict | None = None,
    ) -> OTPDeliveryLog | None:
        normalized_message_id = OTPService._normalize_provider_message_id(
            provider_message_id
        )

        queryset = OTPDeliveryLog.objects.select_related("otp_token", "user").filter(
            channel=channel
        )
        if provider_name:
            queryset = queryset.filter(provider_name__iexact=provider_name)

        if normalized_message_id:
            queryset = queryset.filter(
                Q(provider_message_id=normalized_message_id)
                | Q(provider_message_id__startswith=f"{normalized_message_id}.")
                | Q(provider_message_id__contains=normalized_message_id)
            )
        elif destination:
            queryset = queryset.filter(destination=destination)
        else:
            return None

        delivery_log = queryset.order_by("-created_at").first()
        if not delivery_log:
            return None

        update_fields: list[str] = []
        if provider_name and not delivery_log.provider_name:
            delivery_log.provider_name = provider_name
            update_fields.append("provider_name")
        if normalized_message_id and not delivery_log.provider_message_id:
            delivery_log.provider_message_id = normalized_message_id
            update_fields.append("provider_message_id")
        if status and delivery_log.status != status:
            delivery_log.status = status
            update_fields.append("status")
        if error_message:
            delivery_log.error_message = str(error_message)
            update_fields.append("error_message")
        if provider_payload:
            provider_response = dict(delivery_log.provider_response or {})
            provider_response["delivery_callback"] = provider_payload
            delivery_log.provider_response = provider_response
            update_fields.append("provider_response")

        if not update_fields:
            return delivery_log

        update_fields.append("updated_at")
        delivery_log.save(update_fields=list(dict.fromkeys(update_fields)))

        create_audit_log(
            actor=delivery_log.user,
            action="otp_delivery_status_updated",
            entity_type="OTPDeliveryLog",
            entity_id=delivery_log.id,
            metadata={
                "channel": channel,
                "status": delivery_log.status,
                "provider_name": delivery_log.provider_name,
                "provider_message_id": delivery_log.provider_message_id,
                "otp_token_id": str(delivery_log.otp_token_id),
            },
        )
        return delivery_log

    @staticmethod
    def generate_otp(
        phone: str,
        user: User | None = None,
        *,
        purpose: str = OTPPurpose.VERIFY_PHONE,
        delivery_method: str = None,
        ip_address: str = None,
        user_agent: str = "",
    ) -> tuple[OTPToken, str]:
        """
        Generate a new OTP token for the phone number.
        Returns (OTPToken, plain_code) tuple.
        Invalidates any existing unused OTPs.
        """
        from django.conf import settings
        from core.utils import normalize_kenyan_phone

        # Use default delivery method from settings if not specified
        if delivery_method is None:
            delivery_method = getattr(settings, 'OTP_DEFAULT_DELIVERY_METHOD', 'sms')
        now = timezone.now()
        expires_at = now + timedelta(minutes=OTPService.otp_expiry_minutes())

        with transaction.atomic():
            recent_count = OTPToken.objects.filter(
                phone=phone,
                purpose=purpose,
                created_at__gte=now - timedelta(seconds=OTPService.resend_window_seconds()),
            ).count()
            if recent_count >= OTPService.max_resends_per_window():
                raise OTPRateLimitError(
                    "Too many OTP requests. Please wait before requesting another code."
                )

            recent_otp = (
                OTPToken.objects.select_for_update()
                .filter(
                    phone=phone,
                    purpose=purpose,
                    is_used=False,
                )
                .order_by("-created_at")
                .first()
            )

            if recent_otp and not recent_otp.can_resend:
                remaining = recent_otp.cooldown_seconds - (
                    timezone.now() - recent_otp.last_sent_at
                ).total_seconds()
                raise OTPRateLimitError(
                    f"Please wait {int(max(1, remaining))} seconds before requesting another OTP"
                )

            OTPToken.objects.filter(
                phone=phone,
                purpose=purpose,
                is_used=False,
            ).update(is_used=True, code="")

            code = str(secrets.randbelow(999999)).zfill(6)
            code_hash = OTPToken.hash_code(code, phone, purpose)

            otp = OTPToken.objects.create(
                phone=phone,
                user=user,
                code="",
                code_hash=code_hash,
                purpose=purpose,
                delivery_method=delivery_method,
                expires_at=expires_at,
                max_attempts=OTPService.max_otp_attempts(),
                cooldown_seconds=OTPService.cooldown_seconds(),
                ip_address=ip_address,
                user_agent=user_agent,
            )

        # Log for audit
        create_audit_log(
            actor=user,
            action="otp_generated",
            entity_type="OTPToken",
            entity_id=otp.id,
            metadata={
                "phone": phone,
                "delivery_method": delivery_method,
                "purpose": purpose,
                "expires_in_minutes": OTPService.otp_expiry_minutes(),
                "ip_address": ip_address,
            },
        )

        return otp, code

    @staticmethod
    def send_otp(
        phone: str,
        otp_token: OTPToken,
        plain_code: str,
        user: User | None = None,
    ) -> OTPDispatchResult:
        """
        Send OTP code via one or more channels.
        Returns a structured result and raises if every channel fails.
        """
        from core.utils import normalize_kenyan_phone

        phone = normalize_kenyan_phone(phone)
        message = OTPService._build_message(otp_token, plain_code)
        result = OTPDispatchResult(
            requested_method=otp_token.delivery_method,
            masked_phone=OTPService._mask_phone(phone),
            masked_email=OTPService._mask_email(user.email) if user and user.email else "",
        )

        channels = OTPService._preferred_channels(otp_token.delivery_method, user=user)
        if not channels:
            channels = [OTPDeliveryChannel.SMS]

        if OTPDeliveryChannel.SMS in channels:
            result.sms_sent = OTPService._send_sms_channel(
                phone=phone,
                message=message,
                otp_token=otp_token,
                user=user,
            )
            if not result.sms_sent:
                result.failed_channels.append(OTPDeliveryChannel.SMS)

        if OTPDeliveryChannel.EMAIL in channels and user and user.email:
            result.email_sent = OTPService._send_email_channel(
                user=user,
                message=message,
                otp_token=otp_token,
            )
            if not result.email_sent:
                result.failed_channels.append(OTPDeliveryChannel.EMAIL)

        now = timezone.now()
        update_kwargs: dict[str, object] = {
            "last_sent_at": now,
            "code": "",
        }
        
        # In development mode with PRINT_OTP_IN_CONSOLE, don't clear the code
        from django.conf import settings
        if getattr(settings, 'DEBUG', False) and getattr(settings, 'PRINT_OTP_IN_CONSOLE', False):
            # Keep code for dev retrieval, but print to console
            update_kwargs["code"] = plain_code
            OTPService.print_otp_to_console(phone, plain_code, otp_token.purpose)
        
        successful_count = len(result.channels_sent)
        if successful_count:
            update_kwargs["sent_count"] = F("sent_count") + successful_count
        OTPToken.objects.filter(id=otp_token.id).update(**update_kwargs)
        otp_token.last_sent_at = now
        otp_token.code = plain_code if getattr(settings, 'PRINT_OTP_IN_CONSOLE', False) and settings.DEBUG else ""
        otp_token.sent_count += successful_count

        if not result.success:
            create_audit_log(
                actor=user,
                action="otp_delivery_failed",
                entity_type="OTPToken",
                entity_id=otp_token.id,
                metadata={
                    "phone": phone,
                    "delivery_method": otp_token.delivery_method,
                    "purpose": otp_token.purpose,
                    "failed_channels": result.failed_channels,
                },
            )
            raise OTPDeliveryError(
                "Unable to deliver the verification code right now. Please try again."
            )

        create_audit_log(
            actor=user,
            action="otp_sent",
            entity_type="OTPToken",
            entity_id=otp_token.id,
            metadata={
                "phone": phone,
                "delivery_method": otp_token.delivery_method,
                "purpose": otp_token.purpose,
                "channels_sent": result.channels_sent,
                "failed_channels": result.failed_channels,
            },
        )

        logger.info("OTP sent to %s via %s", phone, ",".join(result.channels_sent))
        return result

    @staticmethod
    def verify_otp(
        phone: str,
        code: str,
        *,
        purpose: str = OTPPurpose.VERIFY_PHONE,
        user: User | None = None,
    ) -> tuple[bool, str]:
        """
        Verify OTP code for phone number.
        Returns (success, message) tuple.
        """
        from core.utils import normalize_kenyan_phone

        try:
            phone = normalize_kenyan_phone(phone)
        except ValueError:
            return False, "Invalid phone number."

        if not code or len(code) != 6 or not code.isdigit():
            return False, "Invalid OTP format. Please enter 6 digits."

        with transaction.atomic():
            otp = (
                OTPToken.objects.select_for_update()
                .filter(
                    phone=phone,
                    purpose=purpose,
                    is_used=False,
                )
                .order_by("-created_at")
                .first()
            )

            if not otp:
                return False, "No active OTP found. Please request a new code."

            lock_target = user or otp.user or User.objects.filter(phone=phone, is_active=True).first()
            if lock_target and lock_target.is_locked():
                return (
                    False,
                    "Too many failed attempts. Please wait before trying again.",
                )

            if otp.is_expired:
                otp.is_used = True
                otp.code = ""
                otp.save(update_fields=["is_used", "code"])
                return False, "OTP has expired. Please request a new code."

            if otp.attempts >= otp.max_attempts:
                if lock_target and not lock_target.is_locked():
                    OTPService._lock_user_for_otp_failures(lock_target)
                return False, "Too many failed attempts. Please request a new OTP."

            if not otp.verify(code):
                logger.warning("Invalid OTP attempt for %s", phone)
                if lock_target and otp.attempts >= otp.max_attempts:
                    OTPService._lock_user_for_otp_failures(lock_target)
                    return (
                        False,
                        "Too many failed attempts. Please request a new OTP and try again later.",
                    )
                remaining = max(0, otp.max_attempts - otp.attempts)
                return False, f"Invalid OTP code. {remaining} attempts remaining."

            audit_actor = user or otp.user
            if audit_actor:
                create_audit_log(
                    actor=audit_actor,
                    action="otp_verified",
                    entity_type="OTPToken",
                    entity_id=otp.id,
                    metadata={
                        "delivery_method": otp.delivery_method,
                        "purpose": purpose,
                    },
                )

        logger.info(f"OTP successfully verified for {phone}")
        return True, "OTP verified successfully."

    # ==================== Development Methods ====================
    # These methods are ONLY enabled in development mode
    # They are automatically disabled in production

    @staticmethod
    def print_otp_to_console(phone: str, code: str, purpose: str) -> None:
        """
        Print OTP to console for development purposes.
        Only works when DEBUG=True and PRINT_OTP_IN_CONSOLE=True.
        NEVER prints in production.
        """
        from django.conf import settings
        
        # Security check: Only run in development
        if not settings.DEBUG:
            return
        
        if not getattr(settings, 'PRINT_OTP_IN_CONSOLE', False):
            return
        
        masked_phone = OTPService._mask_phone(phone)
        logger.info("=" * 60)
        logger.info("DEV OTP for %s (%s): %s", masked_phone, purpose, code)
        logger.info("=" * 60)
        # Also print to stdout for visibility in Docker logs
        print(f"\n{'=' * 60}")
        print(f"DEV OTP for {masked_phone} ({purpose}): {code}")
        print(f"{'=' * 60}\n")

    @staticmethod
    def get_dev_otp(phone: str, purpose: str = None) -> dict | None:
        """
        Retrieve the latest OTP for a phone number in development mode.
        
        SECURITY: This is ONLY available when:
        - DEBUG=True
        - ENABLE_DEV_OTP_ENDPOINT=True
        - Correct DEV_OTP_SECRET_TOKEN is provided
        
        Args:
            phone: The phone number to get OTP for
            purpose: Optional purpose filter. If None, returns latest OTP for any purpose.
        
        Returns dict with OTP info or None if not available/allowed.
        """
        from django.conf import settings
        from django.utils import timezone
        
        # Security check: Only run in development
        if not settings.DEBUG:
            logger.warning("Dev OTP endpoint accessed in production - BLOCKED")
            return None
        
        if not getattr(settings, 'ENABLE_DEV_OTP_ENDPOINT', False):
            return None
        
        # Build query - optionally filter by purpose
        query = OTPToken.objects.filter(
            phone=phone,
            is_used=False,
            expires_at__gt=timezone.now()
        )
        
        if purpose:
            query = query.filter(purpose=purpose)
        
        otp = query.order_by('-created_at').first()
        
        if not otp:
            return None
        
        # Calculate remaining time
        remaining_seconds = int((otp.expires_at - timezone.now()).total_seconds())
        
        result = {
            "phone": phone,
            "purpose": otp.purpose,
            "code": otp.code,  # Always return code in dev mode via secure endpoint
            "expires_in_seconds": remaining_seconds,
            "created_at": otp.created_at.isoformat(),
            "expires_at": otp.expires_at.isoformat(),
        }
        
        return result

    @staticmethod
    def is_dev_mode() -> bool:
        """Check if running in development mode with dev features enabled."""
        from django.conf import settings
        return getattr(settings, 'DEBUG', False) and getattr(settings, 'ENABLE_DEV_OTP_ENDPOINT', False)


class KYCService:
    """Service for KYC document validation."""

    @staticmethod
    def validate_id_image(file: UploadedFile, field_name: str = "image") -> tuple[bool, str]:
        """
        Validate KYC ID image file.
        Returns (valid, error_message) tuple.
        """
        if not file:
            return True, ""  # Optional field

        # Check file size
        if file.size > KYC_MAX_FILE_SIZE:
            return (
                False,
                f"{field_name} size must be less than 5MB (current: {file.size / (1024*1024):.2f}MB)",
            )

        # Check file extension
        if not hasattr(file, "name") or file.name:
            file_name = getattr(file, "name", "")
            if file_name:
                ext = ("." + file_name.split(".")[-1].lower()) if "." in file_name else ""
                if ext and ext not in KYC_ALLOWED_EXTENSIONS:
                    return (
                        False,
                        f"{field_name} must be JPG or PNG format (got {ext})",
                    )

        # Check MIME type if available
        mime_type = getattr(file, "content_type", "")
        if mime_type and mime_type not in KYC_ALLOWED_MIME_TYPES:
            return (
                False,
                f"{field_name} must be a valid image file (got {mime_type})",
            )

        return True, ""

    @staticmethod
    def validate_kyc_images(
        id_front_file: UploadedFile = None, selfie_file: UploadedFile = None
    ) -> dict[str, str]:
        """
        Validate all KYC image files.
        Returns dict of field_name -> error_message (empty if valid).
        """
        errors = {}

        valid, msg = KYCService.validate_id_image(id_front_file, "ID front image")
        if not valid:
            errors["id_front_image"] = msg

        valid, msg = KYCService.validate_id_image(selfie_file, "Selfie image")
        if not valid:
            errors["selfie_image"] = msg

        return errors
