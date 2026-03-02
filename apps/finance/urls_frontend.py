from django.urls import path

from apps.finance import views_frontend

app_name = "finance"

urlpatterns = [
    # Primary routes
    path("contributions/", views_frontend.contribution_form_view, name="contribution_form"),
    path("loans/", views_frontend.loan_application_view, name="loan_application"),
    path("loans/list/", views_frontend.loan_list_view, name="loan_list"),
    path("expenses/", views_frontend.expense_form_view, name="expense_form"),
    path("transactions/", views_frontend.transaction_history_view, name="transaction_history"),

    # Alias routes used by templates/dashboards
    path("contributions/", views_frontend.contribution_form_view, name="contributions"),
    path("loans/", views_frontend.loan_application_view, name="loans"),
    path("expenses/", views_frontend.expense_form_view, name="expenses"),
    path("transactions/", views_frontend.transaction_history_view, name="transactions"),
    path("contributions/record/", views_frontend.contribution_form_view, name="record_contribution"),
    path("loans/approve/", views_frontend.loan_list_view, name="approve_loan"),
    path("expenses/record/", views_frontend.expense_form_view, name="record_expense"),
]
