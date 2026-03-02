from django.urls import path

from apps.payments import views_frontend

app_name = "payments"

urlpatterns = [
    # Legacy pages
    path("", views_frontend.payment_form_view, name="payment_form"),
    path("history/", views_frontend.payment_history_view, name="payment_history"),
    path("methods/", views_frontend.payment_methods_view, name="payment_methods"),

    # Deposit flow
    path("deposit/", views_frontend.deposit_select_view, name="deposit_select"),
    path("deposit/stk/", views_frontend.deposit_stk_push_view, name="deposit_stk_push"),
    path(
        "deposit/paybill/",
        views_frontend.deposit_paybill_instructions_view,
        name="deposit_paybill_instructions",
    ),
    path("transactions/my/", views_frontend.transactions_my_view, name="transactions_my"),

    # Admin/treasurer operations
    path("admin/transactions/", views_frontend.admin_transactions_view, name="admin_transactions"),
    path("reconciliation/runs/", views_frontend.reconciliation_runs_view, name="reconciliation_runs"),

    # Withdrawal flow
    path("withdraw/request/", views_frontend.withdraw_request_view, name="withdraw_request"),
    path("withdraw/approvals/", views_frontend.withdraw_approvals_view, name="withdraw_approvals"),
    path("withdraw/<uuid:intent_id>/", views_frontend.withdraw_detail_view, name="withdraw_detail"),

    # Loan disbursement queue
    path(
        "loan-disbursements/queue/",
        views_frontend.loan_disbursements_queue_view,
        name="loan_disbursements_queue",
    ),
    path(
        "loan-disbursements/<uuid:intent_id>/",
        views_frontend.loan_disbursement_detail_view,
        name="loan_disbursement_detail",
    ),

    # Loan repayment pages
    path("loans/<uuid:loan_id>/pay/", views_frontend.loan_pay_view, name="loan_pay"),
    path("loans/<uuid:loan_id>/pay/stk/", views_frontend.loan_pay_stk_view, name="loan_pay_stk"),
    path(
        "loans/<uuid:loan_id>/pay/paybill/",
        views_frontend.loan_pay_paybill_instructions_view,
        name="loan_pay_paybill_instructions",
    ),
    path(
        "loans/<uuid:loan_id>/repayments/",
        views_frontend.loan_repayment_history_view,
        name="loan_repayment_history",
    ),
    path(
        "loans/<uuid:loan_id>/disbursement-status/",
        views_frontend.loan_disbursement_status_view,
        name="loan_disbursement_status",
    ),

    # Public callback status helper
    path("callback/status/", views_frontend.callback_status_public_view, name="callback_status_public"),
]
