"""
Account management services including OTP generation, verification, and KYC validation.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import timedelta
from io import BytesIO

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from PIL import Image, ImageFilter, ImageStat, UnidentifiedImageError

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
from core.utils import normalize_kenyan_phone

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
        return int(getattr(settings, "OTP_LOCKOUT_SECONDS", 1800))

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
            OTPPurpose.VERIFY_EMAIL: "Your Digital Chama email verification code is:",
        }
        prefix = purpose_messages.get(otp_token.purpose, "Your Digital Chama code is:")
        return (
            f"{prefix} {plain_code}. "
            f"Valid for {OTPService.otp_expiry_minutes()} minutes. "
            "Do not share this code."
        )

    @staticmethod
    def normalize_identifier(identifier: str, purpose: str) -> str:
        raw_identifier = str(identifier or "").strip()
        if not raw_identifier:
            raise ValueError("Verification identifier is required.")

        if purpose == OTPPurpose.VERIFY_EMAIL or "@" in raw_identifier:
            return raw_identifier.lower()

        return normalize_kenyan_phone(raw_identifier)

    @staticmethod
    def _preferred_channels(
        requested_method: str,
        *,
        user: User | None,
    ) -> list[str]:
        channels: list[str] = []
        has_email = bool(user and user.email)

        if requested_method == OTPDeliveryMethod.SMS:
            channels.append(OTPDeliveryChannel.SMS)
        elif requested_method == OTPDeliveryMethod.EMAIL:
            channels.append(OTPDeliveryChannel.EMAIL)
        elif requested_method == OTPDeliveryMethod.BOTH:
            channels.append(OTPDeliveryChannel.SMS)
            if has_email:
                channels.append(OTPDeliveryChannel.EMAIL)

        return channels

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

        from apps.chama.models import ChamaStatus, Membership, MemberStatus

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
        user: User | None,
        message: str,
        otp_token: OTPToken,
        plain_code: str,
        destination_email: str,
    ) -> bool:
        if not destination_email:
            return False

        for attempt_number in range(1, OTPService.delivery_retry_limit() + 2):
            try:
                subject = "Verification Code"
                html_body = ""
                if otp_token.purpose == OTPPurpose.PASSWORD_RESET:
                    try:
                        from django.conf import settings
                        from django.template.loader import render_to_string

                        context = {
                            "user_name": (
                                user.get_display_name()
                                if user and hasattr(user, "get_display_name")
                                else getattr(user, "full_name", "") or "there"
                            ),
                            "otp_code": plain_code,
                            "expiry_minutes": OTPService.otp_expiry_minutes(),
                            "app_url": getattr(settings, "FRONTEND_URL", "") or getattr(settings, "SITE_URL", ""),
                            # Optional: not all clients support direct reset links; code entry in-app is primary.
                            "reset_link": "",
                            "unsubscribe_url": "",
                        }
                        html_body = render_to_string(
                            "emails/auth/03-password-reset-request.html",
                            context,
                        )
                        subject = "MyChama password reset code"
                    except Exception:  # noqa: BLE001
                        html_body = ""
                        subject = "Password reset code"

                result = send_email_message(
                    subject=subject,
                    recipient_list=[destination_email],
                    body=message,
                    html_body=html_body,
                )
                OTPService._log_delivery_attempt(
                    otp_token=otp_token,
                    user=user,
                    channel=OTPDeliveryChannel.EMAIL,
                    status=OTPDeliveryStatus.SENT,
                    destination=destination_email,
                    attempt_number=attempt_number,
                    provider_name=result.provider,
                    provider_message_id=result.provider_message_id,
                    provider_response=result.raw_response or {},
                )
                return True
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Email OTP delivery failed for %s on attempt %s",
                    destination_email,
                    attempt_number,
                )
                OTPService._log_delivery_attempt(
                    otp_token=otp_token,
                    user=user,
                    channel=OTPDeliveryChannel.EMAIL,
                    status=OTPDeliveryStatus.FAILED,
                    destination=destination_email,
                    attempt_number=attempt_number,
                    provider_name=getattr(getattr(exc, "__class__", None), "__name__", ""),
                    error_message=str(exc),
                )
        return False

    @staticmethod
    def _lock_user_for_otp_failures(user: User) -> None:
        locked_until = timezone.now() + timedelta(seconds=OTPService.lockout_seconds())
        user.locked_until = locked_until
        user.account_locked_until = locked_until
        user.save(update_fields=["locked_until", "account_locked_until"])

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
        identifier: str | None = None,
        phone: str | None = None,
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

        # Use default delivery method from settings if not specified
        if delivery_method is None:
            delivery_method = getattr(settings, 'OTP_DEFAULT_DELIVERY_METHOD', 'sms')
        if not identifier:
            identifier = phone or getattr(user, "email", "") or getattr(user, "phone", "")
        identifier = OTPService.normalize_identifier(identifier, purpose)
        normalized_phone = ""
        if phone:
            normalized_phone = normalize_kenyan_phone(phone)
        elif user and user.phone:
            normalized_phone = normalize_kenyan_phone(user.phone)

        now = timezone.now()
        expires_at = now + timedelta(minutes=OTPService.otp_expiry_minutes())

        with transaction.atomic():
            recent_count = OTPToken.objects.filter(
                identifier=identifier,
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
                    identifier=identifier,
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
                identifier=identifier,
                purpose=purpose,
                is_used=False,
            ).update(is_used=True, code="")

            code = str(secrets.randbelow(999999)).zfill(6)
            code_hash = OTPToken.hash_code(code, identifier, purpose)

            otp = OTPToken.objects.create(
                identifier=identifier,
                phone=normalized_phone,
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
                "identifier": identifier,
                "phone": normalized_phone,
                "delivery_method": delivery_method,
                "purpose": purpose,
                "expires_in_minutes": OTPService.otp_expiry_minutes(),
                "ip_address": ip_address,
            },
        )

        return otp, code

    @staticmethod
    def send_otp(
        identifier: str,
        otp_token: OTPToken,
        plain_code: str,
        user: User | None = None,
    ) -> OTPDispatchResult:
        """
        Send OTP code via one or more channels.
        Returns a structured result and raises if every channel fails.
        """
        normalized_identifier = OTPService.normalize_identifier(identifier, otp_token.purpose)
        phone = otp_token.phone or (normalize_kenyan_phone(identifier) if "@" not in normalized_identifier else "")
        email = getattr(user, "email", "") or (normalized_identifier if "@" in normalized_identifier else "")
        message = OTPService._build_message(otp_token, plain_code)
        result = OTPDispatchResult(
            requested_method=otp_token.delivery_method,
            masked_phone=OTPService._mask_phone(phone),
            masked_email=OTPService._mask_email(email) if email else "",
        )

        channels = OTPService._preferred_channels(otp_token.delivery_method, user=user)
        if not channels:
            if otp_token.delivery_method == OTPDeliveryMethod.EMAIL:
                raise OTPDeliveryError("Unable to deliver the verification code right now. Please try again.")
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

        if OTPDeliveryChannel.EMAIL in channels and email:
            result.email_sent = OTPService._send_email_channel(
                user=user or otp_token.user,
                message=message,
                otp_token=otp_token,
                plain_code=plain_code,
                destination_email=email,
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
            user_email = user.email if user else None
            OTPService.print_otp_to_console(phone, plain_code, otp_token.purpose, user_email)
        
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
                    "identifier": normalized_identifier,
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
                "identifier": normalized_identifier,
                "phone": phone,
                "delivery_method": otp_token.delivery_method,
                "purpose": otp_token.purpose,
                "channels_sent": result.channels_sent,
                "failed_channels": result.failed_channels,
            },
        )

        logger.info(
            "OTP sent identifier=%s via %s",
            normalized_identifier,
            ",".join(result.channels_sent),
        )
        return result

    @staticmethod
    def verify_otp(
        *,
        code: str,
        identifier: str | None = None,
        phone: str | None = None,
        purpose: str = OTPPurpose.VERIFY_PHONE,
        user: User | None = None,
    ) -> tuple[bool, str]:
        """
        Verify OTP code for phone number.
        Returns (success, message) tuple.
        """
        try:
            resolved_identifier = OTPService.normalize_identifier(
                identifier or phone or "",
                purpose,
            )
        except ValueError:
            return False, "Invalid verification identifier."

        if not code or len(code) != 6 or not code.isdigit():
            return False, "Invalid OTP format. Please enter 6 digits."

        lock_target = user
        if not lock_target and "@" not in resolved_identifier:
            lock_target = User.objects.filter(phone=resolved_identifier, is_active=True).first()
        if not lock_target and "@" in resolved_identifier:
            lock_target = User.objects.filter(email__iexact=resolved_identifier, is_active=True).first()
        if lock_target and lock_target.is_locked():
            return (
                False,
                "Too many failed attempts. Please wait before trying again.",
            )

        with transaction.atomic():
            otp = (
                OTPToken.objects.select_for_update()
                .filter(
                    identifier=resolved_identifier,
                    purpose=purpose,
                    is_used=False,
                )
                .order_by("-created_at")
                .first()
            )

            if not otp:
                return False, "No active OTP found. Please request a new code."

            lock_target = lock_target or otp.user or User.objects.filter(
                phone=otp.phone,
                is_active=True,
            ).first()
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
                logger.warning("Invalid OTP attempt for %s", resolved_identifier)
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

        logger.info("OTP successfully verified for %s", resolved_identifier)
        return True, "OTP verified successfully."

    # ==================== Development Methods ====================
    # These methods are ONLY enabled in development mode
    # They are automatically disabled in production

    @staticmethod
    def print_otp_to_console(phone: str, code: str, purpose: str, email: str = None) -> None:
        """
        Print OTP to console for development purposes.
        Only works when DEBUG=True and PRINT_OTP_IN_CONSOLE=True.
        NEVER prints in production.
        
        Args:
            phone: The phone number (in E.164 format)
            code: The 6-digit OTP code
            purpose: The OTP purpose (register, verify_phone, login_2fa, password_reset)
            email: Optional email address for additional context
        """
        from django.conf import settings
        from django.utils import timezone
        
        # Security check: Only run in development
        if not settings.DEBUG:
            return
        
        if not getattr(settings, 'PRINT_OTP_IN_CONSOLE', False):
            return
        
        masked_phone = OTPService._mask_phone(phone)
        masked_email = OTPService._mask_email(email) if email else None
        expiry_mins = OTPService.otp_expiry_minutes()
        now = timezone.now()
        expiry_time = now + timedelta(minutes=expiry_mins)
        
        # Purpose display name mapping
        purpose_names = {
            OTPPurpose.REGISTER: "Registration",
            OTPPurpose.VERIFY_PHONE: "Phone Verification",
            OTPPurpose.LOGIN_2FA: "Login 2FA",
            OTPPurpose.PASSWORD_RESET: "Password Reset",
            OTPPurpose.WITHDRAWAL_CONFIRM: "Withdrawal Confirmation",
        }
        purpose_display = purpose_names.get(purpose, purpose)
        
        # Format the output
        print(f"\n{'=' * 70}")
        print(f"🔐 MYCHAMA DEV OTP - {purpose_display.upper()}")
        print(f"{'=' * 70}")
        print(f"  📱 Phone:    {masked_phone}")
        if masked_email:
            print(f"  📧 Email:    {masked_email}")
        print(f"  🔢 Code:     {code}")
        print(f"  ⏱️  Expires:  {expiry_mins} minutes (at {expiry_time.strftime('%H:%M:%S')})")
        print(f"  🎯 Purpose: {purpose_display}")
        print(f"{'=' * 70}")
        print("\n  💡 TIP: Enter this code in the mobile app to verify.")
        print("  📝 Also check your email for the verification code.")
        print(f"\n{'=' * 70}\n")
        
        # Also log for file capture
        logger.info("=" * 70)
        logger.info("MYCHAMA DEV OTP - %s", purpose_display)
        logger.info("Phone: %s | Email: %s | Code: %s | Expires: %dm", 
                 masked_phone, masked_email or "N/A", code, expiry_mins)
        logger.info("=" * 70)

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
    def _get_image_quality_metrics(file: UploadedFile) -> dict[str, float | int | bool]:
        current_pos = file.tell() if hasattr(file, "tell") else None
        raw = file.read()
        if hasattr(file, "seek"):
            file.seek(current_pos or 0)

        try:
            image = Image.open(BytesIO(raw))
            image.load()
        except (OSError, UnidentifiedImageError, ValueError):
            logger.warning("Skipping image quality inspection for unreadable upload.")
            return {
                "width": 0,
                "height": 0,
                "brightness": 0.0,
                "contrast": 0.0,
                "sharpness": 0.0,
                "too_dark": False,
                "too_bright": False,
                "low_contrast": False,
                "blurry": False,
                "low_resolution": False,
                "quality_passed": True,
                "inspection_skipped": True,
            }

        grayscale = image.convert("L")
        stat = ImageStat.Stat(grayscale)
        edges = grayscale.filter(ImageFilter.FIND_EDGES)
        edge_stat = ImageStat.Stat(edges)

        width, height = image.size
        brightness = float(stat.mean[0]) if stat.mean else 0.0
        contrast = float(stat.stddev[0]) if stat.stddev else 0.0
        sharpness = float(edge_stat.mean[0]) if edge_stat.mean else 0.0

        too_dark = brightness < 35
        too_bright = brightness > 220
        low_contrast = contrast < 18
        blurry = sharpness < 12
        low_resolution = width < 600 or height < 600

        return {
            "width": width,
            "height": height,
            "brightness": round(brightness, 2),
            "contrast": round(contrast, 2),
            "sharpness": round(sharpness, 2),
            "too_dark": too_dark,
            "too_bright": too_bright,
            "low_contrast": low_contrast,
            "blurry": blurry,
            "low_resolution": low_resolution,
            "quality_passed": not any(
                [too_dark, too_bright, low_contrast, blurry, low_resolution]
            ),
        }

    @staticmethod
    def assess_image_quality(
        file: UploadedFile | None,
        *,
        field_name: str,
    ) -> tuple[bool, list[str], dict[str, float | int | bool]]:
        if not file:
            return True, [], {}

        metrics = KYCService._get_image_quality_metrics(file)
        errors: list[str] = []
        if metrics.get("low_resolution"):
            errors.append(f"{field_name} resolution is too low. Retake a clearer photo.")
        if metrics.get("blurry"):
            errors.append(f"{field_name} appears blurry. Retake the image.")
        if metrics.get("too_dark"):
            errors.append(f"{field_name} is too dark. Improve lighting and retake.")
        if metrics.get("too_bright"):
            errors.append(f"{field_name} has heavy glare or overexposure. Retake without glare.")
        if metrics.get("low_contrast"):
            errors.append(f"{field_name} contrast is too low. Ensure the document is fully visible.")
        return not errors, errors, metrics

    @staticmethod
    def requires_back_image(document_type: str) -> bool:
        return document_type != "passport"

    @staticmethod
    def run_screening_checks(*, user: User, id_number: str) -> dict[str, bool]:
        def _parse_setting_list(setting_name: str) -> set[str]:
            raw = getattr(settings, setting_name, "")
            if isinstance(raw, str):
                return {item.strip() for item in raw.split(",") if item.strip()}
            if isinstance(raw, (list, tuple, set)):
                return {str(item).strip() for item in raw if str(item).strip()}
            return set()

        normalized_id = str(id_number or "").strip()
        normalized_phone = str(getattr(user, "phone", "") or "").strip()
        pep_ids = _parse_setting_list("KYC_PEP_ID_NUMBERS")
        sanction_ids = _parse_setting_list("KYC_SANCTION_ID_NUMBERS")
        blacklist_ids = _parse_setting_list("KYC_BLACKLIST_ID_NUMBERS")
        pep_phones = _parse_setting_list("KYC_PEP_PHONE_NUMBERS")
        sanction_phones = _parse_setting_list("KYC_SANCTION_PHONE_NUMBERS")
        blacklist_phones = _parse_setting_list("KYC_BLACKLIST_PHONE_NUMBERS")

        return {
            "pep_match": normalized_id in pep_ids or normalized_phone in pep_phones,
            "sanctions_match": normalized_id in sanction_ids or normalized_phone in sanction_phones,
            "blacklist_match": normalized_id in blacklist_ids or normalized_phone in blacklist_phones,
        }

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
