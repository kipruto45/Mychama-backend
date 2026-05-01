import hashlib
import logging
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import models, transaction
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import permissions, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.kyc_integration_service import (
    EnhancedKYCService,
    KYCBusinessRules,
    KYCDocumentType,
    KYCVerificationLevel,
    SmileIdentityService,
)
from apps.accounts.models import (
    LoginEvent,
    MemberCard,
    MemberKYC,
    MemberKYCDocumentType,
    MemberKYCStatus,
    MemberKYCTier,
    OTPDeliveryMethod,
    OTPPurpose,
    PasswordResetToken,
    ReferralReward,
    User,
    UserPreference,
)
from apps.accounts.referrals import get_referral_reward_policy
from apps.accounts.serializers import (
    GENERIC_RESET_MESSAGE,
    ChangePasswordSerializer,
    LoginSerializer,
    LogoutSerializer,
    MemberCardSerializer,
    MemberKYCReviewSerializer,
    MemberKYCSerializer,
    MemberKYCSubmitSerializer,
    MembershipOptionSerializer,
    MembershipStatusSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    ProfileUpdateSerializer,
    PublicOTPRequestSerializer,
    PublicOTPVerifySerializer,
    ReferralLeaderboardSerializer,
    ReferralSummarySerializer,
    RegisterSerializer,
    SwitchChamaSerializer,
    UserPreferenceSerializer,
    UserPreferenceUpdateSerializer,
    UserSerializer,
)
from apps.accounts.services import KYCService as KYCUploadService
from apps.chama.models import (
    Chama,
    ChamaStatus,
    Membership,
    MembershipRequest,
    MembershipRequestStatus,
    MembershipRole,
    MemberStatus,
)
from apps.chama.services import ADMIN_EQUIVALENT_ROLES, get_effective_role
from apps.security.services import SecurityService
from core.throttles import (
    LoginIdentifierRateThrottle,
    LoginRateThrottle,
    OTPIdentifierRateThrottle,
    OTPRequestRateThrottle,
    OTPVerifyIdentifierRateThrottle,
    OTPVerifyRateThrottle,
    PasswordResetIdentifierRateThrottle,
    PasswordResetRateThrottle,
    RegisterIdentifierRateThrottle,
    RegisterRateThrottle,
)
from core.utils import normalize_kenyan_phone

logger = logging.getLogger(__name__)


DOCUMENT_TYPE_TO_PROVIDER_TYPE = {
    MemberKYCDocumentType.NATIONAL_ID: KYCDocumentType.KENYA_NATIONAL_ID,
    MemberKYCDocumentType.PASSPORT: KYCDocumentType.KENYA_PASSPORT,
    MemberKYCDocumentType.ALIEN_ID: KYCDocumentType.ALIEN_ID,
    MemberKYCDocumentType.MILITARY_ID: KYCDocumentType.MILITARY_ID,
}


def _api_message(code: str, message: str, **extra):
    from django.conf import settings
    
    # Add dev_otp in development mode only
    response = {
        "code": code,
        "message": message,
        **extra,
    }
    
    # Only include OTP in development
    if getattr(settings, 'DEBUG', False) and getattr(settings, 'PRINT_OTP_IN_CONSOLE', False):
        if 'plain_code' in extra:
            response['dev_otp'] = extra.pop('plain_code')
    
    return response


def _plain_errors(value):
    if isinstance(value, dict):
        return {str(key): _plain_errors(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_errors(item) for item in value]
    return str(value)


def _validation_error_code(errors: dict) -> str:
    def _flatten_messages(value) -> list[str]:
        if isinstance(value, dict):
            flattened: list[str] = []
            for nested in value.values():
                flattened.extend(_flatten_messages(nested))
            return flattened
        if isinstance(value, (list, tuple)):
            flattened = []
            for nested in value:
                flattened.extend(_flatten_messages(nested))
            return flattened
        text = str(value).strip()
        return [text] if text else []

    normalized: dict[str, list[str]] = {
        str(key): _flatten_messages(value) for key, value in (errors or {}).items()
    }
    all_messages = " ".join(
        message.lower() for messages in normalized.values() for message in messages
    )

    # Required/missing fields should win over field-specific parsing.
    if any(
        keyword in all_messages
        for keyword in (
            "this field is required",
            "required",
            "may not be blank",
            "blank",
        )
    ):
        return "REQUIRED_FIELD_MISSING"

    password_confirm_messages = " ".join(
        msg.lower() for msg in normalized.get("password_confirm", [])
    )
    if "match" in password_confirm_messages:
        return "PASSWORD_MISMATCH"

    # Some password validators raise messages that don't always stay under the "password" key
    # depending on serializer/validator behavior. Detect weak password signals across all messages.
    if any(
        keyword in all_messages
        for keyword in (
            "password is too weak",
            "weak password",
            "entropy",
            "known breaches",
            "breach",
            "pwned",
        )
    ):
        return "WEAK_PASSWORD"

    # Any validation error on password/new_password should be treated as a weak password.
    if "password" in normalized or "new_password" in normalized:
        return "WEAK_PASSWORD"

    if "phone" in normalized:
        phone_messages = " ".join(msg.lower() for msg in normalized.get("phone", []))
        if any(keyword in phone_messages for keyword in ("already", "exists", "taken", "duplicate")):
            return "PHONE_ALREADY_EXISTS"
        return "INVALID_PHONE"

    if "email" in normalized:
        email_messages = " ".join(msg.lower() for msg in normalized.get("email", []))
        if any(keyword in email_messages for keyword in ("already", "exists", "taken", "duplicate")):
            return "EMAIL_ALREADY_EXISTS"
        return "INVALID_EMAIL"

    if "delivery_method" in normalized or "otp_delivery_method" in normalized:
        return "INVALID_DELIVERY_METHOD"

    if "purpose" in normalized:
        return "INVALID_VERIFICATION_PURPOSE"

    if "identifier" in normalized:
        identifier_errors = " ".join(normalized.get("identifier", [])).lower()
        if "phone" in identifier_errors:
            return "INVALID_PHONE"
        if "email" in identifier_errors:
            return "INVALID_EMAIL"
        return "INVALID_IDENTIFIER"

    return "VALIDATION_ERROR"


def _validation_error_message(code: str) -> str:
    if code == "WEAK_PASSWORD":
        return (
            "Your password is too weak. Use at least 8 characters, including uppercase, "
            "lowercase, a number, and a special character."
        )
    if code == "PASSWORD_MISMATCH":
        return "Passwords do not match."
    if code == "EMAIL_ALREADY_EXISTS":
        return "An account with this email already exists."
    if code == "PHONE_ALREADY_EXISTS":
        return "An account with this phone number already exists."
    if code == "INVALID_EMAIL":
        return "Please enter a valid email address."
    if code == "INVALID_PHONE":
        return "Please enter a valid Kenyan phone number."
    if code == "REQUIRED_FIELD_MISSING":
        return "Please fill in all required fields."
    return "Please check your details and try again."


def _auth_response(
    *,
    success: bool,
    code: str,
    message: str,
    data: dict | None = None,
    errors: dict | None = None,
    plain_code: str | None = None,
    **extra,
):
    payload = {
        "success": success,
        "code": code,
        "message": message,
        "errors": errors or {},
    }
    if data is not None:
        payload["data"] = data
    payload.update(extra)
    if (
        plain_code
        and getattr(settings, "DEBUG", False)
        and getattr(settings, "PRINT_OTP_IN_CONSOLE", False)
    ):
        payload["dev_otp"] = plain_code
    return payload


def _otp_payload(
    *,
    identifier: str,
    phone: str = "",
    email: str = "",
    purpose: str,
    delivery_method: str,
    next_route: str = "ChamaSetup",
    expires_in: int | None = None,
):
    payload = {
        "identifier": identifier,
        "phone": phone,
        "email": email,
        "purpose": purpose,
        "delivery_method": delivery_method,
        "next_route": next_route,
    }
    if expires_in is not None:
        payload["expires_in"] = expires_in
    return payload


def _verification_target_for_user(*, user, delivery_method: str) -> tuple[str, str]:
    """
    Determines the verification purpose and identifier based on the selected delivery method.
    
    Returns:
        tuple: (purpose, identifier)
        
    Raises:
        ValueError: If email delivery requested but user has no email
    """
    if delivery_method == OTPDeliveryMethod.EMAIL:
        user_email = (user.email or "").strip().lower()
        if not user_email:
            raise ValueError("Email delivery requested but user has no email address on file.")
        return OTPPurpose.VERIFY_EMAIL, user_email
    return OTPPurpose.VERIFY_PHONE, user.phone


def _otp_error_code(message: str) -> str:
    lowered = str(message or "").lower()
    if "expired" in lowered:
        return "OTP_EXPIRED"
    if "wait before trying again" in lowered or "temporarily locked" in lowered:
        return "OTP_LOCKED"
    if "too many" in lowered:
        return "OTP_MAX_ATTEMPTS"
    if "no active otp" in lowered:
        return "OTP_REQUIRED"
    return "INVALID_OTP"


def _client_ip(request) -> str | None:
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _build_login_metadata(request):
    return {
        "path": request.path,
        "method": request.method,
        "content_type": request.content_type,
    }


def _normalized_identifier(raw_identifier: str | None) -> str:
    value = str(raw_identifier or "").strip()
    if not value:
        return ""

    try:
        return normalize_kenyan_phone(value)
    except ValueError:
        return value.lower()


def _hashed_cache_key(prefix: str, value: str | None) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


def _parse_chama_id(raw_value):
    try:
        return str(uuid.UUID(str(raw_value)))
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid chama id.") from exc


def _get_or_create_preferences(user):
    return UserPreference.objects.get_or_create(user=user)[0]


def _active_membership_for_user(user):
    if not user or not user.is_authenticated:
        return None

    preference = UserPreference.objects.filter(user=user).first()
    if preference and preference.active_chama_id:
        scoped_membership = (
            Membership.objects.select_related("chama")
            .filter(
                user=user,
                chama_id=preference.active_chama_id,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
                exited_at__isnull=True,
            )
            .first()
        )
        if scoped_membership:
            return scoped_membership

    return (
        Membership.objects.select_related("chama")
        .filter(
            user=user,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        )
        .order_by("-updated_at", "-joined_at")
        .first()
    )


def _membership_redirect(status_value: str) -> str:
    mapping = {
        MemberStatus.ACTIVE: "/dashboards/",
        MemberStatus.PENDING: "/chama/join/pending/",
        MemberStatus.SUSPENDED: "/chama/suspended/",
        MemberStatus.EXITED: "/chama/join/rejected/",
        MembershipRequestStatus.PENDING: "/chama/join/pending/",
        MembershipRequestStatus.NEEDS_INFO: "/chama/join/needs-info/",
        MembershipRequestStatus.REJECTED: "/chama/join/rejected/",
        MembershipRequestStatus.EXPIRED: "/chama/join/expired/",
        MembershipRequestStatus.CANCELLED: "/chama/join/cancelled/",
    }
    return mapping.get(status_value, "/chama/create/")


def _active_membership_snapshot(user):
    return (
        Membership.objects.select_related("chama")
        .filter(
            user=user,
            is_active=True,
            is_approved=True,
            exited_at__isnull=True,
        )
        .order_by("joined_at")
        .first()
    )


def _issue_auth_tokens_for_user(
    *,
    request,
    user,
    purpose: str,
    login_metadata: dict | None = None,
):
    from apps.accounts.kyc.services import sync_user_access_state

    ip_address = _client_ip(request)
    user_agent = str(request.META.get("HTTP_USER_AGENT", ""))
    device_id = str(request.headers.get("X-Device-ID", "")).strip()
    device_name = str(request.headers.get("X-Device-Name", "")).strip() or device_id or user_agent[:80] or "Unknown device"
    user = sync_user_access_state(user)
    active_membership = _active_membership_snapshot(user)

    refresh = RefreshToken.for_user(user)
    family_id = str(uuid.uuid4())
    refresh[SecurityService.REFRESH_FAMILY_CLAIM] = family_id
    refresh_jti = str(refresh.get("jti", ""))
    _, _session, new_device = SecurityService.register_refresh_token(
        user=user,
        refresh=refresh,
        device_name=device_name,
        device_id=device_id,
        ip_address=ip_address,
        user_agent=user_agent,
        chama=active_membership.chama if active_membership else None,
        family_id=family_id,
    )

    if purpose == OTPPurpose.LOGIN_2FA:
        user.last_login_at = timezone.now()
        user.last_login_ip = ip_address
        user.save(update_fields=["last_login_at", "last_login_ip"])
        LoginEvent.objects.create(
            user=user,
            identifier_attempted=user.phone,
            ip_address=ip_address,
            user_agent=user_agent,
            success=True,
            device_id=device_id,
            session_key=refresh_jti,
            metadata={**(login_metadata or {}), "new_device": new_device, "otp_verified": True},
        )
        SecurityService.record_login_attempt(
            identifier=user.phone,
            ip_address=ip_address,
            device_info=user_agent,
            success=True,
            user=user,
        )
        SecurityService.create_audit_log(
            action_type="LOGIN_SUCCESS",
            target_type="User",
            target_id=str(user.id),
            actor=user,
            chama=active_membership.chama if active_membership else None,
            metadata={
                "ip_address": ip_address or "",
                "device_name": device_name,
                "device_id": device_id,
                "session_key": refresh_jti,
                "new_device": new_device,
                "two_factor": True,
            },
            ip_address=ip_address,
        )
        if new_device:
            LoginView._send_new_device_alert(
                user=user,
                ip_address=ip_address,
                user_agent=user_agent,
            )

    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "user": UserSerializer(user, context={"request": request}).data,
        "session": {
            "session_key": refresh_jti,
            "new_device": bool(new_device),
            "max_active_devices": SecurityService.max_active_sessions(),
        },
    }


class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [RegisterRateThrottle, RegisterIdentifierRateThrottle]

    @transaction.atomic
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if not serializer.is_valid():
            errors = _plain_errors(serializer.errors)
            code = _validation_error_code(errors)
            return Response(
                _auth_response(
                    success=False,
                    code=code,
                    message=_validation_error_message(code),
                    errors=errors,
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        logger.info(
            "registration_started phone=%s delivery_method=%s email=%s",
            serializer.validated_data["phone"],
            serializer.validated_data["otp_delivery_method"],
            serializer.validated_data.get("email") or "",
        )

        user = serializer.save()
        logger.info("registration_user_created user_id=%s", user.id)

        from apps.accounts.services import (
            OTPDeliveryError,
            OTPRateLimitError,
            OTPService,
        )

        delivery_method = serializer.validated_data["otp_delivery_method"]
        
        try:
            verification_purpose, verification_identifier = _verification_target_for_user(
                user=user,
                delivery_method=delivery_method,
            )
        except ValueError as e:
            logger.warning(
                "registration_otp_delivery_error user_id=%s delivery_method=%s error=%s",
                user.id,
                delivery_method,
                str(e),
            )
            return Response(
                _auth_response(
                    success=False,
                    code="INVALID_DELIVERY_METHOD",
                    message="Email delivery was selected but no email address was provided. Please register with an email or select SMS delivery.",
                    errors={"email": ["Email address is required for email delivery."]},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )
        otp_data = _otp_payload(
            identifier=verification_identifier,
            phone=user.phone,
            email=(user.email or "").strip().lower(),
            purpose=verification_purpose,
            delivery_method=delivery_method,
            expires_in=OTPService.otp_expiry_minutes() * 60,
        )
        registration_verification_data = {
            **otp_data,
            "user_id": str(user.id),
            "otp_delivery_method": delivery_method,
            "verification_purpose": verification_purpose,
            "otp_sent": False,
        }

        try:
            logger.info(
                "registration_otp_generate user_id=%s identifier=%s purpose=%s delivery_method=%s",
                user.id,
                verification_identifier,
                verification_purpose,
                delivery_method,
            )
            otp_token, plain_code = OTPService.generate_otp(
                identifier=verification_identifier,
                phone=user.phone,
                user=user,
                purpose=verification_purpose,
                delivery_method=delivery_method,
            )
            logger.info(
                "registration_otp_send_attempt user_id=%s identifier=%s purpose=%s delivery_method=%s",
                user.id,
                verification_identifier,
                verification_purpose,
                delivery_method,
            )
            delivery_result = OTPService.send_otp(
                verification_identifier,
                otp_token,
                plain_code,
                user,
            )
            logger.info(
                "registration_otp_send_success user_id=%s identifier=%s channels=%s",
                user.id,
                verification_identifier,
                delivery_result.channels_sent,
            )
        except OTPRateLimitError as exc:
            return Response(
                _auth_response(
                    success=False,
                    code="OTP_RESEND_BLOCKED",
                    message="Please wait a moment before requesting another verification code.",
                    errors={},
                    data=otp_data,
                ),
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        except OTPDeliveryError as exc:
            logger.warning(
                "registration_otp_send_failed user_id=%s identifier=%s reason=%s",
                user.id,
                verification_identifier,
                exc,
            )
            return Response(
                _auth_response(
                    success=False,
                    code="REGISTER_SUCCESS_OTP_FAILED",
                    message="Account created successfully, but we could not send the verification code. Please try again.",
                    errors={},
                    data=registration_verification_data,
                    legacy_code="ACCOUNT_CREATED_BUT_OTP_FAILED",
                ),
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            SecurityService.create_audit_log(
                action_type="REGISTER",
                target_type="User",
                target_id=str(user.id),
                actor=user,
                metadata={
                    "phone_verified": user.phone_verified,
                    "otp_identifier": verification_identifier,
                    "otp_purpose": verification_purpose,
                    "otp_delivery_method": delivery_method,
                },
                ip_address=_client_ip(request),
            )
        except Exception as exc:
            logger.exception(
                "RegisterView: failed to create audit log, continuing registration",
                exc_info=exc,
            )

        registration_verification_data["otp_sent"] = True
        message = (
            "Account created successfully. Verification code sent to your email."
            if delivery_method == OTPDeliveryMethod.EMAIL
            else "Account created successfully. Verification code sent to your phone via SMS."
        )
        return Response(
            _auth_response(
                success=True,
                code="REGISTER_SUCCESS_OTP_SENT",
                message=message,
                plain_code=plain_code,
                data=registration_verification_data,
                user=UserSerializer(user, context={"request": request}).data,
                phone_verification_required=True,
                delivery={
                    "channels": delivery_result.channels_sent,
                    "phone": delivery_result.masked_phone if delivery_result.sms_sent else "",
                    "email": delivery_result.masked_email if delivery_result.email_sent else "",
                },
                legacy_code="OTP_SENT",
            ),
            status=status.HTTP_201_CREATED,
        )


class PublicOTPRequestView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [OTPRequestRateThrottle, OTPIdentifierRateThrottle]

    @extend_schema(
        request=PublicOTPRequestSerializer,
        responses={200: dict},
        summary="Request OTP (Public)",
        description="Request a one-time password for phone verification or login."
    )
    def post(self, request):
        serializer = PublicOTPRequestSerializer(data=request.data)
        if not serializer.is_valid():
            errors = _plain_errors(serializer.errors)
            logger.warning("otp_resend_validation_failed errors=%s payload=%s", errors, request.data)
            return Response(
                _auth_response(
                    success=False,
                    code=_validation_error_code(errors),
                    message="Invalid verification request.",
                    errors=errors,
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        identifier = serializer.validated_data["identifier"]
        phone = serializer.validated_data["phone"]
        email = serializer.validated_data.get("email", "")
        purpose = serializer.validated_data["purpose"]
        delivery_method = serializer.validated_data["delivery_method"]
        user = serializer.validated_data.get("resolved_user")
        otp_data = _otp_payload(
            identifier=identifier,
            phone=phone,
            email=email,
            purpose=purpose,
            delivery_method=delivery_method,
        )
        logger.info(
            "otp_request_received identifier=%s purpose=%s delivery_method=%s",
            identifier,
            purpose,
            delivery_method,
        )

        # Get IP address for security tracking
        ip_address = _client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")[:500]
        user = user or User.objects.filter(phone=phone, is_active=True).first()
        
        from apps.accounts.services import (
            OTPDeliveryError,
            OTPRateLimitError,
            OTPService,
        )

        try:
            logger.info(
                "otp_generate identifier=%s purpose=%s delivery_method=%s",
                identifier,
                purpose,
                delivery_method,
            )
            otp_token, plain_code = OTPService.generate_otp(
                identifier=identifier,
                phone=phone,
                user=user,
                purpose=purpose,
                delivery_method=delivery_method,
                ip_address=ip_address,
                user_agent=user_agent,
            )

            logger.info(
                "otp_send_attempt identifier=%s purpose=%s delivery_method=%s",
                identifier,
                purpose,
                delivery_method,
            )
            delivery_result = OTPService.send_otp(identifier, otp_token, plain_code, user)
            logger.info(
                "otp_send_success identifier=%s channels=%s",
                identifier,
                delivery_result.channels_sent,
            )
        except OTPRateLimitError as e:
            return Response(
                _auth_response(
                    success=False,
                    code="OTP_RESEND_BLOCKED",
                    message="Please wait a moment before requesting another verification code.",
                    errors={},
                    data=otp_data,
                ),
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        except OTPDeliveryError as e:
            logger.warning("otp_send_failed identifier=%s reason=%s", identifier, e)
            return Response(
                _auth_response(
                    success=False,
                    code="OTP_SEND_FAILED",
                    message="We could not send the verification code. Please try again.",
                    errors={},
                    data=otp_data,
                ),
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            _auth_response(
                success=True,
                code="OTP_SENT",
                message="Verification code sent successfully.",
                plain_code=plain_code,
                data={
                    **otp_data,
                    "expires_in": OTPService.otp_expiry_minutes() * 60,
                    "otp_sent": True,
                    "otp_delivery_method": delivery_method,
                    "verification_purpose": purpose,
                },
            ),
            status=status.HTTP_200_OK,
        )


class PublicOTPVerifyView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [OTPVerifyRateThrottle, OTPVerifyIdentifierRateThrottle]

    @extend_schema(
        request=PublicOTPVerifySerializer,
        responses={200: dict},
        summary="Verify OTP (Public)",
        description="Verify a one-time password code."
    )
    @transaction.atomic
    def post(self, request):
        serializer = PublicOTPVerifySerializer(data=request.data)
        if not serializer.is_valid():
            errors = _plain_errors(serializer.errors)
            logger.warning("otp_verify_validation_failed errors=%s payload=%s", errors, request.data)
            return Response(
                _auth_response(
                    success=False,
                    code=_validation_error_code(errors),
                    message="Invalid verification request.",
                    errors=errors,
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        identifier = serializer.validated_data["identifier"]
        phone = serializer.validated_data["phone"]
        email = serializer.validated_data.get("email", "")
        purpose = serializer.validated_data["purpose"]
        code = serializer.validated_data["code"]

        # Get user if exists
        user = serializer.validated_data.get("resolved_user") or User.objects.filter(
            phone=phone,
            is_active=True,
        ).first()

        from apps.accounts.services import OTPService

        logger.info(
            "otp_verify_attempt identifier=%s purpose=%s",
            identifier,
            purpose,
        )
        verified, message = OTPService.verify_otp(
            identifier=identifier,
            phone=phone,
            code=code,
            purpose=purpose,
            user=user,
        )

        if not verified:
            error_code = _otp_error_code(message)
            lockout_until = None
            lockout_seconds = None
            if user and user.is_locked():
                until = user.account_locked_until or user.locked_until
                if until:
                    lockout_until = until.isoformat()
                    lockout_seconds = max(0, int((until - timezone.now()).total_seconds()))
                    error_code = "OTP_LOCKED"
            return Response(
                _auth_response(
                    success=False,
                    code=error_code,
                    message=(
                        "The verification code is invalid."
                        if error_code == "INVALID_OTP"
                        else "The verification code has expired. Request a new one."
                        if error_code == "OTP_EXPIRED"
                        else "Your account has been temporarily locked after too many incorrect OTP attempts."
                        if error_code == "OTP_LOCKED"
                        else "The code could not be confirmed. Please check it and try again."
                    ),
                    errors={},
                    data=_otp_payload(
                        identifier=identifier,
                        phone=phone,
                        email=email,
                        purpose=purpose,
                        delivery_method=OTPDeliveryMethod.EMAIL
                        if purpose == OTPPurpose.VERIFY_EMAIL
                        else OTPDeliveryMethod.SMS,
                    ),
                    account_locked_until=lockout_until,
                    lockout_seconds_remaining=lockout_seconds,
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        if user and purpose in (OTPPurpose.VERIFY_PHONE, OTPPurpose.VERIFY_EMAIL):
            now = timezone.now()
            update_fields: list[str] = []
            if purpose == OTPPurpose.VERIFY_PHONE:
                user.phone_verified = True
                user.phone_verified_at = now
                update_fields.extend(["phone_verified", "phone_verified_at"])
            else:
                # Email OTP flow also confirms the member for onboarding in this system,
                # while separately tracking email verification.
                user.email_verified = True
                user.email_verified_at = now
                user.phone_verified = True
                user.phone_verified_at = user.phone_verified_at or now
                update_fields.extend(
                    [
                        "email_verified",
                        "email_verified_at",
                        "phone_verified",
                        "phone_verified_at",
                    ]
                )
            user.save(update_fields=update_fields)
            from apps.accounts.kyc.services import sync_user_access_state

            user = sync_user_access_state(user)
            logger.info(
                "otp_verify_success user_id=%s identifier=%s purpose=%s",
                user.id,
                identifier,
                purpose,
            )

        if user:
            SecurityService.create_audit_log(
                action_type="OTP_VERIFIED",
                target_type="User",
                target_id=str(user.id),
                actor=user,
                metadata={"purpose": purpose},
                ip_address=_client_ip(request),
            )

        if purpose in (OTPPurpose.LOGIN_2FA, OTPPurpose.VERIFY_PHONE, OTPPurpose.VERIFY_EMAIL) and user:
            token_payload = _issue_auth_tokens_for_user(
                request=request,
                user=user,
                purpose=purpose,
                login_metadata=_build_login_metadata(request),
            )
            return Response(
                _auth_response(
                    success=True,
                    code="OTP_VERIFIED",
                    message="Your verification code has been confirmed successfully.",
                    data=_otp_payload(
                        identifier=identifier,
                        phone=phone,
                        email=email,
                        purpose=purpose,
                        delivery_method=OTPDeliveryMethod.EMAIL
                        if purpose == OTPPurpose.VERIFY_EMAIL
                        else OTPDeliveryMethod.SMS,
                    ),
                    purpose=purpose,
                    **token_payload,
                ),
                status=status.HTTP_200_OK,
            )

        return Response(
            _auth_response(
                success=True,
                code="OTP_VERIFIED",
                message="Your verification code has been confirmed successfully.",
                data=_otp_payload(
                    identifier=identifier,
                    phone=phone,
                    email=email,
                    purpose=purpose,
                    delivery_method=OTPDeliveryMethod.EMAIL
                    if purpose == OTPPurpose.VERIFY_EMAIL
                    else OTPDeliveryMethod.SMS,
                ),
                purpose=purpose,
                phone_verified=user.phone_verified if user else True,
            ),
            status=status.HTTP_200_OK,
        )


class DevOTPView(APIView):
    """
    Development-only endpoint to retrieve the latest OTP for a phone number.
    
    This endpoint is ONLY available when:
    1. DEBUG=True or ENABLE_DEV_OTP_ENDPOINT=True in settings
    2. A valid DEV_OTP_SECRET_TOKEN is provided in the request header
    
    SECURITY: This endpoint MUST NEVER be enabled in production.
    """
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request):
        # Security check: Ensure dev endpoint is enabled
        if not getattr(settings, 'ENABLE_DEV_OTP_ENDPOINT', False):
            return Response(
                {"detail": "Dev OTP endpoint is not enabled."},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        # Security check: Validate the secret token
        secret_token = request.headers.get('X-Dev-OTP-Token')
        expected_token = getattr(settings, 'DEV_OTP_SECRET_TOKEN', None)
        
        if not secret_token or secret_token != expected_token:
            return Response(
                {"detail": "Invalid or missing dev token."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        
        # Get the destination (phone number)
        destination = request.query_params.get('destination')
        if not destination:
            return Response(
                {"detail": "destination query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # Import here to avoid circular imports
        from apps.accounts.services import OTPService
        
        # Get the latest OTP for this destination
        otp_data = OTPService.get_dev_otp(destination)
        
        if not otp_data:
            return Response(
                {"detail": "No OTP found for this destination. Please request an OTP first."},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        return Response(
            {
                "destination": destination,
                "code": otp_data['code'],
                "purpose": otp_data['purpose'],
                "created_at": otp_data['created_at'],
                "expires_at": otp_data['expires_at'],
                "message": "DEV ONLY: This endpoint should never be enabled in production!",
            },
            status=status.HTTP_200_OK,
        )


@method_decorator(csrf_exempt, name='dispatch')
class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [LoginRateThrottle, LoginIdentifierRateThrottle]

    @staticmethod
    def _failure_limit() -> int:
        return max(1, int(getattr(settings, "LOGIN_LOCKOUT_FAILURE_LIMIT", 5)))

    @staticmethod
    def _cooldown_seconds() -> int:
        return max(60, int(getattr(settings, "LOGIN_LOCKOUT_COOLDOWN_SECONDS", 900)))

    @classmethod
    def _identifier_failure_key(cls, identifier: str | None) -> str | None:
        return _hashed_cache_key("auth:login:fail:identifier", identifier)

    @classmethod
    def _identifier_lock_key(cls, identifier: str | None) -> str | None:
        return _hashed_cache_key("auth:login:lock:identifier", identifier)

    @classmethod
    def _ip_failure_key(cls, ip_address: str | None) -> str | None:
        return _hashed_cache_key("auth:login:fail:ip", ip_address)

    @classmethod
    def _ip_lock_key(cls, ip_address: str | None) -> str | None:
        return _hashed_cache_key("auth:login:lock:ip", ip_address)

    @classmethod
    def _is_locked(cls, identifier: str | None, ip_address: str | None) -> bool:
        if SecurityService.is_locked(identifier=identifier or ""):
            return True
        lock_keys = [
            cls._identifier_lock_key(identifier),
            cls._ip_lock_key(ip_address),
        ]
        return any(key and cache.get(key) for key in lock_keys)

    @classmethod
    def _record_failed_attempt(
        cls,
        *,
        identifier: str | None,
        ip_address: str | None,
    ) -> None:
        cooldown_seconds = cls._cooldown_seconds()
        failure_limit = cls._failure_limit()

        key_pairs = [
            (
                cls._identifier_failure_key(identifier),
                cls._identifier_lock_key(identifier),
            ),
            (cls._ip_failure_key(ip_address), cls._ip_lock_key(ip_address)),
        ]

        for failure_key, lock_key in key_pairs:
            if not failure_key or not lock_key:
                continue
            failed_attempts = int(cache.get(failure_key, 0)) + 1
            cache.set(failure_key, failed_attempts, timeout=cooldown_seconds)
            if failed_attempts >= failure_limit:
                cache.set(lock_key, 1, timeout=cooldown_seconds)

    @classmethod
    def _clear_failed_attempts(
        cls,
        *,
        identifier: str | None,
        ip_address: str | None,
    ) -> None:
        keys = [
            key
            for key in [
                cls._identifier_failure_key(identifier),
                cls._identifier_lock_key(identifier),
                cls._ip_failure_key(ip_address),
                cls._ip_lock_key(ip_address),
            ]
            if key
        ]
        if keys:
            cache.delete_many(keys)

    @staticmethod
    def _is_new_device_login(user, request, ip_address: str | None) -> bool:
        successful_logins = LoginEvent.objects.filter(user=user, success=True)
        if not successful_logins.exists():
            return False

        device_id = str(request.headers.get("X-Device-ID", "")).strip()
        user_agent = str(request.META.get("HTTP_USER_AGENT", "")).strip()

        if device_id:
            return not successful_logins.filter(device_id=device_id).exists()
        if user_agent and ip_address:
            return not successful_logins.filter(
                user_agent=user_agent,
                ip_address=ip_address,
            ).exists()
        if user_agent:
            return not successful_logins.filter(user_agent=user_agent).exists()
        return False

    @staticmethod
    def _send_new_device_alert(user, ip_address: str | None, user_agent: str):
        if not getattr(settings, "LOGIN_NEW_DEVICE_ALERT_ENABLED", False):
            return

        try:
            from apps.automations.tasks import security_new_device_alert_event

            security_new_device_alert_event.delay(
                str(user.id),
                ip_address or "",
                user_agent or "",
            )
            return
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to queue new-device alert task for %s; using sync fallback.",
                user.id,
            )

        channels = getattr(settings, "LOGIN_NEW_DEVICE_ALERT_CHANNELS", ["email"])
        if isinstance(channels, str):
            channels = [item.strip() for item in channels.split(",") if item.strip()]
        if not channels:
            return

        active_membership = (
            Membership.objects.select_related("chama")
            .filter(
                user=user,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
            )
            .first()
        )
        if not active_membership:
            return

        timestamp = timezone.localtime(timezone.now()).strftime("%Y-%m-%d %H:%M %Z")
        short_ua = (user_agent or "Unknown device")[:120]
        message = (
            "New sign-in detected on your Digital Chama account. "
            f"Time: {timestamp}. IP: {ip_address or 'unknown'}. Device: {short_ua}."
        )

        try:
            from apps.notifications.models import NotificationType
            from apps.notifications.services import NotificationService

            NotificationService.send_notification(
                user=user,
                message=message,
                channels=channels,
                chama=active_membership.chama,
                subject="New device sign-in alert",
                notification_type=NotificationType.SECURITY_ALERT,
                category="security",
                idempotency_key=(
                    f"login-alert:{user.id}:{ip_address or 'unknown'}:{timezone.localdate().isoformat()}"
                ),
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to dispatch new-device login alert for %s", user.id
            )

    def throttled(self, request, wait):
        identifier = _normalized_identifier(request.data.get("phone", ""))
        ip_address = _client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        LoginEvent.objects.create(
            user=None,
            identifier_attempted=identifier,
            ip_address=ip_address,
            user_agent=user_agent,
            success=False,
            device_id=request.headers.get("X-Device-ID", ""),
            session_key=getattr(request.session, "session_key", "") or "",
            metadata={"reason": "throttled", "wait_seconds": wait or 0},
        )
        SecurityService.record_login_attempt(
            identifier=identifier,
            ip_address=ip_address,
            device_info=user_agent,
            success=False,
            user=None,
        )
        SecurityService.maybe_lock_after_failure(
            identifier=identifier,
            user=None,
            reason="throttled_login_attempts",
        )
        super().throttled(request, wait)

    @csrf_exempt
    def post(self, request):
        from apps.accounts.services import (
            OTPDeliveryError,
            OTPRateLimitError,
            OTPService,
        )

        identifier = _normalized_identifier(request.data.get("phone", ""))
        ip_address = _client_ip(request)
        if self._is_locked(identifier, ip_address):
            user_agent = request.META.get("HTTP_USER_AGENT", "")
            LoginEvent.objects.create(
                user=None,
                identifier_attempted=identifier,
                ip_address=ip_address,
                user_agent=user_agent,
                success=False,
                device_id=request.headers.get("X-Device-ID", ""),
                session_key=getattr(request.session, "session_key", "") or "",
                metadata={
                    **_build_login_metadata(request),
                    "reason": "lockout_active",
                },
            )
            SecurityService.record_login_attempt(
                identifier=identifier,
                ip_address=ip_address,
                device_info=user_agent,
                success=False,
                user=None,
            )
            return Response(
                {
                    "message": "Please wait a little while before trying again.",
                    "code": "ACCOUNT_LOCKED",
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        try:
            serializer = LoginSerializer(data=request.data, context={"request": request})

            device_id = request.headers.get("X-Device-ID", "") or "N/A"
            logger.info("Login request received: phone=%s device_id=%s", identifier, device_id)
            logger.debug("Login request payload keys: %s", list(request.data.keys()))

            if not serializer.is_valid():
                logger.error(f"Login serializer errors: {serializer.errors}")
                logger.error(f"Login request data keys: {list(request.data.keys())}")
                self._record_failed_attempt(identifier=identifier, ip_address=ip_address)
                user_agent = request.META.get("HTTP_USER_AGENT", "")
                LoginEvent.objects.create(
                    user=None,
                    identifier_attempted=identifier,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    success=False,
                    device_id=request.headers.get("X-Device-ID", ""),
                    session_key=getattr(request.session, "session_key", "") or "",
                    metadata={
                        **_build_login_metadata(request),
                        "reason": "invalid_credentials",
                    },
                )
                SecurityService.record_login_attempt(
                    identifier=identifier,
                    ip_address=ip_address,
                    device_info=user_agent,
                    success=False,
                    user=None,
                )
                SecurityService.maybe_lock_after_failure(
                    identifier=identifier,
                    user=None,
                    reason="repeated_invalid_credentials",
                )
                return Response(
                    {
                        "message": "Check your phone number and password, then try again.",
                        "code": "INVALID_CREDENTIALS",
                    },
                    status=status.HTTP_401_UNAUTHORIZED,
                )
        except Exception as e:
            logger.error(f"Login validation error: {e}", exc_info=True)
            return Response(
                {
                    "message": "An error occurred during login. Please try again.",
                    "code": "SERVER_ERROR",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        user = serializer.validated_data["user"]
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        device_id = str(request.headers.get("X-Device-ID", "")).strip()
        device_name = str(request.headers.get("X-Device-Name", "")).strip()
        if not device_name:
            device_name = device_id or user_agent[:80] or "Unknown device"

        self._clear_failed_attempts(identifier=identifier, ip_address=ip_address)
        SecurityService.clear_identifier_locks(identifier=identifier)

        # In development, return tokens directly to simplify testing
        if settings.DEBUG:
            from rest_framework_simplejwt.tokens import RefreshToken

            from apps.accounts.serializers import UserSerializer

            refresh = RefreshToken.for_user(user)
            LoginEvent.objects.create(
                user=user,
                identifier_attempted=serializer.validated_data["phone"],
                ip_address=ip_address,
                user_agent=user_agent,
                success=True,
                device_id=device_id,
                session_key="",
                metadata={**_build_login_metadata(request), "direct_login": True},
            )
            SecurityService.create_audit_log(
                action_type="LOGIN_SUCCESS",
                target_type="User",
                target_id=str(user.id),
                actor=user,
                metadata={
                    "ip_address": ip_address or "",
                    "device_name": device_name,
                    "device_id": device_id,
                    "direct_login": True,
                },
                ip_address=ip_address,
            )
            user.last_login_at = timezone.now()
            user.last_login_ip = ip_address
            user.save(update_fields=["last_login_at", "last_login_ip"])
            return Response(
                {
                    "access": str(refresh.access_token),
                    "refresh": str(refresh),
                    "user": UserSerializer(user).data,
                },
                status=status.HTTP_200_OK,
            )

        # Production: require 2FA
        LoginEvent.objects.create(
            user=user,
            identifier_attempted=serializer.validated_data["phone"],
            ip_address=ip_address,
            user_agent=user_agent,
            success=False,
            device_id=device_id,
            session_key="",
            metadata={**_build_login_metadata(request), "reason": "otp_challenge_issued"},
        )
        SecurityService.create_audit_log(
            action_type="LOGIN_OTP_CHALLENGE",
            target_type="User",
            target_id=str(user.id),
            actor=user,
            metadata={
                "ip_address": ip_address or "",
                "device_name": device_name,
                "device_id": device_id,
                "two_factor": True,
            },
            ip_address=ip_address,
        )

        # In development mode, skip 2FA and return tokens directly
        if getattr(settings, 'DEBUG', False) and not getattr(settings, 'OTP_REQUIRED_IN_DEV', True):
            from rest_framework_simplejwt.tokens import RefreshToken
            tokens = RefreshToken.for_user(user)
            return Response(
                {
                    "access": str(tokens.access_token),
                    "refresh": str(tokens),
                    "user": {
                        "id": str(user.id),
                        "phone": user.phone,
                        "full_name": user.full_name,
                        "email": user.email,
                        "phone_verified": user.phone_verified,
                    },
                },
                status=status.HTTP_200_OK,
            )

        try:
            otp_token, plain_code = OTPService.generate_otp(
                phone=user.phone,
                user=user,
                purpose=OTPPurpose.LOGIN_2FA,
                delivery_method="sms",
                ip_address=ip_address,
                user_agent=user_agent[:500],
            )
            delivery_result = OTPService.send_otp(user.phone, otp_token, plain_code, user)
        except OTPRateLimitError:
            return Response(
                _api_message(
                    "RATE_LIMITED",
                    "Please wait a moment before requesting another verification code.",
                ),
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        except OTPDeliveryError:
            return Response(
                _api_message(
                    "OTP_DELIVERY_FAILED",
                    "Your password was verified, but we could not send the sign-in code right now.",
                ),
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            _api_message(
                "OTP_REQUIRED",
                "We sent a login verification code to your registered phone number.",
                identifier=user.phone,
                phone=user.phone,
                purpose=OTPPurpose.LOGIN_2FA,
                delivery={
                    "channels": delivery_result.channels_sent,
                    "phone": delivery_result.masked_phone if delivery_result.sms_sent else "",
                    "email": delivery_result.masked_email if delivery_result.email_sent else "",
                },
            ),
            status=status.HTTP_200_OK,
        )


class RefreshView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        raw_refresh_token = str(request.data.get("refresh", "")).strip()
        if not raw_refresh_token:
            return Response(
                _api_message("TOKEN_REQUIRED", "Refresh token is required."),
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            refresh, record, _is_new_device = SecurityService.rotate_refresh_token(
                raw_refresh_token=raw_refresh_token,
                device_name=str(request.headers.get("X-Device-Name", "")).strip(),
                device_id=str(request.headers.get("X-Device-ID", "")).strip(),
                ip_address=_client_ip(request),
                user_agent=str(request.META.get("HTTP_USER_AGENT", "")),
            )
        except TokenError:
            return Response(
                _api_message("TOKEN_INVALID", "Your session is no longer valid. Please sign in again."),
                status=status.HTTP_401_UNAUTHORIZED,
            )

        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "family_id": str(record.family_id),
            },
            status=status.HTTP_200_OK,
        )


class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        refresh_token = serializer.validated_data.get("refresh")
        refresh_jti = ""
        try:
            if refresh_token:
                token = RefreshToken(refresh_token)
                refresh_jti = str(token.get("jti", ""))
                token.blacklist()
        except TokenError:
            return Response(
                _api_message("TOKEN_INVALID", "Your session is no longer valid. Please sign in again."),
                status=status.HTTP_400_BAD_REQUEST,
            )

        revoked_count = 0
        if refresh_jti:
            revoked_count = request.user.device_sessions.filter(
                session_key=refresh_jti,
                is_revoked=False,
            ).update(is_revoked=True, last_seen=timezone.now())
            request.user.refresh_token_records.filter(
                jti=refresh_jti,
                revoked_at__isnull=True,
            ).update(revoked_at=timezone.now(), revoked_reason="logout")
        SecurityService.create_audit_log(
            action_type="LOGOUT",
            target_type="User",
            target_id=str(request.user.id),
            actor=request.user,
            metadata={"revoked_sessions": revoked_count, "session_key": refresh_jti},
            ip_address=_client_ip(request),
        )

        return Response(status=status.HTTP_205_RESET_CONTENT)


class OTPRequestView(APIView):
    """
    Request OTP code for two-factor authentication.
    Must be authenticated (post-login).
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        """Generate and send OTP code to user."""
        from apps.accounts.serializers import OTPRequestSerializer
        from apps.accounts.services import (
            OTPDeliveryError,
            OTPRateLimitError,
            OTPService,
        )

        serializer = OTPRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        delivery_method = serializer.validated_data["delivery_method"]

        try:
            otp_token, plain_code = OTPService.generate_otp(
                phone=user.phone,
                user=user,
                purpose=OTPPurpose.LOGIN_2FA,
                delivery_method=delivery_method,
            )
            delivery_result = OTPService.send_otp(user.phone, otp_token, plain_code, user)
        except OTPRateLimitError as exc:
            return Response(
                _api_message(
                    "RATE_LIMITED",
                    "Please wait a moment before requesting another verification code.",
                ),
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        except OTPDeliveryError as exc:
            return Response(
                _api_message(
                    "OTP_DELIVERY_FAILED",
                    "We could not send the verification code right now. Please try again shortly.",
                ),
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            _api_message(
                "OTP_SENT",
                "We've sent a verification code. Enter it to continue.",
                plain_code=plain_code,
                expires_in_seconds=OTPService.otp_expiry_minutes() * 60,
                delivery={
                    "channels": delivery_result.channels_sent,
                    "phone": delivery_result.masked_phone if delivery_result.sms_sent else "",
                    "email": delivery_result.masked_email if delivery_result.email_sent else "",
                },
            ),
            status=status.HTTP_200_OK,
        )


class OTPVerifyView(APIView):
    """
    Verify OTP code for two-factor authentication.
    Must be authenticated (post-login).
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        """Verify OTP code."""
        from apps.accounts.serializers import OTPVerifySerializer
        from apps.accounts.services import OTPService

        serializer = OTPVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        code = serializer.validated_data["code"]

        # Verify OTP
        verified, message = OTPService.verify_otp(
            phone=user.phone,
            code=code,
            purpose=OTPPurpose.LOGIN_2FA,
            user=user,
        )
        if not verified:
            return Response(
                _api_message(
                    _otp_error_code(message),
                    "The code could not be confirmed. Please check it and try again.",
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Update user's 2FA confirmation
        user.two_factor_enabled = True
        user.save(update_fields=["two_factor_enabled"])

        from core.audit import create_audit_log

        ip_address = _client_ip(request)
        create_audit_log(
            actor=user,
            action="two_factor_verified",
            entity_type="User",
            entity_id=user.id,
            metadata={"ip_address": ip_address or ""},
        )

        return Response(
            _api_message(
                "OTP_VERIFIED",
                "Your verification code has been confirmed successfully.",
            ),
            status=status.HTTP_200_OK,
        )


class MeView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def get(self, request):
        return Response(
            UserSerializer(request.user, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )

    def patch(self, request):
        serializer = ProfileUpdateSerializer(
            request.user,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        updated_user = serializer.save()
        return Response(
            UserSerializer(updated_user, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )


class ReferralSummaryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        referral_qs = (
            Chama.objects.filter(referred_by=request.user)
            .order_by("-referral_applied_at", "-created_at")
        )
        reward_qs = ReferralReward.objects.filter(referrer=request.user).select_related(
            "referred_chama",
            "rewarded_chama",
        )
        reward_total_earned = sum(reward.reward_value for reward in reward_qs)

        stats = {
            "total_referrals": referral_qs.count(),
            "completed_referrals": referral_qs.filter(setup_completed=True).count(),
            "pending_setup_referrals": referral_qs.filter(setup_completed=False).count(),
            "active_referrals": referral_qs.filter(status=ChamaStatus.ACTIVE).count(),
            "reward_eligible_referrals": referral_qs.filter(setup_completed=True).count(),
            "rewards_applied": reward_qs.filter(status=ReferralReward.APPLIED).count(),
            "rewards_pending": reward_qs.filter(status=ReferralReward.PENDING).count(),
            "reward_days_earned": reward_total_earned,
            "reward_total_earned": reward_total_earned,
        }

        history = [
            {
                "chama_id": chama.id,
                "chama_name": chama.name,
                "status": chama.status,
                "setup_completed": chama.setup_completed,
                "created_at": chama.created_at,
                "referral_applied_at": chama.referral_applied_at,
                "referral_code_used": chama.referral_code_used,
            }
            for chama in referral_qs[:50]
        ]
        rewards = [
            {
                "referred_chama_id": reward.referred_chama_id,
                "referred_chama_name": reward.referred_chama.name,
                "rewarded_chama_id": reward.rewarded_chama_id,
                "rewarded_chama_name": reward.rewarded_chama.name if reward.rewarded_chama else "",
                "reward_type": reward.reward_type,
                "reward_value": reward.reward_value,
                "status": reward.status,
                "note": reward.note,
                "created_at": reward.created_at,
                "applied_at": reward.applied_at,
            }
            for reward in reward_qs[:50]
        ]

        serializer = ReferralSummarySerializer(
            {
                "referral_code": request.user.referral_code or "",
                "policy": get_referral_reward_policy(),
                "stats": stats,
                "history": history,
                "rewards": rewards,
            }
        )
        return Response(serializer.data, status=status.HTTP_200_OK)


class ReferralLeaderboardView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not request.user.is_staff:
            membership = _active_membership_for_user(request.user)
            effective_role = (
                get_effective_role(request.user, membership.chama_id, membership)
                if membership
                else None
            )
            if effective_role not in ADMIN_EQUIVALENT_ROLES:
                return Response(
                    {"detail": "You do not have permission to view the referral leaderboard."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        completed_referrals = (
            Chama.objects.filter(referred_by__isnull=False, setup_completed=True)
            .values("referred_by_id")
            .annotate(total=models.Count("id"))
        )
        completed_referral_map = {
            str(item["referred_by_id"]): item["total"] for item in completed_referrals
        }
        reward_aggregates = (
            ReferralReward.objects.values("referrer_id")
            .annotate(reward_days=models.Sum("reward_value"))
        )
        reward_map = {
            str(item["referrer_id"]): int(item["reward_days"] or 0)
            for item in reward_aggregates
        }

        top_referrers = (
            User.objects.filter(referred_chamas__isnull=False)
            .distinct()
            .order_by("full_name")
        )

        leaderboard = [
            {
                "user_id": user.id,
                "full_name": user.full_name,
                "referral_code": user.referral_code or "",
                "total_referrals": user.referred_chamas.count(),
                "completed_referrals": completed_referral_map.get(str(user.id), 0),
                "reward_days_earned": reward_map.get(str(user.id), 0),
                "reward_total_earned": reward_map.get(str(user.id), 0),
            }
            for user in top_referrers
        ]
        leaderboard.sort(
            key=lambda item: (
                item["completed_referrals"],
                item["total_referrals"],
                item["reward_days_earned"],
            ),
            reverse=True,
        )

        serializer = ReferralLeaderboardSerializer(
            {
                "policy": get_referral_reward_policy(),
                "leaderboard": leaderboard[:10],
            }
        )
        return Response(serializer.data, status=status.HTTP_200_OK)


class MembershipStatusView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        raw_chama_id = request.query_params.get("chama_id") or request.headers.get(
            "X-CHAMA-ID"
        )
        MembershipRequest.objects.filter(
            user=request.user,
            status=MembershipRequestStatus.PENDING,
            expires_at__lte=timezone.now(),
        ).update(status=MembershipRequestStatus.EXPIRED, updated_by_id=request.user.id)
        chama_id = None
        if raw_chama_id:
            try:
                chama_id = _parse_chama_id(raw_chama_id)
            except ValueError as exc:
                return Response(
                    {"detail": str(exc)},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        memberships = Membership.objects.filter(
            user=request.user,
            exited_at__isnull=True,
        ).select_related("chama")
        requests = MembershipRequest.objects.filter(user=request.user).select_related(
            "chama"
        )
        if chama_id:
            memberships = memberships.filter(chama_id=chama_id)
            requests = requests.filter(chama_id=chama_id)

        membership = memberships.order_by("-updated_at").first()
        membership_request = requests.order_by("-updated_at").first()

        role = ""
        status_value = "none"
        can_access = False
        membership_id = None
        request_id = None
        review_note = ""

        if membership and membership.status == MemberStatus.ACTIVE and membership.is_approved:
            status_value = MemberStatus.ACTIVE
            can_access = True
            role = get_effective_role(
                request.user,
                membership.chama_id,
                membership,
            ) or membership.role
            membership_id = membership.id
        elif membership_request:
            status_value = membership_request.status
            request_id = membership_request.id
            review_note = membership_request.review_note
        elif membership:
            status_value = membership.status
            membership_id = membership.id

        payload = {
            "chama_id": (
                membership.chama_id
                if membership
                else membership_request.chama_id if membership_request else None
            ),
            "status": status_value,
            "can_access": can_access,
            "role": role,
            "membership_id": membership_id,
            "membership_request_id": request_id,
            "review_note": review_note,
            "redirect_to": _membership_redirect(status_value),
        }
        return Response(
            MembershipStatusSerializer(payload).data,
            status=status.HTTP_200_OK,
        )


class PasswordResetRequestView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [PasswordResetRateThrottle, PasswordResetIdentifierRateThrottle]

    def post(self, request):
        from apps.accounts.services import (
            OTPDeliveryError,
            OTPRateLimitError,
            OTPService,
        )

        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.get_user()
        plain_code = None
        if user and user.is_active:
            try:
                delivery_method = serializer.validated_data.get("delivery_method", "sms")
                otp_token, plain_code = OTPService.generate_otp(
                    phone=user.phone,
                    user=user,
                    purpose=OTPPurpose.PASSWORD_RESET,
                    delivery_method=delivery_method,
                    ip_address=_client_ip(request),
                    user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
                )
                OTPService.send_otp(user.phone, otp_token, plain_code, user)
            except (OTPRateLimitError, OTPDeliveryError):
                # Keep generic response to prevent account enumeration.
                logger.info(
                    "Password reset OTP dispatch not completed for user=%s", user.id
                )
                plain_code = None

        return Response(
            _api_message(
                "PASSWORD_RESET_CODE_SENT",
                GENERIC_RESET_MESSAGE,
                plain_code=plain_code,
            ),
            status=status.HTTP_200_OK,
        )


class PasswordResetConfirmView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [PasswordResetRateThrottle]

    @transaction.atomic
    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            _api_message(
                "PASSWORD_RESET_SUCCESS",
                "Your password has been reset successfully.",
            ),
            status=status.HTTP_200_OK,
        )


class ChangePasswordView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            _api_message(
                "PASSWORD_CHANGED",
                "Your password has been updated successfully.",
            ),
            status=status.HTTP_200_OK,
        )


class MembershipOptionsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        memberships = (
            Membership.objects.select_related("chama")
            .filter(
                user=request.user,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
                exited_at__isnull=True,
            )
            .order_by("chama__name")
        )
        preference = _get_or_create_preferences(request.user)
        return Response(
            {
                "active_chama": (
                    str(preference.active_chama_id)
                    if preference.active_chama_id
                    else None
                ),
                "memberships": MembershipOptionSerializer(memberships, many=True).data,
            },
            status=status.HTTP_200_OK,
        )


class SwitchChamaView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = SwitchChamaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        chama_id = str(serializer.validated_data["chama_id"])
        membership = Membership.objects.filter(
            user=request.user,
            chama_id=chama_id,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        ).first()
        if not membership:
            return Response(
                {"detail": "You are not an active approved member of that chama."},
                status=status.HTTP_403_FORBIDDEN,
            )

        preference = _get_or_create_preferences(request.user)
        preference.active_chama_id = chama_id
        preference.save(update_fields=["active_chama", "updated_at"])
        request.session["active_chama_id"] = chama_id

        return Response(
            {
                "detail": "Active chama switched successfully.",
                "active_chama_id": chama_id,
                "role": get_effective_role(
                    request.user, membership.chama_id, membership
                )
                or membership.role,
            },
            status=status.HTTP_200_OK,
        )


class UserPreferenceView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        preference = _get_or_create_preferences(request.user)
        return Response(
            UserPreferenceSerializer(preference).data, status=status.HTTP_200_OK
        )

    def patch(self, request):
        preference = _get_or_create_preferences(request.user)
        serializer = UserPreferenceUpdateSerializer(
            preference,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)

        active_chama = serializer.validated_data.get("active_chama")
        if active_chama:
            membership = Membership.objects.filter(
                user=request.user,
                chama=active_chama,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
                exited_at__isnull=True,
            ).first()
            if not membership:
                return Response(
                    {
                        "detail": "active_chama must be one of your approved active memberships."
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        updated = serializer.save()
        if updated.active_chama_id:
            request.session["active_chama_id"] = str(updated.active_chama_id)
        return Response(
            UserPreferenceSerializer(updated).data, status=status.HTTP_200_OK
        )


class MemberKYCView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        chama_id = request.query_params.get("chama_id")
        queryset = MemberKYC.objects.filter(user=request.user).order_by("-created_at")
        if chama_id:
            try:
                parsed = _parse_chama_id(chama_id)
            except ValueError as exc:
                return Response(
                    {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
                )
            queryset = queryset.filter(chama_id=parsed)
        return Response(
            MemberKYCSerializer(queryset, many=True).data, status=status.HTTP_200_OK
        )

    def post(self, request):
        serializer = MemberKYCSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        raw_chama_id = serializer.validated_data.get("chama_id")
        chama = None
        if raw_chama_id:
            membership = (
                Membership.objects.select_related("chama")
                .filter(
                    user=request.user,
                    chama_id=raw_chama_id,
                    is_active=True,
                    is_approved=True,
                    status=MemberStatus.ACTIVE,
                    exited_at__isnull=True,
                )
                .first()
            )
            if membership:
                chama = membership.chama

        document_type = serializer.validated_data["document_type"]
        id_number = serializer.validated_data["id_number"]
        mpesa_registered_name = serializer.validated_data.get("mpesa_registered_name", "").strip()
        location_latitude = serializer.validated_data.get("location_latitude")
        location_longitude = serializer.validated_data.get("location_longitude")
        id_expiry_date = serializer.validated_data.get("id_expiry_date")
        provider_document_type = DOCUMENT_TYPE_TO_PROVIDER_TYPE.get(
            document_type,
            KYCDocumentType.KENYA_NATIONAL_ID,
        )

        id_validation = SmileIdentityService.verify_id_number(
            id_number,
            provider_document_type,
        )
        if not id_validation["valid"]:
            return Response(
                _api_message(
                    "KYC_INVALID_ID_NUMBER",
                    "Invalid ID number format.",
                    success=False,
                    errors=id_validation["errors"],
                    next_steps=["Please check your ID number and try again."],
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get images if provided
        id_front = serializer.validated_data.get("id_front_image")
        id_back = serializer.validated_data.get("id_back_image")
        selfie = serializer.validated_data.get("selfie_image")
        proof_of_address = serializer.validated_data.get("proof_of_address_image")

        quality_errors: list[str] = []
        quality_checks: dict[str, dict] = {}
        for field_name, upload in (
            ("id_front_image", id_front),
            ("id_back_image", id_back),
            ("selfie_image", selfie),
        ):
            passed, errors, metrics = KYCUploadService.assess_image_quality(
                upload,
                field_name=field_name.replace("_", " ").title(),
            )
            quality_checks[field_name] = metrics
            if not passed:
                quality_errors.extend(errors)

        duplicate_kyc = (
            MemberKYC.objects.exclude(user=request.user)
            .filter(id_number=id_number, document_type=document_type)
            .order_by("-created_at")
            .first()
        )
        screening_result = KYCUploadService.run_screening_checks(
            user=request.user,
            id_number=id_number,
        )

        provider_chama_id = str(getattr(chama, "id", "") or "platform")

        kyc, created = MemberKYC.objects.get_or_create(
            user=request.user,
            chama=chama,
            defaults={
                "document_type": document_type,
                "id_number": id_number,
                "status": MemberKYCStatus.PENDING,
                "submission_attempts": 1,
            },
        )
        if not created:
            was_rejected = kyc.status == MemberKYCStatus.REJECTED
            kyc.id_number = id_number
            kyc.status = MemberKYCStatus.PENDING
            kyc.review_note = ""
            kyc.reviewed_by = None
            kyc.reviewed_at = None
            kyc.submission_attempts += 1
            if was_rejected:
                kyc.resubmission_attempts += 1
            kyc.last_rejection_reason = ""
            kyc.escalated_to_system_admin_at = None

        kyc.document_type = document_type
        kyc.mpesa_registered_name = mpesa_registered_name
        kyc.id_expiry_date = id_expiry_date
        kyc.location_latitude = location_latitude
        kyc.location_longitude = location_longitude
        kyc.duplicate_id_detected = bool(duplicate_kyc)
        kyc.pep_match = screening_result["pep_match"]
        kyc.sanctions_match = screening_result["sanctions_match"]
        kyc.blacklist_match = screening_result["blacklist_match"]
        kyc.last_submitted_at = timezone.now()

        if id_front:
            kyc.id_front_image = id_front
        if id_back:
            kyc.id_back_image = id_back
        if selfie:
            kyc.selfie_image = selfie
        if proof_of_address:
            kyc.proof_of_address_image = proof_of_address
        kyc.save()

        # Auto-reject on hard blockers before provider verification.
        if duplicate_kyc or screening_result["blacklist_match"] or screening_result["pep_match"]:
            blocker_reasons: list[str] = []
            if duplicate_kyc:
                blocker_reasons.append("Duplicate ID found on the platform.")
            if screening_result["blacklist_match"]:
                blocker_reasons.append("Blacklisted identity detected.")
            if screening_result["pep_match"]:
                blocker_reasons.append("PEP screening match requires admin intervention.")

            verification_payload = {
                "success": False,
                "status": "auto_rejected",
                "document_type": document_type,
                "quality_checks": quality_checks,
                "duplicate_id_detected": bool(duplicate_kyc),
                "pep_match": screening_result["pep_match"],
                "sanctions_match": screening_result["sanctions_match"],
                "blacklist_match": screening_result["blacklist_match"],
                "next_steps": ["Contact support to appeal this verification decision."],
                "errors": blocker_reasons,
                "score": 0,
            }
            kyc.status = MemberKYCStatus.REJECTED
            kyc.kyc_tier = MemberKYCTier.TIER_0
            kyc.verification_score = 0
            kyc.rejection_attempts += 1
            kyc.last_rejection_reason = "; ".join(blocker_reasons)
            kyc.review_note = kyc.last_rejection_reason
            kyc.verification_result = verification_payload
            if kyc.rejection_attempts >= 3:
                kyc.escalated_to_system_admin_at = timezone.now()
            kyc.save(
                update_fields=[
                    "status",
                    "kyc_tier",
                    "verification_score",
                    "rejection_attempts",
                    "last_rejection_reason",
                    "review_note",
                    "verification_result",
                    "escalated_to_system_admin_at",
                    "updated_at",
                ]
            )
            from apps.automations.domain_services import notify_kyc_result

            notify_kyc_result(kyc_record=kyc, actor=request.user)
            return Response(
                {
                    **_api_message(
                        "KYC_SUBMITTED",
                        "Your KYC details have been submitted successfully.",
                        success=True,
                    ),
                    **MemberKYCSerializer(kyc).data,
                    "verification_result": verification_payload,
                },
                status=status.HTTP_200_OK,
            )

        has_images = bool(id_front and selfie and (id_back or document_type == MemberKYCDocumentType.PASSPORT))
        if has_images:
            try:
                import base64
                id_front_b64 = base64.b64encode(id_front.read()).decode('utf-8')
                id_back_b64 = (
                    base64.b64encode(id_back.read()).decode("utf-8")
                    if id_back
                    else None
                )
                selfie_b64 = base64.b64encode(selfie.read()).decode('utf-8')
                proof_of_address_b64 = None
                if proof_of_address:
                    proof_of_address_b64 = base64.b64encode(proof_of_address.read()).decode('utf-8')
                
                id_front.seek(0)
                if id_back:
                    id_back.seek(0)
                selfie.seek(0)
                if proof_of_address:
                    proof_of_address.seek(0)
                
                verification_request = EnhancedKYCService.KYCVerificationRequest(
                    user_id=str(request.user.id),
                    chama_id=provider_chama_id,
                    id_number=id_number,
                    document_type=provider_document_type,
                    id_document_image=id_front_b64,
                    id_back_image=id_back_b64,
                    selfie_image=selfie_b64,
                    first_name=request.user.full_name.split()[0] if request.user.full_name else "",
                    last_name=" ".join(request.user.full_name.split()[1:]) if request.user.full_name and len(request.user.full_name.split()) > 1 else "",
                    phone_number=request.user.phone or "",
                    mpesa_registered_name=mpesa_registered_name or None,
                    proof_of_address=proof_of_address_b64,
                    location_latitude=float(location_latitude) if location_latitude is not None else None,
                    location_longitude=float(location_longitude) if location_longitude is not None else None,
                )
                
                verification_result = EnhancedKYCService.verify_identity(verification_request)
                verification_provider = (
                    verification_result.reference_id.split("-", 1)[0]
                    if verification_result.reference_id and "-" in verification_result.reference_id
                    else "smile_identity"
                )
                computed_score = 45
                if verification_result.id_verified:
                    computed_score += 15
                if verification_result.face_matched:
                    computed_score += 15
                if verification_result.liveness_passed:
                    computed_score += 15
                if verification_result.government_verified:
                    computed_score += 5
                if verification_result.mpesa_name_matched:
                    computed_score += 5
                computed_score -= min(len(quality_errors) * 10, 30)
                computed_score -= min(len(verification_result.errors or []) * 10, 30)
                if screening_result["sanctions_match"]:
                    computed_score = 0
                computed_score = max(0, min(100, computed_score))

                verification_payload = {
                    "success": verification_result.success,
                    "eligible_for_loans": verification_result.eligible_for_loans,
                    "id_verified": verification_result.id_verified,
                    "face_matched": verification_result.face_matched,
                    "liveness_passed": verification_result.liveness_passed,
                    "mpesa_name_matched": verification_result.mpesa_name_matched,
                    "government_verified": verification_result.government_verified,
                    "warnings": [*quality_errors, *(verification_result.warnings or [])],
                    "errors": verification_result.errors,
                    "next_steps": verification_result.next_steps,
                    "verification_level": verification_result.kyc_level.value,
                    "reference_id": verification_result.reference_id,
                    "provider": verification_provider,
                    "location_shared": location_latitude is not None and location_longitude is not None,
                    "document_type": document_type,
                    "quality_checks": quality_checks,
                    "score": computed_score,
                    "manual_review_required": 70 <= computed_score <= 80,
                    "duplicate_id_detected": bool(duplicate_kyc),
                    "pep_match": screening_result["pep_match"],
                    "sanctions_match": screening_result["sanctions_match"],
                    "blacklist_match": screening_result["blacklist_match"],
                    "resubmission_attempt": kyc.resubmission_attempts,
                }

                rejection_errors = list(verification_result.errors or [])
                if quality_errors:
                    rejection_errors.extend(quality_errors)
                if screening_result["sanctions_match"]:
                    rejection_errors.append("Sanctions screening match detected.")

                if screening_result["sanctions_match"]:
                    kyc.status = MemberKYCStatus.REJECTED
                    kyc.kyc_tier = MemberKYCTier.TIER_0
                    kyc.verification_score = 0
                    kyc.review_note = "Sanctions screening match detected."
                    kyc.last_rejection_reason = kyc.review_note
                    kyc.rejection_attempts += 1
                elif computed_score >= 81 and verification_result.success and verification_result.eligible_for_loans:
                    kyc.status = MemberKYCStatus.APPROVED
                    kyc.kyc_tier = MemberKYCTier.TIER_2
                    kyc.verification_score = computed_score
                    kyc.review_note = (
                        "Verified for loans: "
                        f"ID={verification_result.id_verified}, "
                        f"Face={verification_result.face_matched}, "
                        f"Liveness={verification_result.liveness_passed}, "
                        f"M-Pesa name match={verification_result.mpesa_name_matched}"
                    )
                    kyc.verification_result = verification_payload
                    kyc.auto_verification_provider = verification_provider
                    kyc.auto_verification_reference = verification_result.reference_id or ""
                    kyc.auto_verified_at = timezone.now()
                    kyc.save()
                elif computed_score >= 70:
                    kyc.status = MemberKYCStatus.PENDING
                    kyc.kyc_tier = MemberKYCTier.TIER_1
                    kyc.verification_score = computed_score
                    kyc.review_note = "Queued for manual review."
                    verification_payload["next_steps"] = [
                        "Your submission is under manual review by the system admin team.",
                        "We will notify you once review is complete.",
                    ]
                    kyc.verification_result = verification_payload
                    kyc.auto_verification_provider = verification_provider
                    kyc.auto_verification_reference = verification_result.reference_id or ""
                    kyc.auto_verified_at = timezone.now()
                    kyc.save()
                else:
                    kyc.status = MemberKYCStatus.REJECTED
                    kyc.kyc_tier = MemberKYCTier.TIER_0
                    kyc.verification_score = computed_score
                    kyc.review_note = "; ".join(rejection_errors or ["Verification score below threshold."])
                    kyc.verification_result = verification_payload
                    kyc.rejection_attempts += 1
                    kyc.last_rejection_reason = kyc.review_note
                    kyc.auto_verification_provider = verification_provider
                    kyc.auto_verification_reference = verification_result.reference_id or ""
                    kyc.auto_verified_at = timezone.now()
                    if kyc.rejection_attempts >= 3:
                        kyc.escalated_to_system_admin_at = timezone.now()
                    kyc.save()

                from apps.automations.domain_services import notify_kyc_result

                notify_kyc_result(kyc_record=kyc, actor=request.user)
                    
                return Response(
                    {
                        **_api_message(
                            "KYC_SUBMITTED",
                            "Your KYC details have been submitted successfully.",
                            success=True,
                        ),
                        **MemberKYCSerializer(kyc).data,
                        "verification_result": verification_payload,
                    },
                    status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
                )
            except Exception as e:
                logger.error(f"KYC verification error: {str(e)}")
                kyc.verification_result = {
                    "success": False,
                    "status": "queued",
                    "document_type": document_type,
                    "quality_checks": quality_checks,
                    "warnings": quality_errors,
                    "errors": ["Verification provider unavailable. Submission queued for manual review."],
                    "next_steps": ["We are reviewing your documents manually."],
                    "score": 70 if not quality_errors else 60,
                    "manual_review_required": True,
                }
                kyc.status = MemberKYCStatus.PENDING
                kyc.kyc_tier = MemberKYCTier.TIER_1
                kyc.verification_score = 70 if not quality_errors else 60
                kyc.review_note = "Queued for manual review after provider verification failure."
                kyc.save(
                    update_fields=[
                        "verification_result",
                        "status",
                        "kyc_tier",
                        "verification_score",
                        "review_note",
                        "updated_at",
                    ]
                )

        return Response(
            {
                **_api_message(
                    "KYC_SUBMITTED",
                    "Your KYC details have been submitted successfully.",
                    success=True,
                ),
                **MemberKYCSerializer(kyc).data,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class MemberKYCReverificationTriggerView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id):
        kyc = MemberKYC.objects.select_related("chama").filter(id=id).first()
        if not kyc:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        membership = Membership.objects.filter(
            user=request.user,
            chama=kyc.chama,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
            role__in=[
                MembershipRole.CHAMA_ADMIN,
                MembershipRole.ADMIN,
                MembershipRole.SECRETARY,
            ],
        ).first()
        if not membership and not request.user.is_superuser:
            return Response(
                {"detail": "Only governance/security operators can trigger re-verification."},
                status=status.HTTP_403_FORBIDDEN,
            )

        reason = str(request.data.get("reason", "")).strip()
        if not reason:
            return Response(
                {"detail": "reason is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        kyc.requires_reverification = True
        kyc.reverification_reason = reason
        kyc.next_reverification_due_at = timezone.localdate()
        kyc.status = MemberKYCStatus.PENDING
        kyc.kyc_tier = MemberKYCTier.TIER_0
        kyc.review_note = reason
        kyc.save(
            update_fields=[
                "requires_reverification",
                "reverification_reason",
                "next_reverification_due_at",
                "status",
                "kyc_tier",
                "review_note",
                "updated_at",
            ]
        )

        return Response(MemberKYCSerializer(kyc).data, status=status.HTTP_200_OK)


class MemberKYCReviewView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id):
        kyc = MemberKYC.objects.select_related("chama").filter(id=id).first()
        if not kyc:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        # Compliance: Chama admins must never approve KYC. Only system admins can action escalations.
        if not request.user.is_staff and not request.user.is_superuser:
            return Response(
                {"detail": "Only system administrators can review KYC escalations."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = MemberKYCReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        kyc.status = serializer.validated_data["status"]
        kyc.review_note = serializer.validated_data.get("review_note", "")
        kyc.reviewed_by = request.user
        kyc.reviewed_at = timezone.now()
        if kyc.status == MemberKYCStatus.APPROVED:
            kyc.kyc_tier = MemberKYCTier.TIER_2
            kyc.verification_score = max(kyc.verification_score, 81)
            kyc.auto_verified_at = kyc.auto_verified_at or timezone.now()
        if kyc.status == MemberKYCStatus.REJECTED:
            kyc.kyc_tier = MemberKYCTier.TIER_0
            kyc.rejection_attempts += 1
            kyc.last_rejection_reason = kyc.review_note
        kyc.save(
            update_fields=[
                "status",
                "kyc_tier",
                "verification_score",
                "review_note",
                "reviewed_by",
                "reviewed_at",
                "rejection_attempts",
                "last_rejection_reason",
                "auto_verified_at",
                "updated_at",
            ]
        )

        from apps.automations.domain_services import notify_kyc_result

        notify_kyc_result(kyc_record=kyc, actor=request.user)

        return Response(MemberKYCSerializer(kyc).data, status=status.HTTP_200_OK)


class MemberCardView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        raw_chama_id = request.query_params.get("chama_id") or request.headers.get(
            "X-CHAMA-ID"
        )
        if not raw_chama_id:
            preference = _get_or_create_preferences(request.user)
            raw_chama_id = preference.active_chama_id
        if not raw_chama_id:
            return Response(
                {"detail": "Provide chama_id or set an active chama."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            chama_id = _parse_chama_id(raw_chama_id)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        membership = Membership.objects.filter(
            user=request.user,
            chama_id=chama_id,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        ).first()
        if not membership:
            return Response(
                {"detail": "You are not an active approved member in this chama."},
                status=status.HTTP_403_FORBIDDEN,
            )

        card, _ = MemberCard.objects.get_or_create(
            user=request.user,
            chama_id=chama_id,
            is_active=True,
            defaults={
                "card_number": f"CHM-{str(chama_id).split('-')[0].upper()}-{str(request.user.id).split('-')[0].upper()}",
                "qr_token": secrets.token_hex(24),
            },
        )
        payload = MemberCardSerializer(card).data
        payload["qr_payload"] = (
            f"CARD|{card.qr_token}|USER|{request.user.id}|CHAMA|{chama_id}"
        )
        return Response(payload, status=status.HTTP_200_OK)


class SecurityCenterView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        days = int(request.query_params.get("days", 30))
        since = timezone.now() - timedelta(days=max(days, 1))
        recent_events = LoginEvent.objects.filter(
            user=request.user,
            created_at__gte=since,
        ).order_by("-created_at")

        failed_count = recent_events.filter(success=False).count()
        success_count = recent_events.filter(success=True).count()
        unique_ips = list(
            recent_events.values_list("ip_address", flat=True)
            .exclude(ip_address__isnull=True)
            .distinct()
        )
        recent_devices = list(
            recent_events.values("device_id", "user_agent", "ip_address", "created_at")[
                :10
            ]
        )

        return Response(
            {
                "window_days": days,
                "failed_logins": failed_count,
                "successful_logins": success_count,
                "unique_ip_count": len(unique_ips),
                "unique_ips": unique_ips[:20],
                "recent_devices": recent_devices,
            },
            status=status.HTTP_200_OK,
        )
