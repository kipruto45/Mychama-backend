"""Serializers for Payout workflow."""

from rest_framework import serializers
from decimal import Decimal

from apps.chama.models import Membership
from apps.chama.serializers import MembershipSerializer
from apps.governance.models import ApprovalRequest
from apps.payments.unified_serializers import PaymentIntentResponseSerializer
from apps.finance.models import Wallet, WalletOwnerType

from .models import (
    Payout,
    PayoutAuditLog,
    PayoutEligibilityCheck,
    PayoutRotation,
)


class PayoutRotationSerializer(serializers.ModelSerializer):
    """Serializer for PayoutRotation model."""

    chama_name = serializers.CharField(source="chama.name", read_only=True)
    current_member_id = serializers.SerializerMethodField()

    class Meta:
        model = PayoutRotation
        fields = [
            "id",
            "chama",
            "chama_name",
            "current_position",
            "rotation_cycle",
            "members_in_rotation",
            "current_member_id",
            "last_completed_payout",
            "last_updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_current_member_id(self, obj):
        """Get current member UUID from rotation queue."""
        member_id = obj.get_next_member()
        return member_id


class PayoutEligibilityCheckSerializer(serializers.ModelSerializer):
    """Serializer for PayoutEligibilityCheck model."""

    member_phone = serializers.CharField(
        source="member.user.phone",
        read_only=True,
    )
    member_name = serializers.CharField(
        source="member.user.get_full_name",
        read_only=True,
    )

    class Meta:
        model = PayoutEligibilityCheck
        fields = [
            "id",
            "result",
            "member_phone",
            "member_name",
            "has_outstanding_penalties",
            "penalty_amount",
            "has_active_disputes",
            "has_overdue_loans",
            "overdue_loan_amount",
            "member_is_active",
            "wallet_has_funds",
            "available_balance",
            "checked_at",
        ]
        read_only_fields = ["id", "checked_at"]


class PayoutAuditLogSerializer(serializers.ModelSerializer):
    """Serializer for PayoutAuditLog model."""

    actor_name = serializers.CharField(
        source="actor.get_full_name",
        read_only=True,
    )

    class Meta:
        model = PayoutAuditLog
        fields = [
            "id",
            "action",
            "actor",
            "actor_name",
            "previous_status",
            "new_status",
            "details",
            "reason",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class PayoutDetailSerializer(serializers.ModelSerializer):
    """Detailed payout information (for Treasurer review)."""

    chama_name = serializers.CharField(source="chama.name", read_only=True)
    member = MembershipSerializer(read_only=True)
    eligibility_check = PayoutEligibilityCheckSerializer(read_only=True)
    payment_intent = PaymentIntentResponseSerializer(read_only=True)

    class Meta:
        model = Payout
        fields = [
            "id",
            "chama",
            "chama_name",
            "member",
            "amount",
            "currency",
            "rotation_position",
            "rotation_cycle",
            "status",
            "trigger_type",
            "eligibility_status",
            "eligibility_issues",
            "eligibility_check",
            "payout_method",
            "payment_intent",
            "is_on_hold",
            "hold_reason",
            "treasurer_reviewed_at",
            "chairperson_approved_at",
            "payment_completed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
            "status",
            "eligibility_status",
            "eligibility_issues",
            "approval_request",
        ]


class PayoutCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating a new payout."""

    class Meta:
        model = Payout
        fields = [
            "chama",
            "member",
            "amount",
            "trigger_type",
        ]

    def validate(self, data):
        """Validate payout creation."""
        chama = data.get("chama")
        member = data.get("member")

        # Verify member belongs to chama
        if member.chama_id != chama.id:
            raise serializers.ValidationError(
                "Member does not belong to this chama."
            )

        # Verify member is active
        if member.status != "active":
            raise serializers.ValidationError(
                "Member must be active to receive payout."
            )

        return data


class PayoutListSerializer(serializers.ModelSerializer):
    """Serializer for listing payouts."""

    member_phone = serializers.CharField(
        source="member.user.phone",
        read_only=True,
    )
    member_name = serializers.CharField(
        source="member.user.get_full_name",
        read_only=True,
    )
    chama_name = serializers.CharField(
        source="chama.name",
        read_only=True,
    )

    class Meta:
        model = Payout
        fields = [
            "id",
            "chama",
            "chama_name",
            "member",
            "member_phone",
            "member_name",
            "amount",
            "status",
            "payout_method",
            "rotation_position",
            "eligibility_status",
            "created_at",
            "payment_completed_at",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
        ]


class PayoutMethodUpdateSerializer(serializers.Serializer):
    """Serializer for updating payout method."""

    payout_method = serializers.ChoiceField(
        choices=[
            ("bank_transfer", "Bank Transfer"),
            ("mpesa", "M-Pesa"),
            ("wallet", "Chama Wallet"),
        ]
    )

    def validate_payout_method(self, value):
        """Validate payout method is supported and wallet is active if selected."""
        # If wallet is selected, validate it's active
        if value == "wallet":
            payout = self.context.get("payout")
            if payout:
                wallet, _ = Wallet.objects.get_or_create(
                    owner_type=WalletOwnerType.CHAMA,
                    owner_id=str(payout.chama_id),
                    defaults={
                        "available_balance": Decimal("0.00"),
                        "locked_balance": Decimal("0.00"),
                        "currency": payout.currency or "KES",
                    },
                )
                
                # Check if wallet has positive balance
                if wallet.available_balance <= Decimal("0.00"):
                    raise serializers.ValidationError(
                        "Wallet has no available balance. Please select another payment method."
                    )
                
                # Check if wallet has pending transactions
                # (indicated by locked_balance > 0, which means transactions are processing)
                if wallet.locked_balance > Decimal("0.00"):
                    raise serializers.ValidationError(
                        "Wallet has pending updates. Please try again in a moment."
                    )

        return value


class PayoutApprovalSerializer(serializers.Serializer):
    """Serializer for chairperson approval/rejection."""

    approved = serializers.BooleanField()
    rejection_reason = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=500,
    )

    def validate(self, data):
        """Validate approval decision."""
        if not data.get("approved") and not data.get("rejection_reason"):
            raise serializers.ValidationError(
                "Rejection reason is required when rejecting payout."
            )
        return data


class PayoutFlagHoldSerializer(serializers.Serializer):
    """Serializer for flagging payout as on-hold."""

    reason = serializers.CharField(max_length=500)


class PayoutReleaseHoldSerializer(serializers.Serializer):
    """Serializer for releasing payout from hold."""

    notes = serializers.CharField(required=False, allow_blank=True, max_length=500)
