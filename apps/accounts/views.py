import hashlib
import logging
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import models, transaction
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework import permissions, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from apps.accounts.models import (
    LoginEvent,
    MemberCard,
    MemberKYC,
    MemberKYCStatus,
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
    MembershipStatusSerializer,
    MemberCardSerializer,
    MemberKYCReviewSerializer,
    MemberKYCSerializer,
    MemberKYCSubmitSerializer,
    MembershipOptionSerializer,
    PublicOTPRequestSerializer,
    PublicOTPVerifySerializer,
    ReferralLeaderboardSerializer,
    RegisterSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    ProfileUpdateSerializer,
    ReferralSummarySerializer,
    SwitchChamaSerializer,
    UserPreferenceSerializer,
    UserPreferenceUpdateSerializer,
    UserSerializer,
)
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
    OTPIdentifierRateThrottle,
    OTPRequestRateThrottle,
    RegisterIdentifierRateThrottle,
    RegisterRateThrottle,
    LoginIdentifierRateThrottle,
    LoginRateThrottle,
    PasswordResetIdentifierRateThrottle,
    PasswordResetRateThrottle,
)
from core.utils import normalize_kenyan_phone

logger = logging.getLogger(__name__)


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


class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [RegisterRateThrottle, RegisterIdentifierRateThrottle]

    @transaction.atomic
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Fire phone verification OTP immediately after registration.
        from apps.accounts.services import (
            OTPDeliveryError,
            OTPRateLimitError,
            OTPService,
        )

        try:
            user = serializer.save()
            otp_token, plain_code = OTPService.generate_otp(
                phone=user.phone,
                user=user,
                purpose=OTPPurpose.VERIFY_PHONE,
                delivery_method=serializer.validated_data["otp_delivery_method"],
            )
            delivery_result = OTPService.send_otp(user.phone, otp_token, plain_code, user)
        except OTPRateLimitError as exc:
            transaction.set_rollback(True)
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        except OTPDeliveryError as exc:
            transaction.set_rollback(True)
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        SecurityService.create_audit_log(
            action_type="REGISTER",
            target_type="User",
            target_id=str(user.id),
            actor=user,
            metadata={
                "phone_verified": user.phone_verified,
                "otp_purpose": OTPPurpose.VERIFY_PHONE,
                "otp_delivery_method": serializer.validated_data["otp_delivery_method"],
            },
            ip_address=_client_ip(request),
        )

        return Response(
            {
                "detail": "Account created successfully. Verify your phone with OTP.",
                "user": UserSerializer(user, context={"request": request}).data,
                "phone_verification_required": True,
                "delivery": {
                    "channels": delivery_result.channels_sent,
                    "phone": delivery_result.masked_phone if delivery_result.sms_sent else "",
                    "email": delivery_result.masked_email if delivery_result.email_sent else "",
                },
            },
            status=status.HTTP_201_CREATED,
        )


class PublicOTPRequestView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [OTPRequestRateThrottle, OTPIdentifierRateThrottle]

    def post(self, request):
        serializer = PublicOTPRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone = serializer.validated_data["phone"]
        purpose = serializer.validated_data["purpose"]
        delivery_method = serializer.validated_data["delivery_method"]

        # Get IP address for security tracking
        ip_address = _client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")[:500]
        
        # Check if user exists
        user = User.objects.filter(phone=phone, is_active=True).first()
        
        from apps.accounts.services import (
            OTPDeliveryError,
            OTPRateLimitError,
            OTPService,
        )

        try:
            # Generate OTP - now works for both registered and new users
            otp_token, plain_code = OTPService.generate_otp(
                phone=phone,
                user=user,
                purpose=purpose,
                delivery_method=delivery_method,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            
            # Send OTP
            OTPService.send_otp(phone, otp_token, plain_code, user)
            
        except OTPRateLimitError as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        except OTPDeliveryError as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # Generic response to prevent account enumeration.
        return Response(
            {"detail": "If the account exists, an OTP has been sent."},
            status=status.HTTP_200_OK,
        )


class PublicOTPVerifyView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [OTPRequestRateThrottle, OTPIdentifierRateThrottle]

    @transaction.atomic
    def post(self, request):
        serializer = PublicOTPVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone = serializer.validated_data["phone"]
        purpose = serializer.validated_data["purpose"]
        code = serializer.validated_data["code"]

        # Get user if exists
        user = User.objects.filter(phone=phone, is_active=True).first()
        
        from apps.accounts.services import OTPService

        # Verify OTP using phone (works for both registered and new users)
        verified, message = OTPService.verify_otp(
            phone=phone,
            code=code,
            purpose=purpose,
            user=user,
        )
        
        if not verified:
            return Response(
                {"detail": message},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # If user exists and this is phone verification, update user
        if user and purpose == OTPPurpose.VERIFY_PHONE:
            user.phone_verified = True
            user.phone_verified_at = timezone.now()
            user.save(update_fields=["phone_verified", "phone_verified_at"])

        if user:
            SecurityService.create_audit_log(
                action_type="OTP_VERIFIED",
                target_type="User",
                target_id=str(user.id),
                actor=user,
                metadata={"purpose": purpose},
                ip_address=_client_ip(request),
            )

        return Response(
            {
                "detail": "OTP verified successfully.",
                "purpose": purpose,
                "phone_verified": user.phone_verified if user else True,
            },
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
        from apps.accounts.models import OTPToken
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
                    "detail": "Too many failed login attempts. Try again later.",
                    "code": "account_locked",
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        serializer = LoginSerializer(data=request.data, context={"request": request})

        if not serializer.is_valid():
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
                    "detail": "Invalid phone or password.",
                    "code": "invalid_credentials",
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        user = serializer.validated_data["user"]
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        device_id = str(request.headers.get("X-Device-ID", "")).strip()
        device_name = str(request.headers.get("X-Device-Name", "")).strip()
        if not device_name:
            device_name = device_id or user_agent[:80] or "Unknown device"

        self._clear_failed_attempts(identifier=identifier, ip_address=ip_address)
        SecurityService.clear_identifier_locks(identifier=identifier)
        new_device = self._is_new_device_login(user, request, ip_address)

        user.last_login_at = timezone.now()
        user.last_login_ip = ip_address
        user.save(update_fields=["last_login_at", "last_login_ip"])

        refresh = RefreshToken.for_user(user)
        refresh_jti = str(refresh.get("jti", ""))
        active_membership = (
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
        _, session_is_new_device = SecurityService.register_device_session(
            user=user,
            chama=active_membership.chama if active_membership else None,
            device_name=device_name,
            ip_address=ip_address,
            user_agent=user_agent,
            session_key=refresh_jti,
        )
        new_device = bool(new_device or session_is_new_device)

        LoginEvent.objects.create(
            user=user,
            identifier_attempted=serializer.validated_data["phone"],
            ip_address=ip_address,
            user_agent=user_agent,
            success=True,
            device_id=device_id,
            session_key=refresh_jti,
            metadata={**_build_login_metadata(request), "new_device": new_device},
        )
        SecurityService.record_login_attempt(
            identifier=serializer.validated_data["phone"],
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
                "session_key": refresh_jti,
                "new_device": new_device,
            },
            ip_address=ip_address,
        )
        if new_device:
            self._send_new_device_alert(
                user=user,
                ip_address=ip_address,
                user_agent=user_agent,
            )

        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": UserSerializer(user, context={"request": request}).data,
            },
            status=status.HTTP_200_OK,
        )


class RefreshView(TokenRefreshView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []


class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            token = RefreshToken(serializer.validated_data["refresh"])
            refresh_jti = str(token.get("jti", ""))
            token.blacklist()
        except TokenError:
            return Response(
                {"detail": "Invalid refresh token.", "code": "invalid_token"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        revoked_count = 0
        if refresh_jti:
            revoked_count = request.user.device_sessions.filter(
                session_key=refresh_jti,
                is_revoked=False,
            ).update(is_revoked=True, last_seen=timezone.now())
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
        from apps.accounts.services import (
            OTPDeliveryError,
            OTPRateLimitError,
            OTPService,
        )
        from apps.accounts.serializers import OTPRequestSerializer

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
                {
                    "detail": str(exc),
                    "code": "otp_rate_limited",
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        except OTPDeliveryError as exc:
            return Response(
                {
                    "detail": str(exc),
                    "code": "otp_send_failed",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {
                "detail": f"OTP sent via {', '.join(delivery_result.channels_sent)}.",
                "expires_in_seconds": OTPService.otp_expiry_minutes() * 60,
                "delivery": {
                    "channels": delivery_result.channels_sent,
                    "phone": delivery_result.masked_phone if delivery_result.sms_sent else "",
                    "email": delivery_result.masked_email if delivery_result.email_sent else "",
                },
            },
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
        from apps.accounts.services import OTPService
        from apps.accounts.serializers import OTPVerifySerializer

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
                {
                    "detail": message,
                    "code": "invalid_otp",
                },
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
            {
                "detail": "OTP verified successfully. Two-factor authentication enabled.",
                "code": "otp_verified",
            },
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
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.get_user()
        if user and user.is_active:
            raw_token = secrets.token_urlsafe(32)
            ttl_minutes = getattr(settings, "PASSWORD_RESET_TOKEN_MINUTES", 30)
            expires_at = timezone.now() + timedelta(minutes=ttl_minutes)
            token_hash = PasswordResetToken.hash_token(raw_token)

            PasswordResetToken.objects.create(
                user=user,
                token_hash=token_hash,
                expires_at=expires_at,
            )

        return Response({"detail": GENERIC_RESET_MESSAGE}, status=status.HTTP_200_OK)


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
            {"detail": "Password reset successful."}, status=status.HTTP_200_OK
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
            {"detail": "Password changed successfully."},
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
                {"detail": "You must be an active approved member to submit KYC."},
                status=status.HTTP_403_FORBIDDEN,
            )

        kyc, created = MemberKYC.objects.get_or_create(
            user=request.user,
            chama_id=chama_id,
            defaults={
                "id_number": serializer.validated_data["id_number"],
                "status": MemberKYCStatus.PENDING,
            },
        )
        if not created:
            kyc.id_number = serializer.validated_data["id_number"]
            kyc.status = MemberKYCStatus.PENDING
            kyc.review_note = ""
            kyc.reviewed_by = None
            kyc.reviewed_at = None

        id_front = serializer.validated_data.get("id_front_image")
        selfie = serializer.validated_data.get("selfie_image")
        if id_front:
            kyc.id_front_image = id_front
        if selfie:
            kyc.selfie_image = selfie
        kyc.save()

        return Response(
            MemberKYCSerializer(kyc).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class MemberKYCReviewView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id):
        kyc = MemberKYC.objects.select_related("chama").filter(id=id).first()
        if not kyc:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        reviewer_membership = Membership.objects.filter(
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
        if not reviewer_membership:
            return Response(
                {"detail": "Only chama admin/secretary can review KYC."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = MemberKYCReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        kyc.status = serializer.validated_data["status"]
        kyc.review_note = serializer.validated_data.get("review_note", "")
        kyc.reviewed_by = request.user
        kyc.reviewed_at = timezone.now()
        kyc.save(
            update_fields=[
                "status",
                "review_note",
                "reviewed_by",
                "reviewed_at",
                "updated_at",
            ]
        )

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
