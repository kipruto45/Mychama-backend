from django.urls import path

from apps.automations.views_frontend import (
    automation_job_detail_view,
    automation_notification_log_view,
    automations_dashboard_view,
    reconciliation_report_view,
)

app_name = "automations_frontend"

urlpatterns = [
    path("dashboard", automations_dashboard_view, name="dashboard"),
    path("dashboard/", automations_dashboard_view, name="dashboard-slash"),
    path("jobs/<str:name>", automation_job_detail_view, name="job-detail"),
    path("jobs/<str:name>/", automation_job_detail_view, name="job-detail-slash"),
    path("notification-log", automation_notification_log_view, name="notification-log"),
    path("notification-log/", automation_notification_log_view, name="notification-log-slash"),
    path("reconciliation-report", reconciliation_report_view, name="reconciliation-report"),
    path("reconciliation-report/", reconciliation_report_view, name="reconciliation-report-slash"),
]
