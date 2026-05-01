"""
AI Frontend URLs for Digital Chama
"""

from django.shortcuts import render
from django.urls import path

app_name = "ai_frontend"


def ai_assistant(request):
    """AI Chat Assistant page."""
    return render(request, "ai/assistant.html")


def ai_insights(request):
    """AI Insights dashboard page."""
    return render(request, "ai/insights.html")


def fraud_alerts(request):
    """Fraud alerts management page."""
    return render(request, "ai/fraud_alerts.html")


def risk_profile_view(request):
    """Risk profile view page."""
    return render(request, "ai/risk_profile.html")


urlpatterns = [
    # AI Assistant Page
    path("", ai_assistant, name="ai_assistant"),
    path("insights/", ai_insights, name="ai_insights"),
    path("fraud-alerts/", fraud_alerts, name="fraud_alerts"),
    path("risk-profile/", risk_profile_view, name="risk_profile"),
]


