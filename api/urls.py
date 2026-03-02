from django.urls import include, path

from api.routers import v1_router
from api.views import HealthCheckView, SystemStatusView
from apps.app.calendar_api import calendar_events, calendar_ics
from apps.chama.views import (
    InviteAcceptAliasView,
    InviteCreateAliasView,
    InviteLookupAliasView,
    InviteRevokeAliasView,
)
from core.views_monitoring import dev_otp_latest

app_name = "api"

v1_patterns = [
    path("health", HealthCheckView.as_view(), name="health"),
    path("system-status", SystemStatusView.as_view(), name="system-status"),
    path("calendar/events", calendar_events, name="calendar-events"),
    path("calendar/ics", calendar_ics, name="calendar-ics"),
    path("auth/", include("apps.accounts.urls")),
    # Dev-only OTP endpoint (DEBUG ONLY - MUST BE DISABLED IN PRODUCTION)
    path("dev/otp/latest/", dev_otp_latest, name="dev-otp-latest"),
    path("invites/create", InviteCreateAliasView.as_view(), name="invite-create-alias"),
    path("invites/lookup", InviteLookupAliasView.as_view(), name="invite-lookup-alias"),
    path("invites/accept", InviteAcceptAliasView.as_view(), name="invite-accept-alias"),
    path("invites/revoke", InviteRevokeAliasView.as_view(), name="invite-revoke-alias"),
    path("chamas/", include("apps.chama.urls")),
    path("finance/", include("apps.finance.urls")),
    path("fines/", include("apps.fines.urls")),
    path("meetings/", include("apps.meetings.urls")),
    path("issues/", include("apps.issues.urls")),
    path("payments/", include("apps.payments.urls")),
    path("notifications/", include("apps.notifications.urls")),
    path("security/", include("apps.security.urls")),
    path("reports/", include("apps.reports.urls")),
    path("ai/", include("apps.ai.urls")),
    path("automations/", include("apps.automations.urls")),
    path("app/", include("apps.app.urls")),
    path("", include(v1_router.urls)),
]

urlpatterns = [
    path("v1/", include((v1_patterns, "v1"), namespace="v1")),
]
