from __future__ import annotations

from rest_framework import serializers

from apps.security.models import (
    AccountLock,
    AuditLog,
    DeviceSession,
    LoginAttempt,
    MemberPinSecret,
    TrustedDevice,
)


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


class TrustedDeviceSerializer(serializers.ModelSerializer):
    is_active_trusted = serializers.BooleanField(read_only=True)

    class Meta:
        model = TrustedDevice
        fields = [
            "id",
            "fingerprint",
            "device_name",
            "device_type",
            "user_agent",
            "ip_address",
            "is_trusted",
            "trusted_at",
            "expires_at",
            "last_used_at",
            "is_active_trusted",
        ]
        read_only_fields = fields


class TrustedDeviceCreateSerializer(serializers.Serializer):
    fingerprint = serializers.CharField(max_length=64)
    device_name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    device_type = serializers.ChoiceField(
        choices=["mobile", "tablet", "desktop", "unknown"],
        required=False,
        default="unknown",
    )
    user_agent = serializers.CharField(required=False, allow_blank=True)


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


class PinStatusSerializer(serializers.Serializer):
    has_transaction_pin = serializers.BooleanField()
    has_withdrawal_pin = serializers.BooleanField()
    withdrawal_pin_required = serializers.BooleanField()


class PinSetSerializer(serializers.Serializer):
    pin_type = serializers.ChoiceField(choices=MemberPinSecret.PinType.choices)
    pin = serializers.CharField(min_length=4, max_length=6)
    confirm_pin = serializers.CharField(min_length=4, max_length=6)
    current_pin = serializers.CharField(
        required=False,
        allow_blank=True,
        min_length=4,
        max_length=6,
    )

    def validate(self, attrs):
        if attrs["pin"] != attrs["confirm_pin"]:
            raise serializers.ValidationError({"confirm_pin": "PINs do not match."})
        return attrs


class PinVerifySerializer(serializers.Serializer):
    pin_type = serializers.ChoiceField(choices=MemberPinSecret.PinType.choices)
    pin = serializers.CharField(min_length=4, max_length=6)
    action = serializers.CharField(required=False, allow_blank=True)
    risk_score = serializers.IntegerField(required=False, min_value=0, max_value=100)


class RBACPermissionSerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    scope = serializers.CharField()


class RBACRoleSerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    scope = serializers.CharField()
    membership_role_key = serializers.CharField(allow_blank=True)
    permissions = RBACPermissionSerializer(many=True)


class UserAccessMembershipSerializer(serializers.Serializer):
    membership_id = serializers.CharField(allow_null=True)
    chama_id = serializers.CharField()
    chama_name = serializers.CharField(required=False)
    role = serializers.CharField(allow_null=True)
    permissions = serializers.ListField(child=serializers.CharField())


class UserAccessSerializer(serializers.Serializer):
    chama_id = serializers.CharField(required=False, allow_null=True)
    membership_id = serializers.CharField(required=False, allow_null=True)
    role = serializers.CharField(required=False, allow_null=True)
    permissions = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )
    is_member = serializers.BooleanField(required=False)
    memberships = UserAccessMembershipSerializer(many=True, required=False)
