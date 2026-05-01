"""
Card payment URL configuration for MyChama.

URL patterns for card payment endpoints.
"""

from django.urls import path

from apps.payments.card_views import (
    CardPaymentAdminListView,
    CardPaymentConfirmClientReturnView,
    CardPaymentCreateIntentView,
    CardPaymentListView,
    CardPaymentReceiptView,
    CardPaymentStatusView,
    CardPaymentVerifyView,
    CardPaymentWebhookView,
)

app_name = "card_payments"

urlpatterns = [
    # Create payment intent
    path(
        "create-intent/",
        CardPaymentCreateIntentView.as_view(),
        name="create-intent",
    ),
    # Get payment status
    path(
        "<uuid:id>/status/",
        CardPaymentStatusView.as_view(),
        name="payment-status",
    ),
    # Verify payment with provider
    path(
        "<uuid:id>/verify/",
        CardPaymentVerifyView.as_view(),
        name="verify-payment",
    ),
    # Webhook endpoint
    path(
        "webhook/",
        CardPaymentWebhookView.as_view(),
        name="webhook",
    ),
    # Get payment receipt
    path(
        "<uuid:id>/receipt/",
        CardPaymentReceiptView.as_view(),
        name="payment-receipt",
    ),
    # List user's payments
    path(
        "list/",
        CardPaymentListView.as_view(),
        name="payment-list",
    ),
    # Confirm client return from provider
    path(
        "confirm-client-return/",
        CardPaymentConfirmClientReturnView.as_view(),
        name="confirm-client-return",
    ),
    # Admin list all payments
    path(
        "admin/list/",
        CardPaymentAdminListView.as_view(),
        name="admin-payment-list",
    ),
]
