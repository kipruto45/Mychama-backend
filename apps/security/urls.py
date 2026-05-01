from django.urls import path

from apps.security.views import (
    ApiInventoryView,
    CurrentUserAccessView,
    DeviceSessionListView,
    DeviceSessionRevokeAllView,
    DeviceSessionRevokeView,
    PinSetView,
    PinStatusView,
    PinVerifyView,
    RBACRoleListView,
    SecurityAuditLogExportView,
    SecurityAuditLogListView,
    TrustedDeviceCheckView,
    TrustedDeviceDeleteView,
    TrustedDeviceListCreateView,
    TrustedDeviceRevokeAllView,
)

app_name = "security"

urlpatterns = [
    path("api-inventory", ApiInventoryView.as_view(), name="api-inventory"),
    path("sessions", DeviceSessionListView.as_view(), name="sessions"),
    path(
        "sessions/<uuid:id>/revoke",
        DeviceSessionRevokeView.as_view(),
        name="session-revoke",
    ),
    path("revoke-all", DeviceSessionRevokeAllView.as_view(), name="revoke-all"),
    path("audit", SecurityAuditLogListView.as_view(), name="audit"),
    path("audit/export", SecurityAuditLogExportView.as_view(), name="audit-export"),
    path("pins", PinStatusView.as_view(), name="pin-status"),
    path("pins/set", PinSetView.as_view(), name="pin-set"),
    path("pins/verify", PinVerifyView.as_view(), name="pin-verify"),
    path("rbac/roles", RBACRoleListView.as_view(), name="rbac-roles"),
    path("rbac/access", CurrentUserAccessView.as_view(), name="rbac-access"),
    path("trusted-devices", TrustedDeviceListCreateView.as_view(), name="trusted-devices"),
    path(
        "trusted-devices/<uuid:id>",
        TrustedDeviceDeleteView.as_view(),
        name="trusted-device-delete",
    ),
    path(
        "trusted-devices/revoke-all",
        TrustedDeviceRevokeAllView.as_view(),
        name="trusted-device-revoke-all",
    ),
    path(
        "trusted-devices/check",
        TrustedDeviceCheckView.as_view(),
        name="trusted-device-check",
    ),
]
