"""
Card payment API views for MyChama.

Views for handling card payment operations.
"""

from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chama.models import Chama
from apps.payments.card_serializers import (
    CardPaymentAuditLogSerializer,
    CardPaymentConfirmSerializer,
    CardPaymentIntentCreateSerializer,
    CardPaymentIntentResponseSerializer,
    CardPaymentListSerializer,
    CardPaymentReceiptSerializer,
    CardPaymentTransactionSerializer,
)
from apps.payments.card_services import CardPaymentService, CardPaymentServiceError
from apps.security.rbac import user_has_chama_permission
from core.api_response import ApiResponse
from core.permissions import IsChamaMember, IsTreasurerOrAdmin

logger = logging.getLogger(__name__)


def _can_manage_chama_payment(user, chama_id: str) -> bool:
    return bool(
        user
        and user.is_authenticated
        and (
            user.is_staff
            or user_has_chama_permission(
                user=user,
                permission_code="can_manage_finance",
                chama_id=str(chama_id),
            )
        )
    )


class CardPaymentCreateIntentView(APIView):
    """
    Create card payment intent.

    POST /api/v1/payments/card/create-intent/
    """

    permission_classes = [IsAuthenticated, IsChamaMember]

    def post(self, request: Request) -> Response:
        """Create a new card payment intent."""
        serializer = CardPaymentIntentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            chama_id = serializer.validated_data["chama_id"]
            chama = Chama.objects.get(id=chama_id)

            intent = CardPaymentService.create_payment_intent(
                chama=chama,
                user=request.user,
                amount=serializer.validated_data["amount"],
                currency=serializer.validated_data["currency"],
                purpose=serializer.validated_data["purpose"],
                description=serializer.validated_data.get("description", ""),
                contribution_type_id=serializer.validated_data.get("contribution_type_id"),
                provider_name=serializer.validated_data.get("provider"),
                idempotency_key=serializer.validated_data.get("idempotency_key"),
                metadata=serializer.validated_data.get("metadata"),
            )

            response_serializer = CardPaymentIntentResponseSerializer(intent)
            return ApiResponse.success(
                data=response_serializer.data,
                message="Card payment intent created successfully",
                status_code=status.HTTP_201_CREATED,
            )

        except Chama.DoesNotExist:
            return ApiResponse.error(
                message="Chama not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        except CardPaymentServiceError as e:
            return ApiResponse.error(
                message=str(e),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.error("Card payment intent creation failed: %s", e)
            return ApiResponse.error(
                message="Failed to create payment intent",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CardPaymentStatusView(APIView):
    """
    Get card payment status.

    GET /api/v1/payments/card/{id}/status/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request, id: str) -> Response:
        """Get payment intent status."""
        try:
            intent = CardPaymentService.get_payment_status(id)

            if (
                intent.user != request.user
                and not _can_manage_chama_payment(request.user, intent.chama_id)
            ):
                return ApiResponse.error(
                    message="Not authorized to view this payment",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            transactions = intent.transactions.all()
            receipt = getattr(intent, "receipt", None)
            audit_logs = intent.audit_logs.all()[:20]

            data = {
                "intent": CardPaymentIntentResponseSerializer(intent).data,
                "transactions": CardPaymentTransactionSerializer(transactions, many=True).data,
                "receipt": CardPaymentReceiptSerializer(receipt).data if receipt else None,
                "audit_logs": CardPaymentAuditLogSerializer(audit_logs, many=True).data,
            }

            return ApiResponse.success(data=data)

        except CardPaymentServiceError as e:
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


class CardPaymentVerifyView(APIView):
    """
    Verify card payment with provider.

    POST /api/v1/payments/card/{id}/verify/
    """

    permission_classes = [IsAuthenticated]

    def post(self, request: Request, id: str) -> Response:
        """Verify payment status with provider."""
        try:
            existing_intent = CardPaymentService.get_payment_status(id)
            if (
                existing_intent.user != request.user
                and not _can_manage_chama_payment(request.user, existing_intent.chama_id)
            ):
                return ApiResponse.error(
                    message="Not authorized to verify this payment",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            intent = CardPaymentService.verify_payment(id)

            response_serializer = CardPaymentIntentResponseSerializer(intent)
            return ApiResponse.success(
                data=response_serializer.data,
                message="Payment verified successfully",
            )

        except CardPaymentServiceError as e:
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


class CardPaymentWebhookView(APIView):
    """
    Handle card payment webhooks.

    POST /api/v1/payments/card/webhook/
    """

    permission_classes = []  # No authentication for webhooks
    authentication_classes = []

    def post(self, request: Request) -> Response:
        """Process webhook from payment provider."""
        try:
            provider = request.query_params.get("provider", "stripe")

            payload = request.body
            signature = request.META.get("HTTP_STRIPE_SIGNATURE", "")

            if provider == "flutterwave":
                signature = request.META.get("HTTP_VERIF_HASH", "")

            source_ip = request.META.get("REMOTE_ADDR")

            webhook_log = CardPaymentService.process_webhook(
                provider_name=provider,
                payload=payload,
                signature=signature,
                headers=dict(request.META),
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


class CardPaymentReceiptView(APIView):
    """
    Get card payment receipt.

    GET /api/v1/payments/card/{id}/receipt/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request, id: str) -> Response:
        """Get payment receipt."""
        try:
            receipt = CardPaymentService.get_payment_receipt(id)

            if (
                receipt.payment_intent.user != request.user
                and not _can_manage_chama_payment(
                    request.user,
                    receipt.payment_intent.chama_id,
                )
            ):
                return ApiResponse.error(
                    message="Not authorized to view this receipt",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            serializer = CardPaymentReceiptSerializer(receipt)
            return ApiResponse.success(data=serializer.data)

        except CardPaymentServiceError as e:
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


class CardPaymentListView(APIView):
    """
    List card payments.

    GET /api/v1/payments/card/list/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        """List user's card payments."""
        try:
            chama_id = request.query_params.get("chama_id")
            status_filter = request.query_params.get("status")
            limit = int(request.query_params.get("limit", 50))

            payments = CardPaymentService.get_user_payments(
                user=request.user,
                chama_id=chama_id,
                status=status_filter,
                limit=limit,
            )

            serializer = CardPaymentListSerializer(payments, many=True)
            return ApiResponse.success(data={"payments": serializer.data})

        except Exception as e:
            logger.error("Failed to list payments: %s", e)
            return ApiResponse.error(
                message="Failed to list payments",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CardPaymentConfirmClientReturnView(APIView):
    """
    Confirm client return from payment provider.

    POST /api/v1/payments/card/confirm-client-return/
    """

    permission_classes = [IsAuthenticated]

    def post(self, request: Request) -> Response:
        """Confirm client return and verify payment."""
        serializer = CardPaymentConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            intent_id = serializer.validated_data["intent_id"]
            existing_intent = CardPaymentService.get_payment_status(intent_id)

            if (
                existing_intent.user != request.user
                and not _can_manage_chama_payment(request.user, existing_intent.chama_id)
            ):
                return ApiResponse.error(
                    message="Not authorized to confirm this payment",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            intent = CardPaymentService.verify_payment(intent_id)

            response_serializer = CardPaymentIntentResponseSerializer(intent)
            return ApiResponse.success(
                data=response_serializer.data,
                message="Payment confirmed successfully",
            )

        except CardPaymentServiceError as e:
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


class CardPaymentAdminListView(APIView):
    """
    Admin view to list all card payments.

    GET /api/v1/payments/card/admin/list/
    """

    permission_classes = [IsAuthenticated, IsTreasurerOrAdmin]

    def get(self, request: Request) -> Response:
        """List all card payments for admin."""
        try:
            chama_id = request.query_params.get("chama_id")
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

            payments = CardPaymentService.get_chama_payments(
                chama=chama,
                status=status_filter,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
            )

            serializer = CardPaymentListSerializer(payments, many=True)
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
