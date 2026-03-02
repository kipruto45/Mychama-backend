from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework import serializers

from apps.accounts.serializers import UserSerializer
from apps.chama.models import (
    Chama,
    ChamaStatus,
    Invite,
    InviteLink,
    Membership,
    MembershipRequest,
    MembershipRequestStatus,
    MembershipRole,
    RoleDelegation,
)
from core.utils import normalize_kenyan_phone

INVITE_ASSIGNABLE_ROLES = [
    MembershipRole.MEMBER,
    MembershipRole.TREASURER,
    MembershipRole.SECRETARY,
    MembershipRole.AUDITOR,
]
ROLE_LABELS = dict(MembershipRole.choices)


def _role_label(role: str | None) -> str:
    return ROLE_LABELS.get(role or MembershipRole.MEMBER, ROLE_LABELS[MembershipRole.MEMBER])


class ChamaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Chama
        fields = [
            "id",
            "name",
            "description",
            "county",
            "subcounty",
            "currency",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ChamaCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Chama
        fields = ["id", "name", "description", "county", "subcounty", "currency", "status"]
        read_only_fields = ["id", "status"]


class ChamaUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Chama
        fields = ["name", "description", "county", "subcounty", "currency", "status"]

    def validate_status(self, value):
        if value not in {ChamaStatus.ACTIVE, ChamaStatus.SUSPENDED}:
            raise serializers.ValidationError("Invalid chama status.")
        return value


class MembershipSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    approved_by = UserSerializer(read_only=True)

    class Meta:
        model = Membership
        fields = [
            "id",
            "user",
            "chama",
            "role",
            "status",
            "is_active",
            "is_approved",
            "joined_at",
            "approved_at",
            "approved_by",
            "exited_at",
            "suspension_reason",
            "exit_reason",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "joined_at",
            "approved_at",
            "approved_by",
            "created_at",
            "updated_at",
        ]


class MembershipRoleUpdateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=MembershipRole.choices)


class RequestJoinSerializer(serializers.Serializer):
    request_note = serializers.CharField(required=False, allow_blank=True, max_length=1000)
    invite_token = serializers.CharField(required=False, allow_blank=True, max_length=128)
    join_code = serializers.CharField(required=False, allow_blank=True, max_length=24)

    def validate(self, attrs):
        chama = self.context["chama"]
        user = self.context["user"]

        invite_token = str(attrs.get("invite_token", "")).strip()
        join_code = str(attrs.get("join_code", "")).strip()
        attrs["invite_token"] = invite_token
        attrs["join_code"] = join_code

        invite_link = None
        if invite_token:
            invite_link = InviteLink.resolve_presented_token(
                invite_token,
                queryset=InviteLink.objects.select_related("chama").filter(chama=chama),
            )
            if not invite_link:
                raise serializers.ValidationError({"invite_token": "Invalid invite link."})
            if not invite_link.is_valid():
                raise serializers.ValidationError({"invite_token": "Invite link expired or inactive."})
            if invite_link.restricted_phone:
                try:
                    restricted_phone = normalize_kenyan_phone(invite_link.restricted_phone)
                except ValueError:
                    restricted_phone = invite_link.restricted_phone
                if restricted_phone != user.phone:
                    raise serializers.ValidationError(
                        {"invite_token": "Invite link is restricted to another phone number."}
                    )

        if join_code:
            if (
                not getattr(chama, "join_enabled", True)
                or not chama.join_code
                or join_code.upper() != chama.join_code.upper()
            ):
                raise serializers.ValidationError({"join_code": "Invalid join code."})
            if chama.join_code_expires_at and chama.join_code_expires_at <= timezone.now():
                raise serializers.ValidationError({"join_code": "Join code has expired."})

        if not chama.allow_public_join and not invite_link and not join_code:
            raise serializers.ValidationError(
                {
                    "detail": (
                        "This chama requires a valid invite or join code before "
                        "submitting a join request."
                    )
                }
            )

        attrs["invite_link"] = invite_link
        return attrs


class MembershipRequestSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    reviewed_by = UserSerializer(read_only=True)
    chama_id = serializers.UUIDField(read_only=True)
    chama_name = serializers.CharField(source="chama.name", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = MembershipRequest
        fields = [
            "id",
            "user",
            "chama",
            "chama_id",
            "chama_name",
            "status",
            "status_display",
            "requested_via",
            "invite_link",
            "request_note",
            "ip_address",
            "device_info",
            "ai_decision",
            "ai_confidence",
            "ai_risk_score",
            "reviewed_by",
            "reviewed_at",
            "review_note",
            "expires_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class MembershipRequestDecisionSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, max_length=1000)


class InviteLinkSerializer(serializers.ModelSerializer):
    token = serializers.SerializerMethodField()
    created_by = UserSerializer(read_only=True)
    chama_id = serializers.UUIDField(read_only=True)
    chama_name = serializers.CharField(source="chama.name", read_only=True)
    chama_description = serializers.CharField(source="chama.description", read_only=True)
    code = serializers.SerializerMethodField()
    role = serializers.CharField(read_only=True)
    role_display = serializers.SerializerMethodField()
    preassigned_role_display = serializers.SerializerMethodField()
    use_count = serializers.IntegerField(source="current_uses", read_only=True)
    uses_count = serializers.IntegerField(source="current_uses", read_only=True)
    invite_url = serializers.SerializerMethodField()

    class Meta:
        model = InviteLink
        fields = [
            "id",
            "chama",
            "chama_id",
            "chama_name",
            "chama_description",
            "token",
            "code",
            "created_by",
            "role",
            "role_display",
            "preassigned_role_display",
            "approval_required",
            "max_uses",
            "current_uses",
            "use_count",
            "uses_count",
            "expires_at",
            "is_active",
            "revoked_at",
            "revoke_reason",
            "restricted_phone",
            "preassigned_role",
            "invite_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "token",
            "code",
            "created_by",
            "role",
            "role_display",
            "preassigned_role_display",
            "current_uses",
            "use_count",
            "uses_count",
            "revoked_at",
            "revoke_reason",
            "invite_url",
            "created_at",
            "updated_at",
        ]

    def get_role_display(self, obj):
        return _role_label(obj.role)

    def get_token(self, obj):
        return obj.build_presented_token()

    def get_code(self, obj):
        return obj.build_presented_token()

    def get_preassigned_role_display(self, obj):
        if not obj.preassigned_role:
            return _role_label(MembershipRole.MEMBER)
        return _role_label(obj.preassigned_role)

    def get_invite_url(self, obj):
        site_url = getattr(settings, "SITE_URL", "").rstrip("/")
        if site_url:
            return f"{site_url}/invite/{obj.build_presented_token()}"
        return f"/invite/{obj.build_presented_token()}"


class InviteLinkCreateSerializer(serializers.Serializer):
    expires_in_days = serializers.IntegerField(min_value=1, max_value=90, default=7)
    expires_at = serializers.DateTimeField(required=False)
    max_uses = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    restricted_phone = serializers.CharField(max_length=16, required=False, allow_blank=True)
    role = serializers.ChoiceField(
        choices=INVITE_ASSIGNABLE_ROLES,
        required=False,
        allow_blank=True,
        write_only=True,
    )
    preassigned_role = serializers.ChoiceField(
        choices=INVITE_ASSIGNABLE_ROLES,
        required=False,
        allow_blank=True,
    )
    approval_required = serializers.BooleanField(required=False, default=True)

    def validate(self, attrs):
        provided_role = attrs.pop("role", "")
        if not attrs.get("preassigned_role") and provided_role:
            attrs["preassigned_role"] = provided_role

        expires_at = attrs.get("expires_at")
        if expires_at and expires_at <= timezone.now():
            raise serializers.ValidationError(
                {"expires_at": "Invite expiry must be in the future."}
            )
        return attrs

    def validate_restricted_phone(self, value):
        phone = str(value or "").strip()
        if not phone:
            return ""
        try:
            return normalize_kenyan_phone(phone)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class MembershipRequestFilterSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=MembershipRequestStatus.choices,
        required=False,
    )


class InviteSerializer(serializers.ModelSerializer):
    token = serializers.SerializerMethodField()
    invited_by = UserSerializer(read_only=True)
    accepted_by = UserSerializer(read_only=True)
    chama_id = serializers.UUIDField(read_only=True)
    chama_name = serializers.CharField(source="chama.name", read_only=True)
    role_display = serializers.SerializerMethodField()
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    use_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Invite
        fields = [
            "id",
            "chama",
            "chama_id",
            "chama_name",
            "identifier",
            "token",
            "phone",
            "email",
            "role",
            "role_display",
            "max_uses",
            "use_count",
            "invited_by",
            "accepted_by",
            "accepted_at",
            "status",
            "status_display",
            "expires_at",
            "created_at",
        ]
        read_only_fields = ["id", "token", "created_at", "invited_by"]

    def get_role_display(self, obj):
        return _role_label(obj.role)

    def get_token(self, obj):
        return obj.build_presented_token()

    def create(self, validated_data):
        if "token" not in validated_data:
            validated_data["token"] = Invite.generate_token()
        if "expires_at" not in validated_data:
            validated_data["expires_at"] = timezone.now() + timedelta(days=7)
        return super().create(validated_data)


class RoleDelegationSerializer(serializers.ModelSerializer):
    delegator = UserSerializer(read_only=True)
    delegatee = UserSerializer(read_only=True)

    class Meta:
        model = RoleDelegation
        fields = [
            "id",
            "chama",
            "delegator",
            "delegatee",
            "role",
            "starts_at",
            "ends_at",
            "is_active",
            "revoked_at",
            "revoked_by",
            "revoke_reason",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "delegator",
            "delegatee",
            "is_active",
            "revoked_at",
            "revoked_by",
            "revoke_reason",
            "created_at",
            "updated_at",
        ]


class RoleDelegationCreateSerializer(serializers.Serializer):
    delegatee_id = serializers.UUIDField()
    role = serializers.ChoiceField(
        choices=[
            MembershipRole.TREASURER,
            MembershipRole.SECRETARY,
        ]
    )
    starts_at = serializers.DateTimeField(required=False)
    ends_at = serializers.DateTimeField()
    note = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        starts_at = attrs.get("starts_at") or timezone.now()
        if attrs["ends_at"] <= starts_at:
            raise serializers.ValidationError(
                {"ends_at": "ends_at must be after starts_at."}
            )
        attrs["starts_at"] = starts_at
        return attrs
