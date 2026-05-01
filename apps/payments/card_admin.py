"""
Card payment admin configuration for MyChama.

Django admin configuration for card payment models.
"""

from django.contrib import admin

from apps.payments.card_models import (
    CardPaymentAuditLog,
    CardPaymentIntent,
    CardPaymentReceipt,
    CardPaymentTransaction,
    CardPaymentWebhook,
)


class CardPaymentTransactionInline(admin.TabularInline):
    """Inline admin for card payment transactions."""

    model = CardPaymentTransaction
    extra = 0
    readonly_fields = [
        "id",
        "provider_reference",
        "provider_name",
        "amount",
        "currency",
        "status",
        "card_brand",
        "card_last4",
        "authorization_code",
        "paid_at",
        "failed_at",
        "created_at",
    ]
    fields = [
        "id",
        "provider_reference",
        "status",
        "amount",
        "currency",
        "card_brand",
        "card_last4",
        "paid_at",
        "created_at",
    ]


class CardPaymentAuditLogInline(admin.TabularInline):
    """Inline admin for card payment audit logs."""

    model = CardPaymentAuditLog
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


@admin.register(CardPaymentIntent)
class CardPaymentIntentAdmin(admin.ModelAdmin):
    """Admin configuration for CardPaymentIntent."""

    list_display = [
        "id",
        "chama",
        "user",
        "amount",
        "currency",
        "status",
        "provider",
        "purpose",
        "created_at",
    ]
    list_filter = [
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
        "description",
        "provider",
        "provider_intent_id",
        "client_secret",
        "checkout_url",
        "status",
        "idempotency_key",
        "reference",
        "failure_reason",
        "failure_code",
        "metadata",
        "expires_at",
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
                    "description",
                ),
            },
        ),
        (
            "Provider Details",
            {
                "fields": (
                    "provider",
                    "provider_intent_id",
                    "client_secret",
                    "checkout_url",
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
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )
    inlines = [CardPaymentTransactionInline, CardPaymentAuditLogInline]
    list_per_page = 50
    date_hierarchy = "created_at"

    def get_queryset(self, request):
        """Optimize queryset with select_related."""
        return super().get_queryset(request).select_related(
            "chama",
            "user",
            "contribution",
        )


@admin.register(CardPaymentTransaction)
class CardPaymentTransactionAdmin(admin.ModelAdmin):
    """Admin configuration for CardPaymentTransaction."""

    list_display = [
        "id",
        "payment_intent",
        "provider_reference",
        "provider_name",
        "amount",
        "currency",
        "status",
        "card_brand",
        "card_last4",
        "paid_at",
        "created_at",
    ]
    list_filter = [
        "status",
        "provider_name",
        "card_brand",
        "created_at",
    ]
    search_fields = [
        "id",
        "payment_intent__id",
        "provider_reference",
        "card_last4",
        "authorization_code",
    ]
    readonly_fields = [
        "id",
        "payment_intent",
        "provider_reference",
        "provider_name",
        "amount",
        "currency",
        "status",
        "card_brand",
        "card_last4",
        "card_country",
        "authorization_code",
        "auth_code",
        "raw_response",
        "paid_at",
        "failed_at",
        "created_at",
        "updated_at",
    ]
    list_per_page = 50
    date_hierarchy = "created_at"

    def get_queryset(self, request):
        """Optimize queryset with select_related."""
        return super().get_queryset(request).select_related("payment_intent")


@admin.register(CardPaymentReceipt)
class CardPaymentReceiptAdmin(admin.ModelAdmin):
    """Admin configuration for CardPaymentReceipt."""

    list_display = [
        "id",
        "receipt_number",
        "reference_number",
        "payment_intent",
        "amount",
        "currency",
        "card_brand",
        "card_last4",
        "issued_at",
        "created_at",
    ]
    list_filter = [
        "currency",
        "card_brand",
        "issued_at",
        "created_at",
    ]
    search_fields = [
        "id",
        "receipt_number",
        "reference_number",
        "payment_intent__id",
        "card_last4",
    ]
    readonly_fields = [
        "id",
        "payment_intent",
        "transaction",
        "reference_number",
        "receipt_number",
        "amount",
        "currency",
        "card_brand",
        "card_last4",
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


@admin.register(CardPaymentWebhook)
class CardPaymentWebhookAdmin(admin.ModelAdmin):
    """Admin configuration for CardPaymentWebhook."""

    list_display = [
        "id",
        "provider",
        "event_type",
        "provider_reference",
        "signature_valid",
        "processed",
        "source_ip",
        "created_at",
    ]
    list_filter = [
        "provider",
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


@admin.register(CardPaymentAuditLog)
class CardPaymentAuditLogAdmin(admin.ModelAdmin):
    """Admin configuration for CardPaymentAuditLog."""

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
