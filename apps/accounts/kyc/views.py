from __future__ import annotations

import mimetypes
import logging

from django.http import FileResponse
from rest_framework import permissions, status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.kyc.serializers import (
    KYCDetailsSerializer,
    KYCLocationSerializer,
    KYCReTriggerSerializer,
    KYCResubmitSerializer,
    KYCStartSerializer,
    KYCSubmitSerializer,
    KYCUploadDocumentSerializer,
    KYCUploadSelfieSerializer,
    kyc_response,
)
from apps.accounts.kyc.services import KYCWorkflowService, sync_user_access_state
from apps.accounts.kyc.audit import log_kyc_event
from apps.accounts.models import MemberKYC
from apps.accounts.serializers import MemberKYCSerializer

logger = logging.getLogger(__name__)


def _access_payload(user):
    return {
        "otp_verified": user.otp_verified,
        "tier_access": user.tier_access,
        "kyc_status": user.kyc_status,
        "financial_access_enabled": user.financial_access_enabled,
        "account_frozen": user.account_frozen,
        "account_locked_until": user.account_locked_until.isoformat() if user.account_locked_until else None,
    }


class KYCStatusView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = sync_user_access_state(request.user)
        record = (
            MemberKYC.objects.filter(user=request.user, chama__isnull=True)
            .order_by("-approved_at", "-processed_at", "-updated_at")
            .first()
        ) or (
            MemberKYC.objects.filter(user=request.user)
            .order_by("-approved_at", "-processed_at", "-updated_at")
            .first()
        )
        return Response(
            kyc_response(
                success=True,
                code="KYC_STATUS_FETCHED",
                message="KYC status retrieved successfully.",
                data={
                    "record": MemberKYCSerializer(record).data if record else None,
                    "access": _access_payload(user),
                },
            ),
            status=status.HTTP_200_OK,
        )


class KYCDocumentDownloadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    ALLOWED_ROLES = {
        "id_front_image": "id_front_image",
        "id_back_image": "id_back_image",
        "selfie_image": "selfie_image",
        "proof_of_address_image": "proof_of_address_image",
    }

    def get(self, request, kyc_id, document_role: str):
        resolved_role = self.ALLOWED_ROLES.get(str(document_role or "").strip())
        if not resolved_role:
            return Response(
                kyc_response(
                    success=False,
                    code="KYC_DOCUMENT_ROLE_INVALID",
                    message="Invalid document type.",
                    errors={"document_role": ["Invalid document type."]},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        record = MemberKYC.objects.select_related("user").filter(id=kyc_id).first()
        if not record:
            raise NotFound("KYC record not found.")

        is_owner = record.user_id == request.user.id
        if not is_owner and not request.user.is_staff:
            return Response(
                kyc_response(
                    success=False,
                    code="KYC_DOCUMENT_FORBIDDEN",
                    message="You do not have permission to access this document.",
                ),
                status=status.HTTP_403_FORBIDDEN,
            )

        field = getattr(record, resolved_role, None)
        if not field:
            raise NotFound("Document not found.")

        # Audit every access.
        log_kyc_event(
            kyc_record=record,
            event_type="document_accessed",
            code="KYC_DOCUMENT_ACCESSED",
            message="KYC document accessed.",
            actor=request.user,
            metadata={"document_role": resolved_role, "by_staff": bool(request.user.is_staff and not is_owner)},
        )

        filename = getattr(field, "name", "") or f"{resolved_role}.bin"
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        response = FileResponse(field.open("rb"), content_type=content_type)
        response["Content-Disposition"] = f'inline; filename="{filename.split("/")[-1]}"'
        return response


class KYCStartView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = KYCStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record = KYCWorkflowService.start_session(
            user=request.user,
            onboarding_path=serializer.validated_data["onboarding_path"],
            chama_id=serializer.validated_data.get("chama_id"),
        )

        return Response(
            kyc_response(
                success=True,
                code="KYC_STARTED",
                message="KYC session started successfully.",
                data={"record": MemberKYCSerializer(record).data, "access": _access_payload(request.user)},
            ),
            status=status.HTTP_201_CREATED,
        )


class KYCDetailsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = KYCDetailsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record = MemberKYC.objects.filter(
            id=serializer.validated_data["kyc_id"],
            user=request.user,
        ).first()
        if not record:
            raise NotFound("KYC session not found.")
        record = KYCWorkflowService.update_profile(record, payload=serializer.validated_data)

        return Response(
            kyc_response(
                success=True,
                code="KYC_DETAILS_SAVED",
                message="Personal details saved.",
                data={"record": MemberKYCSerializer(record).data},
            ),
            status=status.HTTP_200_OK,
        )


class KYCUploadDocumentView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = KYCUploadDocumentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record = MemberKYC.objects.filter(
            id=serializer.validated_data["kyc_id"],
            user=request.user,
        ).first()
        if not record:
            raise NotFound("KYC session not found.")
        record, upload_errors, metrics = KYCWorkflowService.attach_document(
            record,
            field_name=serializer.validated_data["document_role"],
            upload=serializer.validated_data["file"],
        )

        return Response(
            kyc_response(
                success=not upload_errors,
                code="KYC_DOCUMENT_UPLOADED" if not upload_errors else "KYC_DOCUMENT_QUALITY_FAILED",
                message="Document uploaded successfully." if not upload_errors else upload_errors[0],
                data={"record": MemberKYCSerializer(record).data, "quality": metrics},
                errors={"file": upload_errors} if upload_errors else {},
            ),
            status=status.HTTP_200_OK if not upload_errors else status.HTTP_400_BAD_REQUEST,
        )


class KYCUploadSelfieView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = KYCUploadSelfieSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record = MemberKYC.objects.filter(
            id=serializer.validated_data["kyc_id"],
            user=request.user,
        ).first()
        if not record:
            raise NotFound("KYC session not found.")
        record.selfie_image = serializer.validated_data["file"]
        # Never trust client-side liveness assertions for compliance decisions.
        # Persist them as telemetry and let the provider determine liveness.
        payload = dict(record.provider_payload or {})
        payload["client_liveness"] = {
            "blink_completed": bool(serializer.validated_data["blink_completed"]),
            "head_turn_completed": bool(serializer.validated_data["head_turn_completed"]),
            "smile_completed": bool(serializer.validated_data["smile_completed"]),
            "captured_at": request.data.get("captured_at"),
            "device": request.data.get("device"),
        }
        record.provider_payload = payload
        record.save(update_fields=["selfie_image", "provider_payload", "updated_at"])

        return Response(
            kyc_response(
                success=True,
                code="KYC_SELFIE_UPLOADED",
                message="Selfie uploaded successfully.",
                data={"record": MemberKYCSerializer(record).data},
            ),
            status=status.HTTP_200_OK,
        )


class KYCSubmitView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = KYCSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record = MemberKYC.objects.filter(
            id=serializer.validated_data["kyc_id"],
            user=request.user,
        ).first()
        if not record:
            raise NotFound("KYC session not found.")
        record = KYCWorkflowService.submit(record)
        from apps.accounts.kyc.tasks import process_kyc_submission

        process_kyc_submission.delay(str(record.id))
        return Response(
            kyc_response(
                success=True,
                code="KYC_SUBMITTED",
                message="Your KYC documents have been submitted.",
                data={"record": MemberKYCSerializer(record).data},
            ),
            status=status.HTTP_202_ACCEPTED,
        )


class KYCResubmitView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = KYCResubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record = MemberKYC.objects.filter(
            id=serializer.validated_data["kyc_id"],
            user=request.user,
        ).first()
        if not record:
            raise NotFound("KYC session not found.")
        record.resubmission_attempts += 1
        record.retry_allowed = record.rejection_attempts < 3
        record.review_note = serializer.validated_data.get("correction_note", "")
        record.save(update_fields=["resubmission_attempts", "retry_allowed", "review_note", "updated_at"])
        record = KYCWorkflowService.submit(record)
        from apps.accounts.kyc.tasks import process_kyc_submission

        process_kyc_submission.delay(str(record.id))
        return Response(
            kyc_response(
                success=True,
                code="KYC_RESUBMITTED",
                message="Your KYC documents have been submitted.",
                data={"kyc_id": str(record.id)},
            ),
            status=status.HTTP_202_ACCEPTED,
        )


class KYCLocationView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = KYCLocationSerializer(data=request.data)
        if not serializer.is_valid():
            payload = request.data if isinstance(request.data, dict) else {}
            safe_payload = {
                "kyc_id": str(payload.get("kyc_id") or ""),
                "share_location": payload.get("share_location"),
                "has_latitude": payload.get("latitude") is not None or payload.get("location_latitude") is not None,
                "has_longitude": payload.get("longitude") is not None or payload.get("location_longitude") is not None,
                "location_label_present": bool(str(payload.get("location_label") or "").strip()),
            }
            logger.warning(
                "Invalid KYC location payload",
                extra={
                    "user_id": str(getattr(request.user, "id", "")),
                    "payload": safe_payload,
                    "errors": serializer.errors,
                },
            )
            return Response(
                kyc_response(
                    success=False,
                    code="INVALID_LOCATION_PAYLOAD",
                    message="Unable to save your location.",
                    errors=serializer.errors,
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        record = MemberKYC.objects.filter(
            id=serializer.validated_data["kyc_id"],
            user=request.user,
        ).first()
        if not record:
            raise NotFound("KYC session not found.")
        record = KYCWorkflowService.update_profile(record, payload=serializer.validated_data)
        return Response(
            kyc_response(
                success=True,
                code="KYC_LOCATION_CAPTURED",
                message="Location captured successfully.",
                data={"kyc_id": str(record.id)},
            ),
            status=status.HTTP_200_OK,
        )


class KYCReasonsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, _request):
        return Response(
            kyc_response(
                success=True,
                code="KYC_REASONS_FETCHED",
                message="KYC reasons fetched successfully.",
                data={"rejection_reasons": list(set(__import__("apps.accounts.kyc.services", fromlist=["REJECTION_REASON_MAP"]).REJECTION_REASON_MAP.values()))},
            ),
            status=status.HTTP_200_OK,
        )


class KYCReTriggerView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = KYCReTriggerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record = KYCWorkflowService.mark_rekyc_required(user=request.user, reason=serializer.validated_data["reason"])
        return Response(
            kyc_response(
                success=True,
                code="KYC_RETRIGGERED",
                message="Re-verification has been triggered.",
                data={"kyc_id": str(record.id) if record else None},
            ),
            status=status.HTTP_200_OK,
        )
