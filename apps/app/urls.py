from django.urls import path

from apps.app import views

app_name = 'app'

urlpatterns = [
    # Dashboard & Summary
    path('dashboard/', views.dashboard_summary, name='dashboard'),
    path('approvals-center/', views.approvals_center, name='approvals-center'),
    path('activity-history/', views.activity_history, name='activity-history'),
    path('audit-logs/', views.audit_logs, name='audit-logs'),
    path('profile/', views.member_profile, name='profile'),
    path('role-workspaces/', views.role_workspaces, name='role-workspaces'),
    path('policy-center/', views.policy_center, name='policy-center'),
    
    # Chama details
    path('chama/<uuid:chama_id>/', views.chama_detail, name='chama-detail'),
    
    # Loan details
    path('loan/<uuid:loan_id>/', views.loan_detail, name='loan-detail'),
    
    # Payment history
    path('payments/', views.payment_history, name='payment-history'),
    
    # Wallet endpoints
    path('wallet/', views.wallet_info, name='wallet'),
    path('wallet/transactions/', views.wallet_transactions, name='wallet-transactions'),
    path('wallet/validate/', views.wallet_validate, name='wallet-validate'),
    
    # Public endpoints
    path('security-info/', views.public_security_info, name='public-security-info'),
]
