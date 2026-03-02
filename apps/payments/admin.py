from django.contrib import admin

from apps.payments.models import (
    CallbackLog,
    MpesaB2CPayout,
    MpesaC2BTransaction,
    MpesaCallbackLog,
    MpesaSTKTransaction,
    MpesaTransaction,
    PaymentActivityLog,
    PaymentAllocationRule,
    PaymentDispute,
    PaymentIntent,
    PaymentRefund,
    PaymentReconciliationRun,
    UssdSessionLog,
    WithdrawalApprovalLog,
)


@admin.register(MpesaTransaction)
class MpesaTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "chama",
        "member",
        "phone",
        "amount",
        "purpose",
        "status",
        "receipt_number",
        "created_at",
    )
    list_filter = ("purpose", "status", "created_at", "chama")
    search_fields = (
        "member__full_name",
        "member__phone",
        "phone",
        "receipt_number",
        "checkout_request_id",
        "merchant_request_id",
        "chama__name",
    )
    readonly_fields = (
        "checkout_request_id",
        "merchant_request_id",
        "receipt_number",
        "raw_callback",
        "created_at",
        "updated_at",
    )


@admin.register(MpesaCallbackLog)
class MpesaCallbackLogAdmin(admin.ModelAdmin):
    list_display = ("transaction", "checkout_request_id", "processed", "created_at")
    list_filter = ("processed", "created_at")
    search_fields = ("checkout_request_id", "merchant_request_id")
    readonly_fields = ("callback_data", "processing_error", "created_at", "updated_at")


@admin.register(PaymentIntent)
class PaymentIntentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "chama",
        "intent_type",
        "purpose",
        "amount",
        "phone",
        "status",
        "created_at",
    )
    list_filter = ("intent_type", "purpose", "status", "chama")
    search_fields = (
        "idempotency_key",
        "phone",
        "reference_type",
        "reference_id",
        "chama__name",
    )
    readonly_fields = ("metadata", "created_at", "updated_at")


@admin.register(MpesaC2BTransaction)
class MpesaC2BTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "trans_id",
        "chama",
        "intent",
        "phone",
        "amount",
        "processing_status",
        "trans_time",
    )
    list_filter = ("processing_status", "transaction_type", "chama")
    search_fields = ("trans_id", "account_reference", "bill_ref_number", "phone")
    readonly_fields = ("raw_payload", "created_at", "updated_at")


@admin.register(MpesaSTKTransaction)
class MpesaSTKTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "checkout_request_id",
        "chama",
        "intent",
        "phone",
        "amount",
        "status",
        "mpesa_receipt_number",
        "created_at",
    )
    list_filter = ("status", "chama")
    search_fields = (
        "checkout_request_id",
        "merchant_request_id",
        "mpesa_receipt_number",
        "phone",
    )
    readonly_fields = ("raw_callback", "created_at", "updated_at")


@admin.register(MpesaB2CPayout)
class MpesaB2CPayoutAdmin(admin.ModelAdmin):
    list_display = (
        "originator_conversation_id",
        "chama",
        "intent",
        "phone",
        "amount",
        "status",
        "transaction_id",
        "created_at",
    )
    list_filter = ("status", "command_id", "chama")
    search_fields = (
        "originator_conversation_id",
        "conversation_id",
        "transaction_id",
        "receipt_number",
        "phone",
    )
    readonly_fields = ("raw_result", "created_at", "updated_at")


@admin.register(PaymentActivityLog)
class PaymentActivityLogAdmin(admin.ModelAdmin):
    list_display = ("payment_intent", "event", "actor", "created_at")
    list_filter = ("event", "created_at")
    search_fields = ("payment_intent__id", "payment_intent__idempotency_key")
    readonly_fields = ("meta", "created_at", "updated_at")


@admin.register(PaymentReconciliationRun)
class PaymentReconciliationRunAdmin(admin.ModelAdmin):
    list_display = ("run_at", "chama", "status", "created_at")
    list_filter = ("status", "run_at")
    search_fields = ("chama__name",)
    readonly_fields = ("totals", "anomalies", "created_at", "updated_at")


@admin.register(WithdrawalApprovalLog)
class WithdrawalApprovalLogAdmin(admin.ModelAdmin):
    list_display = ("payment_intent", "step", "actor", "created_at")
    list_filter = ("step", "created_at")
    search_fields = ("payment_intent__id", "payment_intent__idempotency_key")


@admin.register(CallbackLog)
class CallbackLogAdmin(admin.ModelAdmin):
    list_display = ("callback_type", "source_ip", "signature_valid", "created_at")
    list_filter = ("callback_type", "signature_valid", "created_at")
    search_fields = ("source_ip",)
    readonly_fields = (
        "payload",
        "headers",
        "processing_error",
        "created_at",
        "updated_at",
    )


@admin.register(PaymentRefund)
class PaymentRefundAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "chama",
        "payment_intent",
        "amount",
        "status",
        "requested_by",
        "approved_by",
        "processed_at",
    )
    list_filter = ("status", "chama", "created_at", "processed_at")
    search_fields = (
        "payment_intent__idempotency_key",
        "idempotency_key",
        "reason",
        "notes",
    )


@admin.register(PaymentDispute)
class PaymentDisputeAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "chama",
        "payment_intent",
        "opened_by",
        "category",
        "status",
        "created_at",
    )
    list_filter = ("status", "category", "chama", "created_at")
    search_fields = (
        "reference",
        "reason",
        "resolution_notes",
        "opened_by__full_name",
        "opened_by__phone",
    )


@admin.register(PaymentAllocationRule)
class PaymentAllocationRuleAdmin(admin.ModelAdmin):
    list_display = (
        "chama",
        "strategy",
        "repayment_ratio_percent",
        "welfare_contribution_type",
        "is_active",
        "created_at",
    )
    list_filter = ("strategy", "is_active", "chama")
    search_fields = ("chama__name",)


@admin.register(UssdSessionLog)
class UssdSessionLogAdmin(admin.ModelAdmin):
    list_display = (
        "session_id",
        "phone",
        "chama",
        "user",
        "processed",
        "created_at",
    )
    list_filter = ("processed", "created_at", "chama")
    search_fields = ("session_id", "phone", "response_text", "processing_error")
    readonly_fields = ("created_at", "updated_at")
