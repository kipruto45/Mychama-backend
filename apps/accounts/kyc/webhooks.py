from __future__ import annotations

import hmac
import json

from django.conf import settings
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import MemberKYC
from apps.accounts.kyc.tasks import provider_webhook_followup
from core.encryption import field_encryption_service


class SmileIdentityWebhookView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def _verify_signature(self, request) -> bool:
        shared_secret = str(getattr(settings, "SMILE_IDENTITY_WEBHOOK_SECRET", "")).strip()
        if not shared_secret:
            return True
        received = str(request.headers.get("X-Smile-Signature", "")).strip()
        expected = hmac.new(shared_secret.encode("utf-8"), request.body, "sha256").hexdigest()
        return bool(received) and hmac.compare_digest(received, expected)

    def post(self, request):
        if not self._verify_signature(request):
            return Response(
                {
                    "success": False,
                    "code": "KYC_WEBHOOK_SIGNATURE_INVALID",
                    "message": "Invalid webhook signature.",
                    "errors": {},
                    "data": {},
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        reference_id = str(request.data.get("reference_id", "")).strip()
        if not reference_id:
            return Response({"success": False, "code": "KYC_WEBHOOK_INVALID", "message": "Invalid webhook payload.", "errors": {"reference_id": ["This field is required."]}, "data": {}}, status=status.HTTP_400_BAD_REQUEST)
        kyc = MemberKYC.objects.filter(auto_verification_reference=reference_id).first()
        if not kyc:
            return Response({"success": False, "code": "KYC_NOT_FOUND", "message": "Verification record not found.", "errors": {}, "data": {}}, status=status.HTTP_404_NOT_FOUND)
        raw_payload = request.data if isinstance(request.data, dict) else {"payload": request.data}
        encrypted = field_encryption_service.encrypt(json.dumps(raw_payload, separators=(",", ":"), ensure_ascii=False))
        # Store only a minimal non-sensitive summary in plain JSON.
        kyc.provider_result = {"provider": "smile_identity", "reference_id": reference_id, "received": True}
        kyc.provider_result_encrypted = encrypted
        kyc.verification_result = kyc.provider_result
        kyc.verification_result_encrypted = encrypted
        kyc.save(update_fields=["provider_result", "provider_result_encrypted", "verification_result", "verification_result_encrypted", "updated_at"])
        provider_webhook_followup.delay(str(kyc.id))
        return Response({"success": True, "code": "KYC_WEBHOOK_ACCEPTED", "message": "Webhook processed successfully.", "errors": {}, "data": {"kyc_id": str(kyc.id)}})
