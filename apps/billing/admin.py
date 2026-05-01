"""
Billing Admin Configuration
"""
from django.contrib import admin

from .models import BillingEvent, FeatureOverride, Plan, SeatUsage, Subscription


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'monthly_price', 'yearly_price', 'is_active', 'is_featured', 'sort_order']
    list_filter = ['is_active', 'is_featured']
    search_fields = ['name', 'code', 'description']
    ordering = ['sort_order']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ['chama', 'plan', 'status', 'provider', 'current_period_start', 'current_period_end']
    list_filter = ['status', 'provider']
    search_fields = ['chama__name', 'plan__name']
    readonly_fields = ['id', 'created_at', 'updated_at']
    raw_id_fields = ['chama', 'plan']


@admin.register(SeatUsage)
class SeatUsageAdmin(admin.ModelAdmin):
    list_display = ['chama', 'active_members_count', 'last_updated']
    search_fields = ['chama__name']
    raw_id_fields = ['chama']


@admin.register(FeatureOverride)
class FeatureOverrideAdmin(admin.ModelAdmin):
    list_display = ['chama', 'feature_key', 'value', 'expires_at', 'created_at']
    list_filter = ['feature_key']
    search_fields = ['chama__name', 'feature_key']
    raw_id_fields = ['chama', 'created_by']


@admin.register(BillingEvent)
class BillingEventAdmin(admin.ModelAdmin):
    list_display = ['chama', 'event_type', 'performed_by', 'created_at']
    list_filter = ['event_type']
    search_fields = ['chama__name', 'event_type']
    raw_id_fields = ['chama', 'performed_by']
    readonly_fields = ['created_at']
