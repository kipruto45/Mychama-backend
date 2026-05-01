# Fines Module Serializers

from rest_framework import serializers

from .models import (
    Fine,
    FineAdjustment,
    FineCategory,
    FinePayment,
    FineReminder,
    FineRule,
)


class FineRuleSerializer(serializers.ModelSerializer):
    """Serializer for FineRule model"""
    created_by_name = serializers.SerializerMethodField()
    category_display = serializers.CharField(source='get_category_display', read_only=True)
    trigger_type_display = serializers.CharField(source='get_trigger_type_display', read_only=True)
    amount_type_display = serializers.CharField(source='get_amount_type_display', read_only=True)
    recurrence_display = serializers.CharField(source='get_recurrence_display', read_only=True)
    
    class Meta:
        model = FineRule
        fields = [
            'id', 'name', 'description', 'trigger_type', 'trigger_type_display',
            'category', 'category_display', 'amount_type', 'amount_type_display',
            'amount_value', 'grace_days', 'cap_amount', 'recurrence', 'recurrence_display',
            'enabled', 'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']
    
    def get_created_by_name(self, obj) -> str | None:
        if obj.created_by:
            return obj.created_by.get_full_name()
        return None


class FineAdjustmentSerializer(serializers.ModelSerializer):
    """Serializer for FineAdjustment model"""
    adjusted_by_name = serializers.SerializerMethodField()
    
    class Meta:
        model = FineAdjustment
        fields = ['id', 'fine', 'before_amount', 'after_amount', 'reason', 'adjusted_by', 'adjusted_by_name', 'created_at']
        read_only_fields = ['created_at']
    
    def get_adjusted_by_name(self, obj) -> str | None:
        if obj.adjusted_by:
            return obj.adjusted_by.get_full_name()
        return None


class FineReminderSerializer(serializers.ModelSerializer):
    """Serializer for FineReminder model"""
    sent_to_name = serializers.SerializerMethodField()

    class Meta:
        model = FineReminder
        fields = ['id', 'fine', 'sent_to', 'sent_to_name', 'reminder_type', 'sent_at', 'status']
        read_only_fields = ['sent_at']

    def get_sent_to_name(self, obj) -> str | None:
        if obj.sent_to:
            return obj.sent_to.get_full_name()
        return None


class FinePaymentSerializer(serializers.ModelSerializer):
    """Serializer for FinePayment model"""
    recorded_by_name = serializers.SerializerMethodField()
    method_display = serializers.CharField(source='get_method_display', read_only=True)
    
    class Meta:
        model = FinePayment
        fields = ['id', 'fine', 'amount', 'method', 'method_display', 'transaction_reference', 'recorded_by', 'recorded_by_name', 'notes', 'created_at']
        read_only_fields = ['created_at']
    
    def get_recorded_by_name(self, obj) -> str | None:
        if obj.recorded_by:
            return obj.recorded_by.get_full_name()
        return None


class FineSerializer(serializers.ModelSerializer):
    """Serializer for Fine model"""
    member_name = serializers.SerializerMethodField()
    issued_by_name = serializers.SerializerMethodField()
    category_display = serializers.CharField(source='get_category_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    outstanding_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    adjustments = FineAdjustmentSerializer(many=True, read_only=True)
    payments = FinePaymentSerializer(many=True, read_only=True)
    
    class Meta:
        model = Fine
        fields = [
            'id', 'chama', 'member', 'member_name', 'category', 'category_display',
            'rule', 'amount', 'due_date', 'status', 'status_display',
            'issued_by', 'issued_by_name', 'issued_reason', 'attachments',
            'disputed_at', 'dispute_reason', 'dispute_resolved_at', 'dispute_resolution',
            'paid_at', 'waived_at', 'outstanding_amount',
            'adjustments', 'payments', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']
    
    def get_member_name(self, obj) -> str:
        return obj.member.get_full_name()
    
    def get_issued_by_name(self, obj) -> str | None:
        if obj.issued_by:
            return obj.issued_by.get_full_name()
        return None


class FineListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing fines"""
    member_name = serializers.SerializerMethodField()
    category_display = serializers.CharField(source='get_category_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    outstanding_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    
    class Meta:
        model = Fine
        fields = [
            'id', 'member', 'member_name', 'category', 'category_display',
            'amount', 'due_date', 'status', 'status_display', 'outstanding_amount',
            'created_at'
        ]
    
    def get_member_name(self, obj) -> str:
        return obj.member.get_full_name()


class FineCategorySerializer(serializers.Serializer):
    """Serializer for fine category metadata"""
    value = serializers.CharField()
    label = serializers.CharField()


class FineIssueSerializer(serializers.Serializer):
    """Serializer for issuing a new fine"""
    member_ids = serializers.ListField(child=serializers.UUIDField())
    category = serializers.ChoiceField(choices=FineCategory.choices)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    due_date = serializers.DateField()
    reason = serializers.CharField()
    attachments = serializers.ListField(child=serializers.URLField(), required=False, default=list)


class FineWaiveSerializer(serializers.Serializer):
    """Serializer for waiving a fine"""
    reason = serializers.CharField()


class FineAdjustSerializer(serializers.Serializer):
    """Serializer for adjusting a fine"""
    new_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    reason = serializers.CharField()


class FinePaySerializer(serializers.Serializer):
    """Serializer for paying a fine"""
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    method = serializers.ChoiceField(choices=FinePayment.PaymentMethod.choices)
    transaction_reference = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    idempotency_key = serializers.CharField(required=False, allow_blank=True, max_length=100)


class FineDisputeSerializer(serializers.Serializer):
    """Serializer for disputing a fine"""
    reason = serializers.CharField()


class FineOverviewSerializer(serializers.Serializer):
    """Serializer for fines overview statistics"""
    total_outstanding = serializers.DecimalField(max_digits=14, decimal_places=2)
    collected_this_month = serializers.DecimalField(max_digits=14, decimal_places=2)
    waived_this_month = serializers.DecimalField(max_digits=14, decimal_places=2)
    overdue_count = serializers.IntegerField()
    pending_count = serializers.IntegerField()
    total_fines_count = serializers.IntegerField()
    by_category = serializers.DictField()
    monthly_collections = serializers.ListField()
