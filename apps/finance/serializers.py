from decimal import Decimal

from rest_framework import serializers

from apps.accounts.serializers import UserSerializer
from apps.chama.models import LoanPolicy as ChamaLoanPolicy
from apps.finance.models import (
    Account,
    Contribution,
    ContributionGoal,
    ContributionGoalStatus,
    ContributionType,
    Expense,
    ExpenseCategory,
    FinancialSnapshot,
    InstallmentSchedule,
    JournalEntry,
    LedgerEntry,
    Loan,
    LoanApplication,
    LoanApplicationApproval,
    LoanApplicationGuarantor,
    LoanApprovalDecision,
    LoanApprovalLog,
    LoanEligibilityCheck,
    LoanGuarantor,
    LoanGuarantorStatus,
    LoanProduct,
    LoanRecoveryAction,
    LoanRecoveryActionType,
    LoanRestructure,
    LoanRestructureRequest,
    LoanRestructureStatus,
    LoanTopUpRequest,
    LoanTopUpStatus,
    ManualAdjustment,
    MethodChoices,
    MonthClosure,
    Penalty,
    Repayment,
    Wallet,
)
from core.models import ActivityLog, AuditLog


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
    refunded_by = UserSerializer(read_only=True)
    contribution_type_name = serializers.CharField(
        source="contribution_type.name",
        read_only=True,
    )
    net_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
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
            "refunded_amount",
            "net_amount",
            "date_paid",
            "method",
            "receipt_code",
            "recorded_by",
            "refunded_by",
            "refunded_at",
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


class LoanApplicationApprovalSerializer(serializers.ModelSerializer):
    actor = UserSerializer(read_only=True)

    class Meta:
        model = LoanApplicationApproval
        fields = [
            "id",
            "loan_application",
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


class LoanApplicationGuarantorSerializer(serializers.ModelSerializer):
    guarantor = UserSerializer(read_only=True)

    class Meta:
        model = LoanApplicationGuarantor
        fields = [
            "id",
            "loan_application",
            "guarantor",
            "guaranteed_amount",
            "status",
            "review_note",
            "accepted_at",
            "rejected_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class LoanApplicationSerializer(serializers.ModelSerializer):
    member = UserSerializer(read_only=True)
    reviewed_by = UserSerializer(read_only=True)
    approved_by = UserSerializer(read_only=True)
    loan_product = LoanProductSerializer(read_only=True)
    approval_logs = LoanApplicationApprovalSerializer(many=True, read_only=True)
    guarantors = LoanApplicationGuarantorSerializer(many=True, read_only=True)
    created_loan = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = LoanApplication
        fields = [
            "id",
            "chama",
            "member",
            "loan_product",
            "requested_amount",
            "requested_term_months",
            "purpose",
            "status",
            "eligibility_status",
            "recommended_max_amount",
            "eligible_amount_at_application",
            "savings_balance_at_application",
            "contribution_count_at_application",
            "repayment_history_score",
            "contribution_consistency_score",
            "installment_estimate",
            "total_repayment_estimate",
            "loan_multiplier_at_application",
            "risk_notes",
            "next_steps",
            "approval_requirements",
            "eligibility_snapshot",
            "rejection_reason",
            "submitted_at",
            "reviewed_at",
            "reviewed_by",
            "approved_at",
            "approved_by",
            "disbursed_at",
            "created_loan",
            "approval_logs",
            "guarantors",
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
            "purpose",
            "principal",
            "outstanding_principal",
            "outstanding_interest",
            "outstanding_penalty",
            "total_due",
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
            "escalation_level",
            "escalation_started_at",
            "last_reminder_sent_at",
            "last_escalation_sent_at",
            "recovery_meeting_scheduled",
            "recovery_meeting_date",
            "recovery_notes",
            "recovery_officer",
            "final_status",
            "final_status_date",
            "final_status_by",
            "write_off_amount",
            "write_off_reason",
            "requested_at",
            "due_date",
            "defaulted_at",
            "repaid_at",
            "rejection_reason",
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
            "review_note",
            "accepted_at",
            "rejected_at",
            "notified_at",
            "exposure_amount",
            "recovery_triggered",
            "recovery_triggered_at",
            "recovery_amount",
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


class LoanRestructureSerializer(serializers.ModelSerializer):
    approved_by = UserSerializer(read_only=True)

    class Meta:
        model = LoanRestructure
        fields = [
            "id",
            "loan",
            "source_request",
            "old_terms_snapshot",
            "new_terms_snapshot",
            "approved_by",
            "approved_at",
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
            "paid_amount",
            "paid_principal",
            "paid_interest",
            "paid_penalty",
            "paid_at",
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
            "allocation_breakdown",
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


class AllTransactionsQuerySerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    category = serializers.ChoiceField(
        choices=["inflow", "outflow", "internal", "system"],
        required=False,
        allow_null=True,
        allow_blank=True,
    )
    entry_type = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    method = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    status = serializers.ChoiceField(
        choices=["pending", "success", "failed", "reversed"],
        required=False,
        allow_null=True,
        allow_blank=True,
    )
    search = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    from_date = serializers.DateField(required=False, allow_null=True)
    to_date = serializers.DateField(required=False, allow_null=True)
    cursor = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=200)

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
    purpose = serializers.CharField(required=False, allow_blank=True)


class LoanGuarantorProposalSerializer(serializers.Serializer):
    guarantor_id = serializers.UUIDField()
    guaranteed_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )


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
    purpose = serializers.CharField(required=False, allow_blank=True)
    guarantors = LoanGuarantorProposalSerializer(many=True, required=False)


class LoanApplicationRequestSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    member_id = serializers.UUIDField(required=False)
    loan_product_id = serializers.UUIDField(required=False)
    requested_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    requested_term_months = serializers.IntegerField(min_value=1)
    purpose = serializers.CharField(required=False, allow_blank=True)
    guarantors = LoanGuarantorProposalSerializer(many=True, required=False)


class LoanApplicationDecisionSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(
        choices=[
            LoanApprovalDecision.APPROVED,
            LoanApprovalDecision.REJECTED,
        ]
    )
    note = serializers.CharField(required=False, allow_blank=True)


class LoanGuarantorCreateSerializer(serializers.Serializer):
    loan_id = serializers.UUIDField()
    guarantor_id = serializers.UUIDField()
    guaranteed_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )


class LoanApplicationGuarantorCreateSerializer(serializers.Serializer):
    loan_application_id = serializers.UUIDField()
    guarantor_id = serializers.UUIDField()
    guaranteed_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )


class LoanGuarantorActionSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(
        choices=[LoanGuarantorStatus.ACCEPTED, LoanGuarantorStatus.REJECTED]
    )
    note = serializers.CharField(required=False, allow_blank=True)


class LoanApplicationGuarantorActionSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(
        choices=[LoanGuarantorStatus.ACCEPTED, LoanGuarantorStatus.REJECTED]
    )
    note = serializers.CharField(required=False, allow_blank=True)


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


class MemberWalletActivityQuerySerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    filter = serializers.ChoiceField(
        choices=[
            "all",
            "deposits",
            "withdrawals",
            "transfers",
            "contributions",
            "loan_repayments",
            "pending",
            "failed",
        ],
        required=False,
        default="all",
    )
    search = serializers.CharField(required=False, allow_blank=True)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=50)
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)

    def validate(self, attrs):
        start_date = attrs.get("start_date")
        end_date = attrs.get("end_date")
        if start_date and end_date and start_date > end_date:
            raise serializers.ValidationError(
                {"end_date": "end_date must be greater than or equal to start_date."}
            )
        return attrs


class MemberWalletDepositCreateSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    payment_method = serializers.ChoiceField(choices=["mpesa", "bank"])
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    idempotency_key = serializers.CharField(required=False, allow_blank=True, max_length=100)

    def validate(self, attrs):
        method = str(attrs.get("payment_method") or "").lower()
        phone = str(attrs.get("phone") or "").strip()
        if method == "mpesa" and not phone:
            raise serializers.ValidationError({"phone": "Phone is required for M-Pesa deposits."})
        return attrs


class MemberWalletDepositDetailSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()


class MemberWalletWithdrawalCreateSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    payment_method = serializers.ChoiceField(choices=["mpesa"])
    phone = serializers.CharField(max_length=20)
    pin = serializers.CharField(max_length=6, required=False, allow_blank=True, default="")
    idempotency_key = serializers.CharField(required=False, allow_blank=True, max_length=100)


class MemberWalletWithdrawalDetailSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()


class MemberWalletTransferCreateSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    recipient_member_id = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    note = serializers.CharField(required=False, allow_blank=True, max_length=300, default="")
    idempotency_key = serializers.CharField(required=False, allow_blank=True, max_length=100)


class MemberWalletContributionCreateSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    contribution_type_id = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    idempotency_key = serializers.CharField(required=False, allow_blank=True, max_length=100)


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


class LoanRecoveryActionSerializer(serializers.ModelSerializer):
    created_by = UserSerializer(read_only=True)

    class Meta:
        model = LoanRecoveryAction
        fields = [
            "id",
            "loan",
            "action_type",
            "amount",
            "notes",
            "metadata",
            "performed_by",
            "guarantor",
            "offset_from_savings",
            "offset_from_contributions",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class LoanRecoveryActionCreateSerializer(serializers.Serializer):
    action_type = serializers.ChoiceField(choices=LoanRecoveryActionType.choices)
    amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.00"),
        required=False,
        default=Decimal("0.00"),
    )
    notes = serializers.CharField(required=False, allow_blank=True)
    metadata = serializers.JSONField(required=False)


class LoanOffsetSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    notes = serializers.CharField(required=False, allow_blank=True)
    idempotency_key = serializers.CharField(max_length=100)


class LoanWriteOffSerializer(serializers.Serializer):
    notes = serializers.CharField(required=False, allow_blank=True)
    idempotency_key = serializers.CharField(max_length=100)


class ChamaLoanPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = ChamaLoanPolicy
        fields = [
            "id",
            "chama",
            "loans_enabled",
            "min_membership_days",
            "min_contribution_cycles",
            "min_savings_threshold",
            "minimum_loan_amount",
            "min_contribution_compliance_percent",
            "require_no_overdue_contributions",
            "block_defaulted_loans",
            "block_unpaid_penalties",
            "block_pending_loan_applications",
            "max_active_loans",
            "loan_cap_multiplier",
            "max_member_loan_amount",
            "reserve_liquidity_amount",
            "require_kyc",
            "require_phone_verification",
            "require_email_verification",
            "minimum_credit_score",
            "require_loan_purpose",
            "interest_model",
            "interest_rate",
            "require_guarantors",
            "min_guarantors",
            "guarantor_requirement_threshold",
            "guarantor_capacity_multiplier",
            "medium_loan_threshold",
            "medium_loan_guarantors_count",
            "require_treasurer_approval",
            "require_admin_approval",
            "require_committee_vote",
            "committee_threshold_amount",
            "approval_rules",
            "penalty_rate",
            "grace_period_days",
            "late_fee_type",
            "late_fee_value",
            "default_after_days_overdue",
            "recovery_review_after_days_overdue",
            "restrict_new_loans_on_overdue",
            "restrict_withdrawals_on_default",
            "restrict_voting_on_default",
            "restrict_invites_on_default",
            "notify_guarantors_on_overdue",
            "min_repayment_period",
            "max_repayment_period",
            "repayment_capacity_ratio_limit",
            "allow_early_repayment",
            "early_repayment_discount_percent",
            "allow_restructure",
            "allow_offset_from_savings",
            "restrict_member_privileges_on_default",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


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


class AccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = Account
        fields = [
            "id",
            "chama",
            "code",
            "name",
            "type",
            "is_active",
            "system_managed",
            "meta",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "system_managed",
            "created_at",
            "updated_at",
        ]


class JournalEntrySerializer(serializers.ModelSerializer):
    lines = LedgerEntrySerializer(many=True, read_only=True)

    class Meta:
        model = JournalEntry
        fields = [
            "id",
            "chama",
            "reference",
            "description",
            "source_type",
            "source_id",
            "posted_at",
            "idempotency_key",
            "is_reversal",
            "reversal_of",
            "metadata",
            "lines",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ExpenseSerializer(serializers.ModelSerializer):
    requested_by = UserSerializer(read_only=True)
    approved_by = UserSerializer(read_only=True)
    rejected_by = UserSerializer(read_only=True)
    paid_by = UserSerializer(read_only=True)
    category_name = serializers.CharField(source="category_ref.name", read_only=True)
    receipt_url = serializers.SerializerMethodField()
    audit_trail = serializers.SerializerMethodField()

    class Meta:
        model = Expense
        fields = [
            "id",
            "chama",
            "requested_by",
            "category_ref",
            "category_name",
            "description",
            "category",
            "amount",
            "expense_date",
            "status",
            "vendor_name",
            "receipt_file",
            "receipt_url",
            "receipt_reference",
            "payment_reference",
            "notes",
            "journal_entry",
            "approved_by",
            "approved_at",
            "rejected_by",
            "rejected_at",
            "rejection_reason",
            "paid_by",
            "paid_at",
            "metadata",
            "audit_trail",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "requested_by",
            "journal_entry",
            "approved_by",
            "approved_at",
            "rejected_by",
            "rejected_at",
            "rejection_reason",
            "paid_by",
            "paid_at",
            "receipt_url",
            "audit_trail",
            "created_at",
            "updated_at",
        ]

    def get_receipt_url(self, obj):
        if not obj.receipt_file:
            return None
        try:
            request = self.context.get("request")
            url = obj.receipt_file.url
            return request.build_absolute_uri(url) if request else url
        except Exception:  # noqa: BLE001
            return None

    def get_audit_trail(self, obj):
        activity_rows = list(
            ActivityLog.objects.filter(entity_type="Expense", entity_id=obj.id).select_related("actor")
        )
        audit_rows = list(
            AuditLog.objects.filter(entity_type="Expense", entity_id=obj.id).select_related("actor")
        )
        events = sorted(
            [
                {
                    "id": str(row.id),
                    "action": row.action,
                    "actor_name": row.actor.get_full_name() if row.actor else None,
                    "note": (row.metadata or {}).get("note") or (row.metadata or {}).get("reason"),
                    "created_at": row.created_at,
                }
                for row in activity_rows + audit_rows
            ],
            key=lambda item: item["created_at"],
            reverse=True,
        )
        return events


class ExpenseCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ExpenseCategory
        fields = [
            "id",
            "chama",
            "name",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ExpenseCreateSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    description = serializers.CharField(max_length=255)
    category_id = serializers.UUIDField(required=False)
    category = serializers.CharField(max_length=80, required=False, allow_blank=True)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    expense_date = serializers.DateField(required=False)
    vendor_name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    receipt_file = serializers.FileField(required=False, allow_null=True)
    receipt_reference = serializers.CharField(max_length=120, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    metadata = serializers.JSONField(required=False)
    idempotency_key = serializers.CharField(max_length=100)


class ExpenseDecisionSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField(required=False)
    note = serializers.CharField(required=False, allow_blank=True, max_length=500)


class ExpenseMarkPaidSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField(required=False)
    payment_reference = serializers.CharField(required=False, allow_blank=True, max_length=120)
    note = serializers.CharField(required=False, allow_blank=True, max_length=500)
    idempotency_key = serializers.CharField(max_length=100, required=False)


class FinancialSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = FinancialSnapshot
        fields = [
            "id",
            "chama",
            "snapshot_date",
            "total_balance",
            "total_contributions",
            "total_loans",
            "total_expenses",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
