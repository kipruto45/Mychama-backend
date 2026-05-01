"""Django admin configuration for Payout models."""

from django.contrib import admin

from .models import (
    Payout,
    PayoutAuditLog,
    PayoutEligibilityCheck,
    PayoutRotation,
)


@admin.register(PayoutRotation)
class PayoutRotationAdmin(admin.ModelAdmin):
    """Admin for PayoutRotation."""

    list_display = [
        "chama",
        "current_position",
        "rotation_cycle",
        "members_in_rotation",
        "last_updated_at",
    ]
    list_filter = ["rotation_cycle", "chama__name"]
    search_fields = ["chama__name"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    """Admin for Payout."""

    list_display = [
        "id",
        "chama",
        "member",
        "amount",
        "status",
        "eligibility_status",
        "payout_method",
        "created_at",
    ]
    list_filter = [
        "status",
        "eligibility_status",
        "payout_method",
        "trigger_type",
        "created_at",
    ]
    search_fields = [
        "chama__name",
        "member__user__phone",
        "member__user__first_name",
    ]
    readonly_fields = [
        "id",
        "created_at",
        "updated_at",
        "eligibility_checked_at",
        "treasurer_reviewed_at",
        "chairperson_approved_at",
        "payment_completed_at",
        "hold_flagged_at",
        "hold_resolved_at",
    ]
    fieldsets = (
        (
            "Basic Info",
            {
                "fields": [
                    "id",
                    "chama",
                    "member",
                    "amount",
                    "currency",
                ]
            },
        ),
        (
            "Rotation",
            {
                "fields": [
                    "rotation_position",
                    "rotation_cycle",
                ]
            },
        ),
        (
            "Status",
            {
                "fields": [
                    "status",
                    "trigger_type",
                    "eligibility_status",
                    "eligibility_issues",
                ]
            },
        ),
        (
            "Approvals",
            {
                "fields": [
                    "approval_request",
                    "treasurer_reviewed_by",
                    "treasurer_reviewed_at",
                    "treasurer_rejection_reason",
                    "chairperson_approved_by",
                    "chairperson_approved_at",
                    "chairperson_rejection_reason",
                ]
            },
        ),
        (
            "Payment",
            {
                "fields": [
                    "payout_method",
                    "payment_intent",
                    "payment_started_at",
                    "payment_completed_at",
                    "payment_failed_at",
                    "failure_reason",
                    "failure_code",
                    "retry_count",
                    "max_retries",
                ]
            },
        ),
        (
            "On Hold",
            {
                "fields": [
                    "is_on_hold",
                    "hold_reason",
                    "hold_flagged_by",
                    "hold_flagged_at",
                    "hold_resolved_by",
                    "hold_resolved_at",
                ]
            },
        ),
        (
            "Ledger",
            {
                "fields": [
                    "ledger_entry",
                    "receipt_generated_at",
                ]
            },
        ),
        (
            "Metadata",
            {
                "fields": [
                    "metadata",
                    "skip_reason",
                    "defer_reason",
                ]
            },
        ),
        (
            "Timestamps",
            {
                "fields": [
                    "created_at",
                    "updated_at",
                ]
            },
        ),
    )


@admin.register(PayoutEligibilityCheck)
class PayoutEligibilityCheckAdmin(admin.ModelAdmin):
    """Admin for PayoutEligibilityCheck."""

    list_display = [
        "payout",
        "member",
        "result",
        "has_outstanding_penalties",
        "has_active_disputes",
        "has_overdue_loans",
        "checked_at",
    ]
    list_filter = [
        "result",
        "has_outstanding_penalties",
        "has_active_disputes",
        "has_overdue_loans",
        "checked_at",
    ]
    search_fields = [
        "member__user__phone",
        "member__user__first_name",
        "payout__chama__name",
    ]
    readonly_fields = [
        "id",
        "checked_at",
    ]


@admin.register(PayoutAuditLog)
class PayoutAuditLogAdmin(admin.ModelAdmin):
    """Admin for PayoutAuditLog."""

    list_display = [
        "payout",
        "action",
        "actor",
        "previous_status",
        "new_status",
        "created_at",
    ]
    list_filter = [
        "action",
        "created_at",
    ]
    search_fields = [
        "payout__id",
        "actor__phone",
        "actor__first_name",
        "reason",
    ]
    readonly_fields = [
        "id",
        "created_at",
    ]
