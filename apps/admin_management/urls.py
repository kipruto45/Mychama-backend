"""
Admin Management API URL Configuration
"""
from django.urls import path

from apps.admin_management import views

urlpatterns = [
    # User Management
    path("users/", views.UserListCreateView.as_view(), name="admin-users-list"),
    path("users/<int:user_id>/", views.UserDetailView.as_view(), name="admin-user-detail"),
    path("users/<int:user_id>/activate/", views.UserActivateView.as_view(), name="admin-user-activate"),
    
    # Chama Management
    path("chamas/", views.ChamaListCreateView.as_view(), name="admin-chamas-list"),
    
    # Membership Management
    path("members/", views.MemberListView.as_view(), name="admin-members-list"),
    path("members/<int:membership_id>/approve/", views.MemberApproveView.as_view(), name="admin-member-approve"),
    path("members/<int:membership_id>/reject/", views.MemberRejectView.as_view(), name="admin-member-reject"),
    path("members/<int:membership_id>/role/", views.MemberRoleUpdateView.as_view(), name="admin-member-role-update"),
    
    # Membership Requests
    path("membership-requests/", views.MembershipRequestListView.as_view(), name="admin-membership-requests-list"),
    path("membership-requests/<uuid:request_id>/approve/", views.MembershipRequestApproveView.as_view(), name="admin-membership-request-approve"),
    path("membership-requests/<uuid:request_id>/reject/", views.MembershipRequestRejectView.as_view(), name="admin-membership-request-reject"),
    
    # Loan Management
    path("loans/", views.LoanListView.as_view(), name="admin-loans-list"),
    path("loans/create/", views.LoanCreateView.as_view(), name="admin-loan-create"),
    path("loans/<int:loan_id>/approve/", views.LoanApproveView.as_view(), name="admin-loan-approve"),
    path("loans/<int:loan_id>/reject/", views.LoanRejectView.as_view(), name="admin-loan-reject"),
    path("loans/<int:loan_id>/disburse/", views.LoanDisburseView.as_view(), name="admin-loan-disburse"),
    
    # Contribution Management
    path("contributions/", views.ContributionListView.as_view(), name="admin-contributions-list"),
    path("contributions/create/", views.ContributionCreateView.as_view(), name="admin-contribution-create"),
    path("contributions/summary/", views.ContributionSummaryView.as_view(), name="admin-contribution-summary"),
    
    # Withdrawal Management
    path("withdrawals/", views.WithdrawalListView.as_view(), name="admin-withdrawals-list"),
    path("withdrawals/<int:withdrawal_id>/approve/", views.WithdrawalApproveView.as_view(), name="admin-withdrawal-approve"),
    path("withdrawals/<int:withdrawal_id>/reject/", views.WithdrawalRejectView.as_view(), name="admin-withdrawal-reject"),
    
    # Transactions
    path("transactions/", views.TransactionListView.as_view(), name="admin-transactions-list"),
    path("wallet/balance/", views.WalletBalanceView.as_view(), name="admin-wallet-balance"),
    
    # Payments
    path("payments/", views.PaymentListView.as_view(), name="admin-payments-list"),
    
    # Penalties
    path("penalties/", views.PenaltyListView.as_view(), name="admin-penalties-list"),
    path("penalties/create/", views.PenaltyCreateView.as_view(), name="admin-penalty-create"),
    
    # Manual Adjustments
    path("adjustments/create/", views.ManualAdjustmentCreateView.as_view(), name="admin-adjustment-create"),
    
    # Dashboard & Reports
    path("dashboard/metrics/", views.DashboardMetricsView.as_view(), name="admin-dashboard-metrics"),
    path("dashboard/trends/", views.MonthlyTrendsView.as_view(), name="admin-dashboard-trends"),
    path("dashboard/activity/", views.RecentActivityView.as_view(), name="admin-dashboard-activity"),
    
    # Approvals Center
    path("approvals/summary/", views.ApprovalSummaryView.as_view(), name="admin-approvals-summary"),
    path("approvals/disbursements/", views.PendingDisbursementsView.as_view(), name="admin-pending-disbursements"),
    path("approvals/withdrawals/", views.PendingWithdrawalsView.as_view(), name="admin-pending-withdrawals"),
    
    # Meetings & Governance
    path("meetings/", views.MeetingListView.as_view(), name="admin-meetings-list"),
    path("meetings/<int:meeting_id>/", views.MeetingDetailView.as_view(), name="admin-meeting-detail"),
    path("meetings/<int:meeting_id>/approve-minutes/", views.MeetingApproveMinutesView.as_view(), name="admin-meeting-approve-minutes"),
    path("meetings/attendance/", views.AttendanceListView.as_view(), name="admin-meeting-attendance"),
    path("resolutions/", views.ResolutionListView.as_view(), name="admin-resolutions-list"),
    path("resolutions/<int:resolution_id>/update/", views.ResolutionUpdateView.as_view(), name="admin-resolution-update"),
    
    # Issues & Service Desk
    path("issues/", views.IssueListView.as_view(), name="admin-issues-list"),
    path("issues/<int:issue_id>/", views.IssueDetailView.as_view(), name="admin-issue-detail"),
    path("issues/<int:issue_id>/update/", views.IssueUpdateView.as_view(), name="admin-issue-update"),
    path("issues/<int:issue_id>/assign/", views.IssueAssignView.as_view(), name="admin-issue-assign"),
    path("warnings/", views.WarningListView.as_view(), name="admin-warnings-list"),
    path("warnings/create/", views.WarningCreateView.as_view(), name="admin-warning-create"),
    path("suspensions/", views.SuspensionListView.as_view(), name="admin-suspensions-list"),
    path("suspensions/create/", views.SuspensionCreateView.as_view(), name="admin-suspension-create"),
    path("suspensions/<uuid:suspension_id>/lift/", views.SuspensionLiftView.as_view(), name="admin-suspension-lift"),
    
    # Notifications & Broadcasts
    path("notifications/", views.NotificationListView.as_view(), name="admin-notifications-list"),
    path("notifications/templates/", views.NotificationTemplateListView.as_view(), name="admin-notification-templates"),
    path("broadcasts/", views.BroadcastListView.as_view(), name="admin-broadcasts-list"),
    path("broadcasts/create/", views.BroadcastCreateView.as_view(), name="admin-broadcast-create"),
    
    # Security Center
    path("security/login-attempts/", views.LoginAttemptListView.as_view(), name="admin-login-attempts"),
    path("security/otp-delivery/", views.OTPDeliveryLogListView.as_view(), name="admin-otp-delivery"),
    path("security/sessions/", views.SessionListView.as_view(), name="admin-sessions-list"),
    path("security/sessions/<uuid:session_id>/revoke/", views.SessionRevokeView.as_view(), name="admin-session-revoke"),
    path("security/audit-logs/", views.AuditLogListView.as_view(), name="admin-audit-logs"),
    path("security/alerts/", views.SecurityAlertListView.as_view(), name="admin-security-alerts"),
    path("security/alerts/<int:alert_id>/resolve/", views.SecurityAlertResolveView.as_view(), name="admin-security-alert-resolve"),
    
    # Reports & Analytics
    path("reports/", views.ReportListView.as_view(), name="admin-reports-list"),
    path("reports/generate/", views.ReportGenerateView.as_view(), name="admin-report-generate"),
    path("reports/scheduled/", views.ScheduledReportListView.as_view(), name="admin-scheduled-reports"),
    
    # Payments & M-Pesa
    path("mpesa/transactions/", views.MpesaTransactionListView.as_view(), name="admin-mpesa-transactions"),
    path("payments/disputes/", views.PaymentDisputeListView.as_view(), name="admin-payment-disputes"),
    path("payments/refunds/", views.PaymentRefundListView.as_view(), name="admin-payment-refunds"),
    path("payments/refunds/<uuid:refund_id>/approve/", views.RefundApproveView.as_view(), name="admin-refund-approve"),
    path("payments/refunds/<uuid:refund_id>/reject/", views.RefundRejectView.as_view(), name="admin-refund-reject"),
    
    # AI Admin Dashboard
    path("ai/insights/", views.AIInsightsView.as_view(), name="admin-ai-insights"),
    path("ai/fraud-alerts/", views.AIFraudAlertsView.as_view(), name="admin-ai-fraud-alerts"),
    path("ai/risk-scores/", views.AIRiskScoresView.as_view(), name="admin-ai-risk-scores"),
    
    # Automations Center
    path("automations/", views.AutomationListView.as_view(), name="admin-automations-list"),
    path("automations/<int:automation_id>/toggle/", views.AutomationToggleView.as_view(), name="admin-automation-toggle"),
    path("automations/logs/", views.AutomationLogListView.as_view(), name="admin-automation-logs"),
    
    # Finance Ledger
    path("ledger/", views.LedgerListView.as_view(), name="admin-ledger-list"),
    path("ledger/summary/", views.LedgerSummaryView.as_view(), name="admin-ledger-summary"),
    
    # Admin Settings
    path("settings/", views.AdminSettingsView.as_view(), name="admin-settings"),
    path("system/health/", views.SystemHealthView.as_view(), name="admin-system-health"),
]
