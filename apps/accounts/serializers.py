from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.utils.html import strip_tags
from django.utils import timezone
from rest_framework import serializers

from apps.accounts.models import (
    MemberCard,
    MemberKYC,
    MemberKYCStatus,
    OTPDeliveryMethod,
    OTPPurpose,
    PasswordResetToken,
    User,
    UserPreference,
)
from apps.accounts.services import KYCService
from apps.chama.models import Membership
from core.utils import normalize_kenyan_phone

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

    class Meta:
        model = User
        fields = [
            "id",
            "phone",
            "email",
            "full_name",
            "first_name",
            "last_name",
            "avatar",
            "is_active",
            "last_login_at",
            "last_login_ip",
            "two_factor_enabled",
            "two_factor_method",
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
        ]

    def get_role(self, obj):
        """Get the user's role from their active membership."""
        from apps.chama.models import Membership, MemberStatus
        
        membership = Membership.objects.filter(
            user=obj,
            is_active=True,
            is_approved=True,
            exited_at__isnull=True,
        ).order_by("joined_at").first()
        
        if membership:
            return membership.role
        return None

    def get_first_name(self, obj):
        first_name, _ = _split_full_name(obj.full_name)
        return first_name

    def get_last_name(self, obj):
        _, last_name = _split_full_name(obj.full_name)
        return last_name

    def get_avatar(self, obj):
        if not obj.avatar:
            return None

        avatar_url = obj.avatar.url
        request = self.context.get("request")
        if request is None:
            return avatar_url
        return request.build_absolute_uri(avatar_url)

    def get_referral_count(self, obj):
        return obj.referred_chamas.count()


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
        choices=OTPDeliveryMethod.choices,
        default=OTPDeliveryMethod.SMS,
    )

    def validate_phone(self, value):
        try:
            normalized = normalize_kenyan_phone(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

        if User.objects.filter(phone=normalized).exists():
            raise serializers.ValidationError("A user with this phone already exists.")
        return normalized

    def validate_full_name(self, value):
        cleaned = strip_tags(str(value or "")).strip()
        if not cleaned:
            raise serializers.ValidationError("Full name is required.")
        return cleaned

    def validate_email(self, value):
        cleaned = str(value or "").strip().lower()
        if cleaned and User.objects.filter(email__iexact=cleaned).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return cleaned

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError({"password_confirm": "Passwords do not match."})

        validate_password(attrs["password"])
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
        "invalid_credentials": "Invalid phone or password.",
        "inactive_account": "Account is inactive.",
    }

    def validate(self, attrs):
        phone = attrs.get("phone")
        password = attrs.get("password")

        try:
            normalized_phone = normalize_kenyan_phone(phone)
        except ValueError:
            self.fail("invalid_credentials")

        user = authenticate(
            request=self.context.get("request"),
            phone=normalized_phone,
            password=password,
        )

        if not user:
            self.fail("invalid_credentials")
        if not user.is_active:
            self.fail("inactive_account")

        attrs["phone"] = normalized_phone
        attrs["user"] = user
        return attrs


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class PasswordResetRequestSerializer(serializers.Serializer):
    identifier = serializers.CharField(max_length=255)

    def validate_identifier(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Identifier is required.")
        return value

    def get_user(self):
        identifier = self.validated_data["identifier"]

        if "@" in identifier:
            return User.objects.filter(email__iexact=identifier).first()

        try:
            phone = normalize_kenyan_phone(identifier)
        except ValueError:
            return None

        return User.objects.filter(phone=phone).first()


class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=512)
    new_password = serializers.CharField(write_only=True, min_length=8)

    default_error_messages = {
        "invalid_token": "Reset token is invalid or expired.",
    }

    def validate_new_password(self, value):
        validate_password(value)
        return value

    def validate(self, attrs):
        raw_token = attrs.get("token")
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
        return attrs

    def save(self, **kwargs):
        token_obj = self.validated_data["token_obj"]
        new_password = self.validated_data["new_password"]

        user = token_obj.user
        user.set_password(new_password)
        user.save(update_fields=["password"])

        # Invalidate all outstanding reset tokens for this user.
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
        return value

    def save(self, **kwargs):
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.save(update_fields=["password"])
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

    class Meta:
        model = MemberKYC
        fields = [
            "id",
            "user",
            "chama",
            "id_number",
            "id_front_image",
            "selfie_image",
            "status",
            "review_note",
            "reviewed_by",
            "reviewed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "user",
            "status",
            "review_note",
            "reviewed_by",
            "reviewed_at",
            "created_at",
            "updated_at",
        ]


class MemberKYCSubmitSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    id_number = serializers.CharField(max_length=32)
    id_front_image = serializers.FileField(required=False, allow_null=True)
    selfie_image = serializers.FileField(required=False, allow_null=True)

    def validate_id_number(self, value):
        cleaned = str(value).strip()
        if len(cleaned) < 6:
            raise serializers.ValidationError("ID number must be at least 6 characters.")
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

    def validate(self, attrs):
        """Validate all KYC images together."""
        id_front = attrs.get("id_front_image")
        selfie = attrs.get("selfie_image")
        
        # At least one image should be provided
        if not id_front and not selfie:
            raise serializers.ValidationError(
                "At least one image (ID front or selfie) is required."
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
    phone = serializers.CharField(max_length=16)
    purpose = serializers.ChoiceField(
        choices=[
            OTPPurpose.VERIFY_PHONE,
            OTPPurpose.PASSWORD_RESET,
            OTPPurpose.REGISTER,
            OTPPurpose.LOGIN_2FA,
            OTPPurpose.WITHDRAWAL_CONFIRM,
        ],
        default=OTPPurpose.VERIFY_PHONE,
    )
    delivery_method = serializers.ChoiceField(
        choices=OTPDeliveryMethod.choices,
        default=OTPDeliveryMethod.SMS,
    )

    def validate_phone(self, value):
        try:
            return normalize_kenyan_phone(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class PublicOTPVerifySerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=16)
    purpose = serializers.ChoiceField(
        choices=[
            OTPPurpose.VERIFY_PHONE,
            OTPPurpose.PASSWORD_RESET,
            OTPPurpose.LOGIN_2FA,
            OTPPurpose.REGISTER,
            OTPPurpose.WITHDRAWAL_CONFIRM,
        ],
        default=OTPPurpose.VERIFY_PHONE,
    )
    code = serializers.CharField(max_length=6, min_length=6)

    def validate_phone(self, value):
        try:
            return normalize_kenyan_phone(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

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
