"""
AI Admin for Digital Chama
"""

from django.contrib import admin

from .models import AIInsight, AIInteraction, FraudFlag, LoanEligibility, RiskProfile


@admin.register(AIInteraction)
class AIInteractionAdmin(admin.ModelAdmin):
    list_display = ["user", "chama", "question", "created_at"]
    list_filter = ["chama", "created_at"]
    search_fields = ["question", "response", "user__phone_number"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(RiskProfile)
class RiskProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "chama", "risk_score", "risk_level", "loan_multiplier", "last_calculated"]
    list_filter = ["chama", "risk_level"]
    search_fields = ["user__phone_number", "chama__name"]
    readonly_fields = ["last_calculated", "created_at", "updated_at"]


@admin.register(FraudFlag)
class FraudFlagAdmin(admin.ModelAdmin):
    list_display = ["user", "chama", "fraud_type", "severity", "resolved", "created_at"]
    list_filter = ["chama", "fraud_type", "severity", "resolved"]
    search_fields = ["user__phone_number", "chama__name", "description"]
    readonly_fields = ["created_at", "updated_at"]
    
    actions = ["resolve_flags"]
    
    def resolve_flags(self, request, queryset):
        queryset.update(resolved=True)
    resolve_flags.short_description = "Mark selected flags as resolved"


@admin.register(AIInsight)
class AIInsightAdmin(admin.ModelAdmin):
    list_display = ["chama", "insight_type", "title", "is_active", "created_at"]
    list_filter = ["chama", "insight_type", "is_active"]
    search_fields = ["title", "description", "chama__name"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(LoanEligibility)
class LoanEligibilityAdmin(admin.ModelAdmin):
    list_display = ["user", "chama", "max_loan_amount", "eligible", "created_at"]
    list_filter = ["chama", "eligible"]
    search_fields = ["user__phone_number", "chama__name"]
    readonly_fields = ["created_at", "updated_at"]
