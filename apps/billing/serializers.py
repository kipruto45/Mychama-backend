"""
Billing Serializers
DRF serializers for billing endpoints
"""
from decimal import Decimal

from rest_framework import serializers
from .models import (
    BillingCredit,
    BillingEvent,
    BillingRule,
    FeatureOverride,
    Invoice,
    InvoiceLineItem,
    Plan,
    SeatUsage,
    Subscription,
    UsageMetric,
)
from .entitlements import PLAN_ENTITLEMENTS, FEATURE_DESCRIPTIONS


class PlanSerializer(serializers.ModelSerializer):
    """Serializer for Plan model"""
    features = serializers.SerializerMethodField()
    prices = serializers.SerializerMethodField()
    
    class Meta:
        model = Plan
        fields = [
            'id', 'code', 'name', 'description', 'monthly_price', 'yearly_price',
            'features', 'prices', 'is_active', 'is_featured', 'sort_order',
            'stripe_monthly_price_id', 'stripe_yearly_price_id',
            'created_at', 'updated_at',
        ]
    
    def get_features(self, obj):
        """Return features as dict"""
        return obj.features or {}
    
    def get_prices(self, obj):
        """Return formatted prices"""
        return {
            'monthly': {
                'amount': float(obj.monthly_price) if obj.monthly_price else 0,
                'currency': 'KES',
                'formatted': f'KES {obj.monthly_price:,.0f}/month' if obj.monthly_price else 'Free',
            },
            'yearly': {
                'amount': float(obj.yearly_price) if obj.yearly_price else 0,
                'currency': 'KES',
                'formatted': f'KES {obj.yearly_price:,.0f}/year' if obj.yearly_price else 'Free',
            },
        }


class PlanListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for plan listings"""
    price_display = serializers.SerializerMethodField()
    
    class Meta:
        model = Plan
        fields = ['id', 'code', 'name', 'description', 'monthly_price', 'yearly_price', 'price_display', 'is_featured', 'sort_order']
    
    def get_price_display(self, obj):
        if obj.monthly_price:
            return f'KES {obj.monthly_price:,.0f}/mo'
        return 'Free'


class SubscriptionSerializer(serializers.ModelSerializer):
    """Serializer for Subscription model"""
    plan_details = PlanListSerializer(source='plan', read_only=True)
    scheduled_plan_details = PlanListSerializer(source='scheduled_plan', read_only=True)
    
    class Meta:
        model = Subscription
        fields = [
            'id', 'plan', 'plan_details', 'status', 'provider',
            'billing_cycle', 'auto_renew', 'grace_period_ends_at',
            'suspended_at', 'scheduled_plan', 'scheduled_plan_details',
            'scheduled_change_at', 'failed_payment_count',
            'current_period_start', 'current_period_end',
            'cancel_at_period_end', 'created_at', 'updated_at',
        ]


class SubscriptionDetailSerializer(serializers.ModelSerializer):
    """Extended subscription details"""
    plan_details = PlanSerializer(source='plan', read_only=True)
    scheduled_plan_details = PlanSerializer(source='scheduled_plan', read_only=True)
    days_remaining = serializers.SerializerMethodField()
    
    class Meta:
        model = Subscription
        fields = [
            'id', 'plan', 'plan_details', 'status', 'provider',
            'billing_cycle', 'auto_renew', 'grace_period_ends_at',
            'suspended_at', 'scheduled_plan', 'scheduled_plan_details',
            'scheduled_change_at', 'failed_payment_count',
            'current_period_start', 'current_period_end',
            'cancel_at_period_end', 'days_remaining',
            'created_at', 'updated_at',
        ]
    
    def get_days_remaining(self, obj):
        from django.utils import timezone
        from datetime import timedelta
        
        if obj.current_period_end:
            delta = obj.current_period_end - timezone.now()
            return max(0, delta.days)
        return 0


class SeatUsageSerializer(serializers.ModelSerializer):
    """Serializer for SeatUsage model"""
    limit = serializers.SerializerMethodField()
    percentage = serializers.SerializerMethodField()
    
    class Meta:
        model = SeatUsage
        fields = ['id', 'active_members_count', 'limit', 'percentage', 'last_updated']
    
    def get_limit(self, obj):
        # This requires context - handled in view
        return getattr(obj, 'seat_limit', 25)
    
    def get_percentage(self, obj):
        limit = getattr(obj, 'seat_limit', 25)
        if limit > 0:
            return round(obj.active_members_count / limit * 100, 1)
        return 100


class FeatureOverrideSerializer(serializers.ModelSerializer):
    """Serializer for FeatureOverride model"""
    feature_details = serializers.SerializerMethodField()
    
    class Meta:
        model = FeatureOverride
        fields = ['id', 'feature_key', 'feature_details', 'value', 'reason', 'expires_at', 'created_at']
    
    def get_feature_details(self, obj):
        return FEATURE_DESCRIPTIONS.get(obj.feature_key, {'name': obj.feature_key, 'description': ''})


class BillingEventSerializer(serializers.ModelSerializer):
    """Serializer for BillingEvent model"""
    performer_name = serializers.SerializerMethodField()
    
    class Meta:
        model = BillingEvent
        fields = ['id', 'event_type', 'details', 'performer_name', 'created_at']
    
    def get_performer_name(self, obj):
        if obj.performed_by:
            return obj.performed_by.get_full_name() or getattr(obj.performed_by, 'phone', '')
        return None


class CheckoutRequestSerializer(serializers.Serializer):
    """Serializer for checkout request"""
    plan_id = serializers.IntegerField()
    billing_cycle = serializers.ChoiceField(choices=['monthly', 'yearly'])
    provider = serializers.ChoiceField(choices=['stripe', 'paypal', 'mpesa', 'manual'], default='stripe')
    phone = serializers.CharField(required=False, allow_blank=True)
    auto_renew = serializers.BooleanField(required=False)
    success_url = serializers.URLField(required=False)
    cancel_url = serializers.URLField(required=False)
    next = serializers.CharField(required=False, allow_blank=True)


class CheckoutResponseSerializer(serializers.Serializer):
    """Serializer for checkout response"""
    checkout_url = serializers.URLField()
    session_id = serializers.CharField()


class EntitlementsSerializer(serializers.Serializer):
    """Serializer for entitlements"""
    plan_code = serializers.CharField()
    plan_name = serializers.CharField()
    seat_limit = serializers.IntegerField()
    storage_limit_mb = serializers.IntegerField()
    support_level = serializers.CharField()
    features = serializers.DictField()


class PlanComparisonSerializer(serializers.Serializer):
    """Serializer for plan comparison data"""
    plans = PlanListSerializer(many=True)
    features = serializers.ListField()
    feature_descriptions = serializers.DictField()


class BillingRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillingRule
        fields = [
            'id',
            'name',
            'chama',
            'grace_period_days',
            'enforcement_mode',
            'upgrade_approval_threshold',
            'auto_renew_enabled',
            'payment_retry_schedule',
            'allow_enterprise_overrides',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']


class UsageMetricSerializer(serializers.ModelSerializer):
    remaining = serializers.SerializerMethodField()

    class Meta:
        model = UsageMetric
        fields = [
            'id',
            'metric_key',
            'used_quantity',
            'limit_quantity',
            'remaining',
            'period_started_at',
            'period_ends_at',
            'reset_at',
            'updated_at',
        ]

    def get_remaining(self, obj):
        return max(0, obj.limit_quantity - obj.used_quantity)


class BillingCreditSerializer(serializers.ModelSerializer):
    chama_id = serializers.UUIDField(read_only=True)
    chama_name = serializers.CharField(source='chama.name', read_only=True)
    is_expired = serializers.BooleanField(read_only=True)

    class Meta:
        model = BillingCredit
        fields = [
            'id',
            'chama_id',
            'chama_name',
            'source_type',
            'source_reference',
            'description',
            'total_amount',
            'remaining_amount',
            'currency',
            'expires_at',
            'is_expired',
            'metadata',
            'created_at',
            'updated_at',
        ]


class BillingCreditIssueSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField(required=False)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    description = serializers.CharField(required=False, allow_blank=True, max_length=255)
    source_reference = serializers.CharField(required=False, allow_blank=True, max_length=120)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)


class BillingCreditUpdateSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=['update', 'revoke'], required=False)
    remaining_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        min_value=Decimal("0.00"),
    )
    description = serializers.CharField(required=False, allow_blank=True, max_length=255)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)


class InvoiceLineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceLineItem
        fields = ['id', 'title', 'description', 'quantity', 'unit_price', 'total_price', 'metadata']


class InvoiceSerializer(serializers.ModelSerializer):
    line_items = InvoiceLineItemSerializer(many=True, read_only=True)
    plan_details = PlanListSerializer(source='plan', read_only=True)

    class Meta:
        model = Invoice
        fields = [
            'id',
            'invoice_number',
            'status',
            'provider',
            'currency',
            'subtotal',
            'tax_amount',
            'total_amount',
            'amount_paid',
            'payment_reference',
            'provider_transaction_id',
            'billing_period_start',
            'billing_period_end',
            'due_at',
            'paid_at',
            'customer_email',
            'pdf_file',
            'plan',
            'plan_details',
            'created_at',
            'updated_at',
            'line_items',
        ]
