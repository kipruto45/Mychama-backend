from django.urls import path

from apps.security.views import (
    DeviceSessionListView,
    DeviceSessionRevokeAllView,
    DeviceSessionRevokeView,
    SecurityAuditLogExportView,
    SecurityAuditLogListView,
)

app_name = "security"

urlpatterns = [
    path("sessions", DeviceSessionListView.as_view(), name="sessions"),
    path(
        "sessions/<uuid:id>/revoke",
        DeviceSessionRevokeView.as_view(),
        name="session-revoke",
    ),
    path("revoke-all", DeviceSessionRevokeAllView.as_view(), name="revoke-all"),
    path("audit", SecurityAuditLogListView.as_view(), name="audit"),
    path("audit/export", SecurityAuditLogExportView.as_view(), name="audit-export"),
]
