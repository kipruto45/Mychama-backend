"""
Serializers for wallet transfers, chama payments, and loan updates.
"""

from decimal import Decimal
from rest_framework import serializers
from django.db import transaction
from django.utils import timezone

from apps.finance.models import (
    WalletTransfer,
    ChamaPayment,
    LoanUpdateRequest,
    Wallet,
    WalletOwnerType,
    Loan,
    LoanStatus,
)
from apps.chama.models import Chama
from apps.members.models import User


class WalletTransferSerializer(serializers.ModelSerializer):
    """Serializer for member-to-member wallet transfers."""
    
    sender_name = serializers.CharField(source="sender.get_full_name", read_only=True)
    recipient_name = serializers.CharField(source="recipient.get_full_name", read_only=True)
    
    class Meta:
        model = WalletTransfer
        fields = [
            "id",
            "chama",
            "sender",
            "sender_name",
            "recipient",
            "recipient_name",
            "amount",
            "currency",
            "reference",
            "description",
            "status",
            "requested_at",
            "completed_at",
            "failure_reason",
            "metadata",
        ]
        read_only_fields = [
            "id",
            "reference",
            "status",
            "requested_at",
            "completed_at",
            "failure_reason",
            "sender_name",
            "recipient_name",
        ]


class WalletTransferRequestSerializer(serializers.Serializer):
    """Request serializer for creating wallet transfers."""
    
    recipient_id = serializers.CharField(
        help_text="ID of recipient member",
    )
    amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        help_text="Amount to transfer",
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=500,
        help_text="Transfer description/memo",
    )
    reference = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=100,
        help_text="Optional reference code",
    )

    def validate_amount(self, value):
        """Validate amount is positive."""
        if value <= Decimal("0.00"):
            raise serializers.ValidationError("Amount must be positive.")
        return value

    def validate_recipient_id(self, value):
        """Validate recipient exists and is active."""
        try:
            user = User.objects.get(id=value)
            if not user.is_active:
                raise serializers.ValidationError("Recipient account is inactive.")
            return user
        except User.DoesNotExist:
            raise serializers.ValidationError("Recipient not found.")


class ChamaPaymentSerializer(serializers.ModelSerializer):
    """Serializer for member-to-chama wallet payments."""
    
    member_name = serializers.CharField(source="member.get_full_name", read_only=True)
    chama_name = serializers.CharField(source="chama.name", read_only=True)
    contribution_type_name = serializers.CharField(
        source="contribution_type.name",
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = ChamaPayment
        fields = [
            "id",
            "chama",
            "chama_name",
            "member",
            "member_name",
            "amount",
            "currency",
            "reference",
            "contribution_type",
            "contribution_type_name",
            "description",
            "status",
            "requested_at",
            "completed_at",
            "failure_reason",
            "metadata",
        ]
        read_only_fields = [
            "id",
            "reference",
            "status",
            "requested_at",
            "completed_at",
            "failure_reason",
            "member_name",
            "chama_name",
            "contribution_type_name",
        ]


class ChamaPaymentRequestSerializer(serializers.Serializer):
    """Request serializer for creating chama payments."""
    
    amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        help_text="Amount to contribute to chama",
    )
    contribution_type_id = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Optional contribution type ID",
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=500,
        help_text="Payment description/memo",
    )
    reference = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=100,
        help_text="Optional reference code",
    )

    def validate_amount(self, value):
        """Validate amount is positive."""
        if value <= Decimal("0.00"):
            raise serializers.ValidationError("Amount must be positive.")
        return value


class LoanUpdateRequestSerializer(serializers.ModelSerializer):
    """Serializer for loan update requests."""
    
    loan_details = serializers.SerializerMethodField(read_only=True)
    reviewed_by_name = serializers.CharField(
        source="reviewed_by.get_full_name",
        read_only=True,
        allow_null=True,
    )
    applied_by_name = serializers.CharField(
        source="applied_by.get_full_name",
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = LoanUpdateRequest
        fields = [
            "id",
            "loan",
            "loan_details",
            "requested_principal",
            "requested_duration_months",
            "requested_interest_rate",
            "reason",
            "old_principal",
            "old_duration_months",
            "old_interest_rate",
            "status",
            "reviewed_by",
            "reviewed_by_name",
            "reviewed_at",
            "review_notes",
            "applied_by",
            "applied_by_name",
            "applied_at",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "old_principal",
            "old_duration_months",
            "old_interest_rate",
            "status",
            "reviewed_by",
            "reviewed_by_name",
            "reviewed_at",
            "review_notes",
            "applied_by",
            "applied_by_name",
            "applied_at",
            "created_at",
            "loan_details",
        ]

    def get_loan_details(self, obj):
        """Return basic loan details."""
        loan = obj.loan
        return {
            "id": str(loan.id),
            "principal": str(loan.principal),
            "duration_months": loan.duration_months,
            "interest_rate": str(loan.interest_rate),
            "status": loan.status,
            "member": {
                "id": str(loan.member.id),
                "name": loan.member.get_full_name(),
            },
        }


class LoanUpdateRequestCreateSerializer(serializers.Serializer):
    """Request serializer for creating loan update requests."""
    
    loan_id = serializers.CharField(help_text="ID of loan to update")
    new_principal = serializers.DecimalField(
        required=False,
        allow_null=True,
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        help_text="New principal amount (optional)",
    )
    new_duration_months = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=1,
        help_text="New duration in months (optional)",
    )
    new_interest_rate = serializers.DecimalField(
        required=False,
        allow_null=True,
        max_digits=5,
        decimal_places=2,
        min_value=Decimal("0.00"),
        help_text="New interest rate (optional)",
    )
    reason = serializers.CharField(
        max_length=1000,
        help_text="Reason for loan amount update",
    )

    def validate(self, data):
        """Validate at least one field is being updated."""
        if not any([
            data.get("new_principal"),
            data.get("new_duration_months"),
            data.get("new_interest_rate"),
        ]):
            raise serializers.ValidationError(
                "At least one of principal, duration_months, or interest_rate must be provided."
            )
        return data

    def validate_loan_id(self, value):
        """Validate loan exists and is in valid state for updates."""
        try:
            loan = Loan.objects.get(id=value)
            # Only allow updates for requested or approved loans (not yet disbursed)
            if loan.status not in [LoanStatus.REQUESTED, LoanStatus.APPROVED]:
                raise serializers.ValidationError(
                    f"Cannot update loan with status '{loan.status}'. Only REQUESTED or APPROVED loans can be updated."
                )
            return loan
        except Loan.DoesNotExist:
            raise serializers.ValidationError("Loan not found.")


class LoanUpdateApprovalSerializer(serializers.Serializer):
    """Serializer for approving or rejecting loan update requests."""
    
    action = serializers.ChoiceField(
        choices=["approve", "reject"],
        help_text="Action to take on the update request",
    )
    notes = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=1000,
        help_text="Approval/rejection notes",
    )
