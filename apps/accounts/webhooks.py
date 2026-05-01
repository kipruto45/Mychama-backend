from __future__ import annotations

from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.webhook_service import KYCWebhookService


@method_decorator(csrf_exempt, name="dispatch")
class MemberKYCWebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request, *args, **kwargs):
        provider = (
            request.query_params.get("provider")
            or request.headers.get("X-KYC-Provider")
            or (request.data.get("provider") if isinstance(request.data, dict) else "")
            or "generic"
        )
        signature_header = getattr(
            settings,
            "KYC_CALLBACK_SIGNATURE_HEADER",
            "X-KYC-Signature",
        )
        signature = (
            request.headers.get(signature_header)
            or request.headers.get("X-Signature")
            or request.headers.get("X-Hub-Signature-256")
        )
        payload_bytes = request.body or b"{}"
        if not KYCWebhookService.verify_signature(
            provider=provider,
            payload_bytes=payload_bytes,
            received_signature=signature,
        ):
            return Response(
                {"detail": "Invalid webhook signature."},
                status=status.HTTP_403_FORBIDDEN,
            )

        payload = request.data if isinstance(request.data, dict) else {}
        if not payload:
            return Response(
                {"detail": "Invalid callback payload."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = KYCWebhookService.process_callback(provider=provider, payload=payload)
        response_status = status.HTTP_200_OK
        if result.get("reason") == "kyc_not_found":
            response_status = status.HTTP_202_ACCEPTED
        elif result.get("reason") == "missing_reference":
            response_status = status.HTTP_400_BAD_REQUEST
        return Response(result, status=response_status)
