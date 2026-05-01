"""Views for Payout workflow."""

from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.types import OpenApiTypes
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.chama.models import Chama
from apps.chama.permissions import (
    IsApprovedActiveMember,
    IsChamaAdmin,
    IsTreasurerOrAdmin,
)
from core.api_response import ApiResponse

from .models import Payout, PayoutRotation, PayoutStatus
from .serializers import (
    PayoutApprovalSerializer,
    PayoutCreateSerializer,
    PayoutDetailSerializer,
    PayoutFlagHoldSerializer,
    PayoutListSerializer,
    PayoutMethodUpdateSerializer,
    PayoutReleaseHoldSerializer,
    PayoutRotationSerializer,
)
from .services import PayoutService


class PayoutViewSet(viewsets.ModelViewSet):
    """ViewSet for managing payouts."""

    queryset = Payout.objects.select_related(
        "chama",
        "member",
        "payment_intent",
        "approval_request",
    )
    permission_classes = [IsAuthenticated, IsApprovedActiveMember]
    http_method_names = ["get", "post", "head", "options"]

    def get_serializer_class(self):
        """Return appropriate serializer based on action."""
        if self.action == "create":
            return PayoutCreateSerializer
        elif self.action == "retrieve":
            return PayoutDetailSerializer
        elif self.action in ["list", "list_by_chama"]:
            return PayoutListSerializer
        return PayoutDetailSerializer

    def get_queryset(self):
        """Filter payouts by chama membership."""
        user = self.request.user
        chama_id = self.request.query_params.get("chama_id")

        if chama_id:
            # Check user is member of this chama
            if not user.memberships.filter(chama_id=chama_id).exists():
                return Payout.objects.none()
            return Payout.objects.filter(chama_id=chama_id)

        # Return payouts from all member's chamas
        chama_ids = user.memberships.values_list("chama_id", flat=True)
        return Payout.objects.filter(chama__in=chama_ids)

    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated, IsTreasurerOrAdmin])
    def trigger_payout(self, request):
        """
        Trigger a new payout manually.

        POST /api/payouts/trigger_payout/
        {
            "chama_id": "...",
            "member_id": "...",  # optional, uses rotation if omitted
            "amount": 10000,      # optional
            "trigger_type": "manual"
        }
        """
        try:
            chama_id = request.data.get("chama_id")
            member_id = request.data.get("member_id")
            amount = request.data.get("amount")
            trigger_type = request.data.get("trigger_type", "manual")

            if not chama_id:
                return ApiResponse.error(
                    message="chama_id is required",
                    code="MISSING_CHAMA",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            # Verify user is treasurer/admin of chama
            membership = request.user.memberships.filter(
                chama_id=chama_id,
                role__in=["TREASURER", "ADMIN", "CHAMA_ADMIN"],
            ).first()

            if not membership:
                return ApiResponse.error(
                    message="You must be a treasurer or admin to trigger payouts",
                    code="PERMISSION_DENIED",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            # Trigger payout
            payout = PayoutService.trigger_payout(
                chama_id=chama_id,
                member_id=member_id,
                trigger_type=trigger_type,
                amount=amount,
                triggered_by_id=request.user.id,
            )

            # Immediately run eligibility check
            payout, eligibility_check = PayoutService.check_eligibility(payout.id)

            return ApiResponse.success(
                data=PayoutDetailSerializer(payout).data,
                message="Payout triggered and eligibility checked",
                status_code=status.HTTP_201_CREATED,
            )

        except Exception as e:
            return ApiResponse.error(
                message=str(e),
                code="PAYOUT_TRIGGER_ERROR",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsTreasurerOrAdmin])
    def send_to_review(self, request, pk=None):
        """
        Send eligible payout to treasurer review.

        POST /api/payouts/{id}/send_to_review/
        """
        try:
            payout = self.get_object()

            # Verify user is treasurer/admin
            membership = request.user.memberships.filter(
                chama_id=payout.chama_id,
                role__in=["TREASURER", "ADMIN", "CHAMA_ADMIN"],
            ).first()

            if not membership:
                return ApiResponse.error(
                    message="You must be a treasurer or admin",
                    code="PERMISSION_DENIED",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            payout = PayoutService.send_to_treasurer_review(payout.id)

            return ApiResponse.success(
                data=PayoutDetailSerializer(payout).data,
                message="Payout sent to treasurer review",
                status_code=status.HTTP_200_OK,
            )

        except Exception as e:
            return ApiResponse.error(
                message=str(e),
                code="REVIEW_ERROR",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsTreasurerOrAdmin])
    def treasurer_approve(self, request, pk=None):
        """
        Treasurer approves payout.

        POST /api/payouts/{id}/treasurer_approve/
        """
        try:
            payout = self.get_object()

            # Verify user is treasurer
            membership = request.user.memberships.filter(
                chama_id=payout.chama_id,
                role__in=["TREASURER"],
            ).first()

            if not membership:
                return ApiResponse.error(
                    message="Only treasurer can approve",
                    code="PERMISSION_DENIED",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            payout = PayoutService.treasurer_approve(payout.id, request.user.id)

            return ApiResponse.success(
                data=PayoutDetailSerializer(payout).data,
                message="Payout approved by treasurer, sent to chairperson",
                status_code=status.HTTP_200_OK,
            )

        except Exception as e:
            return ApiResponse.error(
                message=str(e),
                code="APPROVAL_ERROR",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsChamaAdmin])
    def treasurer_reject(self, request, pk=None):
        """
        Treasurer rejects payout.

        POST /api/payouts/{id}/treasurer_reject/
        {
            "rejection_reason": "..."
        }
        """
        try:
            payout = self.get_object()

            # Verify user is treasurer
            membership = request.user.memberships.filter(
                chama_id=payout.chama_id,
                role__in=["TREASURER"],
            ).first()

            if not membership:
                return ApiResponse.error(
                    message="Only treasurer can reject",
                    code="PERMISSION_DENIED",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            reason = request.data.get("rejection_reason", "No reason provided")
            payout = PayoutService.treasurer_reject(
                payout.id,
                reason,
                request.user.id,
            )

            return ApiResponse.success(
                data=PayoutDetailSerializer(payout).data,
                message="Payout rejected by treasurer",
                status_code=status.HTTP_200_OK,
            )

        except Exception as e:
            return ApiResponse.error(
                message=str(e),
                code="REJECTION_ERROR",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsChamaAdmin])
    def chairperson_approve(self, request, pk=None):
        """
        Chairperson approves payout and initiates payment.

        POST /api/payouts/{id}/chairperson_approve/
        """
        try:
            payout = self.get_object()

            # Verify user is chairperson or admin
            membership = request.user.memberships.filter(
                chama_id=payout.chama_id,
                role__in=["CHAIRPERSON", "ADMIN", "CHAMA_ADMIN"],
            ).first()

            if not membership:
                return ApiResponse.error(
                    message="Only chairperson or admin can approve",
                    code="PERMISSION_DENIED",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            payout = PayoutService.chairperson_approve(payout.id, request.user.id)

            return ApiResponse.success(
                data=PayoutDetailSerializer(payout).data,
                message="Payout approved by chairperson",
                status_code=status.HTTP_200_OK,
            )

        except Exception as e:
            return ApiResponse.error(
                message=str(e),
                code="APPROVAL_ERROR",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsTreasurerOrAdmin])
    def initiate_payment(self, request, pk=None):
        """
        Treasurer/Admin initiates payment for an approved payout.

        POST /api/payouts/{id}/initiate_payment/
        """
        try:
            payout = self.get_object()

            membership = request.user.memberships.filter(
                chama_id=payout.chama_id,
                role__in=["TREASURER", "ADMIN", "CHAMA_ADMIN"],
            ).first()
            if not membership:
                return ApiResponse.error(
                    message="You must be a treasurer or admin to initiate payouts.",
                    code="PERMISSION_DENIED",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            PayoutService.initiate_payment(payout.id)
            payout.refresh_from_db()
            return ApiResponse.success(
                data=PayoutDetailSerializer(payout).data,
                message="Payout payment initiated",
                status_code=status.HTTP_200_OK,
            )
        except Exception as e:
            return ApiResponse.error(
                message=str(e),
                code="PAYMENT_INITIATION_ERROR",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsChamaAdmin])
    def chairperson_reject(self, request, pk=None):
        """
        Chairperson rejects payout.

        POST /api/payouts/{id}/chairperson_reject/
        {
            "rejection_reason": "..."
        }
        """
        try:
            payout = self.get_object()

            # Verify user is chairperson
            membership = request.user.memberships.filter(
                chama_id=payout.chama_id,
                role__in=["CHAIRPERSON", "ADMIN", "CHAMA_ADMIN"],
            ).first()

            if not membership:
                return ApiResponse.error(
                    message="Only chairperson can reject",
                    code="PERMISSION_DENIED",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            reason = request.data.get("rejection_reason", "No reason provided")
            payout = PayoutService.chairperson_reject(
                payout.id,
                reason,
                request.user.id,
            )

            return ApiResponse.success(
                data=PayoutDetailSerializer(payout).data,
                message="Payout rejected by chairperson",
                status_code=status.HTTP_200_OK,
            )

        except Exception as e:
            return ApiResponse.error(
                message=str(e),
                code="REJECTION_ERROR",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsTreasurerOrAdmin])
    def set_payout_method(self, request, pk=None):
        """
        Set or change payout method.

        POST /api/payouts/{id}/set_payout_method/
        {
            "payout_method": "mpesa"  // or "bank_transfer", "wallet"
        }
        """
        try:
            payout = self.get_object()

            serializer = PayoutMethodUpdateSerializer(
                data=request.data,
                context={"payout": payout}
            )
            if not serializer.is_valid():
                return ApiResponse.error(
                    message=serializer.errors,
                    code="VALIDATION_ERROR",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            payout.payout_method = serializer.validated_data["payout_method"]
            payout.save()

            return ApiResponse.success(
                data=PayoutDetailSerializer(payout).data,
                message="Payout method updated",
                status_code=status.HTTP_200_OK,
            )

        except Exception as e:
            return ApiResponse.error(
                message=str(e),
                code="METHOD_UPDATE_ERROR",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsTreasurerOrAdmin])
    def flag_hold(self, request, pk=None):
        """
        Flag payout to place on hold.

        POST /api/payouts/{id}/flag_hold/
        {
            "reason": "Awaiting member to update bank details"
        }
        """
        try:
            payout = self.get_object()

            serializer = PayoutFlagHoldSerializer(data=request.data)
            if not serializer.is_valid():
                return ApiResponse.error(
                    message=serializer.errors,
                    code="VALIDATION_ERROR",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            payout = PayoutService.flag_payout_on_hold(
                payout.id,
                serializer.validated_data["reason"],
                request.user.id,
            )

            return ApiResponse.success(
                data=PayoutDetailSerializer(payout).data,
                message="Payout placed on hold",
                status_code=status.HTTP_200_OK,
            )

        except Exception as e:
            return ApiResponse.error(
                message=str(e),
                code="HOLD_ERROR",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsTreasurerOrAdmin])
    def release_hold(self, request, pk=None):
        """
        Release payout from hold.

        POST /api/payouts/{id}/release_hold/
        {
            "notes": "Issue resolved"
        }
        """
        try:
            payout = self.get_object()

            serializer = PayoutReleaseHoldSerializer(data=request.data)
            if not serializer.is_valid():
                return ApiResponse.error(
                    message=serializer.errors,
                    code="VALIDATION_ERROR",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            payout = PayoutService.release_payout_from_hold(
                payout.id,
                request.user.id,
                serializer.validated_data.get("notes", ""),
            )

            return ApiResponse.success(
                data=PayoutDetailSerializer(payout).data,
                message="Payout released from hold",
                status_code=status.HTTP_200_OK,
            )

        except Exception as e:
            return ApiResponse.error(
                message=str(e),
                code="RELEASE_ERROR",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsTreasurerOrAdmin])
    def retry_payment(self, request, pk=None):
        """
        Retry failed payout payment.

        POST /api/payouts/{id}/retry_payment/
        """
        try:
            payout = self.get_object()

            if payout.status != PayoutStatus.FAILED:
                return ApiResponse.error(
                    message=f"Payout must be in FAILED status. Current: {payout.status}",
                    code="INVALID_STATUS",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            if not payout.can_retry():
                return ApiResponse.error(
                    message=f"Max retries ({payout.max_retries}) exceeded",
                    code="MAX_RETRIES_EXCEEDED",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            payment_intent = PayoutService.retry_failed_payout(payout.id)

            return ApiResponse.success(
                data=PayoutDetailSerializer(payout).data,
                message=f"Payment retry initiated (attempt {payout.retry_count + 1})",
                status_code=status.HTTP_200_OK,
            )

        except Exception as e:
            return ApiResponse.error(
                message=str(e),
                code="RETRY_ERROR",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PayoutRotationViewSet(viewsets.ViewSet):
    """ViewSet for managing payout rotation."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter(name='pk', location=OpenApiParameter.PATH, type=OpenApiTypes.STR),
        ]
    )
    def retrieve(self, request, pk=None):
        """
        Get payout rotation for a chama.

        GET /api/v1/payouts/rotations/{chama_id}/
        """
        return self.get_rotation(request, pk=pk)

    @action(detail=True, methods=["get"], permission_classes=[IsAuthenticated])
    def get_rotation(self, request, pk=None):
        """
        Get payout rotation for a chama.

        GET /api/v1/payouts/rotations/{chama_id}/get_rotation/
        """
        try:
            chama = Chama.objects.get(id=pk)

            # Verify user is member of chama
            if not request.user.memberships.filter(chama=chama).exists():
                return ApiResponse.error(
                    message="You are not a member of this chama",
                    code="NOT_MEMBER",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            rotation, _ = PayoutRotation.objects.get_or_create(chama=chama)

            return ApiResponse.success(
                data=PayoutRotationSerializer(rotation).data,
                message="Rotation retrieved",
                status_code=status.HTTP_200_OK,
            )

        except Chama.DoesNotExist:
            return ApiResponse.error(
                message="Chama not found",
                code="NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            return ApiResponse.error(
                message=str(e),
                code="ROTATION_ERROR",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
