from django.contrib import admin

from apps.security.models import (
    AccountLock,
    AuditChainCheckpoint,
    AuditLog,
    DeviceSession,
    LoginAttempt,
    MemberPinSecret,
    Permission,
    RefreshTokenRecord,
    Role,
    RolePermission,
    SecurityEvent,
    TrustedDevice,
)


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
        "chain_index",
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
    readonly_fields = (
        "created_at",
        "chain_index",
        "prev_hash",
        "event_hash",
        "trace_id",
    )

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditChainCheckpoint)
class AuditChainCheckpointAdmin(admin.ModelAdmin):
    list_display = (
        "checkpoint_date",
        "last_chain_index",
        "record_count",
        "created_at",
    )
    readonly_fields = (
        "checkpoint_date",
        "last_chain_index",
        "last_event_hash",
        "record_count",
        "signature",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "scope", "membership_role_key", "is_system")
    list_filter = ("scope", "is_system")
    search_fields = ("code", "name", "membership_role_key")


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "scope", "is_system", "is_sensitive")
    list_filter = ("scope", "is_system", "is_sensitive")
    search_fields = ("code", "name")


@admin.register(RolePermission)
class RolePermissionAdmin(admin.ModelAdmin):
    list_display = ("role", "permission", "created_at")
    list_filter = ("role", "permission")
    search_fields = ("role__code", "permission__code")
    readonly_fields = ("created_at", "updated_at")


@admin.register(SecurityEvent)
class SecurityEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "event_type", "ip_address")
    list_filter = ("event_type", "created_at")
    search_fields = ("user__phone", "user__full_name", "description", "ip_address")
    readonly_fields = ("created_at",)


@admin.register(TrustedDevice)
class TrustedDeviceAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "device_name",
        "device_type",
        "is_trusted",
        "trusted_at",
        "expires_at",
        "last_used_at",
    )
    list_filter = ("device_type", "is_trusted")
    search_fields = ("user__phone", "user__full_name", "fingerprint", "device_name")
    readonly_fields = ("created_at", "last_used_at")


@admin.register(RefreshTokenRecord)
class RefreshTokenRecordAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "family_id",
        "device_name",
        "issued_at",
        "expires_at",
        "revoked_at",
        "used_at",
    )
    list_filter = ("issued_at", "expires_at", "revoked_at", "used_at")
    search_fields = ("user__phone", "user__full_name", "jti", "family_id", "device_id")
    readonly_fields = (
        "issued_at",
        "expires_at",
        "used_at",
        "revoked_at",
        "reuse_detected_at",
    )


@admin.register(MemberPinSecret)
class MemberPinSecretAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "pin_type",
        "failed_attempts",
        "lockout_level",
        "is_locked",
        "locked_until",
        "rotated_at",
    )
    list_filter = ("pin_type", "is_locked", "lockout_level")
    search_fields = ("user__phone", "user__full_name")
    readonly_fields = ("pin_hash", "salt", "created_at", "updated_at", "rotated_at")
