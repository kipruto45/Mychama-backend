from django.urls import path

from apps.automations.views import (
    AutomationCatalogView,
    AutomationNotificationLogListView,
    JobDetailRunsView,
    JobRunListView,
    ReconciliationReportView,
    ScheduledJobListView,
)
from apps.automations.views_hub import automation_hub_view
from apps.automations.views_mobile import (
    active_delegations_view,
    anomalies_view,
    # Audit
    audit_logs_view,
    chama_compliance_view,
    check_permission_view,
    # Anomaly
    check_withdrawal_anomaly_view,
    # Contributions
    compliance_view,
    device_sessions_view,
    # Membership
    effective_role_view,
    export_audit_report_view,
    loan_approval_queue_view,
    # Loans
    loan_eligibility_view,
    locked_accounts_view,
    overdue_loans_view,
    pending_contributions_view,
    report_suspicious_activity_view,
    revoke_delegation_view,
    revoke_device_session_view,
    route_loan_view,
    # Security
    security_alerts_view,
    send_contribution_reminder_view,
    send_overdue_reminder_view,
    unlock_account_view,
)

app_name = "automations"

urlpatterns = [
    # Existing endpoints
    path("catalog", AutomationCatalogView.as_view(), name="catalog"),
    path("jobs", ScheduledJobListView.as_view(), name="jobs"),
    path("job-runs", JobRunListView.as_view(), name="job-runs"),
    path("jobs/<str:name>", JobDetailRunsView.as_view(), name="job-detail"),
    path("notification-logs", AutomationNotificationLogListView.as_view(), name="notification-logs"),
    path("reconciliation-report", ReconciliationReportView.as_view(), name="reconciliation-report"),
    path("hub/", automation_hub_view, name="hub"),
    
    # Membership endpoints
    path("effective-role/<uuid:membership_id>/", effective_role_view, name="effective-role"),
    path("check-permission/", check_permission_view, name="check-permission"),
    path("delegations/active/<uuid:user_id>/", active_delegations_view, name="active-delegations"),
    path("delegations/<uuid:delegation_id>/revoke/", revoke_delegation_view, name="revoke-delegation"),
    
    # Contribution endpoints
    path("compliance/", compliance_view, name="compliance"),
    path("compliance/chama/<uuid:chama_id>/", chama_compliance_view, name="chama-compliance"),
    path("contributions/remind/", send_contribution_reminder_view, name="contribution-remind"),
    path("contributions/pending/<uuid:chama_id>/", pending_contributions_view, name="pending-contributions"),
    
    # Loan endpoints
    path("loans/eligibility/", loan_eligibility_view, name="loan-eligibility"),
    path("loans/queue/", loan_approval_queue_view, name="loan-queue"),
    path("loans/<uuid:loan_id>/route/", route_loan_view, name="route-loan"),
    path("loans/overdue/<uuid:chama_id>/", overdue_loans_view, name="overdue-loans"),
    path("loans/<uuid:loan_id>/notify-overdue/", send_overdue_reminder_view, name="notify-overdue"),
    
    # Security endpoints
    path("security/alerts/", security_alerts_view, name="security-alerts"),
    path("security/devices/<uuid:user_id>/", device_sessions_view, name="device-sessions"),
    path("security/devices/<uuid:session_id>/revoke/", revoke_device_session_view, name="revoke-device"),
    path("security/report/", report_suspicious_activity_view, name="report-suspicious"),
    path("security/locked/", locked_accounts_view, name="locked-accounts"),
    path("security/<uuid:user_id>/unlock/", unlock_account_view, name="unlock-account"),
    
    # Anomaly endpoints
    path("anomaly/withdrawal/", check_withdrawal_anomaly_view, name="withdrawal-anomaly"),
    path("anomaly/<uuid:chama_id>/", anomalies_view, name="anomalies"),
    
    # Audit endpoints
    path("audit/", audit_logs_view, name="audit-logs"),
    path("audit/export/", export_audit_report_view, name="export-audit"),
]
