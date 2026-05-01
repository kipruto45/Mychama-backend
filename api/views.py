import logging

from django.conf import settings
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from core.api_response import success_response

logger = logging.getLogger(__name__)


health_check_response = inline_serializer(
    name="HealthCheckResponse",
    fields={
        "success": serializers.BooleanField(default=True),
        "data": inline_serializer(
            name="HealthCheckPayload",
            fields={"status": serializers.CharField()},
        ),
    },
)

system_status_response = inline_serializer(
    name="SystemStatusResponse",
    fields={
        "success": serializers.BooleanField(default=True),
        "data": serializers.DictField(),
    },
)


class HealthCheckView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = health_check_response

    @extend_schema(
        tags=["System"],
        operation_id="get_health_check",
        responses={200: health_check_response},
    )
    def get(self, request, *args, **kwargs):
        return success_response(data={"status": "ok"})


class SystemStatusView(APIView):
    """
    Universal system status endpoint for all dashboards.
    Provides health checks for: API, AI, and M-Pesa callbacks.
    """
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = system_status_response

    @extend_schema(
        tags=["System"],
        operation_id="get_system_status",
        responses={200: system_status_response},
    )
    def get(self, request, *args, **kwargs):
        from apps.payments.models import (
            PaymentIntent,
            PaymentIntentStatus,
            PaymentReconciliationRun,
        )

        now = timezone.now()
        result = {
            "api": {
                "status": "operational",
                "timestamp": now.isoformat(),
            },
            "ai": {
                "status": "operational",
                "model": getattr(settings, "AI_CHAT_MODEL", "gpt-4o-mini"),
            },
            "mpesa": {
                "status": "unknown",
                "last_callback": None,
                "pending_transactions": 0,
                "last_reconciliation": None,
            },
        }

        # Get M-Pesa callback health
        try:
            # Find the most recent callback (PaymentIntent with callback received)
            last_callback = PaymentIntent.objects.filter(
                status__in=[
                    PaymentIntentStatus.SUCCESS,
                    PaymentIntentStatus.FAILED,
                    PaymentIntentStatus.PENDING,
                ]
            ).order_by("-updated_at").first()

            if last_callback:
                result["mpesa"]["last_callback"] = last_callback.updated_at.isoformat()

                # Check if last callback is stale (> 10 minutes for pending)
                time_since_callback = (now - last_callback.updated_at).total_seconds()
                if last_callback.status == PaymentIntentStatus.PENDING and time_since_callback > 600:
                    result["mpesa"]["status"] = "degraded"
                    result["mpesa"]["warning"] = "Pending transactions older than 10 minutes"
                elif last_callback.status in [PaymentIntentStatus.SUCCESS, PaymentIntentStatus.FAILED]:
                    result["mpesa"]["status"] = "operational"

            # Count pending transactions
            pending_count = PaymentIntent.objects.filter(
                status=PaymentIntentStatus.PENDING
            ).count()
            result["mpesa"]["pending_transactions"] = pending_count

            # Get last reconciliation run
            last_recon = PaymentReconciliationRun.objects.order_by("-created_at").first()
            if last_recon:
                result["mpesa"]["last_reconciliation"] = {
                    "id": str(last_recon.id),
                    "status": last_recon.status,
                    "created_at": last_recon.created_at.isoformat(),
                }

        except Exception:  # noqa: BLE001
            logger.exception("Failed to build M-Pesa system status payload")
            result["mpesa"]["status"] = "error"
            result["mpesa"]["message"] = "Unable to load M-Pesa status."

        return success_response(data=result)
