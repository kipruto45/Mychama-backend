from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.utils import timezone
from rest_framework import serializers

from apps.accounts.serializers import UserSerializer
from apps.chama.models import (
    Chama,
    ChamaContributionSetting,
    ChamaFinanceSetting,
    ChamaMeetingSetting,
    ChamaPrivacy,
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
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = Chama
        fields = [
            "id",
            "name",
            "description",
            "privacy",
            "chama_type",
            "county",
            "subcounty",
            "currency",
            "status",
            "member_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_member_count(self, obj) -> int:
        return obj.memberships.filter(
            is_active=True,
            is_approved=True,
            status="active",
            exited_at__isnull=True,
        ).count()


class ContributionSetupSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    frequency = serializers.ChoiceField(choices=ChamaContributionSetting._meta.get_field("contribution_frequency").choices)
    due_day = serializers.IntegerField(min_value=1, max_value=31)
    grace_period_days = serializers.IntegerField(min_value=0, max_value=60)
    late_fine_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0"),
        default=Decimal("0"),
    )


class FinanceSettingsSerializer(serializers.Serializer):
    currency = serializers.ChoiceField(choices=ChamaFinanceSetting._meta.get_field("currency").choices, default="KES")
    payment_methods = serializers.ListField(child=serializers.ChoiceField(choices=["mpesa", "cash"]), allow_empty=False)
    loans_enabled = serializers.BooleanField(default=True)
    fines_enabled = serializers.BooleanField(default=True)
    approval_rule = serializers.CharField(max_length=50, default="maker_checker")


class MeetingSettingsSerializer(serializers.Serializer):
    meeting_frequency = serializers.ChoiceField(choices=ChamaMeetingSetting._meta.get_field("meeting_frequency").choices)
    quorum_percentage = serializers.IntegerField(min_value=1, max_value=100)
    voting_enabled = serializers.BooleanField(default=True)


class MembershipRulesSerializer(serializers.Serializer):
    invite_only = serializers.BooleanField(default=True)
    approval_required = serializers.BooleanField(default=True)
    max_members = serializers.IntegerField(min_value=2, max_value=100000, default=100)


class NotificationDefaultsSerializer(serializers.Serializer):
    member_join_alerts = serializers.BooleanField(default=True)
    payment_received_alerts = serializers.BooleanField(default=True)
    meeting_reminders = serializers.BooleanField(default=True)
    loan_updates = serializers.BooleanField(default=True)


class PayoutRulesSerializer(serializers.Serializer):
    rotation_order = serializers.CharField(max_length=64, default="member_join_order")
    trigger_mode = serializers.ChoiceField(choices=["manual", "auto"], default="manual")
    payout_method = serializers.ChoiceField(
        choices=["mpesa", "bank_transfer", "wallet"],
        default="mpesa",
    )


class LoanRulesSerializer(serializers.Serializer):
    loans_enabled = serializers.BooleanField(default=True)
    max_loan_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0"),
        default=Decimal("0"),
    )
    interest_rate = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        min_value=Decimal("0"),
        max_value=Decimal("100"),
        default=Decimal("10"),
    )
    repayment_period_months = serializers.IntegerField(min_value=1, max_value=120, default=12)
    approval_layers = serializers.IntegerField(min_value=1, max_value=3, default=2)


class GovernanceRulesSerializer(serializers.Serializer):
    minimum_members_to_start = serializers.IntegerField(min_value=2, max_value=100000, default=3)
    quorum_percentage = serializers.IntegerField(min_value=1, max_value=100, default=50)
    missed_payment_penalty_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0"),
        default=Decimal("0"),
    )
    constitution_summary = serializers.CharField(required=False, allow_blank=True, max_length=4000)


class ChamaCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True)
    category = serializers.ChoiceField(choices=Chama._meta.get_field("chama_type").choices, source="chama_type")
    location = serializers.DictField(required=False)
    county = serializers.CharField(required=False, allow_blank=True)
    subcounty = serializers.CharField(required=False, allow_blank=True)
    privacy = serializers.ChoiceField(choices=ChamaPrivacy.choices, default=ChamaPrivacy.INVITE_ONLY)
    contribution_setup = ContributionSetupSerializer()
    finance_settings = FinanceSettingsSerializer()
    meeting_settings = MeetingSettingsSerializer()
    membership_rules = MembershipRulesSerializer()
    notification_defaults = NotificationDefaultsSerializer(required=False)
    payout_rules = PayoutRulesSerializer(required=False)
    loan_rules = LoanRulesSerializer(required=False)
    governance_rules = GovernanceRulesSerializer(required=False)

    def validate(self, attrs):
        location = attrs.pop("location", None) or {}
        attrs["county"] = str(attrs.get("county") or location.get("county") or "").strip()
        attrs["subcounty"] = str(attrs.get("subcounty") or location.get("subcounty") or "").strip()
        if not attrs["county"]:
            raise serializers.ValidationError({"county": "County is required."})
        if not attrs["subcounty"]:
            raise serializers.ValidationError({"subcounty": "Subcounty is required."})

        finance_settings = attrs.get("finance_settings", {})
        loan_rules = attrs.get("loan_rules")
        if loan_rules is not None:
            finance_settings["loans_enabled"] = bool(loan_rules.get("loans_enabled", True))
            attrs["finance_settings"] = finance_settings

        governance_rules = attrs.get("governance_rules")
        if governance_rules is not None:
            meeting_settings = attrs.get("meeting_settings", {})
            meeting_settings["quorum_percentage"] = governance_rules["quorum_percentage"]
            attrs["meeting_settings"] = meeting_settings

        return attrs


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
        ref_name = "Membership"
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
        ref_name = "MembershipRequest"
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
            "code",
            "phone",
            "email",
            "invitee_phone",
            "invitee_email",
            "invitee_user",
            "role",
            "role_to_assign",
            "role_display",
            "max_uses",
            "use_count",
            "invited_by",
            "accepted_by",
            "accepted_at",
            "declined_at",
            "status",
            "status_display",
            "expires_at",
            "revoked_at",
            "revoke_reason",
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


class SecureInviteCreateSerializer(serializers.Serializer):
    invitee_phone = serializers.CharField(max_length=16, required=False, allow_blank=True)
    invitee_email = serializers.EmailField(required=False, allow_blank=True)
    invitee_user_id = serializers.UUIDField(required=False)
    role_to_assign = serializers.ChoiceField(choices=INVITE_ASSIGNABLE_ROLES, default=MembershipRole.MEMBER)
    expires_in_days = serializers.IntegerField(min_value=1, max_value=30, default=7)
    max_uses = serializers.IntegerField(min_value=1, max_value=3, default=1)

    def validate(self, attrs):
        if not attrs.get("invitee_phone") and not attrs.get("invitee_email") and not attrs.get("invitee_user_id"):
            raise serializers.ValidationError(
                {"detail": "Provide invitee_phone, invitee_email, or invitee_user_id."}
            )
        return attrs

    def validate_invitee_phone(self, value):
        phone = str(value or "").strip()
        if not phone:
            return ""
        try:
            return normalize_kenyan_phone(phone)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class InviteDecisionSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=300)


class InviteCodeSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=12)


class InviteTokenLookupSerializer(serializers.ModelSerializer):
    token = serializers.SerializerMethodField()
    role_display = serializers.SerializerMethodField()
    assigned_role = serializers.CharField(source="role_to_assign", read_only=True)
    assigned_role_display = serializers.SerializerMethodField()
    chama_name = serializers.CharField(source="chama.name", read_only=True)
    chama_description = serializers.CharField(source="chama.description", read_only=True)
    invited_by_name = serializers.CharField(source="invited_by.full_name", read_only=True)
    recipient_hint = serializers.SerializerMethodField()
    is_targeted = serializers.SerializerMethodField()
    is_valid = serializers.SerializerMethodField()

    class Meta:
        model = Invite
        fields = [
            "id",
            "chama",
            "chama_name",
            "identifier",
            "token",
            "code",
            "invitee_phone",
            "invitee_email",
            "assigned_role",
            "assigned_role_display",
            "role_to_assign",
            "role_display",
            "invited_by_name",
            "chama_description",
            "recipient_hint",
            "is_targeted",
            "status",
            "is_valid",
            "expires_at",
            "created_at",
        ]
        read_only_fields = fields

    def get_token(self, obj):
        return obj.build_presented_token()

    def get_role_display(self, obj):
        return _role_label(obj.role_to_assign or obj.role)

    def get_assigned_role_display(self, obj):
        return _role_label(obj.role_to_assign or obj.role)

    def get_recipient_hint(self, obj):
        return obj.invitee_phone or obj.invitee_email or ""

    def get_is_targeted(self, obj):
        return bool(obj.invitee_phone or obj.invitee_email or obj.invitee_user_id)

    def get_is_valid(self, obj):
        return obj.is_valid()


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
