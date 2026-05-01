# Investments Module Serializers

from django.db import models
from rest_framework import serializers

from .models import (
    Investment,
    InvestmentApprovalRequest,
    InvestmentDistribution,
    InvestmentDistributionDetail,
    InvestmentFundingSource,
    InvestmentPayout,
    InvestmentPayoutDestination,
    InvestmentProduct,
    InvestmentRedemptionRequest,
    InvestmentRedemptionType,
    InvestmentReturn,
    InvestmentReturnLedger,
    InvestmentTransaction,
    InvestmentTransactionRecord,
    InvestmentValuation,
    InvestmentUtilizationAction,
    MemberInvestmentPosition,
    MemberInvestment,
    ReturnType,
)


class InvestmentTransactionSerializer(serializers.ModelSerializer):
    """Serializer for InvestmentTransaction"""
    transaction_type_display = serializers.CharField(source='get_transaction_type_display', read_only=True)
    recorded_by_name = serializers.SerializerMethodField()
    
    class Meta:
        model = InvestmentTransaction
        fields = [
            'id', 'investment', 'transaction_type', 'transaction_type_display',
            'amount', 'transaction_date', 'reference', 'notes',
            'recorded_by', 'recorded_by_name', 'created_at'
        ]
        read_only_fields = ['created_at']
    
    def get_recorded_by_name(self, obj) -> str | None:
        if obj.recorded_by:
            return obj.recorded_by.get_full_name()
        return None


class InvestmentReturnSerializer(serializers.ModelSerializer):
    """Serializer for InvestmentReturn"""
    return_type_display = serializers.CharField(source='get_return_type_display', read_only=True)
    recorded_by_name = serializers.SerializerMethodField()
    
    class Meta:
        model = InvestmentReturn
        fields = [
            'id', 'investment', 'return_type', 'return_type_display',
            'amount', 'date', 'notes', 'reference',
            'reinvested', 'distributed_to_members', 'distributed_amount',
            'recorded_by', 'recorded_by_name', 'created_at'
        ]
        read_only_fields = ['created_at']
    
    def get_recorded_by_name(self, obj) -> str | None:
        if obj.recorded_by:
            return obj.recorded_by.get_full_name()
        return None


class InvestmentValuationSerializer(serializers.ModelSerializer):
    """Serializer for InvestmentValuation"""
    recorded_by_name = serializers.SerializerMethodField()
    
    class Meta:
        model = InvestmentValuation
        fields = ['id', 'investment', 'value', 'date', 'notes', 'recorded_by', 'recorded_by_name', 'created_at']
        read_only_fields = ['created_at']
    
    def get_recorded_by_name(self, obj) -> str | None:
        if obj.recorded_by:
            return obj.recorded_by.get_full_name()
        return None


class InvestmentApprovalRequestSerializer(serializers.ModelSerializer):
    """Serializer for InvestmentApprovalRequest"""
    action_type_display = serializers.CharField(source='get_action_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    requested_by_name = serializers.SerializerMethodField()
    approved_by_name = serializers.SerializerMethodField()
    
    class Meta:
        model = InvestmentApprovalRequest
        fields = [
            'id', 'investment', 'action_type', 'action_type_display',
            'amount', 'status', 'status_display', 'requested_by', 'requested_by_name',
            'approved_by', 'approved_by_name', 'approved_at',
            'rejection_reason', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']
    
    def get_requested_by_name(self, obj) -> str | None:
        if obj.requested_by:
            return obj.requested_by.get_full_name()
        return None
    
    def get_approved_by_name(self, obj) -> str | None:
        if obj.approved_by:
            return obj.approved_by.get_full_name()
        return None


class MemberInvestmentSerializer(serializers.ModelSerializer):
    """Serializer for MemberInvestment"""
    member_name = serializers.SerializerMethodField()
    
    class Meta:
        model = MemberInvestment
        fields = [
            'id', 'investment', 'member', 'member_name',
            'contribution_amount', 'share_percentage',
            'returns_received', 'joined_at'
        ]
        read_only_fields = ['joined_at']
    
    def get_member_name(self, obj) -> str:
        return obj.member.get_full_name()


class InvestmentDistributionDetailSerializer(serializers.ModelSerializer):
    """Serializer for InvestmentDistributionDetail"""
    member_name = serializers.SerializerMethodField()
    
    class Meta:
        model = InvestmentDistributionDetail
        fields = ['id', 'distribution', 'member', 'member_name', 'amount', 'share_percentage', 'paid', 'paid_at']
    
    def get_member_name(self, obj) -> str:
        return obj.member.get_full_name()


class InvestmentDistributionSerializer(serializers.ModelSerializer):
    """Serializer for InvestmentDistribution"""
    details = InvestmentDistributionDetailSerializer(many=True, read_only=True)
    created_by_name = serializers.SerializerMethodField()
    
    class Meta:
        model = InvestmentDistribution
        fields = [
            'id', 'investment', 'total_amount', 'distribution_date',
            'method', 'status', 'notes', 'created_by', 'created_by_name',
            'details', 'created_at'
        ]
        read_only_fields = ['created_at']
    
    def get_created_by_name(self, obj) -> str | None:
        if obj.created_by:
            return obj.created_by.get_full_name()
        return None


class InvestmentSerializer(serializers.ModelSerializer):
    """Serializer for Investment model"""
    investment_type_display = serializers.CharField(source='get_investment_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    created_by_name = serializers.SerializerMethodField()
    roi = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    
    transactions = InvestmentTransactionSerializer(many=True, read_only=True)
    returns = InvestmentReturnSerializer(many=True, read_only=True)
    valuations = InvestmentValuationSerializer(many=True, read_only=True)
    approval_request = InvestmentApprovalRequestSerializer(read_only=True)
    member_investments = MemberInvestmentSerializer(many=True, read_only=True)
    distributions = InvestmentDistributionSerializer(many=True, read_only=True)
    
    total_returns = serializers.SerializerMethodField()
    total_withdrawals = serializers.SerializerMethodField()
    
    class Meta:
        model = Investment
        fields = [
            'id', 'chama', 'name', 'investment_type', 'investment_type_display',
            'institution', 'principal_amount', 'current_value', 'currency',
            'start_date', 'maturity_date', 'status', 'status_display',
            'account_number', 'reference_number', 'documents', 'notes',
            'reinvest_returns', 'distribution_rule', 'roi',
            'transactions', 'returns', 'valuations', 'approval_request',
            'member_investments', 'distributions', 'total_returns', 'total_withdrawals',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']
    
    def get_created_by_name(self, obj) -> str | None:
        if obj.created_by:
            return obj.created_by.get_full_name()
        return None
    
    def get_total_returns(self, obj) -> int | float:
        return obj.returns.aggregate(total=models.Sum('amount'))['total'] or 0
    
    def get_total_withdrawals(self, obj) -> int | float:
        return obj.transactions.filter(transaction_type='WITHDRAWAL').aggregate(total=models.Sum('amount'))['total'] or 0


class InvestmentListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing investments"""
    investment_type_display = serializers.CharField(source='get_investment_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    roi = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    total_returns = serializers.SerializerMethodField()
    
    class Meta:
        model = Investment
        fields = [
            'id', 'name', 'investment_type', 'investment_type_display',
            'institution', 'principal_amount', 'current_value',
            'start_date', 'maturity_date', 'status', 'status_display',
            'roi', 'total_returns', 'created_at'
        ]
    
    def get_total_returns(self, obj) -> int | float:
        return obj.returns.aggregate(total=models.Sum('amount'))['total'] or 0


class InvestmentCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating investments"""
    class Meta:
        model = Investment
        fields = [
            'name', 'investment_type', 'institution', 'principal_amount',
            'start_date', 'maturity_date', 'account_number', 'reference_number',
            'documents', 'notes', 'reinvest_returns', 'distribution_rule'
        ]


class InvestmentOverviewSerializer(serializers.Serializer):
    """Serializer for investment overview statistics"""
    total_invested = serializers.DecimalField(max_digits=14, decimal_places=2)
    current_valuation = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_returns = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_profit_loss = serializers.DecimalField(max_digits=14, decimal_places=2)
    average_roi = serializers.DecimalField(max_digits=10, decimal_places=2)
    active_investments_count = serializers.IntegerField()
    matured_investments_count = serializers.IntegerField()
    pending_approvals_count = serializers.IntegerField()
    by_type = serializers.DictField()
    performance_history = serializers.ListField()


class AddReturnSerializer(serializers.Serializer):
    """Serializer for adding returns to an investment"""
    return_type = serializers.ChoiceField(choices=ReturnType.choices)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    date = serializers.DateField()
    reference = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    reinvested = serializers.BooleanField(default=False)


class UpdateValuationSerializer(serializers.Serializer):
    """Serializer for updating investment valuation"""
    value = serializers.DecimalField(max_digits=14, decimal_places=2)
    date = serializers.DateField()
    notes = serializers.CharField(required=False, allow_blank=True)


class RequestWithdrawalSerializer(serializers.Serializer):
    """Serializer for requesting investment withdrawal"""
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    reason = serializers.CharField()


class ApproveInvestmentSerializer(serializers.Serializer):
    """Serializer for approving/rejecting investment requests"""
    action = serializers.ChoiceField(choices=['approve', 'reject'])
    notes = serializers.CharField(required=False, allow_blank=True)


class InvestmentProductListSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    risk_level_display = serializers.CharField(source="get_risk_level_display", read_only=True)
    category_display = serializers.CharField(source="get_category_display", read_only=True)

    class Meta:
        model = InvestmentProduct
        fields = [
            "id",
            "code",
            "name",
            "description",
            "category",
            "category_display",
            "status",
            "status_display",
            "risk_level",
            "risk_level_display",
            "currency",
            "minimum_amount",
            "maximum_amount",
            "expected_return_rate",
            "projected_return_min_rate",
            "projected_return_max_rate",
            "term_days",
            "lock_in_days",
            "liquidity_summary",
            "partial_redemption_allowed",
            "returns_utilization_allowed",
            "wallet_funding_enabled",
            "mpesa_funding_enabled",
            "hybrid_funding_enabled",
            "wallet_payout_enabled",
            "mpesa_payout_enabled",
            "comparison_highlights",
            "suitability_notes",
        ]


class InvestmentProductDetailSerializer(InvestmentProductListSerializer):
    class Meta(InvestmentProductListSerializer.Meta):
        fields = InvestmentProductListSerializer.Meta.fields + [
            "return_method",
            "payout_frequency",
            "disclosure_title",
            "disclosure_body",
            "terms_summary",
            "faq_items",
            "trust_markers",
            "partial_redemption_min_amount",
            "reinvestment_enabled",
            "auto_reinvest_available",
            "early_redemption_penalty_rate",
            "management_fee_rate",
            "withholding_tax_rate",
            "usage_stats",
        ]


class InvestmentProjectionSerializer(serializers.Serializer):
    gross_returns = serializers.DecimalField(max_digits=14, decimal_places=2)
    management_fee = serializers.DecimalField(max_digits=14, decimal_places=2)
    withholding_tax = serializers.DecimalField(max_digits=14, decimal_places=2)
    net_returns = serializers.DecimalField(max_digits=14, decimal_places=2)
    expected_value = serializers.DecimalField(max_digits=14, decimal_places=2)


class StartInvestmentSerializer(serializers.Serializer):
    product_id = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    funding_source = serializers.ChoiceField(choices=InvestmentFundingSource.choices)
    wallet_amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False, default="0.00"
    )
    mpesa_amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False, default="0.00"
    )
    phone = serializers.CharField(required=False, allow_blank=True)
    auto_reinvest = serializers.BooleanField(required=False, default=False)
    idempotency_key = serializers.CharField(required=False, allow_blank=True)


class InvestmentPayoutSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    destination_display = serializers.CharField(source="get_destination_display", read_only=True)
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = InvestmentPayout
        fields = [
            "id",
            "reference",
            "kind",
            "kind_display",
            "destination",
            "destination_display",
            "status",
            "status_display",
            "gross_amount",
            "fee_amount",
            "tax_amount",
            "penalty_amount",
            "net_amount",
            "currency",
            "destination_phone",
            "failure_reason",
            "processed_at",
            "completed_at",
            "created_at",
        ]


class InvestmentReturnLedgerSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = InvestmentReturnLedger
        fields = [
            "id",
            "period_start",
            "period_end",
            "status",
            "status_display",
            "gross_returns",
            "management_fee",
            "withholding_tax",
            "net_returns",
            "available_amount",
            "utilized_amount",
            "accrued_at",
            "available_at",
        ]


class InvestmentTransactionRecordSerializer(serializers.ModelSerializer):
    transaction_type_display = serializers.CharField(
        source="get_transaction_type_display", read_only=True
    )
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = InvestmentTransactionRecord
        fields = [
            "id",
            "reference",
            "transaction_type",
            "transaction_type_display",
            "status",
            "status_display",
            "amount",
            "fee_amount",
            "tax_amount",
            "penalty_amount",
            "net_amount",
            "currency",
            "destination",
            "notes",
            "external_reference",
            "processed_at",
            "created_at",
        ]


class InvestmentUtilizationActionSerializer(serializers.ModelSerializer):
    action_type_display = serializers.CharField(source="get_action_type_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    payout = InvestmentPayoutSerializer(read_only=True)

    class Meta:
        model = InvestmentUtilizationAction
        fields = [
            "id",
            "reference",
            "action_type",
            "action_type_display",
            "status",
            "status_display",
            "amount",
            "fee_amount",
            "tax_amount",
            "net_amount",
            "beneficiary_phone",
            "failure_reason",
            "payout",
            "processed_at",
            "completed_at",
            "created_at",
        ]


class InvestmentRedemptionRequestSerializer(serializers.ModelSerializer):
    redemption_type_display = serializers.CharField(
        source="get_redemption_type_display", read_only=True
    )
    destination_display = serializers.CharField(source="get_destination_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    payout = InvestmentPayoutSerializer(read_only=True)

    class Meta:
        model = InvestmentRedemptionRequest
        fields = [
            "id",
            "reference",
            "redemption_type",
            "redemption_type_display",
            "destination",
            "destination_display",
            "status",
            "status_display",
            "requested_amount",
            "principal_amount",
            "profit_amount",
            "fee_amount",
            "tax_amount",
            "penalty_amount",
            "net_amount",
            "beneficiary_phone",
            "reason",
            "failure_reason",
            "payout",
            "processed_at",
            "completed_at",
            "created_at",
        ]


class MemberInvestmentPositionListSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    funding_source_display = serializers.CharField(source="get_funding_source_display", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_code = serializers.CharField(source="product.code", read_only=True)
    risk_level = serializers.CharField(source="product.risk_level", read_only=True)
    risk_level_display = serializers.CharField(source="product.get_risk_level_display", read_only=True)
    redemption_eligible = serializers.BooleanField(read_only=True)

    class Meta:
        model = MemberInvestmentPosition
        fields = [
            "id",
            "reference",
            "product",
            "product_name",
            "product_code",
            "status",
            "status_display",
            "funding_source",
            "funding_source_display",
            "principal_amount",
            "current_value",
            "accrued_returns",
            "available_returns",
            "expected_value_at_maturity",
            "maturity_date",
            "next_payout_date",
            "risk_level",
            "risk_level_display",
            "beneficiary_phone",
            "latest_status_note",
            "redemption_eligible",
            "created_at",
        ]


class MemberInvestmentPositionDetailSerializer(MemberInvestmentPositionListSerializer):
    product = InvestmentProductDetailSerializer(read_only=True)
    transactions = InvestmentTransactionRecordSerializer(
        source="transactions_v2",
        many=True,
        read_only=True,
    )
    returns_ledger = InvestmentReturnLedgerSerializer(
        source="return_ledgers",
        many=True,
        read_only=True,
    )
    payouts = InvestmentPayoutSerializer(many=True, read_only=True)
    redemptions = InvestmentRedemptionRequestSerializer(
        source="redemption_requests",
        many=True,
        read_only=True,
    )
    utilizations = InvestmentUtilizationActionSerializer(
        source="utilization_actions",
        many=True,
        read_only=True,
    )

    class Meta(MemberInvestmentPositionListSerializer.Meta):
        fields = MemberInvestmentPositionListSerializer.Meta.fields + [
            "currency",
            "wallet_funded_amount",
            "mpesa_funded_amount",
            "realized_returns",
            "redeemed_principal",
            "total_fees_charged",
            "total_penalties_charged",
            "auto_reinvest",
            "metadata",
            "started_at",
            "funded_at",
            "last_accrual_at",
            "closed_at",
            "transactions",
            "returns_ledger",
            "payouts",
            "redemptions",
            "utilizations",
        ]


class PortfolioSummarySerializer(serializers.Serializer):
    currency = serializers.CharField()
    total_invested = serializers.DecimalField(max_digits=14, decimal_places=2)
    current_value = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_returns = serializers.DecimalField(max_digits=14, decimal_places=2)
    available_returns = serializers.DecimalField(max_digits=14, decimal_places=2)
    active_count = serializers.IntegerField()
    matured_count = serializers.IntegerField()
    next_maturity = serializers.DictField(allow_null=True)
    best_performing_investment = serializers.DictField(allow_null=True)
    alerts = serializers.ListField()


class PortfolioAnalyticsSerializer(serializers.Serializer):
    allocation = serializers.ListField()
    growth_series = serializers.ListField()
    realized_returns = serializers.DecimalField(max_digits=14, decimal_places=2)
    unrealized_returns = serializers.DecimalField(max_digits=14, decimal_places=2)
    wallet_withdrawals = serializers.DecimalField(max_digits=14, decimal_places=2)
    reinvestment_total = serializers.DecimalField(max_digits=14, decimal_places=2)


class UtilizeReturnsSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    action_type = serializers.ChoiceField(choices=InvestmentPayoutDestination.choices)
    beneficiary_phone = serializers.CharField(required=False, allow_blank=True)


class RedeemInvestmentSerializer(serializers.Serializer):
    redemption_type = serializers.ChoiceField(choices=InvestmentRedemptionType.choices)
    amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        required=False,
        allow_null=True,
    )
    destination = serializers.ChoiceField(choices=InvestmentPayoutDestination.choices)
    beneficiary_phone = serializers.CharField(required=False, allow_blank=True)
    reason = serializers.CharField(required=False, allow_blank=True)


class RedemptionProcessSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["approve", "complete", "reject"])
    failure_reason = serializers.CharField(required=False, allow_blank=True)
