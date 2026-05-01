"""
Views for wallet transfers, chama payments, and loan updates.
"""

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from django.db import transaction
from decimal import Decimal

from apps.finance.models import (
    WalletTransfer,
    ChamaPayment,
    LoanUpdateRequest,
    Wallet,
    Loan,
)
from apps.finance.serializers_new import (
    WalletTransferSerializer,
    WalletTransferRequestSerializer,
    ChamaPaymentSerializer,
    ChamaPaymentRequestSerializer,
    LoanUpdateRequestSerializer,
    LoanUpdateRequestCreateSerializer,
    LoanUpdateApprovalSerializer,
)
from apps.finance.services_new import (
    WalletTransferService,
    ChamaPaymentService,
    LoanUpdateService,
)
from apps.chama.models import Chama
from core.mixins import ChamaScopeMixin
from core.permissions import IsAuthenticated as CoreIsAuthenticated


class WalletTransferViewSet(ChamaScopeMixin, viewsets.ModelViewSet):
    """
    ViewSet for member-to-member wallet transfers.
    
    Endpoints:
    - POST /finance/transfers/ - Initiate a transfer
    - GET /finance/transfers/ - List transfers
    - GET /finance/transfers/{id}/ - Get transfer details
    """
    
    serializer_class = WalletTransferSerializer
    permission_classes = [IsAuthenticated]
    queryset = WalletTransfer.objects.all()
    filterset_fields = ["chama", "status", "sender", "recipient"]
    ordering_fields = ["requested_at", "created_at"]
    ordering = ["-requested_at"]

    def get_queryset(self):
        """Filter transfers to current chama."""
        queryset = super().get_queryset()
        chama_id = self.request.query_params.get("chama")
        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        return queryset

    @action(detail=False, methods=["post"], url_path="request")
    def request_transfer(self, request):
        """
        Initiate a wallet transfer.
        
        Request body:
        {
            "recipient_id": "user_id",
            "amount": "100.00",
            "description": "Optional memo",
            "reference": "Optional-REF-CODE"
        }
        """
        serializer = WalletTransferRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        try:
            chama_id = request.query_params.get("chama") or request.data.get("chama")
            if not chama_id:
                return Response(
                    {"error": "chama parameter required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            transfer = WalletTransferService.request_transfer(
                chama_id=chama_id,
                sender_id=request.user.id,
                recipient_id=serializer.validated_data["recipient_id"].id,
                amount=serializer.validated_data["amount"],
                description=serializer.validated_data.get("description", ""),
                reference=serializer.validated_data.get("reference", ""),
            )
            
            return Response(
                WalletTransferSerializer(transfer).data,
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["post"], url_path="complete")
    def complete_transfer(self, request, pk=None):
        """
        Complete a wallet transfer by processing ledger entries.
        Only treasury/admin can complete transfers.
        """
        transfer = self.get_object()
        
        # Check permissions - only treasurer or admin
        if not (request.user.roles & {"TREASURER", "ADMIN"}):
            return Response(
                {"error": "Only treasurer or admin can complete transfers"},
                status=status.HTTP_403_FORBIDDEN,
            )
        
        try:
            with transaction.atomic():
                transfer = WalletTransferService.complete_transfer(transfer.id, actor=request.user)
            return Response(
                WalletTransferSerializer(transfer).data,
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )


class ChamaPaymentViewSet(ChamaScopeMixin, viewsets.ModelViewSet):
    """
    ViewSet for member-to-chama wallet payments/contributions.
    
    Endpoints:
    - POST /finance/payments/ - Initiate a payment
    - GET /finance/payments/ - List payments
    - GET /finance/payments/{id}/ - Get payment details
    """
    
    serializer_class = ChamaPaymentSerializer
    permission_classes = [IsAuthenticated]
    queryset = ChamaPayment.objects.all()
    filterset_fields = ["chama", "status", "member", "contribution_type"]
    ordering_fields = ["requested_at", "created_at"]
    ordering = ["-requested_at"]

    def get_queryset(self):
        """Filter payments to current chama."""
        queryset = super().get_queryset()
        chama_id = self.request.query_params.get("chama")
        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        return queryset

    @action(detail=False, methods=["post"], url_path="request")
    def request_payment(self, request):
        """
        Initiate a chama wallet payment.
        
        Request body:
        {
            "chama": "chama_id",
            "amount": "100.00",
            "contribution_type_id": "Optional",
            "description": "Optional memo",
            "reference": "Optional-REF-CODE"
        }
        """
        serializer = ChamaPaymentRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        try:
            chama_id = request.query_params.get("chama") or request.data.get("chama")
            if not chama_id:
                return Response(
                    {"error": "chama parameter required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            payment = ChamaPaymentService.request_payment(
                chama_id=chama_id,
                member_id=request.user.id,
                amount=serializer.validated_data["amount"],
                contribution_type_id=serializer.validated_data.get("contribution_type_id", ""),
                description=serializer.validated_data.get("description", ""),
                reference=serializer.validated_data.get("reference", ""),
            )
            
            return Response(
                ChamaPaymentSerializer(payment).data,
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["post"], url_path="complete")
    def complete_payment(self, request, pk=None):
        """
        Complete a chama wallet payment by processing ledger entries.
        Only treasury/admin can complete payments.
        """
        payment = self.get_object()
        
        # Check permissions - only treasurer or admin
        if not (request.user.roles & {"TREASURER", "ADMIN"}):
            return Response(
                {"error": "Only treasurer or admin can complete payments"},
                status=status.HTTP_403_FORBIDDEN,
            )
        
        try:
            with transaction.atomic():
                payment = ChamaPaymentService.complete_payment(payment.id, actor=request.user)
            return Response(
                ChamaPaymentSerializer(payment).data,
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )


class LoanUpdateRequestViewSet(ChamaScopeMixin, viewsets.ModelViewSet):
    """
    ViewSet for loan amount update requests.
    
    Endpoints:
    - POST /finance/loan-updates/ - Create update request
    - GET /finance/loan-updates/ - List update requests
    - GET /finance/loan-updates/{id}/ - Get update request details
    - POST /finance/loan-updates/{id}/approve/ - Approve update
    - POST /finance/loan-updates/{id}/reject/ - Reject update
    - POST /finance/loan-updates/{id}/apply/ - Apply approved update
    """
    
    serializer_class = LoanUpdateRequestSerializer
    permission_classes = [IsAuthenticated]
    queryset = LoanUpdateRequest.objects.all()
    filterset_fields = ["loan", "status", "reviewed_by"]
    ordering_fields = ["created_at", "reviewed_at"]
    ordering = ["-created_at"]

    def create(self, request, *args, **kwargs):
        """
        Create a loan update request.
        
        Request body:
        {
            "loan_id": "loan_id",
            "new_principal": "150.00" (optional),
            "new_duration_months": 12 (optional),
            "new_interest_rate": "15.00" (optional),
            "reason": "Reason for update"
        }
        """
        serializer = LoanUpdateRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        try:
            update_request = LoanUpdateService.request_update(
                loan_id=serializer.validated_data["loan_id"].id,
                new_principal=serializer.validated_data.get("new_principal"),
                new_duration_months=serializer.validated_data.get("new_duration_months"),
                new_interest_rate=serializer.validated_data.get("new_interest_rate"),
                reason=serializer.validated_data["reason"],
            )
            
            return Response(
                LoanUpdateRequestSerializer(update_request).data,
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """
        Approve a loan update request.
        Only treasurer/admin can approve.
        
        Request body (optional):
        {
            "notes": "Approval notes"
        }
        """
        update_request = self.get_object()
        
        # Check permissions
        if not (request.user.roles & {"TREASURER", "ADMIN"}):
            return Response(
                {"error": "Only treasurer or admin can approve updates"},
                status=status.HTTP_403_FORBIDDEN,
            )
        
        try:
            notes = request.data.get("notes", "")
            update_request = LoanUpdateService.approve_update(
                update_request.id,
                notes=notes,
                actor=request.user,
            )
            
            return Response(
                LoanUpdateRequestSerializer(update_request).data,
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        """
        Reject a loan update request.
        Only treasurer/admin can reject.
        
        Request body (optional):
        {
            "notes": "Rejection reason"
        }
        """
        update_request = self.get_object()
        
        # Check permissions
        if not (request.user.roles & {"TREASURER", "ADMIN"}):
            return Response(
                {"error": "Only treasurer or admin can reject updates"},
                status=status.HTTP_403_FORBIDDEN,
            )
        
        try:
            notes = request.data.get("notes", "")
            update_request = LoanUpdateService.reject_update(
                update_request.id,
                notes=notes,
                actor=request.user,
            )
            
            return Response(
                LoanUpdateRequestSerializer(update_request).data,
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["post"])
    def apply(self, request, pk=None):
        """
        Apply an approved loan update to the loan.
        Only admin can apply updates.
        """
        update_request = self.get_object()
        
        # Check permissions
        if not request.user.roles.get("ADMIN"):
            return Response(
                {"error": "Only admin can apply updates"},
                status=status.HTTP_403_FORBIDDEN,
            )
        
        try:
            with transaction.atomic():
                update_request = LoanUpdateService.apply_update(
                    update_request.id,
                    actor=request.user,
                )
            
            return Response(
                LoanUpdateRequestSerializer(update_request).data,
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
