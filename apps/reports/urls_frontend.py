from django.urls import path

from apps.reports import views_frontend

app_name = "reports"

urlpatterns = [
    path("", views_frontend.report_list_view, name="report_list"),
    path("financial/", views_frontend.financial_report_view, name="financial_report"),
    path("members/", views_frontend.member_report_view, name="member_report"),
    path("meetings/", views_frontend.meeting_report_view, name="meeting_report"),
    path("contributions/", views_frontend.contribution_report_view, name="contribution_report"),

    # Aliases expected by dashboards/templates
    path("financial/", views_frontend.financial_report_view, name="financial"),
    path("members/", views_frontend.member_report_view, name="member_reports"),
    path("members/", views_frontend.member_report_view, name="member_activity"),
    path("meetings/", views_frontend.meeting_report_view, name="meeting_reports"),
    path("activity-log/", views_frontend.activity_log_view, name="activity_log"),
]
