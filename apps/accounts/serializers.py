import logging

from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.html import strip_tags
from rest_framework import serializers

from apps.accounts.models import (
    AccessTier,
    MemberCard,
    MemberKYC,
    MemberKYCDocumentType,
    MemberKYCTier,
    MemberKYCStatus,
    OTPDeliveryMethod,
    OTPPurpose,
    PasswordResetToken,
    User,
    UserKYCState,
    UserPreference,
)
from apps.accounts.password_security import validate_password_security
from apps.accounts.services import KYCService
from apps.chama.models import Membership
from apps.security.services import SecurityService
from core.encryption import PIIMaskingService
from core.utils import normalize_kenyan_phone

logger = logging.getLogger(__name__)

from core.feedback import (  # noqa: E402
    AUTH_FEEDBACK,
    CHAMA_FEEDBACK,
    KYC_FEEDBACK,
)

GENERIC_RESET_MESSAGE = "If an account exists, password reset instructions have been sent."


def _split_full_name(value: str | None) -> tuple[str, str]:
    parts = str(value or "").strip().split()
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


class UserSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()
    first_name = serializers.SerializerMethodField()
    last_name = serializers.SerializerMethodField()
    avatar = serializers.SerializerMethodField()
    referral_count = serializers.SerializerMethodField()
    profile_completed = serializers.SerializerMethodField()
    active_chama_id = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "phone",
            "phone_verified",
            "otp_verified",
            "phone_verified_at",
            "email_verified",
            "email_verified_at",
            "email",
            "full_name",
            "first_name",
            "last_name",
            "avatar",
            "profile_completed",
            "active_chama_id",
            "is_active",
            "last_login_at",
            "last_login_ip",
            "two_factor_enabled",
            "two_factor_method",
            "tier_access",
            "kyc_status",
            "kyc_verified_at",
            "financial_access_enabled",
            "account_frozen",
            "account_locked_until",
            "date_joined",
            "role",
            "referral_code",
            "referral_count",
        ]
        read_only_fields = [
            "id",
            "is_active",
            "last_login_at",
            "last_login_ip",
            "date_joined",
            "role",
            "referral_code",
            "referral_count",
            "tier_access",
            "kyc_status",
            "kyc_verified_at",
            "financial_access_enabled",
            "account_frozen",
            "account_locked_until",
        ]

    def get_role(self, obj) -> str | None:
        """Get the user's role from their active membership."""
        from apps.chama.models import Membership
        
        membership = Membership.objects.filter(
            user=obj,
            is_active=True,
            is_approved=True,
            exited_at__isnull=True,
        ).order_by("joined_at").first()
        
        if membership:
            return membership.role
        return None

    def get_first_name(self, obj) -> str:
        first_name, _ = _split_full_name(obj.full_name)
        return first_name

    def get_last_name(self, obj) -> str:
        _, last_name = _split_full_name(obj.full_name)
        return last_name

    def get_avatar(self, obj) -> str | None:
        if not obj.avatar:
            return None

        avatar_url = obj.avatar.url
        request = self.context.get("request")
        if request is None:
            return avatar_url
        return request.build_absolute_uri(avatar_url)

    def get_referral_count(self, obj) -> int:
        return obj.referred_chamas.count()

    def get_profile_completed(self, obj) -> bool:
        return bool(str(obj.full_name or "").strip() and str(obj.email or "").strip())

    def get_active_chama_id(self, obj) -> str | None:
        preference = getattr(obj, "preferences", None)
        if preference and preference.active_chama_id:
            return str(preference.active_chama_id)

        membership = (
            Membership.objects.filter(
                user=obj,
                is_active=True,
                is_approved=True,
                exited_at__isnull=True,
            )
            .order_by("joined_at")
            .first()
        )
        return str(membership.chama_id) if membership else None


class ProfileUpdateSerializer(serializers.ModelSerializer):
    first_name = serializers.CharField(required=False, allow_blank=True, write_only=True)
    last_name = serializers.CharField(required=False, allow_blank=True, write_only=True)

    class Meta:
        model = User
        fields = ["email", "full_name", "first_name", "last_name", "avatar"]

    def validate_email(self, value):
        cleaned = str(value or "").strip().lower()
        if not cleaned:
            return ""

        queryset = User.objects.filter(email__iexact=cleaned)
        if self.instance:
            queryset = queryset.exclude(id=self.instance.id)
        if queryset.exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return cleaned

    def validate_full_name(self, value):
        cleaned = strip_tags(str(value or "")).strip()
        if not cleaned:
            raise serializers.ValidationError("Full name cannot be empty.")
        return cleaned

    def validate(self, attrs):
        first_name = attrs.pop("first_name", None)
        last_name = attrs.pop("last_name", None)

        if first_name is not None or last_name is not None:
            current_first, current_last = _split_full_name(
                self.instance.full_name if self.instance else ""
            )
            resolved_first = strip_tags(
                str(first_name if first_name is not None else current_first)
            ).strip()
            resolved_last = strip_tags(
                str(last_name if last_name is not None else current_last)
            ).strip()
            combined_name = " ".join(
                item for item in [resolved_first, resolved_last] if item
            ).strip()
            if not combined_name:
                raise serializers.ValidationError(
                    {"first_name": "Provide at least one name."}
                )
            attrs["full_name"] = combined_name

        return attrs


class RegisterSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=16)
    full_name = serializers.CharField(max_length=255)
    email = serializers.EmailField(required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, min_length=8)
    password_confirm = serializers.CharField(write_only=True, min_length=8)
    otp_delivery_method = serializers.ChoiceField(
        choices=[OTPDeliveryMethod.SMS, OTPDeliveryMethod.EMAIL],
        default=OTPDeliveryMethod.SMS,
    )

    default_error_messages = {
        "invalid_phone": AUTH_FEEDBACK["REGISTER_INVALID_PHONE"].message,
        "duplicate_phone": AUTH_FEEDBACK["REGISTER_PHONE_EXISTS"].message,
        "invalid_email": AUTH_FEEDBACK["REGISTER_INVALID_EMAIL"].message,
        "duplicate_email": AUTH_FEEDBACK["REGISTER_EMAIL_EXISTS"].message,
        "password_mismatch": AUTH_FEEDBACK["REGISTER_PASSWORD_MISMATCH"].message,
        "email_required_for_email_delivery": "Email address is required when delivery method is email.",
        "weak_password": AUTH_FEEDBACK["REGISTER_WEAK_PASSWORD"].message,
        "empty_name": "Full name is required.",
    }

    def validate_phone(self, value):
        try:
            normalized = normalize_kenyan_phone(value)
        except ValueError as exc:
            logger.info(
                "register_phone_invalid phone=%s reason=%s",
                PIIMaskingService.mask_phone(str(value or "")),
                str(exc),
            )
            raise serializers.ValidationError(self.error_messages.get("invalid_phone"))

        if User.objects.filter(phone=normalized).exists():
            logger.info(
                "register_phone_duplicate phone=%s",
                PIIMaskingService.mask_phone(normalized),
            )
            raise serializers.ValidationError(self.error_messages.get("duplicate_phone"))
        return normalized

    def validate_full_name(self, value):
        cleaned = strip_tags(str(value or "")).strip()
        if not cleaned:
            raise serializers.ValidationError(self.error_messages.get("empty_name"))
        return cleaned

    def validate_email(self, value):
        cleaned = str(value or "").strip().lower()
        if cleaned and User.objects.filter(email__iexact=cleaned).exists():
            raise serializers.ValidationError(self.error_messages.get("duplicate_email"))
        return cleaned

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError(
                {"password_confirm": self.error_messages.get("password_mismatch")}
            )

        if (
            attrs.get("otp_delivery_method") == OTPDeliveryMethod.EMAIL
            and not str(attrs.get("email") or "").strip()
        ):
            raise serializers.ValidationError(
                {"email": self.error_messages.get("email_required_for_email_delivery")}
            )

        try:
            validate_password(attrs["password"])
            validate_password_security(attrs["password"])
        except ValidationError as e:
            messages = [str(item).strip() for item in getattr(e, "messages", []) if str(item).strip()]
            # Fall back to a safe, user-facing summary when validators return no messages.
            if not messages:
                messages = [self.error_messages.get("weak_password")]
            # Deduplicate while preserving order.
            deduped: list[str] = []
            for message in messages:
                if message and message not in deduped:
                    deduped.append(message)
            raise serializers.ValidationError({"password": deduped})
        return attrs

    def save(self, **kwargs):
        user = User.objects.create_user(
            phone=self.validated_data["phone"],
            password=self.validated_data["password"],
            full_name=self.validated_data["full_name"],
            email=self.validated_data.get("email") or "",
        )
        return user


class LoginSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=16)
    password = serializers.CharField(write_only=True)

    default_error_messages = {
        "invalid_credentials": AUTH_FEEDBACK["LOGIN_FAILED"].message,
        "inactive_account": AUTH_FEEDBACK["LOGIN_ACCOUNT_INACTIVE"].message,
    }

    def validate(self, attrs):
        phone = attrs.get("phone")
        password = attrs.get("password")

        logger.info(f"Login attempt for phone: {phone}")

        try:
            normalized_phone = normalize_kenyan_phone(phone)
            logger.info(f"Normalized phone: {normalized_phone}")
        except ValueError as e:
            logger.error(f"Phone normalization failed: {e}")
            self.fail("invalid_credentials")

        try:
            user = authenticate(
                request=self.context.get("request"),
                phone=normalized_phone,
                password=password,
            )
            logger.info(f"Authentication result: {user}")
        except Exception as e:
            logger.error(f"Authentication error: {e}", exc_info=True)
            raise

        if not user:
            logger.warning(f"Authentication failed for phone: {normalized_phone}")
            self.fail("invalid_credentials")
        if not user.is_active:
            logger.warning(f"Inactive user attempted login: {normalized_phone}")
            self.fail("inactive_account")

        attrs["phone"] = normalized_phone
        attrs["user"] = user
        return attrs


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(required=False, allow_blank=True)

    def validate_refresh(self, value):
        normalized = str(value or "").strip()
        return normalized


class PasswordResetRequestSerializer(serializers.Serializer):
    identifier = serializers.CharField(max_length=255, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=16, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    delivery_method = serializers.ChoiceField(
        choices=OTPDeliveryMethod.choices,
        required=False,
        default=OTPDeliveryMethod.SMS
    )

    def validate(self, attrs):
        identifier = str(
            attrs.get("identifier") or attrs.get("email") or attrs.get("phone") or ""
        ).strip()
        if not identifier:
            raise serializers.ValidationError(
                {"identifier": "Phone number or email is required."}
            )

        attrs["identifier"] = identifier
        if "@" in identifier:
            attrs["email"] = serializers.EmailField().run_validation(identifier.lower())
            attrs["phone"] = ""
            return attrs

        try:
            attrs["phone"] = normalize_kenyan_phone(identifier)
        except ValueError as exc:
            raise serializers.ValidationError({"identifier": str(exc)}) from exc
        attrs["email"] = ""
        return attrs

    def get_user(self):
        email = self.validated_data.get("email")
        if email:
            return User.objects.filter(email__iexact=email).first()

        phone = self.validated_data.get("phone")
        if not phone:
            return None
        return User.objects.filter(phone=phone).first()


class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=512, required=False, allow_blank=True)
    identifier = serializers.CharField(max_length=255, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=16, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    code = serializers.CharField(max_length=6, min_length=6, required=False, allow_blank=True)
    new_password = serializers.CharField(write_only=True, min_length=8)

    default_error_messages = {
        "invalid_token": "Reset token is invalid or expired.",
        "invalid_code": "Reset code is invalid or expired.",
    }

    def validate_new_password(self, value):
        validate_password(value)
        validate_password_security(value)
        return value

    def validate(self, attrs):
        from apps.accounts.services import OTPService

        raw_token = str(attrs.get("token") or "").strip()
        if raw_token:
            token_hash = PasswordResetToken.hash_token(raw_token)
            token_obj = (
                PasswordResetToken.objects.select_related("user")
                .filter(token_hash=token_hash)
                .order_by("-created_at")
                .first()
            )

            if not token_obj or not token_obj.is_usable:
                self.fail("invalid_token")

            attrs["token_obj"] = token_obj
            attrs["user"] = token_obj.user
            attrs["mode"] = "token"
            return attrs

        identifier = str(
            attrs.get("identifier") or attrs.get("email") or attrs.get("phone") or ""
        ).strip()
        code = str(attrs.get("code") or "").strip()
        if not identifier or not code:
            raise serializers.ValidationError(
                {
                    "identifier": "Phone number or email is required when token is not provided.",
                    "code": "Reset code is required when token is not provided.",
                }
            )

        if not code.isdigit():
            raise serializers.ValidationError({"code": "Reset code must contain only digits."})

        if "@" in identifier:
            email = serializers.EmailField().run_validation(identifier.lower())
            user = User.objects.filter(email__iexact=email, is_active=True).first()
            phone = user.phone if user else ""
        else:
            try:
                phone = normalize_kenyan_phone(identifier)
            except ValueError as exc:
                raise serializers.ValidationError({"identifier": str(exc)}) from exc
            user = User.objects.filter(phone=phone, is_active=True).first()

        if not user or not phone:
            self.fail("invalid_code")

        verified, _ = OTPService.verify_otp(
            phone=phone,
            code=code,
            purpose=OTPPurpose.PASSWORD_RESET,
            user=user,
        )
        if not verified:
            self.fail("invalid_code")

        attrs["user"] = user
        attrs["mode"] = "otp"
        return attrs

    def save(self, **kwargs):
        mode = self.validated_data.get("mode", "token")
        token_obj = self.validated_data.get("token_obj")
        new_password = self.validated_data["new_password"]
        user = self.validated_data["user"]
        user.set_password(new_password)
        user.password_changed_at = timezone.now()
        user.save(update_fields=["password", "password_changed_at"])
        SecurityService.revoke_all_sessions(user=user)
        SecurityService.revoke_all_refresh_tokens(user=user, reason="password_reset")

        if mode == "token" and token_obj:
            PasswordResetToken.objects.filter(user=user, used_at__isnull=True).update(
                used_at=timezone.now()
            )

        return user


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)

    def validate_old_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Old password is incorrect.")
        return value

    def validate_new_password(self, value):
        validate_password(value, user=self.context["request"].user)
        validate_password_security(value)
        return value

    def save(self, **kwargs):
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.password_changed_at = timezone.now()
        user.save(update_fields=["password", "password_changed_at"])
        SecurityService.revoke_all_sessions(user=user)
        SecurityService.revoke_all_refresh_tokens(user=user, reason="password_changed")
        return user


class MembershipOptionSerializer(serializers.ModelSerializer):
    chama_id = serializers.UUIDField(source="chama.id", read_only=True)
    chama_name = serializers.CharField(source="chama.name", read_only=True)

    class Meta:
        model = Membership
        fields = [
            "id",
            "chama_id",
            "chama_name",
            "role",
            "is_active",
            "is_approved",
            "joined_at",
        ]


class ReferralHistoryEntrySerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    chama_name = serializers.CharField()
    status = serializers.CharField()
    setup_completed = serializers.BooleanField()
    created_at = serializers.DateTimeField()
    referral_applied_at = serializers.DateTimeField(allow_null=True)
    referral_code_used = serializers.CharField(allow_blank=True)


class ReferralRewardSerializer(serializers.Serializer):
    referred_chama_id = serializers.UUIDField()
    referred_chama_name = serializers.CharField()
    rewarded_chama_id = serializers.UUIDField(allow_null=True)
    rewarded_chama_name = serializers.CharField(allow_blank=True)
    reward_type = serializers.CharField()
    reward_value = serializers.IntegerField()
    status = serializers.CharField()
    note = serializers.CharField(allow_blank=True)
    created_at = serializers.DateTimeField()
    applied_at = serializers.DateTimeField(allow_null=True)


class ReferralPolicySerializer(serializers.Serializer):
    reward_type = serializers.CharField()
    reward_days = serializers.IntegerField()
    reward_credit_amount = serializers.IntegerField()
    reward_unit = serializers.CharField()
    reward_label = serializers.CharField()
    reward_display_value = serializers.IntegerField()
    description = serializers.CharField()


class ReferralSummarySerializer(serializers.Serializer):
    referral_code = serializers.CharField()
    policy = ReferralPolicySerializer()
    stats = serializers.DictField()
    history = ReferralHistoryEntrySerializer(many=True)
    rewards = ReferralRewardSerializer(many=True)


class ReferralLeaderboardEntrySerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    full_name = serializers.CharField()
    referral_code = serializers.CharField(allow_blank=True)
    total_referrals = serializers.IntegerField()
    completed_referrals = serializers.IntegerField()
    reward_days_earned = serializers.IntegerField()
    reward_total_earned = serializers.IntegerField()


class ReferralLeaderboardSerializer(serializers.Serializer):
    policy = ReferralPolicySerializer()
    leaderboard = ReferralLeaderboardEntrySerializer(many=True)


class SwitchChamaSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()


class UserPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserPreference
        fields = [
            "active_chama",
            "low_data_mode",
            "ussd_enabled",
            "quiet_hours_start",
            "quiet_hours_end",
            "prefer_sms",
            "prefer_email",
            "prefer_in_app",
            "updated_at",
        ]
        read_only_fields = ["updated_at"]


class UserPreferenceUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserPreference
        fields = [
            "active_chama",
            "low_data_mode",
            "ussd_enabled",
            "quiet_hours_start",
            "quiet_hours_end",
            "prefer_sms",
            "prefer_email",
            "prefer_in_app",
        ]


class MemberKYCSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    verification_result = serializers.JSONField(read_only=True)
    chama_id = serializers.SerializerMethodField()
    chama_name = serializers.SerializerMethodField()
    rejection_reason = serializers.CharField(source="last_rejection_reason", read_only=True)
    documents = serializers.SerializerMethodField()
    has_id_front_image = serializers.SerializerMethodField()
    has_id_back_image = serializers.SerializerMethodField()
    has_selfie_image = serializers.SerializerMethodField()
    has_proof_of_address_image = serializers.SerializerMethodField()

    class Meta:
        model = MemberKYC
        fields = [
            "id",
            "user",
            "chama",
            "chama_id",
            "chama_name",
            "document_type",
            "id_number",
            "provider",
            "onboarding_path",
            "legal_name",
            "date_of_birth",
            "gender",
            "nationality",
            "phone_number",
            "mpesa_registered_name",
            "id_expiry_date",
            "has_id_front_image",
            "has_id_back_image",
            "has_selfie_image",
            "has_proof_of_address_image",
            "location_latitude",
            "location_longitude",
            "location_label",
            "status",
            "kyc_tier",
            "verification_score",
            "confidence_score",
            "quality_front_passed",
            "quality_back_passed",
            "liveness_passed",
            "face_match_score",
            "duplicate_id_detected",
            "pep_match",
            "sanctions_match",
            "blacklist_match",
            "iprs_match_status",
            "submission_attempts",
            "resubmission_attempts",
            "rejection_attempts",
            "retry_allowed",
            "last_submitted_at",
            "submitted_at",
            "processed_at",
            "approved_at",
            "rejected_at",
            "expires_at",
            "last_rekyc_at",
            "auto_verification_provider",
            "auto_verification_reference",
            "auto_verified_at",
            "last_rejection_reason",
            "rejection_reason",
            "escalated_to_system_admin_at",
            "requires_reverification",
            "reverification_reason",
            "next_reverification_due_at",
            "last_sanctions_screened_at",
            "last_sanctions_screening_result",
            "account_frozen_for_compliance",
            "review_note",
            "review_reason",
            "provider_payload",
            "provider_result",
            "verification_result",
            "documents",
            "reviewed_by",
            "reviewed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "user",
            "status",
            "kyc_tier",
            "verification_score",
            "duplicate_id_detected",
            "pep_match",
            "sanctions_match",
            "blacklist_match",
            "iprs_match_status",
            "submission_attempts",
            "resubmission_attempts",
            "rejection_attempts",
            "retry_allowed",
            "last_submitted_at",
            "submitted_at",
            "processed_at",
            "approved_at",
            "rejected_at",
            "expires_at",
            "last_rekyc_at",
            "auto_verification_provider",
            "auto_verification_reference",
            "auto_verified_at",
            "last_rejection_reason",
            "escalated_to_system_admin_at",
            "requires_reverification",
            "reverification_reason",
            "next_reverification_due_at",
            "last_sanctions_screened_at",
            "last_sanctions_screening_result",
            "account_frozen_for_compliance",
            "review_note",
            "review_reason",
            "provider_payload",
            "provider_result",
            "verification_result",
            "reviewed_by",
            "reviewed_at",
            "created_at",
            "updated_at",
        ]

    def get_documents(self, obj):
        documents = []
        for label, role, field in (
            ("ID Front", "id_front_image", getattr(obj, "id_front_image", None)),
            ("ID Back", "id_back_image", getattr(obj, "id_back_image", None)),
            ("Selfie", "selfie_image", getattr(obj, "selfie_image", None)),
            ("Proof of Address", "proof_of_address_image", getattr(obj, "proof_of_address_image", None)),
        ):
            if not field:
                continue
            # Never expose raw storage URLs for encrypted KYC uploads.
            url = None
            documents.append(
                {
                    "name": label,
                    "type": role,
                    "url": url,
                    "download_url": f"/api/v1/kyc/documents/{obj.id}/{role}/download/",
                    "created_at": obj.updated_at,
                }
            )
        return documents

    def get_has_id_front_image(self, obj) -> bool:
        return bool(getattr(obj, "id_front_image", None))

    def get_has_id_back_image(self, obj) -> bool:
        return bool(getattr(obj, "id_back_image", None))

    def get_has_selfie_image(self, obj) -> bool:
        return bool(getattr(obj, "selfie_image", None))

    def get_has_proof_of_address_image(self, obj) -> bool:
        return bool(getattr(obj, "proof_of_address_image", None))

    def get_chama_id(self, obj):
        chama = getattr(obj, "chama", None)
        return str(chama.id) if chama else None

    def get_chama_name(self, obj):
        chama = getattr(obj, "chama", None)
        return str(chama.name) if chama else None


class MemberKYCSubmitSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField(required=False, allow_null=True)
    document_type = serializers.ChoiceField(
        choices=MemberKYCDocumentType.choices,
        default=MemberKYCDocumentType.NATIONAL_ID,
    )
    id_number = serializers.CharField(max_length=32)
    mpesa_registered_name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    id_expiry_date = serializers.DateField(required=False, allow_null=True)
    id_front_image = serializers.FileField(required=False, allow_null=True)
    id_back_image = serializers.FileField(required=False, allow_null=True)
    selfie_image = serializers.FileField(required=False, allow_null=True)
    proof_of_address_image = serializers.FileField(required=False, allow_null=True)
    location_latitude = serializers.DecimalField(required=False, max_digits=9, decimal_places=6)
    location_longitude = serializers.DecimalField(required=False, max_digits=9, decimal_places=6)

    def validate_id_number(self, value):
        cleaned = str(value).strip()
        if len(cleaned) < 6:
            raise serializers.ValidationError("ID number must be at least 6 characters.")
        return cleaned

    def validate_mpesa_registered_name(self, value):
        cleaned = str(value or "").strip()
        if not cleaned:
            return ""
        if len(cleaned) < 3:
            raise serializers.ValidationError("M-Pesa registered name must be at least 3 characters.")
        return cleaned

    def validate_id_front_image(self, value):
        """Validate ID front image file."""
        if not value:
            return value
        
        valid, error_msg = KYCService.validate_id_image(value, "ID front image")
        if not valid:
            raise serializers.ValidationError(error_msg)
        return value

    def validate_selfie_image(self, value):
        """Validate selfie image file."""
        if not value:
            return value
        
        valid, error_msg = KYCService.validate_id_image(value, "Selfie image")
        if not valid:
            raise serializers.ValidationError(error_msg)
        return value

    def validate_id_back_image(self, value):
        if not value:
            return value

        valid, error_msg = KYCService.validate_id_image(value, "ID back image")
        if not valid:
            raise serializers.ValidationError(error_msg)
        return value

    def validate_proof_of_address_image(self, value):
        if not value:
            return value

        valid, error_msg = KYCService.validate_id_image(value, "Proof of address image")
        if not valid:
            raise serializers.ValidationError(error_msg)
        return value

    def validate(self, attrs):
        """Validate all KYC images together."""
        id_front = attrs.get("id_front_image")
        id_back = attrs.get("id_back_image")
        selfie = attrs.get("selfie_image")
        document_type = attrs.get("document_type", MemberKYCDocumentType.NATIONAL_ID)
        latitude = attrs.get("location_latitude")
        longitude = attrs.get("location_longitude")
        
        # At least one image should be provided
        if not id_front and not selfie:
            raise serializers.ValidationError(
                "At least one image (ID front or selfie) is required."
            )

        if document_type != MemberKYCDocumentType.PASSPORT and not id_back:
            raise serializers.ValidationError(
                {"id_back_image": "ID back image is required for this document type."}
            )

        if (latitude is None) ^ (longitude is None):
            raise serializers.ValidationError(
                "Both location_latitude and location_longitude are required when sharing location."
            )

        if latitude is not None and not (-90 <= float(latitude) <= 90):
            raise serializers.ValidationError("location_latitude must be between -90 and 90.")
        if longitude is not None and not (-180 <= float(longitude) <= 180):
            raise serializers.ValidationError("location_longitude must be between -180 and 180.")
        id_expiry_date = attrs.get("id_expiry_date")
        if id_expiry_date and id_expiry_date < timezone.localdate():
            raise serializers.ValidationError(
                {"id_expiry_date": "ID expiry date cannot be in the past."}
            )
        
        return attrs



class MemberKYCReviewSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[MemberKYCStatus.APPROVED, MemberKYCStatus.REJECTED]
    )
    review_note = serializers.CharField(required=False, allow_blank=True)


class MemberCardSerializer(serializers.ModelSerializer):
    class Meta:
        model = MemberCard
        fields = [
            "id",
            "user",
            "chama",
            "card_number",
            "qr_token",
            "is_active",
            "issued_at",
            "updated_at",
        ]
        read_only_fields = fields


class PublicOTPRequestSerializer(serializers.Serializer):
    identifier = serializers.CharField(max_length=255, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=16, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    purpose = serializers.ChoiceField(
        choices=[
            OTPPurpose.VERIFY_PHONE,
            OTPPurpose.VERIFY_EMAIL,
            OTPPurpose.PASSWORD_RESET,
            OTPPurpose.REGISTER,
            OTPPurpose.LOGIN_2FA,
            OTPPurpose.WITHDRAWAL_CONFIRM,
        ],
        default=OTPPurpose.VERIFY_PHONE,
    )
    delivery_method = serializers.ChoiceField(
        choices=[OTPDeliveryMethod.SMS, OTPDeliveryMethod.EMAIL],
        default=OTPDeliveryMethod.SMS,
    )

    def validate(self, attrs):
        identifier = (
            str(attrs.get("identifier") or attrs.get("email") or attrs.get("phone") or "")
            .strip()
        )
        if not identifier:
            raise serializers.ValidationError(
                {"identifier": "Phone number or email is required."}
            )

        delivery_method = attrs.get("delivery_method", OTPDeliveryMethod.SMS)
        purpose = attrs.get("purpose", OTPPurpose.VERIFY_PHONE)
        attrs["identifier"] = identifier

        if delivery_method == OTPDeliveryMethod.EMAIL:
            if purpose == OTPPurpose.VERIFY_PHONE:
                raise serializers.ValidationError(
                    {
                        "purpose": (
                            "Phone verification codes can only be sent by SMS. "
                            "Use verify_email for email delivery."
                        )
                    }
                )
            email = str(attrs.get("email") or identifier).strip().lower()
            email = serializers.EmailField().run_validation(email)
            if identifier and "@" not in identifier:
                raise serializers.ValidationError(
                    {"identifier": "Use an email address when delivery method is email."}
                )
            attrs["email"] = email
            attrs["resolved_user"] = User.objects.filter(
                email__iexact=email,
                is_active=True,
            ).first()
            if purpose == OTPPurpose.VERIFY_EMAIL and not attrs["resolved_user"]:
                raise serializers.ValidationError(
                    {"identifier": "No account found for this email address."}
                )
            attrs["phone"] = attrs["resolved_user"].phone if attrs["resolved_user"] else ""
            attrs["identifier"] = email
            return attrs

        if purpose == OTPPurpose.VERIFY_EMAIL:
            raise serializers.ValidationError(
                {
                    "purpose": (
                        "Email verification codes must use email delivery."
                    )
                }
            )

        try:
            attrs["phone"] = normalize_kenyan_phone(attrs.get("phone") or identifier)
        except ValueError as exc:
            raise serializers.ValidationError({"identifier": str(exc)}) from exc

        attrs["identifier"] = attrs["phone"]
        return attrs


class PublicOTPVerifySerializer(serializers.Serializer):
    identifier = serializers.CharField(max_length=255, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=16, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    purpose = serializers.ChoiceField(
        choices=[
            OTPPurpose.VERIFY_PHONE,
            OTPPurpose.VERIFY_EMAIL,
            OTPPurpose.PASSWORD_RESET,
            OTPPurpose.LOGIN_2FA,
            OTPPurpose.REGISTER,
            OTPPurpose.WITHDRAWAL_CONFIRM,
        ],
        default=OTPPurpose.VERIFY_PHONE,
    )
    code = serializers.CharField(max_length=6, min_length=6)

    def validate(self, attrs):
        identifier = (
            str(attrs.get("identifier") or attrs.get("email") or attrs.get("phone") or "")
            .strip()
        )
        if not identifier:
            raise serializers.ValidationError(
                {"identifier": "Phone number or email is required."}
            )

        attrs["identifier"] = identifier
        purpose = attrs.get("purpose", OTPPurpose.VERIFY_PHONE)

        # Strict purpose/identifier pairing for verification flows.
        if purpose == OTPPurpose.VERIFY_PHONE and ("@" in identifier or attrs.get("email")):
            raise serializers.ValidationError(
                {
                    "purpose": (
                        "Email identifiers require verify_email purpose."
                    )
                }
            )
        if purpose == OTPPurpose.VERIFY_EMAIL and ("@" not in identifier and not attrs.get("email")):
            raise serializers.ValidationError(
                {"identifier": "Email address is required for email verification."}
            )

        if purpose == OTPPurpose.VERIFY_EMAIL or "@" in identifier or attrs.get("email"):
            email = str(attrs.get("email") or identifier).strip().lower()
            email = serializers.EmailField().run_validation(email)
            user = User.objects.filter(email__iexact=email, is_active=True).first()
            if not user:
                raise serializers.ValidationError(
                    {"identifier": "No account found for this email address."}
                )
            attrs["email"] = email
            attrs["resolved_user"] = user
            attrs["phone"] = user.phone
            attrs["identifier"] = email
            return attrs

        try:
            attrs["phone"] = normalize_kenyan_phone(attrs.get("phone") or identifier)
        except ValueError as exc:
            raise serializers.ValidationError({"identifier": str(exc)}) from exc

        if purpose == OTPPurpose.VERIFY_EMAIL:
            raise serializers.ValidationError(
                {"purpose": "Email verification requires an email identifier."}
            )

        attrs["identifier"] = attrs["phone"]
        return attrs

    def validate_code(self, value):
        if not str(value).isdigit():
            raise serializers.ValidationError("OTP must contain only digits.")
        return value


class MembershipStatusSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField(required=False, allow_null=True)
    status = serializers.CharField()
    can_access = serializers.BooleanField()
    role = serializers.CharField(required=False, allow_blank=True)
    membership_id = serializers.UUIDField(required=False, allow_null=True)
    membership_request_id = serializers.UUIDField(required=False, allow_null=True)
    review_note = serializers.CharField(required=False, allow_blank=True)
    redirect_to = serializers.CharField()


class OTPRequestSerializer(serializers.Serializer):
    """Serializer for requesting OTP generation."""
    delivery_method = serializers.ChoiceField(
        choices=OTPDeliveryMethod.choices,
        default=OTPDeliveryMethod.SMS,
    )


class OTPVerifySerializer(serializers.Serializer):
    """Serializer for verifying OTP code."""
    code = serializers.CharField(max_length=6, min_length=6)

    def validate_code(self, value):
        if not value.isdigit():
            raise serializers.ValidationError("OTP must contain only digits.")
        return value
