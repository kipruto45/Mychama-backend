"""
Monitoring URLs for Digital Chama System
Health checks and metrics endpoints
"""

from django.urls import path
from core.views_monitoring import (
    health_check,
    detailed_health_check,
    metrics_prometheus,
    notifications_health_check,
    payments_health_check,
    dev_otp_latest,
)

app_name = "monitoring"

urlpatterns = [
    # Basic health check
    path("", health_check, name="health_check"),

    # Detailed health check with metrics
    path("detailed/", detailed_health_check, name="detailed_health_check"),

    # Prometheus metrics endpoint
    path("metrics/", metrics_prometheus, name="prometheus_metrics"),
    
    # Notifications health check
    path("notifications/", notifications_health_check, name="notifications_health_check"),

    # Payments health check (M-Pesa)
    path("payments/", payments_health_check, name="payments_health_check"),
]