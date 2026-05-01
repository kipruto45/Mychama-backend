from django.contrib import admin

from apps.finance.models import (
    Contribution,
    ContributionGoal,
    ContributionType,
    InstallmentSchedule,
    LedgerEntry,
    Loan,
    LoanApprovalLog,
    LoanEligibilityCheck,
    LoanGuarantor,
    LoanProduct,
    LoanRestructureRequest,
    LoanTopUpRequest,
    ManualAdjustment,
    MonthClosure,
    Penalty,
    Repayment,
    Wallet,
)


class ImmutableFinancialRecordAdmin(admin.ModelAdmin):
    """
    Financial records are append-only.
    Admin can inspect but cannot mutate or delete persisted records.
    """

    def get_readonly_fields(self, request, obj=None):
        if obj is not None:
            return [field.name for field in self.model._meta.fields]
        return super().get_readonly_fields(request, obj)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        if obj is None:
            return super().has_change_permission(request, obj)
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return super().has_view_permission(
            request, obj
        ) or super().has_change_permission(
            request,
            obj,
        )


@admin.register(ContributionType)
class ContributionTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "chama", "frequency", "default_amount", "is_active")
    list_filter = ("frequency", "is_active", "chama")
    search_fields = ("name", "chama__name")


@admin.register(Contribution)
class ContributionAdmin(ImmutableFinancialRecordAdmin):
    list_display = (
        "member",
        "chama",
        "contribution_type",
        "amount",
        "date_paid",
        "method",
        "receipt_code",
    )
    list_filter = ("method", "date_paid", "chama")
    search_fields = ("member__full_name", "member__phone", "receipt_code")


@admin.register(ContributionGoal)
class ContributionGoalAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "member",
        "chama",
        "target_amount",
        "current_amount",
        "status",
        "is_active",
    )
    list_filter = ("status", "is_active", "chama")
    search_fields = ("title", "member__full_name", "member__phone", "chama__name")


@admin.register(Loan)
class LoanAdmin(ImmutableFinancialRecordAdmin):
    list_display = (
        "member",
        "chama",
        "principal",
        "interest_rate",
        "duration_months",
        "status",
        "requested_at",
    )
    list_filter = ("status", "interest_type", "chama")
    search_fields = ("member__full_name", "member__phone")


@admin.register(LoanProduct)
class LoanProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "chama",
        "is_active",
        "is_default",
        "max_loan_amount",
        "interest_type",
        "interest_rate",
    )
    list_filter = ("is_active", "is_default", "interest_type", "chama")
    search_fields = ("name", "chama__name")


@admin.register(LoanEligibilityCheck)
class LoanEligibilityCheckAdmin(ImmutableFinancialRecordAdmin):
    list_display = (
        "loan",
        "member",
        "status",
        "requested_amount",
        "recommended_max_amount",
        "created_at",
    )
    list_filter = ("status", "chama")
    search_fields = ("loan__id", "member__full_name", "member__phone")
    readonly_fields = ("reasons",)


@admin.register(LoanApprovalLog)
class LoanApprovalLogAdmin(ImmutableFinancialRecordAdmin):
    list_display = ("loan", "stage", "decision", "actor", "acted_at")
    list_filter = ("stage", "decision")
    search_fields = ("loan__id", "actor__full_name", "actor__phone")


@admin.register(LoanGuarantor)
class LoanGuarantorAdmin(admin.ModelAdmin):
    list_display = (
        "loan",
        "guarantor",
        "guaranteed_amount",
        "status",
        "accepted_at",
    )
    list_filter = ("status", "accepted_at")
    search_fields = ("loan__id", "guarantor__full_name", "guarantor__phone")


@admin.register(LoanTopUpRequest)
class LoanTopUpRequestAdmin(admin.ModelAdmin):
    list_display = (
        "loan",
        "requested_amount",
        "status",
        "reviewed_by",
        "reviewed_at",
    )
    list_filter = ("status", "reviewed_at")
    search_fields = ("loan__id", "loan__member__full_name", "review_note")


@admin.register(LoanRestructureRequest)
class LoanRestructureRequestAdmin(admin.ModelAdmin):
    list_display = (
        "loan",
        "requested_duration_months",
        "requested_interest_rate",
        "status",
        "reviewed_by",
        "reviewed_at",
    )
    list_filter = ("status", "reviewed_at")
    search_fields = ("loan__id", "loan__member__full_name", "review_note")


@admin.register(InstallmentSchedule)
class InstallmentScheduleAdmin(ImmutableFinancialRecordAdmin):
    list_display = (
        "loan",
        "due_date",
        "expected_amount",
        "expected_principal",
        "expected_interest",
        "status",
    )
    list_filter = ("status", "due_date")
    search_fields = ("loan__member__full_name", "loan__member__phone")


@admin.register(Repayment)
class RepaymentAdmin(ImmutableFinancialRecordAdmin):
    list_display = ("loan", "amount", "date_paid", "method", "receipt_code")
    list_filter = ("method", "date_paid")
    search_fields = ("loan__member__full_name", "loan__member__phone", "receipt_code")


@admin.register(Penalty)
class PenaltyAdmin(ImmutableFinancialRecordAdmin):
    list_display = ("member", "chama", "amount", "status", "due_date", "issued_by")
    list_filter = ("status", "due_date", "chama")
    search_fields = ("member__full_name", "member__phone", "reason")


@admin.register(LedgerEntry)
class LedgerEntryAdmin(ImmutableFinancialRecordAdmin):
    list_display = (
        "chama",
        "entry_type",
        "direction",
        "amount",
        "currency",
        "idempotency_key",
        "created_at",
    )
    list_filter = ("entry_type", "direction", "currency", "created_at", "chama")
    search_fields = ("idempotency_key", "narration")


@admin.register(ManualAdjustment)
class ManualAdjustmentAdmin(ImmutableFinancialRecordAdmin):
    list_display = ("chama", "amount", "direction", "idempotency_key", "created_at")
    list_filter = ("direction", "chama", "created_at")
    search_fields = ("idempotency_key", "reason")


@admin.register(MonthClosure)
class MonthClosureAdmin(admin.ModelAdmin):
    list_display = ("chama", "month", "closed_by", "created_at")
    list_filter = ("month", "chama")
    search_fields = ("chama__name", "closed_by__full_name", "closed_by__phone")


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("owner_type", "owner_id", "available_balance", "locked_balance", "total_balance", "currency", "created_at")
    list_filter = ("owner_type", "currency")
    search_fields = ("owner_id",)
    readonly_fields = ("available_balance", "locked_balance", "created_at", "updated_at")
    
    def total_balance(self, obj):
        return obj.total_balance
    total_balance.short_description = "Total Balance"
