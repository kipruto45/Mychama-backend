"""
Unified Payment Admin Configuration for MyChama.

Django admin configuration for unified payment models.
"""

from django.contrib import admin

from apps.payments.unified_models import (
    BankPaymentDetails,
    CardPaymentDetails,
    CashPaymentDetails,
    MpesaPaymentDetails,
    PaymentAuditLog,
    PaymentIntent,
    PaymentReceipt,
    PaymentTransaction,
    PaymentWebhook,
)


class MpesaPaymentDetailsInline(admin.StackedInline):
    """Inline admin for M-Pesa payment details."""

    model = MpesaPaymentDetails
    extra = 0
    readonly_fields = [
        "id",
        "phone",
        "checkout_request_id",
        "merchant_request_id",
        "mpesa_receipt_number",
        "callback_received_at",
        "created_at",
    ]
    fields = [
        "id",
        "phone",
        "checkout_request_id",
        "merchant_request_id",
        "mpesa_receipt_number",
        "callback_received_at",
        "created_at",
    ]


class CardPaymentDetailsInline(admin.StackedInline):
    """Inline admin for card payment details."""

    model = CardPaymentDetails
    extra = 0
    readonly_fields = [
        "id",
        "provider_intent_id",
        "client_secret",
        "checkout_url",
        "card_brand",
        "card_last4",
        "card_country",
        "authorization_code",
        "created_at",
    ]
    fields = [
        "id",
        "provider_intent_id",
        "checkout_url",
        "card_brand",
        "card_last4",
        "card_country",
        "authorization_code",
        "created_at",
    ]


class CashPaymentDetailsInline(admin.StackedInline):
    """Inline admin for cash payment details."""

    model = CashPaymentDetails
    extra = 0
    readonly_fields = [
        "id",
        "received_by",
        "receipt_number",
        "proof_photo",
        "notes",
        "verified_by",
        "verified_at",
        "created_at",
    ]
    fields = [
        "id",
        "received_by",
        "receipt_number",
        "proof_photo",
        "notes",
        "verified_by",
        "verified_at",
        "created_at",
    ]


class BankPaymentDetailsInline(admin.StackedInline):
    """Inline admin for bank payment details."""

    model = BankPaymentDetails
    extra = 0
    readonly_fields = [
        "id",
        "bank_name",
        "account_number",
        "account_name",
        "transfer_reference",
        "proof_document",
        "notes",
        "verified_by",
        "verified_at",
        "created_at",
    ]
    fields = [
        "id",
        "bank_name",
        "account_number",
        "account_name",
        "transfer_reference",
        "proof_document",
        "notes",
        "verified_by",
        "verified_at",
        "created_at",
    ]


class PaymentTransactionInline(admin.TabularInline):
    """Inline admin for payment transactions."""

    model = PaymentTransaction
    extra = 0
    readonly_fields = [
        "id",
        "provider_reference",
        "provider_name",
        "payment_method",
        "amount",
        "currency",
        "status",
        "payer_reference",
        "verified_at",
        "failed_at",
        "created_at",
    ]
    fields = [
        "id",
        "provider_reference",
        "provider_name",
        "payment_method",
        "status",
        "amount",
        "currency",
        "payer_reference",
        "paid_at",
        "created_at",
    ]


class PaymentAuditLogInline(admin.TabularInline):
    """Inline admin for payment audit logs."""

    model = PaymentAuditLog
    extra = 0
    readonly_fields = [
        "id",
        "actor",
        "event",
        "previous_status",
        "new_status",
        "created_at",
    ]
    fields = [
        "id",
        "actor",
        "event",
        "previous_status",
        "new_status",
        "created_at",
    ]


@admin.register(PaymentIntent)
class PaymentIntentAdmin(admin.ModelAdmin):
    """Admin configuration for PaymentIntent."""

    list_display = [
        "id",
        "chama",
        "user",
        "amount",
        "currency",
        "payment_method",
        "status",
        "provider",
        "purpose",
        "created_at",
    ]
    list_filter = [
        "payment_method",
        "status",
        "provider",
        "purpose",
        "currency",
        "created_at",
    ]
    search_fields = [
        "id",
        "chama__name",
        "user__email",
        "user__first_name",
        "user__last_name",
        "provider_intent_id",
        "reference",
        "idempotency_key",
    ]
    readonly_fields = [
        "id",
        "chama",
        "user",
        "contribution",
        "amount",
        "currency",
        "purpose",
        "purpose_id",
        "description",
        "payment_method",
        "provider",
        "provider_intent_id",
        "status",
        "idempotency_key",
        "reference",
        "failure_reason",
        "failure_code",
        "metadata",
        "expires_at",
        "initiated_at",
        "completed_at",
        "created_at",
        "updated_at",
    ]
    fieldsets = (
        (
            "Payment Details",
            {
                "fields": (
                    "id",
                    "chama",
                    "user",
                    "contribution",
                    "amount",
                    "currency",
                    "purpose",
                    "purpose_id",
                    "description",
                ),
            },
        ),
        (
            "Payment Method",
            {
                "fields": (
                    "payment_method",
                    "provider",
                    "provider_intent_id",
                    "status",
                ),
            },
        ),
        (
            "Reference & Idempotency",
            {
                "fields": (
                    "idempotency_key",
                    "reference",
                ),
            },
        ),
        (
            "Failure Details",
            {
                "fields": (
                    "failure_reason",
                    "failure_code",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Metadata",
            {
                "fields": (
                    "metadata",
                    "expires_at",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Timestamps",
            {
                "fields": (
                    "initiated_at",
                    "completed_at",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )
    inlines = [
        MpesaPaymentDetailsInline,
        CardPaymentDetailsInline,
        CashPaymentDetailsInline,
        BankPaymentDetailsInline,
        PaymentTransactionInline,
        PaymentAuditLogInline,
    ]
    list_per_page = 50
    date_hierarchy = "created_at"

    def get_queryset(self, request):
        """Optimize queryset with select_related."""
        return super().get_queryset(request).select_related(
            "chama",
            "user",
            "contribution",
        )


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    """Admin configuration for PaymentTransaction."""

    list_display = [
        "id",
        "payment_intent",
        "provider_reference",
        "provider_name",
        "payment_method",
        "amount",
        "currency",
        "status",
        "payer_reference",
        "verified_at",
        "created_at",
    ]
    list_filter = [
        "status",
        "provider_name",
        "payment_method",
        "created_at",
    ]
    search_fields = [
        "id",
        "payment_intent__id",
        "provider_reference",
        "payer_reference",
    ]
    readonly_fields = [
        "id",
        "payment_intent",
        "provider_reference",
        "provider_name",
        "payment_method",
        "amount",
        "currency",
        "status",
        "payer_reference",
        "raw_response",
        "verified_at",
        "verified_by",
        "failed_at",
        "created_at",
        "updated_at",
    ]
    list_per_page = 50
    date_hierarchy = "created_at"

    def get_queryset(self, request):
        """Optimize queryset with select_related."""
        return super().get_queryset(request).select_related("payment_intent")


@admin.register(PaymentReceipt)
class PaymentReceiptAdmin(admin.ModelAdmin):
    """Admin configuration for PaymentReceipt."""

    list_display = [
        "id",
        "receipt_number",
        "reference_number",
        "payment_intent",
        "amount",
        "currency",
        "payment_method",
        "issued_at",
        "created_at",
    ]
    list_filter = [
        "currency",
        "payment_method",
        "issued_at",
        "created_at",
    ]
    search_fields = [
        "id",
        "receipt_number",
        "reference_number",
        "payment_intent__id",
    ]
    readonly_fields = [
        "id",
        "payment_intent",
        "transaction",
        "reference_number",
        "receipt_number",
        "amount",
        "currency",
        "payment_method",
        "issued_at",
        "issued_by",
        "metadata",
        "created_at",
        "updated_at",
    ]
    list_per_page = 50
    date_hierarchy = "issued_at"

    def get_queryset(self, request):
        """Optimize queryset with select_related."""
        return super().get_queryset(request).select_related(
            "payment_intent",
            "transaction",
            "issued_by",
        )


@admin.register(PaymentWebhook)
class PaymentWebhookAdmin(admin.ModelAdmin):
    """Admin configuration for PaymentWebhook."""

    list_display = [
        "id",
        "provider",
        "payment_method",
        "event_type",
        "provider_reference",
        "signature_valid",
        "processed",
        "source_ip",
        "created_at",
    ]
    list_filter = [
        "provider",
        "payment_method",
        "event_type",
        "signature_valid",
        "processed",
        "created_at",
    ]
    search_fields = [
        "id",
        "provider_reference",
        "event_type",
        "source_ip",
    ]
    readonly_fields = [
        "id",
        "provider",
        "payment_method",
        "event_type",
        "provider_reference",
        "signature_valid",
        "signature",
        "payload",
        "headers",
        "processed",
        "processed_at",
        "processing_error",
        "source_ip",
        "created_at",
        "updated_at",
    ]
    fieldsets = (
        (
            "Webhook Details",
            {
                "fields": (
                    "id",
                    "provider",
                    "payment_method",
                    "event_type",
                    "provider_reference",
                ),
            },
        ),
        (
            "Verification",
            {
                "fields": (
                    "signature_valid",
                    "signature",
                ),
            },
        ),
        (
            "Payload",
            {
                "fields": (
                    "payload",
                    "headers",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Processing",
            {
                "fields": (
                    "processed",
                    "processed_at",
                    "processing_error",
                    "source_ip",
                ),
            },
        ),
        (
            "Timestamps",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )
    list_per_page = 50
    date_hierarchy = "created_at"


@admin.register(PaymentAuditLog)
class PaymentAuditLogAdmin(admin.ModelAdmin):
    """Admin configuration for PaymentAuditLog."""

    list_display = [
        "id",
        "payment_intent",
        "actor",
        "event",
        "previous_status",
        "new_status",
        "created_at",
    ]
    list_filter = [
        "event",
        "previous_status",
        "new_status",
        "created_at",
    ]
    search_fields = [
        "id",
        "payment_intent__id",
        "actor__email",
        "actor__first_name",
        "actor__last_name",
        "event",
    ]
    readonly_fields = [
        "id",
        "payment_intent",
        "actor",
        "event",
        "previous_status",
        "new_status",
        "metadata",
        "ip_address",
        "user_agent",
        "created_at",
        "updated_at",
    ]
    fieldsets = (
        (
            "Audit Details",
            {
                "fields": (
                    "id",
                    "payment_intent",
                    "actor",
                    "event",
                ),
            },
        ),
        (
            "Status Changes",
            {
                "fields": (
                    "previous_status",
                    "new_status",
                ),
            },
        ),
        (
            "Metadata",
            {
                "fields": (
                    "metadata",
                    "ip_address",
                    "user_agent",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Timestamps",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )
    list_per_page = 50
    date_hierarchy = "created_at"

    def get_queryset(self, request):
        """Optimize queryset with select_related."""
        return super().get_queryset(request).select_related(
            "payment_intent",
            "actor",
        )
