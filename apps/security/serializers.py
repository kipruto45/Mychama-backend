from __future__ import annotations

from rest_framework import serializers

from apps.security.models import AccountLock, AuditLog, DeviceSession, LoginAttempt


class LoginAttemptSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoginAttempt
        fields = [
            "id",
            "user",
            "user_identifier",
            "ip_address",
            "device_info",
            "success",
            "created_at",
        ]
        read_only_fields = fields


class AccountLockSerializer(serializers.ModelSerializer):
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = AccountLock
        fields = [
            "id",
            "user",
            "user_identifier",
            "locked_until",
            "reason",
            "created_at",
            "is_active",
        ]
        read_only_fields = fields


class DeviceSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceSession
        fields = [
            "id",
            "user",
            "chama",
            "device_name",
            "ip_address",
            "user_agent",
            "session_key",
            "last_seen",
            "is_revoked",
            "created_at",
        ]
        read_only_fields = fields


class SecurityAuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = [
            "id",
            "chama",
            "actor",
            "action_type",
            "target_type",
            "target_id",
            "metadata",
            "ip_address",
            "created_at",
        ]
        read_only_fields = fields


class RevokeSessionSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True)


class AuditFilterSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField(required=False)
    action_type = serializers.CharField(required=False)
