from django.contrib import admin

from apps.security.models import AccountLock, AuditLog, DeviceSession, LoginAttempt


@admin.register(LoginAttempt)
class LoginAttemptAdmin(admin.ModelAdmin):
    list_display = (
        "user_identifier",
        "ip_address",
        "success",
        "created_at",
    )
    list_filter = ("success", "created_at")
    search_fields = ("user_identifier", "ip_address", "device_info")
    readonly_fields = ("created_at",)


@admin.register(AccountLock)
class AccountLockAdmin(admin.ModelAdmin):
    list_display = ("user_identifier", "locked_until", "reason", "created_at")
    list_filter = ("locked_until", "created_at")
    search_fields = ("user_identifier", "reason")
    readonly_fields = ("created_at",)


@admin.register(DeviceSession)
class DeviceSessionAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "chama",
        "device_name",
        "ip_address",
        "is_revoked",
        "last_seen",
        "created_at",
    )
    list_filter = ("is_revoked", "created_at", "last_seen")
    search_fields = (
        "user__phone",
        "user__full_name",
        "device_name",
        "ip_address",
        "session_key",
    )
    readonly_fields = ("created_at", "last_seen")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "chama",
        "actor",
        "action_type",
        "target_type",
        "target_id",
    )
    list_filter = ("action_type", "target_type", "created_at")
    search_fields = (
        "action_type",
        "target_type",
        "target_id",
        "actor__phone",
        "actor__full_name",
    )
    readonly_fields = ("created_at",)
