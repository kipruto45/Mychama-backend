"""
Unified Payment URL Configuration for MyChama.

URL patterns for unified payment endpoints.
"""

from django.urls import path

from apps.payments.unified_views import (
    BankPaymentProofUploadView,
    BankPaymentRejectView,
    BankPaymentVerifyView,
    CashPaymentRejectView,
    CashPaymentVerifyView,
    ManualPaymentApprovalPolicyView,
    PaymentAdminListView,
    PaymentConfirmClientReturnView,
    PaymentCreateIntentView,
    PaymentDisputeListCreateView,
    PaymentDisputeResolveView,
    PaymentListView,
    PaymentReceiptView,
    PaymentReconciliationListView,
    PaymentReconciliationResolveView,
    PaymentRefundApproveView,
    PaymentRefundListView,
    PaymentRefundProcessView,
    PaymentRefundRequestView,
    PaymentSettlementView,
    PaymentStatementImportView,
    PaymentStatusView,
    PaymentVerifyView,
    PaymentWebhookView,
)

app_name = "unified_payments"

urlpatterns = [
    # Create payment intent
    path(
        "create-intent/",
        PaymentCreateIntentView.as_view(),
        name="create-intent",
    ),
    path(
        "intents/",
        PaymentCreateIntentView.as_view(),
        name="payment-intents",
    ),
    # Get payment status
    path(
        "<uuid:id>/status/",
        PaymentStatusView.as_view(),
        name="payment-status",
    ),
    path(
        "intents/<uuid:id>/status/",
        PaymentStatusView.as_view(),
        name="payment-intent-status",
    ),
    # Verify payment with provider
    path(
        "<uuid:id>/verify/",
        PaymentVerifyView.as_view(),
        name="verify-payment",
    ),
    # Webhook endpoint
    path(
        "webhook/",
        PaymentWebhookView.as_view(),
        name="webhook",
    ),
    # Get payment receipt
    path(
        "<uuid:id>/receipt/",
        PaymentReceiptView.as_view(),
        name="payment-receipt",
    ),
    path(
        "receipts/<uuid:id>/",
        PaymentReceiptView.as_view(),
        name="receipt-detail",
    ),
    # List user's payments
    path(
        "list/",
        PaymentListView.as_view(),
        name="payment-list",
    ),
    path(
        "history/",
        PaymentListView.as_view(),
        name="payment-history",
    ),
    path(
        "refunds/",
        PaymentRefundListView.as_view(),
        name="refund-list",
    ),
    path(
        "refunds/request/",
        PaymentRefundRequestView.as_view(),
        name="refund-request",
    ),
    path(
        "refunds/<uuid:id>/approve/",
        PaymentRefundApproveView.as_view(),
        name="refund-approve",
    ),
    path(
        "refunds/<uuid:id>/process/",
        PaymentRefundProcessView.as_view(),
        name="refund-process",
    ),
    path(
        "disputes/",
        PaymentDisputeListCreateView.as_view(),
        name="dispute-list-create",
    ),
    path(
        "disputes/<uuid:id>/resolve/",
        PaymentDisputeResolveView.as_view(),
        name="dispute-resolve",
    ),
    # Confirm client return from provider
    path(
        "confirm-client-return/",
        PaymentConfirmClientReturnView.as_view(),
        name="confirm-client-return",
    ),
    # Cash payment verification
    path(
        "cash/verify/",
        CashPaymentVerifyView.as_view(),
        name="cash-verify",
    ),
    path(
        "cash/approve/",
        CashPaymentVerifyView.as_view(),
        name="cash-approve",
    ),
    path(
        "cash/reject/",
        CashPaymentRejectView.as_view(),
        name="cash-reject",
    ),
    path(
        "bank/proof/",
        BankPaymentProofUploadView.as_view(),
        name="bank-proof",
    ),
    path(
        "bank/verify/",
        BankPaymentVerifyView.as_view(),
        name="bank-verify",
    ),
    path(
        "bank/reject/",
        BankPaymentRejectView.as_view(),
        name="bank-reject",
    ),
    path(
        "reconciliation/",
        PaymentReconciliationListView.as_view(),
        name="reconciliation-list",
    ),
    path(
        "reconciliation/import-statement/",
        PaymentStatementImportView.as_view(),
        name="reconciliation-import-statement",
    ),
    path(
        "settlements/",
        PaymentSettlementView.as_view(),
        name="settlement-list-create",
    ),
    path(
        "reconciliation/<uuid:id>/resolve/",
        PaymentReconciliationResolveView.as_view(),
        name="reconciliation-resolve",
    ),
    path(
        "manual-policy/",
        ManualPaymentApprovalPolicyView.as_view(),
        name="manual-payment-policy",
    ),
    # Admin list all payments
    path(
        "admin/list/",
        PaymentAdminListView.as_view(),
        name="admin-payment-list",
    ),
]
