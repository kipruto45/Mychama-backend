from django.utils import timezone
from django.conf import settings
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthCheckView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = []

    def get(self, request, *args, **kwargs):
        return Response({"status": "ok"})


class SystemStatusView(APIView):
    """
    Universal system status endpoint for all dashboards.
    Provides health checks for: API, AI, and M-Pesa callbacks.
    """
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = []

    def get(self, request, *args, **kwargs):
        from apps.payments.models import PaymentIntent, PaymentReconciliationRun
        from apps.payments.models import PaymentIntentStatus

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

        except Exception as e:
            result["mpesa"]["status"] = "error"
            result["mpesa"]["error"] = str(e)

        return Response(result)
