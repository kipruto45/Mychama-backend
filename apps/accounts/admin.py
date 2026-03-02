from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from apps.accounts.forms import CustomUserChangeForm, CustomUserCreationForm
from apps.accounts.models import (
    LoginEvent,
    MemberCard,
    MemberKYC,
    OTPDeliveryLog,
    OTPToken,
    PasswordHistory,
    PasswordResetToken,
    User,
    UserPreference,
)


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    add_form = CustomUserCreationForm
    form = CustomUserChangeForm
    model = User

    list_display = (
        "phone",
        "full_name",
        "email",
        "phone_verified",
        "is_active",
        "is_staff",
        "is_superuser",
        "last_login_at",
    )
    list_filter = (
        "is_active",
        "is_staff",
        "is_superuser",
        "two_factor_enabled",
        "phone_verified",
    )
    search_fields = ("phone", "full_name", "email")
    ordering = ("-date_joined",)

    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        (
            "Personal info",
            {"fields": ("full_name", "email", "last_login_at", "last_login_ip")},
        ),
        (
            "Verification",
            {"fields": ("phone_verified", "phone_verified_at")},
        ),
        (
            "2FA",
            {"fields": ("two_factor_enabled", "two_factor_method", "two_factor_secret")},
        ),
        (
            "Security",
            {"fields": ("password_changed_at", "failed_login_attempts", "locked_until")},
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Important dates", {"fields": ("date_joined",)}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "phone",
                    "full_name",
                    "email",
                    "password1",
                    "password2",
                    "is_active",
                    "is_staff",
                ),
            },
        ),
    )

    readonly_fields = (
        "date_joined",
        "last_login_at",
        "last_login_ip",
        "phone_verified_at",
        "password_changed_at",
    )


@admin.register(LoginEvent)
class LoginEventAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "identifier_attempted",
        "user",
        "ip_address",
        "success",
        "device_id",
    )
    list_filter = ("success", "created_at")
    search_fields = ("identifier_attempted", "ip_address", "user__phone", "user__email")
    readonly_fields = (
        "created_at",
        "identifier_attempted",
        "user",
        "ip_address",
        "user_agent",
        "success",
        "device_id",
        "session_key",
        "metadata",
    )


@admin.register(PasswordResetToken)
class PasswordResetTokenAdmin(admin.ModelAdmin):
    list_display = ("user", "created_at", "expires_at", "used_at")
    list_filter = ("created_at", "expires_at", "used_at")
    search_fields = ("user__phone", "user__email", "token_hash")
    readonly_fields = ("created_at", "token_hash", "expires_at", "used_at")


@admin.register(UserPreference)
class UserPreferenceAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "active_chama",
        "low_data_mode",
        "ussd_enabled",
        "prefer_sms",
        "prefer_email",
        "prefer_in_app",
        "updated_at",
    )
    search_fields = ("user__phone", "user__email", "user__full_name")
    list_filter = ("low_data_mode", "ussd_enabled", "prefer_sms", "prefer_email")


@admin.register(MemberKYC)
class MemberKYCAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "chama",
        "id_number",
        "status",
        "reviewed_by",
        "reviewed_at",
        "created_at",
    )
    search_fields = ("user__phone", "user__full_name", "id_number", "chama__name")
    list_filter = ("status", "created_at", "reviewed_at")


@admin.register(MemberCard)
class MemberCardAdmin(admin.ModelAdmin):
    list_display = (
        "card_number",
        "user",
        "chama",
        "is_active",
        "issued_at",
    )
    search_fields = ("card_number", "user__phone", "user__full_name", "chama__name")
    list_filter = ("is_active", "issued_at")


@admin.register(OTPToken)
class OTPTokenAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "purpose",
        "delivery_method",
        "sent_count",
        "is_used",
        "created_at",
        "expires_at",
        "attempts",
    )
    list_filter = ("purpose", "delivery_method", "is_used", "created_at")
    search_fields = ("user__phone", "user__email", "user__full_name")
    readonly_fields = ("code", "created_at", "expires_at", "attempts", "last_attempt_at")


@admin.register(OTPDeliveryLog)
class OTPDeliveryLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "otp_token",
        "user",
        "channel",
        "provider_name",
        "status",
        "attempt_number",
    )
    list_filter = ("channel", "status", "provider_name", "created_at")
    search_fields = (
        "otp_token__phone",
        "user__phone",
        "user__email",
        "destination",
        "provider_message_id",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
        "otp_token",
        "user",
        "channel",
        "provider_name",
        "provider_message_id",
        "status",
        "destination",
        "attempt_number",
        "error_message",
        "provider_response",
    )


@admin.register(PasswordHistory)
class PasswordHistoryAdmin(admin.ModelAdmin):
    list_display = ("user", "created_at")
    list_filter = ("created_at",)
    search_fields = ("user__phone", "user__email", "user__full_name")
    readonly_fields = ("user", "password_hash", "created_at")
