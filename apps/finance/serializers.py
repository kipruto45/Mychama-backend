from decimal import Decimal

from rest_framework import serializers

from apps.accounts.serializers import UserSerializer
from apps.finance.models import (
    Contribution,
    ContributionGoal,
    ContributionGoalStatus,
    ContributionType,
    InstallmentSchedule,
    LedgerEntry,
    Loan,
    LoanApprovalDecision,
    LoanApprovalLog,
    LoanEligibilityCheck,
    LoanRestructureRequest,
    LoanRestructureStatus,
    LoanTopUpRequest,
    LoanTopUpStatus,
    LoanGuarantor,
    LoanProduct,
    ManualAdjustment,
    MethodChoices,
    MonthClosure,
    Penalty,
    Repayment,
    Wallet,
)


class ContributionTypeSerializer(serializers.ModelSerializer):
    chama_id = serializers.UUIDField(write_only=True, required=False)

    class Meta:
        model = ContributionType
        fields = [
            "id",
            "chama",
            "chama_id",
            "name",
            "frequency",
            "default_amount",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "chama", "created_at", "updated_at"]

    def validate_default_amount(self, value):
        if value <= Decimal("0"):
            raise serializers.ValidationError(
                "default_amount must be greater than zero."
            )
        return value


class LoanProductSerializer(serializers.ModelSerializer):
    chama_id = serializers.UUIDField(write_only=True, required=False)

    class Meta:
        model = LoanProduct
        fields = [
            "id",
            "chama",
            "chama_id",
            "name",
            "is_active",
            "is_default",
            "max_loan_amount",
            "contribution_multiple",
            "interest_type",
            "interest_rate",
            "min_duration_months",
            "max_duration_months",
            "grace_period_days",
            "late_penalty_type",
            "late_penalty_value",
            "early_repayment_discount_percent",
            "minimum_membership_months",
            "minimum_contribution_months",
            "block_if_unpaid_penalties",
            "block_if_overdue_loans",
            "require_treasurer_review",
            "require_separate_disburser",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "chama", "created_at", "updated_at"]

    def validate(self, attrs):
        min_duration = attrs.get("min_duration_months")
        max_duration = attrs.get("max_duration_months")
        if (
            min_duration is not None
            and max_duration is not None
            and max_duration < min_duration
        ):
            raise serializers.ValidationError(
                {
                    "max_duration_months": "max_duration_months must be >= min_duration_months."
                }
            )
        return attrs


class ContributionSerializer(serializers.ModelSerializer):
    member = UserSerializer(read_only=True)
    recorded_by = UserSerializer(read_only=True)
    contribution_type_name = serializers.CharField(
        source="contribution_type.name",
        read_only=True,
    )

    class Meta:
        model = Contribution
        fields = [
            "id",
            "chama",
            "member",
            "contribution_type",
            "contribution_type_name",
            "amount",
            "date_paid",
            "method",
            "receipt_code",
            "recorded_by",
            "created_at",
            "updated_at",
        ]


class ContributionGoalSerializer(serializers.ModelSerializer):
    chama_id = serializers.UUIDField(write_only=True, required=False)
    member_id = serializers.UUIDField(write_only=True, required=False)

    class Meta:
        model = ContributionGoal
        fields = [
            "id",
            "chama",
            "chama_id",
            "member",
            "member_id",
            "title",
            "target_amount",
            "current_amount",
            "due_date",
            "status",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "chama",
            "member",
            "current_amount",
            "created_at",
            "updated_at",
        ]


class LoanApprovalLogSerializer(serializers.ModelSerializer):
    actor = UserSerializer(read_only=True)

    class Meta:
        model = LoanApprovalLog
        fields = [
            "id",
            "loan",
            "stage",
            "decision",
            "actor",
            "note",
            "acted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class LoanEligibilityCheckSerializer(serializers.ModelSerializer):
    member = UserSerializer(read_only=True)

    class Meta:
        model = LoanEligibilityCheck
        fields = [
            "id",
            "loan",
            "chama",
            "member",
            "requested_amount",
            "recommended_max_amount",
            "duration_months",
            "status",
            "reasons",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class LoanSerializer(serializers.ModelSerializer):
    member = UserSerializer(read_only=True)
    approved_by = UserSerializer(read_only=True)
    disbursed_by = UserSerializer(read_only=True)
    loan_product = LoanProductSerializer(read_only=True)
    approval_logs = LoanApprovalLogSerializer(many=True, read_only=True)
    guarantors = serializers.SerializerMethodField()

    class Meta:
        model = Loan
        fields = [
            "id",
            "chama",
            "member",
            "loan_product",
            "principal",
            "interest_type",
            "interest_rate",
            "duration_months",
            "grace_period_days",
            "late_penalty_type",
            "late_penalty_value",
            "early_repayment_discount_percent",
            "eligibility_status",
            "eligibility_reason",
            "recommended_max_amount",
            "status",
            "requested_at",
            "approved_by",
            "approved_at",
            "disbursement_reference",
            "disbursed_by",
            "disbursed_at",
            "approval_logs",
            "guarantors",
            "created_at",
            "updated_at",
        ]

    def get_guarantors(self, obj):
        rows = obj.guarantors.select_related("guarantor").order_by("created_at")
        return LoanGuarantorSerializer(rows, many=True).data


class LoanGuarantorSerializer(serializers.ModelSerializer):
    guarantor = UserSerializer(read_only=True)

    class Meta:
        model = LoanGuarantor
        fields = [
            "id",
            "loan",
            "guarantor",
            "guaranteed_amount",
            "status",
            "accepted_at",
            "rejected_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class LoanTopUpRequestSerializer(serializers.ModelSerializer):
    reviewed_by = UserSerializer(read_only=True)

    class Meta:
        model = LoanTopUpRequest
        fields = [
            "id",
            "loan",
            "requested_amount",
            "reason",
            "status",
            "reviewed_by",
            "reviewed_at",
            "review_note",
            "created_loan",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class LoanRestructureRequestSerializer(serializers.ModelSerializer):
    reviewed_by = UserSerializer(read_only=True)

    class Meta:
        model = LoanRestructureRequest
        fields = [
            "id",
            "loan",
            "requested_duration_months",
            "requested_interest_rate",
            "reason",
            "status",
            "reviewed_by",
            "reviewed_at",
            "review_note",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class InstallmentScheduleSerializer(serializers.ModelSerializer):
    class Meta:
        model = InstallmentSchedule
        fields = [
            "id",
            "loan",
            "due_date",
            "expected_amount",
            "expected_principal",
            "expected_interest",
            "expected_penalty",
            "status",
            "created_at",
            "updated_at",
        ]


class RepaymentSerializer(serializers.ModelSerializer):
    recorded_by = UserSerializer(read_only=True)

    class Meta:
        model = Repayment
        fields = [
            "id",
            "loan",
            "amount",
            "date_paid",
            "method",
            "receipt_code",
            "recorded_by",
            "created_at",
            "updated_at",
        ]


class PenaltySerializer(serializers.ModelSerializer):
    member = UserSerializer(read_only=True)
    issued_by = UserSerializer(read_only=True)
    resolved_by = UserSerializer(read_only=True)

    class Meta:
        model = Penalty
        fields = [
            "id",
            "chama",
            "member",
            "amount",
            "reason",
            "due_date",
            "status",
            "issued_by",
            "resolved_by",
            "resolved_at",
            "created_at",
            "updated_at",
        ]


class LedgerEntrySerializer(serializers.ModelSerializer):
    wallet_id = serializers.UUIDField(source='wallet.id', read_only=True, allow_null=True)

    class Meta:
        model = LedgerEntry
        fields = [
            "id",
            "wallet",
            "wallet_id",
            "chama",
            "entry_type",
            "direction",
            "amount",
            "currency",
            "status",
            "provider",
            "provider_reference",
            "idempotency_key",
            "reversal_of",
            "narration",
            "created_at",
        ]
        read_only_fields = fields


class WalletSerializer(serializers.ModelSerializer):
    """Serializer for Wallet model with proper UUID support."""
    owner_id_str = serializers.SerializerMethodField()

    class Meta:
        model = Wallet
        fields = [
            "id",
            "owner_type",
            "owner_id",
            "owner_id_str",
            "available_balance",
            "locked_balance",
            "total_balance",
            "currency",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_owner_id_str(self, obj):
        """Return owner_id as string for proper UUID handling."""
        return str(obj.owner_id)


class ManualAdjustmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ManualAdjustment
        fields = [
            "id",
            "chama",
            "amount",
            "direction",
            "reason",
            "idempotency_key",
            "created_by",
            "created_at",
            "updated_at",
        ]


class MonthClosureSerializer(serializers.ModelSerializer):
    class Meta:
        model = MonthClosure
        fields = [
            "id",
            "chama",
            "month",
            "closed_by",
            "notes",
            "created_at",
            "updated_at",
        ]


class ContributionRecordSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    member_id = serializers.UUIDField()
    contribution_type_id = serializers.UUIDField()
    amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    date_paid = serializers.DateField()
    method = serializers.ChoiceField(choices=MethodChoices.choices)
    receipt_code = serializers.CharField(max_length=100)
    idempotency_key = serializers.CharField(max_length=100)


class LoanEligibilitySerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    member_id = serializers.UUIDField(required=False)
    loan_product_id = serializers.UUIDField(required=False)
    principal = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    duration_months = serializers.IntegerField(min_value=1)


class LoanRequestSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    member_id = serializers.UUIDField(required=False)
    loan_product_id = serializers.UUIDField(required=False)
    principal = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    duration_months = serializers.IntegerField(min_value=1)


class LoanGuarantorCreateSerializer(serializers.Serializer):
    loan_id = serializers.UUIDField()
    guarantor_id = serializers.UUIDField()
    guaranteed_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )


class ContributionGoalUpsertSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    member_id = serializers.UUIDField(required=False)
    title = serializers.CharField(max_length=160)
    target_amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    due_date = serializers.DateField(required=False, allow_null=True)
    status = serializers.ChoiceField(
        choices=ContributionGoalStatus.choices,
        required=False,
        default=ContributionGoalStatus.ACTIVE,
    )


class WalletQuerySerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    member_id = serializers.UUIDField(required=False)


class WalletSummarySerializer(serializers.Serializer):
    """Serializer for wallet summary with aggregated financial data"""
    chama_id = serializers.CharField()
    member_id = serializers.CharField()
    currency = serializers.CharField()
    available_balance = serializers.DecimalField(max_digits=12, decimal_places=2)
    pending_balance = serializers.DecimalField(max_digits=12, decimal_places=2)
    inflow_today = serializers.DecimalField(max_digits=12, decimal_places=2)
    inflow_this_month = serializers.DecimalField(max_digits=12, decimal_places=2)
    outflow_today = serializers.DecimalField(max_digits=12, decimal_places=2)
    outflow_this_month = serializers.DecimalField(max_digits=12, decimal_places=2)
    pending_deposits = serializers.IntegerField()
    pending_withdrawals = serializers.IntegerField()
    last_transaction_date = serializers.DateTimeField(allow_null=True)


class CreditScoreQuerySerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    member_id = serializers.UUIDField(required=False)


class LoanTopUpRequestCreateSerializer(serializers.Serializer):
    requested_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    reason = serializers.CharField(required=False, allow_blank=True)


class LoanTopUpReviewSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(
        choices=[LoanTopUpStatus.APPROVED, LoanTopUpStatus.REJECTED]
    )
    note = serializers.CharField(required=False, allow_blank=True)


class LoanRestructureRequestCreateSerializer(serializers.Serializer):
    requested_duration_months = serializers.IntegerField(min_value=1)
    requested_interest_rate = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        min_value=Decimal("0.00"),
        required=False,
    )
    reason = serializers.CharField(required=False, allow_blank=True)


class LoanRestructureReviewSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(
        choices=[LoanRestructureStatus.APPROVED, LoanRestructureStatus.REJECTED]
    )
    note = serializers.CharField(required=False, allow_blank=True)


class LoanReviewSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(
        choices=[
            LoanApprovalDecision.APPROVED,
            LoanApprovalDecision.REJECTED,
        ]
    )
    note = serializers.CharField(required=False, allow_blank=True)


class IdempotencyOnlySerializer(serializers.Serializer):
    idempotency_key = serializers.CharField(max_length=100)


class RepaymentPostSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    date_paid = serializers.DateField()
    method = serializers.ChoiceField(choices=MethodChoices.choices)
    receipt_code = serializers.CharField(max_length=100)
    idempotency_key = serializers.CharField(max_length=100)


class PenaltyIssueSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    member_id = serializers.UUIDField()
    amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    reason = serializers.CharField()
    due_date = serializers.DateField()
    idempotency_key = serializers.CharField(max_length=100)


class LedgerQuerySerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    from_date = serializers.DateField(required=False, source="from")
    to_date = serializers.DateField(required=False, source="to")


class DashboardQuerySerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()


class StatementQuerySerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    member_id = serializers.UUIDField(required=False)
    from_date = serializers.DateField(required=False, source="from")
    to_date = serializers.DateField(required=False, source="to")


class MonthlyAggregateQuerySerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    months = serializers.IntegerField(
        required=False, min_value=1, max_value=60, default=12
    )


class LoanPortfolioQuerySerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    mask_members = serializers.BooleanField(required=False, default=False)


class ManualAdjustmentPostSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    direction = serializers.ChoiceField(
        choices=ManualAdjustment._meta.get_field("direction").choices
    )
    reason = serializers.CharField()
    idempotency_key = serializers.CharField(max_length=100)


class LedgerReverseSerializer(serializers.Serializer):
    idempotency_key = serializers.CharField(max_length=100)
    reason = serializers.CharField(required=False, allow_blank=True)


class MonthCloseSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    month = serializers.DateField(help_text="Any date within the month to be closed")
    notes = serializers.CharField(required=False, allow_blank=True)
