# Investments Module Serializers

from django.db import models
from rest_framework import serializers
from .models import (
    Investment, InvestmentTransaction, InvestmentReturn, InvestmentValuation,
    InvestmentApprovalRequest, MemberInvestment, InvestmentDistribution, InvestmentDistributionDetail
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
    
    def get_recorded_by_name(self, obj):
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
    
    def get_recorded_by_name(self, obj):
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
    
    def get_recorded_by_name(self, obj):
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
    
    def get_requested_by_name(self, obj):
        if obj.requested_by:
            return obj.requested_by.get_full_name()
        return None
    
    def get_approved_by_name(self, obj):
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
    
    def get_member_name(self, obj):
        return obj.member.get_full_name()


class InvestmentDistributionDetailSerializer(serializers.ModelSerializer):
    """Serializer for InvestmentDistributionDetail"""
    member_name = serializers.SerializerMethodField()
    
    class Meta:
        model = InvestmentDistributionDetail
        fields = ['id', 'distribution', 'member', 'member_name', 'amount', 'share_percentage', 'paid', 'paid_at']
    
    def get_member_name(self, obj):
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
    
    def get_created_by_name(self, obj):
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
    
    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name()
        return None
    
    def get_total_returns(self, obj):
        return obj.returns.aggregate(total=models.Sum('amount'))['total'] or 0
    
    def get_total_withdrawals(self, obj):
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
    
    def get_total_returns(self, obj):
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
    return_type = serializers.ChoiceField(choices=InvestmentReturn.ReturnType.choices)
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
