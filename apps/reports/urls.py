from django.urls import path

from apps.reports.views import (
    AllReportsView,
    ChamaHealthScoreView,
    ChamaSummaryReportView,
    CohortAnalysisView,
    CollectionForecastView,
    DefaulterRiskView,
    LoanApprovalsLogReportView,
    LoanMonthlySummaryReportView,
    LoanScheduleReportView,
    LoanStatementReportView,
    MemberStatementReportView,
    MyReportsView,
    ReportDownloadView,
    ReportPreviewView,
    ReportRequestView,
    ReportRunDetailView,
    ReportRunListView,
    StatementDownloadHistoryView,
)

app_name = "reports"

urlpatterns = [
    # Existing endpoints
    path(
        "member-statement", MemberStatementReportView.as_view(), name="member-statement"
    ),
    path("loan-statement", LoanStatementReportView.as_view(), name="loan-statement"),
    path("chama-summary", ChamaSummaryReportView.as_view(), name="chama-summary"),
    path(
        "loan-monthly-summary",
        LoanMonthlySummaryReportView.as_view(),
        name="loan-monthly-summary",
    ),
    path("loan-schedule", LoanScheduleReportView.as_view(), name="loan-schedule"),
    path(
        "loan-approvals-log",
        LoanApprovalsLogReportView.as_view(),
        name="loan-approvals-log",
    ),
    path("chama-health", ChamaHealthScoreView.as_view(), name="chama-health"),
    path(
        "collection-forecast",
        CollectionForecastView.as_view(),
        name="collection-forecast",
    ),
    path("defaulter-risk", DefaulterRiskView.as_view(), name="defaulter-risk"),
    path("cohort-analysis", CohortAnalysisView.as_view(), name="cohort-analysis"),
    path(
        "download-history",
        StatementDownloadHistoryView.as_view(),
        name="download-history",
    ),
    path("runs", ReportRunListView.as_view(), name="report-runs"),
    path("runs/<uuid:id>", ReportRunDetailView.as_view(), name="report-run-detail"),
    
    # New endpoints for Flutter Reports Service
    path("request/", ReportRequestView.as_view(), name="report-request"),
    path("my/", MyReportsView.as_view(), name="my-reports"),
    path("all/", AllReportsView.as_view(), name="all-reports"),
    path("preview/<str:report_type>/", ReportPreviewView.as_view(), name="report-preview"),
    path("download/<int:report_id>/", ReportDownloadView.as_view(), name="report-download"),
]
