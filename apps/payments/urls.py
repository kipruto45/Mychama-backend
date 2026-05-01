from django.urls import include, path

from apps.payments.unified_views import (
    CashPaymentRejectView as UnifiedCashPaymentRejectView,
)
from apps.payments.unified_views import (
    CashPaymentVerifyView as UnifiedCashPaymentVerifyView,
)
from apps.payments.unified_views import (
    ManualPaymentApprovalPolicyView as UnifiedManualPaymentApprovalPolicyView,
)
from apps.payments.unified_views import (
    PaymentAdminListView as UnifiedPaymentAdminListView,
)
from apps.payments.unified_views import (
    PaymentConfirmClientReturnView as UnifiedPaymentConfirmClientReturnView,
)
from apps.payments.unified_views import (
    PaymentCreateIntentView as UnifiedPaymentCreateIntentView,
)
from apps.payments.unified_views import (
    PaymentDisputeListCreateView as UnifiedPaymentDisputeListCreateView,
)
from apps.payments.unified_views import (
    PaymentDisputeResolveView as UnifiedPaymentDisputeResolveView,
)
from apps.payments.unified_views import (
    PaymentListView as UnifiedPaymentListView,
)
from apps.payments.unified_views import (
    PaymentReceiptView as UnifiedPaymentReceiptView,
)
from apps.payments.unified_views import (
    PaymentReceiptPdfDownloadView as UnifiedPaymentReceiptPdfDownloadView,
)
from apps.payments.unified_views import (
    PaymentReceiptPdfLinkView as UnifiedPaymentReceiptPdfLinkView,
)
from apps.payments.unified_views import (
    PaymentReconciliationListView as UnifiedPaymentReconciliationListView,
)
from apps.payments.unified_views import (
    PaymentReconciliationResolveView as UnifiedPaymentReconciliationResolveView,
)
from apps.payments.unified_views import (
    PaymentRefundApproveView as UnifiedPaymentRefundApproveView,
)
from apps.payments.unified_views import (
    PaymentRefundListView as UnifiedPaymentRefundListView,
)
from apps.payments.unified_views import (
    PaymentRefundProcessView as UnifiedPaymentRefundProcessView,
)
from apps.payments.unified_views import (
    PaymentRefundRequestView as UnifiedPaymentRefundRequestView,
)
from apps.payments.unified_views import (
    PaymentSettlementView as UnifiedPaymentSettlementView,
)
from apps.payments.unified_views import (
    PaymentStatementImportView as UnifiedPaymentStatementImportView,
)
from apps.payments.unified_views import (
    PaymentStatusView as UnifiedPaymentStatusView,
)
from apps.payments.unified_views import (
    PaymentVerifyView as UnifiedPaymentVerifyView,
)
from apps.payments.unified_views import (
    PaymentWebhookView as UnifiedPaymentWebhookView,
)
from apps.payments.views import (
    AdminRegisterC2BUrlsView,
    AdminTransactionsView,
    B2CResultCallbackView,
    B2CTimeoutCallbackView,
    C2BConfirmationCallbackView,
    C2BValidationCallbackView,
    DepositC2BIntentView,
    DepositSTKInitiateView,
    InitiateMpesaPaymentView,
    LoanDisbursementApproveView,
    LoanDisbursementRejectView,
    LoanDisbursementSendView,
    LoanRepaymentC2BIntentView,
    LoanRepaymentStatusView,
    LoanRepaymentSTKInitiateView,
    MpesaCallbackLogListView,
    MpesaCallbackView,
    MpesaReconcileView,
    MpesaSTKPushAPIView,
    MpesaTransactionDetailView,
    MpesaTransactionListView,
    MyTransactionsView,
    PaymentAllocationRuleView,
    PaymentDisputeListCreateView,
    PaymentDisputeResolveView,
    PaymentIntentActivityLogView,
    PaymentStatusView,
    PendingLoanDisbursementListView,
    ReconciliationRunsView,
    RefundApproveView,
    RefundListView,
    RefundProcessView,
    RefundRequestView,
    SplitPaymentC2BIntentView,
    SplitPaymentSTKInitiateView,
    STKCallbackV2View,
    UssdCallbackView,
    WithdrawalApproveView,
    WithdrawalRejectView,
    WithdrawalRequestView,
    WithdrawalSendView,
)

app_name = "payments"

urlpatterns = [
    path("unified/", include("apps.payments.unified_urls")),
    path("intents/", UnifiedPaymentCreateIntentView.as_view(), name="payment-intents"),
    path("history/", UnifiedPaymentListView.as_view(), name="payment-history"),
    path("intents/<uuid:id>/status/", UnifiedPaymentStatusView.as_view(), name="payment-intent-status"),
    path("intents/<uuid:id>/verify/", UnifiedPaymentVerifyView.as_view(), name="payment-intent-verify"),
    path("<uuid:id>/receipt/", UnifiedPaymentReceiptView.as_view(), name="payment-receipt"),
    path("<uuid:id>/receipt/pdf-link/", UnifiedPaymentReceiptPdfLinkView.as_view(), name="payment-receipt-pdf-link"),
    path("receipt/pdf/<str:token>/", UnifiedPaymentReceiptPdfDownloadView.as_view(), name="payment-receipt-pdf-download"),
    path("confirm-client-return/", UnifiedPaymentConfirmClientReturnView.as_view(), name="payment-confirm-client-return"),
    path("webhook/", UnifiedPaymentWebhookView.as_view(), name="payment-webhook"),
    path("cash/approve/", UnifiedCashPaymentVerifyView.as_view(), name="cash-approve"),
    path("cash/reject/", UnifiedCashPaymentRejectView.as_view(), name="cash-reject"),
    path("refunds/", UnifiedPaymentRefundListView.as_view(), name="payment-refund-list"),
    path("refunds/request/", UnifiedPaymentRefundRequestView.as_view(), name="payment-refund-request"),
    path("refunds/<uuid:id>/approve/", UnifiedPaymentRefundApproveView.as_view(), name="payment-refund-approve"),
    path("refunds/<uuid:id>/process/", UnifiedPaymentRefundProcessView.as_view(), name="payment-refund-process"),
    path("disputes/", UnifiedPaymentDisputeListCreateView.as_view(), name="payment-dispute-list-create"),
    path("disputes/<uuid:id>/resolve/", UnifiedPaymentDisputeResolveView.as_view(), name="payment-dispute-resolve"),
    path("manual-policy/", UnifiedManualPaymentApprovalPolicyView.as_view(), name="payment-manual-policy"),
    path("admin/history/", UnifiedPaymentAdminListView.as_view(), name="admin-payment-history"),
    path("reconciliation/", UnifiedPaymentReconciliationListView.as_view(), name="payment-reconciliation"),
    path("reconciliation/import-statement/", UnifiedPaymentStatementImportView.as_view(), name="payment-reconciliation-import-statement"),
    path("reconciliation/<uuid:id>/resolve/", UnifiedPaymentReconciliationResolveView.as_view(), name="payment-reconciliation-resolve"),
    path("settlements/", UnifiedPaymentSettlementView.as_view(), name="payment-settlement-list-create"),
    path("mpesa/stk-push/", MpesaSTKPushAPIView.as_view(), name="mpesa-stk-push-exact"),
    path("mpesa/callback/", STKCallbackV2View.as_view(), name="mpesa-callback-exact"),
    path("mpesa/reconcile/", MpesaReconcileView.as_view(), name="mpesa-reconcile-exact"),
    path(
        "mpesa/c2b/validation/",
        C2BValidationCallbackView.as_view(),
        name="mpesa-c2b-validation-exact",
    ),
    path(
        "mpesa/c2b/confirmation/",
        C2BConfirmationCallbackView.as_view(),
        name="mpesa-c2b-confirmation-exact",
    ),
    path("<uuid:id>/status/", PaymentStatusView.as_view(), name="payment-status-exact"),
    # New enterprise endpoints.
    path("deposit/stk/initiate", DepositSTKInitiateView.as_view(), name="deposit-stk-initiate"),
    path("deposit/c2b/intent", DepositC2BIntentView.as_view(), name="deposit-c2b-intent"),
    path(
        "split/stk/initiate",
        SplitPaymentSTKInitiateView.as_view(),
        name="split-stk-initiate",
    ),
    path(
        "split/c2b/intent",
        SplitPaymentC2BIntentView.as_view(),
        name="split-c2b-intent",
    ),
    path(
        "allocation-rule",
        PaymentAllocationRuleView.as_view(),
        name="allocation-rule",
    ),
    path(
        "loans/<uuid:loan_id>/repay/stk/initiate",
        LoanRepaymentSTKInitiateView.as_view(),
        name="loan-repay-stk-initiate",
    ),
    path(
        "loans/<uuid:loan_id>/repay/c2b/intent",
        LoanRepaymentC2BIntentView.as_view(),
        name="loan-repay-c2b-intent",
    ),
    path(
        "loans/<uuid:loan_id>/repayment-status",
        LoanRepaymentStatusView.as_view(),
        name="loan-repayment-status",
    ),
    path("my/transactions", MyTransactionsView.as_view(), name="my-transactions"),
    path("withdraw/request", WithdrawalRequestView.as_view(), name="withdraw-request"),
    path("withdraw/request", WithdrawalRequestView.as_view(), name="withdrawal-create"),
    path(
        "withdraw/<uuid:intent_id>/approve",
        WithdrawalApproveView.as_view(),
        name="withdraw-approve",
    ),
    path(
        "withdraw/<uuid:intent_id>/reject",
        WithdrawalRejectView.as_view(),
        name="withdraw-reject",
    ),
    path(
        "withdraw/<uuid:intent_id>/send",
        WithdrawalSendView.as_view(),
        name="withdraw-send",
    ),
    path(
        "loan-disbursements/pending",
        PendingLoanDisbursementListView.as_view(),
        name="loan-disbursement-pending",
    ),
    path(
        "loan-disbursements/<uuid:intent_id>/approve",
        LoanDisbursementApproveView.as_view(),
        name="loan-disbursement-approve",
    ),
    path(
        "loan-disbursements/<uuid:intent_id>/send",
        LoanDisbursementSendView.as_view(),
        name="loan-disbursement-send",
    ),
    path(
        "loan-disbursements/<uuid:intent_id>/reject",
        LoanDisbursementRejectView.as_view(),
        name="loan-disbursement-reject",
    ),
    path("admin/transactions", AdminTransactionsView.as_view(), name="admin-transactions"),
    path("refunds/request", RefundRequestView.as_view(), name="refund-request"),
    path("refunds", RefundListView.as_view(), name="refund-list"),
    path(
        "refunds/<uuid:refund_id>/approve",
        RefundApproveView.as_view(),
        name="refund-approve",
    ),
    path(
        "refunds/<uuid:refund_id>/process",
        RefundProcessView.as_view(),
        name="refund-process",
    ),
    path("disputes", PaymentDisputeListCreateView.as_view(), name="dispute-list-create"),
    path(
        "disputes/<uuid:dispute_id>/resolve",
        PaymentDisputeResolveView.as_view(),
        name="dispute-resolve",
    ),
    path(
        "admin/register-c2b-urls",
        AdminRegisterC2BUrlsView.as_view(),
        name="admin-register-c2b-urls",
    ),
    path(
        "intents/<uuid:intent_id>/activity",
        PaymentIntentActivityLogView.as_view(),
        name="intent-activity",
    ),
    path("reconciliation/runs", ReconciliationRunsView.as_view(), name="reconciliation-runs"),
    path(
        "callbacks/c2b/validation",
        C2BValidationCallbackView.as_view(),
        name="callbacks-c2b-validation",
    ),
    path(
        "callbacks/c2b/confirmation",
        C2BConfirmationCallbackView.as_view(),
        name="callbacks-c2b-confirmation",
    ),
    path("callbacks/stk", STKCallbackV2View.as_view(), name="callbacks-stk"),
    path("callbacks/b2c/result", B2CResultCallbackView.as_view(), name="callbacks-b2c-result"),
    path(
        "callbacks/b2c/timeout",
        B2CTimeoutCallbackView.as_view(),
        name="callbacks-b2c-timeout",
    ),
    path("ussd/callback", UssdCallbackView.as_view(), name="ussd-callback"),

    # Legacy endpoints retained.
    path(
        "mpesa/transactions",
        MpesaTransactionListView.as_view(),
        name="mpesa-transaction-list-query",
    ),
    path("mpesa/stk-push", InitiateMpesaPaymentView.as_view(), name="mpesa-stk-push"),
    path("mpesa/callback", MpesaCallbackView.as_view(), name="mpesa-callback-v2"),
    path(
        "<uuid:chama_id>/transactions",
        MpesaTransactionListView.as_view(),
        name="mpesa-transaction-list",
    ),
    path(
        "<uuid:chama_id>/transactions/<uuid:id>",
        MpesaTransactionDetailView.as_view(),
        name="mpesa-transaction-detail",
    ),
    path("<uuid:chama_id>/initiate", InitiateMpesaPaymentView.as_view(), name="mpesa-initiate"),
    path(
        "<uuid:chama_id>/callback-logs",
        MpesaCallbackLogListView.as_view(),
        name="mpesa-callback-logs",
    ),
    path("callback", MpesaCallbackView.as_view(), name="mpesa-callback"),
]
