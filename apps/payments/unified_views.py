"""
Unified Payment API Views for MyChama.

Views for handling all payment operations through a single interface.
"""

from __future__ import annotations

import io
import logging
from datetime import timedelta

from django.conf import settings
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chama.models import Chama
from apps.payments.unified_models import (
    PaymentIntent,
    PaymentMethod,
    PaymentReceipt,
    PaymentReceiptDownloadToken,
)
from apps.payments.unified_serializers import (
    BankPaymentProofUploadSerializer,
    BankPaymentVerifySerializer,
    CashPaymentVerifySerializer,
    ManualPaymentApprovalPolicySerializer,
    ManualPaymentRejectSerializer,
    PaymentAuditLogSerializer,
    PaymentConfirmSerializer,
    PaymentDisputeCreateSerializer,
    PaymentDisputeDecisionSerializer,
    PaymentDisputeRecordSerializer,
    PaymentIntentCreateSerializer,
    PaymentIntentResponseSerializer,
    PaymentListSerializer,
    PaymentReceiptSerializer,
    PaymentReconciliationCaseSerializer,
    PaymentReconciliationListSerializer,
    PaymentReconciliationResolveSerializer,
    PaymentRefundDecisionSerializer,
    PaymentRefundRecordSerializer,
    PaymentRefundSerializer,
    PaymentSettlementCreateSerializer,
    PaymentSettlementSerializer,
    PaymentStatementImportRequestSerializer,
    PaymentStatementImportSerializer,
    PaymentStatusResponseSerializer,
    PaymentTransactionSerializer,
    PaymentWebhookSerializer,
)
from apps.payments.unified_services import PaymentServiceError, UnifiedPaymentService
from core.api_response import ApiResponse
from core.permissions import IsAuthenticatedAndActive
from core.schema import error_response_serializer

logger = logging.getLogger(__name__)
payment_success_response = inline_serializer(
    name="UnifiedPaymentSuccessEnvelope",
    fields={
        "success": serializers.BooleanField(default=True),
        "data": serializers.JSONField(required=False),
        "message": serializers.CharField(required=False, allow_blank=True),
        "meta": serializers.JSONField(required=False),
    },
)
payment_error_response = error_response_serializer(name="UnifiedPaymentErrorEnvelope")


def _can_access_payment(user, intent: PaymentIntent) -> bool:
    if not user or not user.is_authenticated:
        return False
    if intent.user_id == user.id or user.is_staff or user.is_superuser:
        return True
    membership = UnifiedPaymentService._get_membership(intent.chama, user)
    return bool(membership and membership.role in {"SUPERADMIN", "ADMIN", "CHAMA_ADMIN", "TREASURER"})


class PaymentCreateIntentView(APIView):
    """
    Create payment intent.

    POST /api/v1/payments/create-intent/
    """

    permission_classes = [IsAuthenticated]
    serializer_class = PaymentIntentCreateSerializer

    @extend_schema(
        tags=["Payments"],
        operation_id="create_unified_payment_intent",
        request=PaymentIntentCreateSerializer,
        responses={201: payment_success_response, 400: payment_error_response},
    )
    def post(self, request: Request) -> Response:
        """Create a new payment intent."""
        serializer = PaymentIntentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            chama_id = serializer.validated_data["chama_id"]
            chama = Chama.objects.get(id=chama_id)

            # Prepare method-specific kwargs
            method_kwargs = {}
            payment_method = serializer.validated_data["payment_method"]

            if payment_method == PaymentMethod.MPESA:
                method_kwargs["phone"] = serializer.validated_data.get("phone", "")
            elif payment_method == PaymentMethod.CASH:
                method_kwargs["received_by"] = serializer.validated_data.get("received_by")
                method_kwargs["notes"] = serializer.validated_data.get("notes", "")
            elif payment_method == PaymentMethod.BANK:
                method_kwargs["bank_name"] = serializer.validated_data.get("bank_name", "")
                method_kwargs["account_number"] = serializer.validated_data.get("account_number", "")
                method_kwargs["account_name"] = serializer.validated_data.get("account_name", "")
                method_kwargs["transfer_reference"] = serializer.validated_data.get("transfer_reference", "")
                method_kwargs["notes"] = serializer.validated_data.get("notes", "")

            intent = UnifiedPaymentService.create_payment_intent(
                chama=chama,
                user=request.user,
                amount=serializer.validated_data["amount"],
                currency=serializer.validated_data["currency"],
                payment_method=payment_method,
                purpose=serializer.validated_data["purpose"],
                purpose_id=serializer.validated_data.get("purpose_id"),
                description=serializer.validated_data.get("description", ""),
                contribution_id=serializer.validated_data.get("contribution_id"),
                provider_name=serializer.validated_data.get("provider"),
                idempotency_key=serializer.validated_data.get("idempotency_key"),
                metadata=serializer.validated_data.get("metadata"),
                contribution_type_id=serializer.validated_data.get("contribution_type_id"),
                **method_kwargs,
            )

            response_serializer = PaymentIntentResponseSerializer(intent)
            return ApiResponse.success(
                data=response_serializer.data,
                message="Payment intent created successfully",
                status_code=status.HTTP_201_CREATED,
            )

        except Chama.DoesNotExist:
            return ApiResponse.error(
                message="Chama not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        except PaymentServiceError as e:
            return ApiResponse.error(
                message=str(e),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.error("Payment intent creation failed: %s", e)
            return ApiResponse.error(
                message="Failed to create payment intent",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PaymentStatusView(APIView):
    """
    Get payment status.

    GET /api/v1/payments/{id}/status/
    """

    permission_classes = [IsAuthenticated]
    serializer_class = PaymentStatusResponseSerializer

    @extend_schema(
        tags=["Payments"],
        operation_id="retrieve_unified_payment_status",
        responses={200: payment_success_response, 403: payment_error_response, 404: payment_error_response},
    )
    def get(self, request: Request, id: str) -> Response:
        """Get payment intent status."""
        try:
            intent = UnifiedPaymentService.get_payment_status(id)

            if not _can_access_payment(request.user, intent):
                return ApiResponse.error(
                    message="Not authorized to view this payment",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            transactions = intent.transactions.all()
            receipt = getattr(intent, "receipt", None)
            audit_logs = intent.audit_logs.all()[:20]

            data = {
                "intent": PaymentIntentResponseSerializer(intent).data,
                "transactions": PaymentTransactionSerializer(transactions, many=True).data,
                "receipt": PaymentReceiptSerializer(receipt).data if receipt else None,
                "audit_logs": PaymentAuditLogSerializer(audit_logs, many=True).data,
            }

            return ApiResponse.success(data=data)

        except PaymentServiceError as e:
            return ApiResponse.error(
                message=str(e),
                status_code=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            logger.error("Failed to get payment status: %s", e)
            return ApiResponse.error(
                message="Failed to get payment status",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PaymentVerifyView(APIView):
    """
    Verify payment with provider.

    POST /api/v1/payments/{id}/verify/
    """

    permission_classes = [IsAuthenticated]
    serializer_class = PaymentIntentResponseSerializer

    @extend_schema(
        tags=["Payments"],
        operation_id="verify_unified_payment",
        responses={200: payment_success_response, 400: payment_error_response},
    )
    def post(self, request: Request, id: str) -> Response:
        """Verify payment status with provider."""
        try:
            intent = UnifiedPaymentService.get_payment_status(id)

            if not _can_access_payment(request.user, intent):
                return ApiResponse.error(
                    message="Not authorized to verify this payment",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            intent = UnifiedPaymentService.verify_payment(id)
            response_serializer = PaymentIntentResponseSerializer(intent)
            return ApiResponse.success(
                data=response_serializer.data,
                message="Payment verified successfully",
            )

        except PaymentServiceError as e:
            return ApiResponse.error(
                message=str(e),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.error("Payment verification failed: %s", e)
            return ApiResponse.error(
                message="Failed to verify payment",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PaymentWebhookView(APIView):
    """
    Handle payment webhooks.

    POST /api/v1/payments/webhook/
    """

    permission_classes = []  # No authentication for webhooks
    authentication_classes = []
    serializer_class = PaymentWebhookSerializer

    @extend_schema(
        tags=["Payments"],
        operation_id="process_unified_payment_webhook",
        request=OpenApiTypes.BINARY,
        responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT, 500: OpenApiTypes.OBJECT},
    )
    def post(self, request: Request) -> Response:
        """Process webhook from payment provider."""
        try:
            payment_method = request.query_params.get("payment_method", "mpesa")
            provider = request.query_params.get("provider", "safaricom")

            payload = request.body
            signature = None
            if payment_method == "mpesa":
                header_name = getattr(settings, "MPESA_CALLBACK_SIGNATURE_HEADER", "X-MPESA-SIGNATURE")
                signature = request.headers.get(header_name)
            elif provider == "stripe":
                signature = request.headers.get("Stripe-Signature")

            source_ip = request.META.get("REMOTE_ADDR")
            safe_headers: dict[str, str] = {}
            for key, value in (request.META or {}).items():
                if not isinstance(key, str):
                    continue
                if not (
                    key.startswith("HTTP_")
                    or key in {"REMOTE_ADDR", "CONTENT_TYPE", "CONTENT_LENGTH", "REQUEST_METHOD", "PATH_INFO"}
                ):
                    continue
                try:
                    safe_headers[key] = str(value)
                except Exception:  # pragma: no cover
                    continue

            webhook_log = UnifiedPaymentService.process_webhook(
                payment_method=payment_method,
                provider_name=provider,
                payload=payload,
                signature=signature,
                headers=safe_headers,
                source_ip=source_ip,
            )

            if webhook_log.processed:
                return Response(
                    {"status": "success", "message": "Webhook processed"},
                    status=status.HTTP_200_OK,
                )
            else:
                return Response(
                    {
                        "status": "error",
                        "message": webhook_log.processing_error or "Processing failed",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        except Exception as e:
            logger.error("Webhook processing failed: %s", e)
            return Response(
                {"status": "error", "message": "Webhook processing failed"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PaymentReceiptView(APIView):
    """
    Get payment receipt.

    GET /api/v1/payments/{id}/receipt/
    """

    permission_classes = [IsAuthenticated]
    serializer_class = PaymentReceiptSerializer

    @extend_schema(
        tags=["Payments"],
        operation_id="retrieve_unified_payment_receipt",
        responses={200: payment_success_response, 403: payment_error_response, 404: payment_error_response},
    )
    def get(self, request: Request, id: str) -> Response:
        """Get payment receipt."""
        try:
            receipt = UnifiedPaymentService.get_payment_receipt(id)

            if not _can_access_payment(request.user, receipt.payment_intent):
                return ApiResponse.error(
                    message="Not authorized to view this receipt",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            serializer = PaymentReceiptSerializer(receipt)
            return ApiResponse.success(data=serializer.data)

        except PaymentServiceError as e:
            return ApiResponse.error(
                message=str(e),
                status_code=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            logger.error("Failed to get receipt: %s", e)
            return ApiResponse.error(
                message="Failed to get receipt",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


def _render_receipt_pdf(*, receipt: PaymentReceipt) -> bytes:
    from apps.payments.receipt_pdf import render_receipt_pdf

    return render_receipt_pdf(receipt=receipt)


class PaymentReceiptPdfLinkView(APIView):
    """
    Create a short-lived URL to download a receipt PDF without Authorization headers.

    POST /api/v1/payments/{id}/receipt/pdf-link/
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Payments"],
        operation_id="create_unified_payment_receipt_pdf_link",
        responses={200: payment_success_response, 403: payment_error_response, 404: payment_error_response},
    )
    def post(self, request: Request, id: str) -> Response:
        try:
            receipt = UnifiedPaymentService.get_payment_receipt(id)
            if not _can_access_payment(request.user, receipt.payment_intent):
                return ApiResponse.error(
                    message="Not authorized to view this receipt",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            from django.utils import timezone

            expires_at = timezone.now() + timedelta(minutes=5)
            token = PaymentReceiptDownloadToken.objects.create(
                payment_intent=receipt.payment_intent,
                requested_by=request.user,
                expires_at=expires_at,
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=str(request.META.get("HTTP_USER_AGENT") or "")[:500],
                created_by=request.user,
                updated_by=request.user,
            )

            from django.urls import NoReverseMatch, reverse

            try:
                download_path = reverse("payments:payment-receipt-pdf-download", args=[token.token])
            except NoReverseMatch:
                download_path = reverse("payment-receipt-pdf-download", args=[token.token])
            return ApiResponse.success(
                data={
                    "download_url": request.build_absolute_uri(download_path),
                    "expires_at": expires_at.isoformat(),
                }
            )
        except PaymentServiceError as e:
            return ApiResponse.error(message=str(e), status_code=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error("Failed to create receipt pdf link: %s", e)
            return ApiResponse.error(
                message="Failed to create receipt download link",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PaymentReceiptPdfDownloadView(APIView):
    """
    Download a receipt PDF using a short-lived token.

    GET /api/v1/payments/receipt/pdf/{token}/
    """

    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Payments"],
        operation_id="download_unified_payment_receipt_pdf",
        responses={200: OpenApiTypes.BINARY, 404: payment_error_response},
    )
    def get(self, request: Request, token: str) -> Response:
        from django.db import transaction
        from django.http import HttpResponse
        from django.utils import timezone

        with transaction.atomic():
            token_record = (
                PaymentReceiptDownloadToken.objects.select_for_update()
                .select_related("payment_intent", "payment_intent__chama", "requested_by")
                .filter(token=token)
                .first()
            )
            if not token_record:
                return ApiResponse.error(message="Download link not found", status_code=status.HTTP_404_NOT_FOUND)
            if token_record.consumed_at:
                return ApiResponse.error(message="Download link has already been used", status_code=status.HTTP_404_NOT_FOUND)
            if token_record.expires_at and token_record.expires_at < timezone.now():
                return ApiResponse.error(message="Download link expired", status_code=status.HTTP_404_NOT_FOUND)

            receipt = PaymentReceipt.objects.filter(payment_intent=token_record.payment_intent).first()
            if not receipt:
                return ApiResponse.error(message="Receipt not found", status_code=status.HTTP_404_NOT_FOUND)

            pdf_bytes = _render_receipt_pdf(receipt=receipt)
            token_record.consumed_at = timezone.now()
            token_record.save(update_fields=["consumed_at", "updated_at"])

        filename = f"MyChama-Receipt-{receipt.receipt_number}.pdf"
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class PaymentListView(APIView):
    """
    List payments.

    GET /api/v1/payments/list/
    """

    permission_classes = [IsAuthenticated]
    serializer_class = PaymentListSerializer

    @extend_schema(
        tags=["Payments"],
        operation_id="list_unified_payments",
        responses={200: payment_success_response},
    )
    def get(self, request: Request) -> Response:
        """List user's payments."""
        try:
            chama_id = request.query_params.get("chama_id")
            payment_method = request.query_params.get("payment_method")
            status_filter = request.query_params.get("status")
            limit = int(request.query_params.get("limit", 50))

            payments = UnifiedPaymentService.get_user_payments(
                user=request.user,
                chama_id=chama_id,
                payment_method=payment_method,
                status=status_filter,
                limit=limit,
            )

            serializer = PaymentListSerializer(payments, many=True)
            return ApiResponse.success(data={"payments": serializer.data})

        except Exception as e:
            logger.error("Failed to list payments: %s", e)
            return ApiResponse.error(
                message="Failed to list payments",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PaymentRefundRequestView(APIView):
    """Request a refund for a verified payment."""

    permission_classes = [IsAuthenticatedAndActive]
    serializer_class = PaymentRefundSerializer

    @extend_schema(
        tags=["Payments"],
        operation_id="request_unified_payment_refund",
        request=PaymentRefundSerializer,
        responses={201: payment_success_response, 400: payment_error_response},
    )
    def post(self, request: Request) -> Response:
        serializer = PaymentRefundSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            refund = UnifiedPaymentService.request_refund(
                intent_id=serializer.validated_data["intent_id"],
                actor=request.user,
                amount=serializer.validated_data.get("amount"),
                reason=serializer.validated_data.get("reason", ""),
                idempotency_key=serializer.validated_data.get("idempotency_key", ""),
            )
            return ApiResponse.success(
                data=PaymentRefundRecordSerializer(refund).data,
                message="Refund request submitted",
                status_code=status.HTTP_201_CREATED,
            )
        except PaymentServiceError as e:
            return ApiResponse.error(message=str(e), status_code=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error("Refund request failed: %s", e)
            return ApiResponse.error(
                message="Failed to request refund",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PaymentRefundApproveView(APIView):
    """Approve or reject a pending refund request."""

    permission_classes = [IsAuthenticatedAndActive]
    serializer_class = PaymentRefundDecisionSerializer

    @extend_schema(
        tags=["Payments"],
        operation_id="approve_unified_payment_refund",
        request=PaymentRefundDecisionSerializer,
        responses={200: payment_success_response, 400: payment_error_response},
    )
    def post(self, request: Request, id: str) -> Response:
        serializer = PaymentRefundDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            refund = UnifiedPaymentService.approve_refund(
                refund_id=id,
                actor=request.user,
                approve=serializer.validated_data.get("approve", True),
                note=serializer.validated_data.get("note", ""),
            )
            return ApiResponse.success(
                data=PaymentRefundRecordSerializer(refund).data,
                message="Refund decision recorded",
            )
        except PaymentServiceError as e:
            return ApiResponse.error(message=str(e), status_code=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error("Refund approval failed: %s", e)
            return ApiResponse.error(
                message="Failed to review refund",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PaymentRefundProcessView(APIView):
    """Process an approved refund."""

    permission_classes = [IsAuthenticatedAndActive]
    serializer_class = PaymentRefundRecordSerializer

    @extend_schema(
        tags=["Payments"],
        operation_id="process_unified_payment_refund",
        responses={200: payment_success_response, 400: payment_error_response},
    )
    def post(self, request: Request, id: str) -> Response:
        try:
            refund = UnifiedPaymentService.process_refund(
                refund_id=id,
                actor=request.user,
            )
            return ApiResponse.success(
                data=PaymentRefundRecordSerializer(refund).data,
                message="Refund processed successfully",
            )
        except PaymentServiceError as e:
            return ApiResponse.error(message=str(e), status_code=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error("Refund processing failed: %s", e)
            return ApiResponse.error(
                message="Failed to process refund",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PaymentRefundListView(APIView):
    """List refunds for a chama."""

    permission_classes = [IsAuthenticatedAndActive]
    serializer_class = PaymentRefundRecordSerializer

    @extend_schema(
        tags=["Payments"],
        operation_id="list_unified_payment_refunds",
        responses={200: payment_success_response, 400: payment_error_response},
    )
    def get(self, request: Request) -> Response:
        chama_id = request.query_params.get("chama_id")
        if not chama_id:
            return ApiResponse.error(
                message="chama_id is required",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        try:
            chama = Chama.objects.get(id=chama_id)
            refunds = UnifiedPaymentService.list_refunds(
                chama=chama,
                actor=request.user,
                status_filter=request.query_params.get("status"),
                limit=int(request.query_params.get("limit", 100)),
            )
            return ApiResponse.success(
                data={"refunds": PaymentRefundRecordSerializer(refunds, many=True).data}
            )
        except Chama.DoesNotExist:
            return ApiResponse.error(message="Chama not found", status_code=status.HTTP_404_NOT_FOUND)
        except PaymentServiceError as e:
            return ApiResponse.error(message=str(e), status_code=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error("Refund list failed: %s", e)
            return ApiResponse.error(
                message="Failed to load refunds",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ManualPaymentApprovalPolicyView(APIView):
    """Read or update manual payment approval policy."""

    permission_classes = [IsAuthenticatedAndActive]
    serializer_class = ManualPaymentApprovalPolicySerializer

    @extend_schema(
        tags=["Payments"],
        operation_id="get_manual_payment_approval_policy",
        responses={200: payment_success_response, 400: payment_error_response},
    )
    def get(self, request: Request) -> Response:
        chama_id = request.query_params.get("chama_id")
        if not chama_id:
            return ApiResponse.error(
                message="chama_id is required",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        try:
            chama = Chama.objects.get(id=chama_id)
            policy = UnifiedPaymentService.get_manual_approval_policy(
                chama=chama,
                actor=request.user,
            )
            return ApiResponse.success(data=ManualPaymentApprovalPolicySerializer(policy).data)
        except Chama.DoesNotExist:
            return ApiResponse.error(message="Chama not found", status_code=status.HTTP_404_NOT_FOUND)
        except PaymentServiceError as e:
            return ApiResponse.error(message=str(e), status_code=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error("Manual policy load failed: %s", e)
            return ApiResponse.error(
                message="Failed to load manual payment policy",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        tags=["Payments"],
        operation_id="patch_manual_payment_approval_policy",
        request=ManualPaymentApprovalPolicySerializer,
        responses={200: payment_success_response, 400: payment_error_response},
    )
    def patch(self, request: Request) -> Response:
        chama_id = request.data.get("chama_id")
        if not chama_id:
            return ApiResponse.error(
                message="chama_id is required",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        try:
            chama = Chama.objects.get(id=chama_id)
            existing_policy = UnifiedPaymentService.get_manual_approval_policy(
                chama=chama,
                actor=request.user,
            )
            policy_payload = request.data.copy()
            policy_payload.pop("chama_id", None)
            serializer = ManualPaymentApprovalPolicySerializer(
                existing_policy,
                data=policy_payload,
                partial=True,
            )
            serializer.is_valid(raise_exception=True)
            policy = UnifiedPaymentService.update_manual_approval_policy(
                chama=chama,
                actor=request.user,
                payload=serializer.validated_data,
            )
            return ApiResponse.success(
                data=ManualPaymentApprovalPolicySerializer(policy).data,
                message="Manual payment policy updated",
            )
        except Chama.DoesNotExist:
            return ApiResponse.error(message="Chama not found", status_code=status.HTTP_404_NOT_FOUND)
        except PaymentServiceError as e:
            return ApiResponse.error(message=str(e), status_code=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error("Manual policy update failed: %s", e)
            return ApiResponse.error(
                message="Failed to update manual payment policy",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PaymentStatementImportView(APIView):
    """Import statement rows and run reconciliation matching."""

    permission_classes = [IsAuthenticatedAndActive]
    serializer_class = PaymentStatementImportRequestSerializer

    @extend_schema(
        tags=["Payments"],
        operation_id="list_payment_statement_imports",
        responses={200: payment_success_response, 400: payment_error_response},
    )
    def get(self, request: Request) -> Response:
        chama_id = request.query_params.get("chama_id")
        if not chama_id:
            return ApiResponse.error(
                message="chama_id is required",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        try:
            chama = Chama.objects.get(id=chama_id)
            imports = UnifiedPaymentService.list_statement_imports(
                chama=chama,
                actor=request.user,
                limit=int(request.query_params.get("limit", 50)),
            )
            return ApiResponse.success(
                data={"imports": PaymentStatementImportSerializer(imports, many=True).data}
            )
        except Chama.DoesNotExist:
            return ApiResponse.error(message="Chama not found", status_code=status.HTTP_404_NOT_FOUND)
        except PaymentServiceError as e:
            return ApiResponse.error(message=str(e), status_code=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error("Statement import list failed: %s", e)
            return ApiResponse.error(
                message="Failed to load statement imports",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        tags=["Payments"],
        operation_id="create_payment_statement_import",
        request=PaymentStatementImportRequestSerializer,
        responses={201: payment_success_response, 400: payment_error_response},
    )
    def post(self, request: Request) -> Response:
        serializer = PaymentStatementImportRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            chama = Chama.objects.get(id=serializer.validated_data["chama_id"])
            statement_import = UnifiedPaymentService.import_statement(
                chama=chama,
                actor=request.user,
                payment_method=serializer.validated_data["payment_method"],
                provider_name=serializer.validated_data.get("provider_name", ""),
                source_name=serializer.validated_data.get("source_name", ""),
                statement_date=serializer.validated_data.get("statement_date"),
                csv_text=serializer.validated_data.get("csv_text", ""),
                rows=serializer.validated_data.get("rows"),
            )
            return ApiResponse.success(
                data=PaymentStatementImportSerializer(statement_import).data,
                message="Statement imported successfully",
                status_code=status.HTTP_201_CREATED,
            )
        except Chama.DoesNotExist:
            return ApiResponse.error(message="Chama not found", status_code=status.HTTP_404_NOT_FOUND)
        except PaymentServiceError as e:
            return ApiResponse.error(message=str(e), status_code=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error("Statement import failed: %s", e)
            return ApiResponse.error(
                message="Failed to import statement",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PaymentSettlementView(APIView):
    """Create and list settlement postings."""

    permission_classes = [IsAuthenticatedAndActive]
    serializer_class = PaymentSettlementCreateSerializer

    @extend_schema(
        tags=["Payments"],
        operation_id="list_payment_settlements",
        responses={200: payment_success_response, 400: payment_error_response},
    )
    def get(self, request: Request) -> Response:
        chama_id = request.query_params.get("chama_id")
        if not chama_id:
            return ApiResponse.error(
                message="chama_id is required",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        try:
            chama = Chama.objects.get(id=chama_id)
            settlements = UnifiedPaymentService.list_settlements(
                chama=chama,
                actor=request.user,
                payment_method=request.query_params.get("payment_method") or None,
                limit=min(int(request.query_params.get("limit", 100)), 200),
            )
            return ApiResponse.success(
                data={"settlements": PaymentSettlementSerializer(settlements, many=True).data}
            )
        except Chama.DoesNotExist:
            return ApiResponse.error(
                message="Chama not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        except PaymentServiceError as e:
            return ApiResponse.error(
                message=str(e),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.error("Settlement listing failed: %s", e)
            return ApiResponse.error(
                message="Failed to list settlements",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        tags=["Payments"],
        operation_id="create_payment_settlement",
        request=PaymentSettlementCreateSerializer,
        responses={201: payment_success_response, 400: payment_error_response},
    )
    def post(self, request: Request) -> Response:
        serializer = PaymentSettlementCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            chama = Chama.objects.get(id=serializer.validated_data["chama_id"])
            settlement = UnifiedPaymentService.record_settlement(
                chama=chama,
                actor=request.user,
                payment_method=serializer.validated_data["payment_method"],
                settlement_reference=serializer.validated_data["settlement_reference"],
                gross_amount=serializer.validated_data["gross_amount"],
                fee_amount=serializer.validated_data.get("fee_amount"),
                settlement_date=serializer.validated_data.get("settlement_date"),
                provider_name=serializer.validated_data.get("provider_name", ""),
                currency=serializer.validated_data.get("currency", "KES"),
                statement_import_id=serializer.validated_data.get("statement_import_id"),
                transaction_ids=serializer.validated_data.get("transaction_ids"),
                metadata=serializer.validated_data.get("metadata"),
            )
            return ApiResponse.success(
                data=PaymentSettlementSerializer(settlement).data,
                message="Settlement posted successfully",
                status_code=status.HTTP_201_CREATED,
            )
        except Chama.DoesNotExist:
            return ApiResponse.error(
                message="Chama not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        except PaymentServiceError as e:
            return ApiResponse.error(
                message=str(e),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.error("Settlement posting failed: %s", e)
            return ApiResponse.error(
                message="Failed to post settlement",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PaymentDisputeListCreateView(APIView):
    """List and open unified payment disputes."""

    permission_classes = [IsAuthenticatedAndActive]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return PaymentDisputeCreateSerializer
        return PaymentDisputeRecordSerializer

    @extend_schema(
        tags=["Payments"],
        operation_id="list_unified_payment_disputes",
        responses={200: payment_success_response, 400: payment_error_response},
    )
    def get(self, request: Request) -> Response:
        chama_id = request.query_params.get("chama_id")
        if not chama_id:
            return ApiResponse.error(
                message="chama_id is required",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        try:
            chama = Chama.objects.get(id=chama_id)
            disputes = UnifiedPaymentService.list_disputes(
                chama=chama,
                actor=request.user,
                intent_id=request.query_params.get("intent_id") or None,
                status_filter=request.query_params.get("status") or None,
                limit=min(int(request.query_params.get("limit", 100)), 200),
            )
            return ApiResponse.success(
                data={"disputes": PaymentDisputeRecordSerializer(disputes, many=True).data}
            )
        except Chama.DoesNotExist:
            return ApiResponse.error(message="Chama not found", status_code=status.HTTP_404_NOT_FOUND)
        except PaymentServiceError as e:
            return ApiResponse.error(message=str(e), status_code=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error("Failed to list payment disputes: %s", e)
            return ApiResponse.error(
                message="Failed to list payment disputes",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        tags=["Payments"],
        operation_id="create_unified_payment_dispute",
        request=PaymentDisputeCreateSerializer,
        responses={201: payment_success_response, 400: payment_error_response},
    )
    def post(self, request: Request) -> Response:
        serializer = PaymentDisputeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            chama = Chama.objects.get(id=serializer.validated_data["chama_id"])
            dispute = UnifiedPaymentService.open_dispute(
                chama=chama,
                actor=request.user,
                intent_id=serializer.validated_data.get("intent_id"),
                category=serializer.validated_data.get("category"),
                amount=serializer.validated_data.get("amount"),
                reason=serializer.validated_data["reason"],
                reference=serializer.validated_data.get("reference", ""),
                provider_case_reference=serializer.validated_data.get("provider_case_reference", ""),
            )
            return ApiResponse.success(
                data=PaymentDisputeRecordSerializer(dispute).data,
                message="Payment dispute opened successfully",
                status_code=status.HTTP_201_CREATED,
            )
        except Chama.DoesNotExist:
            return ApiResponse.error(message="Chama not found", status_code=status.HTTP_404_NOT_FOUND)
        except PaymentServiceError as e:
            return ApiResponse.error(message=str(e), status_code=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error("Failed to open payment dispute: %s", e)
            return ApiResponse.error(
                message="Failed to open payment dispute",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PaymentDisputeResolveView(APIView):
    """Resolve unified payment disputes and chargebacks."""

    permission_classes = [IsAuthenticatedAndActive]
    serializer_class = PaymentDisputeDecisionSerializer

    @extend_schema(
        tags=["Payments"],
        operation_id="resolve_unified_payment_dispute",
        request=PaymentDisputeDecisionSerializer,
        responses={200: payment_success_response, 400: payment_error_response},
    )
    def post(self, request: Request, id: str) -> Response:
        serializer = PaymentDisputeDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            dispute = UnifiedPaymentService.resolve_dispute(
                dispute_id=id,
                actor=request.user,
                status=serializer.validated_data["status"],
                resolution_notes=serializer.validated_data.get("resolution_notes", ""),
                amount=serializer.validated_data.get("amount"),
                provider_case_reference=serializer.validated_data.get("provider_case_reference", ""),
            )
            return ApiResponse.success(
                data=PaymentDisputeRecordSerializer(dispute).data,
                message="Payment dispute updated successfully",
            )
        except PaymentServiceError as e:
            return ApiResponse.error(message=str(e), status_code=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error("Failed to resolve payment dispute: %s", e)
            return ApiResponse.error(
                message="Failed to resolve payment dispute",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PaymentConfirmClientReturnView(APIView):
    """
    Confirm client return from payment provider.

    POST /api/v1/payments/confirm-client-return/
    """

    permission_classes = [IsAuthenticated]
    serializer_class = PaymentConfirmSerializer

    @extend_schema(
        tags=["Payments"],
        operation_id="confirm_unified_payment_client_return",
        request=PaymentConfirmSerializer,
        responses={200: payment_success_response, 400: payment_error_response},
    )
    def post(self, request: Request) -> Response:
        """Confirm client return and verify payment."""
        serializer = PaymentConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            intent_id = serializer.validated_data["intent_id"]
            existing_intent = UnifiedPaymentService.get_payment_status(intent_id)

            if existing_intent.user != request.user:
                return ApiResponse.error(
                    message="Not authorized to confirm this payment",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            intent = UnifiedPaymentService.verify_payment(intent_id)
            response_serializer = PaymentIntentResponseSerializer(intent)
            return ApiResponse.success(
                data=response_serializer.data,
                message="Payment confirmed successfully",
            )

        except PaymentServiceError as e:
            return ApiResponse.error(
                message=str(e),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.error("Payment confirmation failed: %s", e)
            return ApiResponse.error(
                message="Failed to confirm payment",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CashPaymentVerifyView(APIView):
    """
    Verify cash payment.

    POST /api/v1/payments/cash/verify/
    """

    permission_classes = [IsAuthenticatedAndActive]
    serializer_class = CashPaymentVerifySerializer

    @extend_schema(
        tags=["Payments"],
        operation_id="verify_cash_payment",
        request=CashPaymentVerifySerializer,
        responses={200: payment_success_response, 400: payment_error_response},
    )
    def post(self, request: Request) -> Response:
        """Verify cash payment."""
        serializer = CashPaymentVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            intent = UnifiedPaymentService.approve_cash_payment(
                intent_id=serializer.validated_data["intent_id"],
                actor=request.user,
                receipt_number=serializer.validated_data.get("receipt_number", ""),
                notes=serializer.validated_data.get("notes", ""),
            )

            response_serializer = PaymentIntentResponseSerializer(intent)
            return ApiResponse.success(
                data=response_serializer.data,
                message="Cash payment verified successfully",
            )

        except PaymentIntent.DoesNotExist:
            return ApiResponse.error(
                message="Payment intent not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        except PaymentServiceError as e:
            return ApiResponse.error(
                message=str(e),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.error("Cash payment verification failed: %s", e)
            return ApiResponse.error(
                message="Failed to verify cash payment",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CashPaymentRejectView(APIView):
    """
    Reject cash payment.

    POST /api/v1/payments/cash/reject/
    """

    permission_classes = [IsAuthenticatedAndActive]
    serializer_class = ManualPaymentRejectSerializer

    @extend_schema(
        tags=["Payments"],
        operation_id="reject_cash_payment",
        request=ManualPaymentRejectSerializer,
        responses={200: payment_success_response, 400: payment_error_response},
    )
    def post(self, request: Request) -> Response:
        serializer = ManualPaymentRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            intent = UnifiedPaymentService.reject_cash_payment(
                intent_id=serializer.validated_data["intent_id"],
                actor=request.user,
                notes=serializer.validated_data.get("notes", ""),
            )
            response_serializer = PaymentIntentResponseSerializer(intent)
            return ApiResponse.success(
                data=response_serializer.data,
                message="Cash payment rejected successfully",
            )
        except PaymentServiceError as e:
            return ApiResponse.error(
                message=str(e),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.error("Cash payment rejection failed: %s", e)
            return ApiResponse.error(
                message="Failed to reject cash payment",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class BankPaymentProofUploadView(APIView):
    """
    Upload bank transfer reference/notes (and optionally proof) for a bank intent.

    POST /api/v1/payments/bank/proof/
    """

    permission_classes = [IsAuthenticatedAndActive]
    serializer_class = BankPaymentProofUploadSerializer

    @extend_schema(
        tags=["Payments"],
        operation_id="upload_bank_transfer_proof",
        request=BankPaymentProofUploadSerializer,
        responses={200: payment_success_response, 400: payment_error_response},
    )
    def post(self, request: Request) -> Response:
        serializer = BankPaymentProofUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            intent = UnifiedPaymentService.upload_bank_transfer_proof(
                intent_id=serializer.validated_data["intent_id"],
                actor=request.user,
                transfer_reference=serializer.validated_data.get("transfer_reference", ""),
                notes=serializer.validated_data.get("notes", ""),
                proof_document=None,
            )
            return ApiResponse.success(
                data=PaymentIntentResponseSerializer(intent).data,
                message="Bank transfer details submitted",
            )
        except PaymentServiceError as e:
            return ApiResponse.error(message=str(e), status_code=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error("Bank proof upload failed: %s", e)
            return ApiResponse.error(
                message="Failed to submit bank transfer details",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class BankPaymentVerifyView(APIView):
    """
    Verify bank transfer payment.

    POST /api/v1/payments/bank/verify/
    """

    permission_classes = [IsAuthenticatedAndActive]
    serializer_class = BankPaymentVerifySerializer

    @extend_schema(
        tags=["Payments"],
        operation_id="verify_bank_payment",
        request=BankPaymentVerifySerializer,
        responses={200: payment_success_response, 400: payment_error_response},
    )
    def post(self, request: Request) -> Response:
        serializer = BankPaymentVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            intent = UnifiedPaymentService.verify_bank_payment(
                intent_id=serializer.validated_data["intent_id"],
                actor=request.user,
                transfer_reference=serializer.validated_data.get("transfer_reference", ""),
                notes=serializer.validated_data.get("notes", ""),
            )
            return ApiResponse.success(
                data=PaymentIntentResponseSerializer(intent).data,
                message="Bank transfer verified successfully",
            )
        except PaymentServiceError as e:
            return ApiResponse.error(message=str(e), status_code=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error("Bank payment verification failed: %s", e)
            return ApiResponse.error(
                message="Failed to verify bank transfer",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class BankPaymentRejectView(APIView):
    """
    Reject bank transfer payment.

    POST /api/v1/payments/bank/reject/
    """

    permission_classes = [IsAuthenticatedAndActive]
    serializer_class = ManualPaymentRejectSerializer

    @extend_schema(
        tags=["Payments"],
        operation_id="reject_bank_payment",
        request=ManualPaymentRejectSerializer,
        responses={200: payment_success_response, 400: payment_error_response},
    )
    def post(self, request: Request) -> Response:
        serializer = ManualPaymentRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            intent = UnifiedPaymentService.reject_bank_payment(
                intent_id=serializer.validated_data["intent_id"],
                actor=request.user,
                notes=serializer.validated_data.get("notes", ""),
            )
            return ApiResponse.success(
                data=PaymentIntentResponseSerializer(intent).data,
                message="Bank transfer rejected successfully",
            )
        except PaymentServiceError as e:
            return ApiResponse.error(message=str(e), status_code=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error("Bank payment rejection failed: %s", e)
            return ApiResponse.error(
                message="Failed to reject bank transfer",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PaymentAdminListView(APIView):
    """
    Admin view to list all payments.

    GET /api/v1/payments/admin/list/
    """

    permission_classes = [IsAuthenticatedAndActive]
    serializer_class = PaymentListSerializer

    @extend_schema(
        tags=["Payments"],
        operation_id="list_unified_admin_payments",
        responses={200: payment_success_response, 400: payment_error_response},
    )
    def get(self, request: Request) -> Response:
        """List all payments for admin."""
        try:
            chama_id = request.query_params.get("chama_id")
            payment_method = request.query_params.get("payment_method")
            status_filter = request.query_params.get("status")
            start_date = request.query_params.get("start_date")
            end_date = request.query_params.get("end_date")
            limit = int(request.query_params.get("limit", 100))

            if not chama_id:
                return ApiResponse.error(
                    message="chama_id is required",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            chama = Chama.objects.get(id=chama_id)

            payments = UnifiedPaymentService.get_chama_payments(
                chama=chama,
                payment_method=payment_method,
                status=status_filter,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
            )

            serializer = PaymentListSerializer(payments, many=True)
            return ApiResponse.success(data={"payments": serializer.data})

        except Chama.DoesNotExist:
            return ApiResponse.error(
                message="Chama not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            logger.error("Failed to list admin payments: %s", e)
            return ApiResponse.error(
                message="Failed to list payments",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PaymentReconciliationListView(APIView):
    """
    Admin reconciliation queue.

    GET /api/v1/payments/reconciliation/
    """

    permission_classes = [IsAuthenticatedAndActive]
    serializer_class = PaymentReconciliationListSerializer

    @extend_schema(
        tags=["Payments"],
        operation_id="list_payment_reconciliation_cases",
        responses={200: payment_success_response, 400: payment_error_response},
    )
    def get(self, request: Request) -> Response:
        try:
            chama_id = request.query_params.get("chama_id")
            payment_method = request.query_params.get("payment_method")
            limit = int(request.query_params.get("limit", 100))

            if not chama_id:
                return ApiResponse.error(
                    message="chama_id is required",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            chama = Chama.objects.get(id=chama_id)
            issues = UnifiedPaymentService.get_reconciliation_queue(
                chama=chama,
                actor=request.user,
                payment_method=payment_method,
                limit=limit,
            )
            serializer = PaymentReconciliationListSerializer(issues, many=True)
            return ApiResponse.success(data={"issues": serializer.data})
        except Chama.DoesNotExist:
            return ApiResponse.error(
                message="Chama not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        except PaymentServiceError as e:
            return ApiResponse.error(
                message=str(e),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.error("Failed to load reconciliation queue: %s", e)
            return ApiResponse.error(
                message="Failed to load reconciliation queue",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PaymentReconciliationResolveView(APIView):
    """
    Resolve a reconciliation issue.

    POST /api/v1/payments/reconciliation/{id}/resolve/
    """

    permission_classes = [IsAuthenticatedAndActive]
    serializer_class = PaymentReconciliationResolveSerializer

    @extend_schema(
        tags=["Payments"],
        operation_id="resolve_payment_reconciliation_case",
        request=PaymentReconciliationResolveSerializer,
        responses={200: payment_success_response, 400: payment_error_response},
    )
    def post(self, request: Request, id: str) -> Response:
        serializer = PaymentReconciliationResolveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            reconciliation_case = UnifiedPaymentService.resolve_reconciliation_issue(
                case_id=id,
                actor=request.user,
                action=serializer.validated_data["action"],
                notes=serializer.validated_data.get("notes", ""),
            )
            response_serializer = PaymentReconciliationCaseSerializer(reconciliation_case)
            return ApiResponse.success(
                data=response_serializer.data,
                message="Reconciliation issue resolved",
            )
        except PaymentServiceError as e:
            return ApiResponse.error(
                message=str(e),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.error("Failed to resolve reconciliation issue: %s", e)
            return ApiResponse.error(
                message="Failed to resolve reconciliation issue",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
