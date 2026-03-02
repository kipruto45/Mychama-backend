from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from apps.accounts.urls import urlpatterns as account_api_patterns
from apps.ai import views as ai_views
from apps.ai.urls import urlpatterns as ai_api_patterns
from apps.chama.urls import urlpatterns as chama_api_patterns
from apps.payments.urls import urlpatterns as payments_api_patterns
from apps.admin_management.urls import urlpatterns as admin_management_patterns
from apps.billing.urls import urlpatterns as billing_api_patterns

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/public-ai/chat/stream", ai_views.public_ai_chat_stream),
    path("api/public-ai/suggestions", ai_views.public_ai_suggestions),
    path("api/ai/chat/stream", ai_views.ai_chat_stream),
    path("api/ai/chat/stop", ai_views.ai_chat_stop),
    path("api/me/context", ai_views.ai_me_context),
    # Legacy-compatible un-namespaced API wiring (kept for existing clients/tests).
    path("api/v1/auth/", include(account_api_patterns)),
    path("api/v1/chamas/", include(chama_api_patterns)),
    path("api/v1/payments/", include(payments_api_patterns)),
    path("api/v1/ai/", include(ai_api_patterns)),
    path("api/v1/admin/", include(admin_management_patterns)),
    path("api/v1/billing/", include(billing_api_patterns)),
    path("api/", include("api.urls")),
    path("api/auth/", include("rest_framework.urls")),
    # API Schema
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/schema/swagger-ui/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path(
        "api/schema/redoc/",
        SpectacularRedocView.as_view(url_name="schema"),
        name="redoc",
    ),
    # Monitoring endpoints
    path("health/", include("core.urls_monitoring")),
    # Frontend Templates
    path("", include(("apps.accounts.urls_frontend", "auth"), namespace="auth")),
    path("dashboards/", include("apps.accounts.urls_dashboards")),
    path("chama/", include("apps.chama.urls_frontend")),
    path("finance/", include("apps.finance.urls_frontend")),
    path("meetings/", include("apps.meetings.urls_frontend")),
    path("reports/", include("apps.reports.urls_frontend")),
    path("payments/", include("apps.payments.urls_frontend")),
    path("notifications/", include("apps.notifications.urls_frontend")),
    path("issues/", include("apps.issues.urls_frontend")),
    path("ai/", include("apps.ai.urls_frontend")),
    path("automations/", include("apps.automations.urls_frontend")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

# Error handlers
handler400 = "core.views_errors.error_400_view"
handler403 = "core.views_errors.error_403_view"
handler404 = "core.views_errors.error_404_view"
handler500 = "core.views_errors.error_500_view"
