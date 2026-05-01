# Investments Module Views
# API endpoints for investments management

from django.db.models import Sum
from django.utils import timezone
from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.types import OpenApiTypes
from rest_framework import status, views, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.billing.gating import BillingAccessMixin

from .models import (
    Investment,
    InvestmentApprovalRequest,
    InvestmentDistribution,
    InvestmentProduct,
    InvestmentRedemptionRequest,
    InvestmentReturn,
    InvestmentTransaction,
    InvestmentValuation,
    MemberInvestmentPosition,
    MemberInvestment,
)
from .serializers import (
    AddReturnSerializer,
    ApproveInvestmentSerializer,
    InvestmentApprovalRequestSerializer,
    InvestmentCreateSerializer,
    InvestmentProductDetailSerializer,
    InvestmentProductListSerializer,
    InvestmentProjectionSerializer,
    InvestmentRedemptionRequestSerializer,
    InvestmentListSerializer,
    InvestmentOverviewSerializer,
    InvestmentPayoutSerializer,
    InvestmentReturnSerializer,
    InvestmentSerializer,
    InvestmentTransactionSerializer,
    InvestmentTransactionRecordSerializer,
    InvestmentUtilizationActionSerializer,
    MemberInvestmentPositionDetailSerializer,
    MemberInvestmentPositionListSerializer,
    InvestmentValuationSerializer,
    MemberInvestmentSerializer,
    PortfolioAnalyticsSerializer,
    PortfolioSummarySerializer,
    RedeemInvestmentSerializer,
    RedemptionProcessSerializer,
    RequestWithdrawalSerializer,
    StartInvestmentSerializer,
    UpdateValuationSerializer,
    UtilizeReturnsSerializer,
)
from .services import InvestmentService, InvestmentServiceError


class CalculateROIView(BillingAccessMixin, views.APIView):
    """ViewSet for calculating ROI"""
    permission_classes = [IsAuthenticated]
    billing_feature_key = 'full_finance_management'
    
    def get(self, request):
        """Calculate ROI for investments"""
        chama_id = request.query_params.get('chama_id')
        
        if not chama_id:
            return Response(
                {'error': 'chama_id required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        investments = Investment.objects.filter(chama_id=chama_id)
        
        # Calculate ROI for each investment
        roi_data = []
        for inv in investments:
            roi_data.append({
                'investment_id': inv.id,
                'name': inv.name,
                'roi': inv.roi
            })
        
        return Response({'roi_data': roi_data})


class UpcomingMaturitiesView(BillingAccessMixin, views.APIView):
    """ViewSet for upcoming maturities"""
    permission_classes = [IsAuthenticated]
    billing_feature_key = 'full_finance_management'
    
    def get(self, request):
        """Get upcoming maturities"""
        chama_id = request.query_params.get('chama_id')
        
        if not chama_id:
            return Response(
                {'error': 'chama_id required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        from datetime import date, timedelta
        today = date.today()
        upcoming = Investment.objects.filter(
            chama_id=chama_id,
            maturity_date__isnull=False,
            maturity_date__gte=today,
            maturity_date__lte=today + timedelta(days=30)
        ).order_by('maturity_date')
        
        serializer = InvestmentSerializer(upcoming, many=True)
        return Response(serializer.data)


class InvestmentOverviewView(BillingAccessMixin, views.APIView):
    """ViewSet for investment overview statistics"""
    permission_classes = [IsAuthenticated]
    billing_feature_key = 'full_finance_management'
    
    def list(self, request):
        """Get investment overview statistics"""
        chama_id = request.query_params.get('chama_id')
        
        if not chama_id:
            return Response(
                {'error': 'chama_id required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        investments = Investment.objects.filter(chama_id=chama_id)
        
        # Calculate totals
        total_invested = investments.aggregate(
            total=Sum('principal_amount')
        )['total'] or 0
        
        current_valuation = investments.aggregate(
            total=Sum('current_value')
        )['total'] or 0
        
        total_returns = InvestmentReturn.objects.filter(
            investment__chama_id=chama_id
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        total_profit_loss = float(current_valuation) + float(total_returns) - float(total_invested)
        
        # Average ROI
        investments_with_roi = investments.exclude(principal_amount=0)
        avg_roi = 0
        if investments_with_roi.exists():
            roi_values = []
            for inv in investments_with_roi:
                roi_values.append(inv.roi)
            avg_roi = sum(roi_values) / len(roi_values)
        
        # Counts
        active_count = investments.filter(status='ACTIVE').count()
        matured_count = investments.filter(status='MATURED').count()
        pending_count = investments.filter(status='PENDING_APPROVAL').count()
        
        # By type
        by_type = {}
        for inv_type in Investment.InvestmentType:
            type_total = investments.filter(investment_type=inv_type).aggregate(
                total=Sum('current_value')
            )['total'] or 0
            by_type[inv_type] = float(type_total)
        
        # Performance history (simplified)
        performance_history = []
        
        data = {
            'total_invested': total_invested,
            'current_valuation': current_valuation,
            'total_returns': total_returns,
            'total_profit_loss': total_profit_loss,
            'average_roi': avg_roi,
            'active_investments_count': active_count,
            'matured_investments_count': matured_count,
            'pending_approvals_count': pending_count,
            'by_type': by_type,
            'performance_history': performance_history
        }
        
        serializer = InvestmentOverviewSerializer(data)
        return Response(serializer.data)


class InvestmentViewSet(BillingAccessMixin, viewsets.ModelViewSet):
    """ViewSet for managing investments"""
    serializer_class = InvestmentSerializer
    permission_classes = [IsAuthenticated]
    billing_feature_key = 'full_finance_management'
    
    def get_queryset(self):
        chama_id = self.request.query_params.get('chama_id')
        investment_type = self.request.query_params.get('type')
        status_filter = self.request.query_params.get('status')
        
        queryset = Investment.objects.select_related('chama', 'created_by').all()
        
        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        
        if investment_type:
            queryset = queryset.filter(investment_type=investment_type)
        
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        return queryset
    
    def get_serializer_class(self):
        if self.action == 'list':
            return InvestmentListSerializer
        if self.action == 'create':
            return InvestmentCreateSerializer
        return InvestmentSerializer
    
    def perform_create(self, serializer):
        investment = serializer.save(
            created_by=self.request.user,
            status='PENDING_APPROVAL'
        )
        
        # Create approval request
        InvestmentApprovalRequest.objects.create(
            investment=investment,
            action_type='CREATE',
            requested_by=self.request.user
        )
    
    @action(detail=False, methods=['get'])
    def overview(self, request):
        """Get investment overview statistics"""
        chama_id = request.query_params.get('chama_id')
        
        if not chama_id:
            return Response(
                {'error': 'chama_id required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        investments = Investment.objects.filter(chama_id=chama_id)
        
        # Calculate totals
        total_invested = investments.aggregate(
            total=Sum('principal_amount')
        )['total'] or 0
        
        current_valuation = investments.aggregate(
            total=Sum('current_value')
        )['total'] or 0
        
        total_returns = InvestmentReturn.objects.filter(
            investment__chama_id=chama_id
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        total_profit_loss = float(current_valuation) + float(total_returns) - float(total_invested)
        
        # Average ROI
        investments_with_roi = investments.exclude(principal_amount=0)
        avg_roi = 0
        if investments_with_roi.exists():
            roi_values = []
            for inv in investments_with_roi:
                roi_values.append(inv.roi)
            avg_roi = sum(roi_values) / len(roi_values)
        
        # Counts
        active_count = investments.filter(status='ACTIVE').count()
        matured_count = investments.filter(status='MATURED').count()
        pending_count = investments.filter(status='PENDING_APPROVAL').count()
        
        # By type
        by_type = {}
        for inv_type in Investment.InvestmentType:
            type_total = investments.filter(investment_type=inv_type).aggregate(
                total=Sum('current_value')
            )['total'] or 0
            by_type[inv_type] = float(type_total)
        
        # Performance history (simplified)
        performance_history = []
        
        data = {
            'total_invested': total_invested,
            'current_valuation': current_valuation,
            'total_returns': total_returns,
            'total_profit_loss': total_profit_loss,
            'average_roi': avg_roi,
            'active_investments_count': active_count,
            'matured_investments_count': matured_count,
            'pending_approvals_count': pending_count,
            'by_type': by_type,
            'performance_history': performance_history
        }
        
        serializer = InvestmentOverviewSerializer(data)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def add_return(self, request, pk=None):
        """Add return (dividend/interest) to investment"""
        investment = self.get_object()
        serializer = AddReturnSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        data = serializer.validated_data
        
        # Create return record
        ret = InvestmentReturn.objects.create(
            investment=investment,
            return_type=data['return_type'],
            amount=data['amount'],
            date=data['date'],
            reference=data.get('reference', ''),
            notes=data.get('notes', ''),
            reinvested=data.get('reinvested', False),
            recorded_by=request.user
        )
        
        # Update investment current value if reinvested
        if data.get('reinvested', False):
            investment.current_value += data['amount']
            investment.save()
        
        return Response({
            'status': 'success',
            'return_id': ret.id
        })
    
    @action(detail=True, methods=['post'])
    def update_valuation(self, request, pk=None):
        """Update investment valuation"""
        investment = self.get_object()
        serializer = UpdateValuationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        data = serializer.validated_data
        
        # Create valuation record
        valuation = InvestmentValuation.objects.create(
            investment=investment,
            value=data['value'],
            date=data['date'],
            notes=data.get('notes', ''),
            recorded_by=request.user
        )
        
        # Update current value
        investment.current_value = data['value']
        investment.save()
        
        return Response({
            'status': 'success',
            'valuation_id': valuation.id
        })
    
    @action(detail=True, methods=['post'])
    def request_withdrawal(self, request, pk=None):
        """Request withdrawal from investment"""
        investment = self.get_object()
        serializer = RequestWithdrawalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        data = serializer.validated_data
        
        # Create approval request
        approval_request = InvestmentApprovalRequest.objects.create(
            investment=investment,
            action_type='WITHDRAW',
            amount=data['amount'],
            requested_by=request.user
        )
        
        return Response({
            'status': 'success',
            'request_id': approval_request.id
        })
    
    @action(detail=True, methods=['get'])
    def performance(self, request, pk=None):
        """Get investment performance data"""
        investment = self.get_object()
        
        # Get returns over time
        returns = investment.returns.all().order_by('date')
        
        # Get valuations over time
        valuations = investment.valuations.all().order_by('date')
        
        # Calculate performance metrics
        total_returns = sum(float(r.amount) for r in returns)
        roi = investment.roi
        
        return Response({
            'investment': InvestmentSerializer(investment).data,
            'total_returns': total_returns,
            'roi': roi,
            'returns_history': InvestmentReturnSerializer(returns, many=True).data,
            'valuations_history': InvestmentValuationSerializer(valuations, many=True).data
        })


class InvestmentApprovalViewSet(BillingAccessMixin, viewsets.ModelViewSet):
    """ViewSet for managing investment approvals"""
    serializer_class = InvestmentApprovalRequestSerializer
    permission_classes = [IsAuthenticated]
    billing_feature_key = 'full_finance_management'
    
    def get_queryset(self):
        chama_id = self.request.query_params.get('chama_id')
        status_filter = self.request.query_params.get('status')
        
        queryset = InvestmentApprovalRequest.objects.select_related(
            'investment__chama', 'requested_by', 'approved_by'
        ).all()
        
        if chama_id:
            queryset = queryset.filter(investment__chama_id=chama_id)
        
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        return queryset
    
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Approve an investment request"""
        approval = self.get_object()
        serializer = ApproveInvestmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        action_type = serializer.validated_data['action']
        
        if action_type == 'approve':
            approval.status = 'APPROVED'
            approval.approved_by = request.user
            approval.approved_at = timezone.now()
            approval.save()
            
            # Update investment status
            investment = approval.investment
            if approval.action_type == 'CREATE':
                investment.status = 'ACTIVE'
            elif approval.action_type == 'WITHDRAW':
                # Process withdrawal
                pass
            investment.save()
            
            return Response({'status': 'approved'})
        
        else:
            approval.status = 'REJECTED'
            approval.rejection_reason = serializer.validated_data.get('notes', '')
            approval.save()
            
            return Response({'status': 'rejected'})
    
    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        """Reject an investment request"""
        approval = self.get_object()
        
        approval.status = 'REJECTED'
        approval.rejection_reason = request.data.get('reason', '')
        approval.save()
        
        return Response({'status': 'rejected'})


class InvestmentTransactionViewSet(BillingAccessMixin, viewsets.ModelViewSet):
    """ViewSet for managing investment transactions"""
    serializer_class = InvestmentTransactionSerializer
    permission_classes = [IsAuthenticated]
    billing_feature_key = 'full_finance_management'
    
    def get_queryset(self):
        investment_id = self.request.query_params.get('investment_id')
        
        queryset = InvestmentTransaction.objects.select_related('investment').all()
        
        if investment_id:
            queryset = queryset.filter(investment_id=investment_id)
        
        return queryset


class InvestmentReturnViewSet(BillingAccessMixin, viewsets.ModelViewSet):
    """ViewSet for managing investment returns"""
    serializer_class = InvestmentReturnSerializer
    permission_classes = [IsAuthenticated]
    billing_feature_key = 'full_finance_management'
    
    def get_queryset(self):
        investment_id = self.request.query_params.get('investment_id')
        
        queryset = InvestmentReturn.objects.select_related('investment').all()
        
        if investment_id:
            queryset = queryset.filter(investment_id=investment_id)
        
        return queryset


class InvestmentValuationViewSet(BillingAccessMixin, viewsets.ModelViewSet):
    """ViewSet for managing investment valuations"""
    serializer_class = InvestmentValuationSerializer
    permission_classes = [IsAuthenticated]
    billing_feature_key = 'full_finance_management'
    
    def get_queryset(self):
        investment_id = self.request.query_params.get('investment_id')
        
        queryset = InvestmentValuation.objects.select_related('investment').all()
        
        if investment_id:
            queryset = queryset.filter(investment_id=investment_id)
        
        return queryset


class InvestmentDistributionViewSet(BillingAccessMixin, viewsets.ModelViewSet):
    """ViewSet for managing investment distributions"""
    serializer_class = InvestmentReturnSerializer
    permission_classes = [IsAuthenticated]
    billing_feature_key = 'full_finance_management'
    
    def get_queryset(self):
        chama_id = self.request.query_params.get('chama_id')
        
        queryset = InvestmentDistribution.objects.select_related('investment').all()
        
        if chama_id:
            queryset = queryset.filter(investment__chama_id=chama_id)
        
        return queryset


class MemberInvestmentViewSet(BillingAccessMixin, viewsets.ModelViewSet):
    """ViewSet for managing member investments"""
    serializer_class = MemberInvestmentSerializer
    permission_classes = [IsAuthenticated]
    billing_feature_key = 'full_finance_management'
    
    def get_queryset(self):
        investment_id = self.request.query_params.get('investment_id')
        member_id = self.request.query_params.get('member_id')
        
        queryset = MemberInvestment.objects.select_related('investment', 'member').all()
        
        if investment_id:
            queryset = queryset.filter(investment_id=investment_id)
        
        if member_id:
            queryset = queryset.filter(member_id=member_id)
        
        return queryset


class InvestmentProductViewSet(views.APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        chama_id = request.query_params.get("chama_id")
        if not chama_id:
            return Response({"detail": "chama_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        queryset = InvestmentProduct.objects.filter(chama_id=chama_id).order_by("name")
        if not request.user.is_staff:
            queryset = queryset.filter(status="active")

        search = str(request.query_params.get("search") or "").strip()
        category = request.query_params.get("category")
        risk = request.query_params.get("risk")
        product_status = request.query_params.get("status")

        if search:
            queryset = queryset.filter(name__icontains=search)
        if category:
            queryset = queryset.filter(category=category)
        if risk:
            queryset = queryset.filter(risk_level=risk)
        if product_status:
            queryset = queryset.filter(status=product_status)

        serializer = InvestmentProductListSerializer(queryset, many=True)
        return Response(serializer.data)

    def post(self, request):
        chama_id = request.data.get("chama_id") or request.query_params.get("chama_id")
        if not chama_id:
            return Response({"detail": "chama_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = InvestmentProductDetailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            product = InvestmentService.create_product(
                actor=request.user,
                chama_id=chama_id,
                payload=serializer.validated_data,
            )
        except InvestmentServiceError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(InvestmentProductDetailSerializer(product).data, status=status.HTTP_201_CREATED)


class InvestmentProductDetailView(views.APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, pk):
        return InvestmentProduct.objects.select_related("chama").get(pk=pk)

    def get(self, request, pk):
        try:
            product = self.get_object(pk)
        except InvestmentProduct.DoesNotExist:
            return Response({"detail": "Product not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(InvestmentProductDetailSerializer(product).data)

    def patch(self, request, pk):
        try:
            product = self.get_object(pk)
        except InvestmentProduct.DoesNotExist:
            return Response({"detail": "Product not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            InvestmentService._require_admin_membership(user=request.user, chama_id=product.chama_id)
        except InvestmentServiceError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        serializer = InvestmentProductDetailSerializer(product, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = serializer.save(updated_by=request.user)
        return Response(InvestmentProductDetailSerializer(updated).data)


class InvestmentProductProjectionView(views.APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        amount = request.data.get("amount")
        if not amount:
            return Response({"detail": "amount is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            product = InvestmentProduct.objects.get(pk=pk)
        except InvestmentProduct.DoesNotExist:
            return Response({"detail": "Product not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            projection = InvestmentService.calculate_projection(product=product, amount=amount)
        except Exception as exc:  # noqa: BLE001
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            InvestmentProjectionSerializer(
                {
                    "gross_returns": projection.gross_returns,
                    "management_fee": projection.management_fee,
                    "withholding_tax": projection.withholding_tax,
                    "net_returns": projection.net_returns,
                    "expected_value": projection.expected_value,
                }
            ).data
        )


class MemberPortfolioSummaryView(views.APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        chama_id = request.query_params.get("chama_id")
        if not chama_id:
            return Response({"detail": "chama_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            payload = InvestmentService.portfolio_summary(actor=request.user, chama_id=chama_id)
        except InvestmentServiceError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(PortfolioSummarySerializer(payload).data)


class MemberPortfolioAnalyticsView(views.APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        chama_id = request.query_params.get("chama_id")
        if not chama_id:
            return Response({"detail": "chama_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            payload = InvestmentService.portfolio_analytics(actor=request.user, chama_id=chama_id)
        except InvestmentServiceError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(PortfolioAnalyticsSerializer(payload).data)


class MemberInvestmentPositionViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def _get_queryset(self, request):
        chama_id = request.query_params.get("chama_id") or request.data.get("chama_id")
        queryset = MemberInvestmentPosition.objects.select_related("product", "chama", "member").filter(
            member=request.user
        )
        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        status_filter = request.query_params.get("status")
        product_id = request.query_params.get("product_id")
        risk = request.query_params.get("risk")
        if status_filter and status_filter != "all":
            queryset = queryset.filter(status=status_filter)
        if product_id:
            queryset = queryset.filter(product_id=product_id)
        if risk:
            queryset = queryset.filter(product__risk_level=risk)
        return queryset.order_by("-created_at")

    def list(self, request):
        queryset = list(self._get_queryset(request))
        for investment in queryset:
            InvestmentService.refresh_position_metrics(investment)
        return Response(MemberInvestmentPositionListSerializer(queryset, many=True).data)

    @extend_schema(
        parameters=[
            OpenApiParameter(name='pk', location=OpenApiParameter.PATH, type=OpenApiTypes.STR),
        ]
    )
    def retrieve(self, request, pk=None):
        investment = self._get_queryset(request).filter(pk=pk).first()
        if not investment:
            return Response({"detail": "Investment not found."}, status=status.HTTP_404_NOT_FOUND)
        InvestmentService.refresh_position_metrics(investment)
        return Response(MemberInvestmentPositionDetailSerializer(investment).data)

    def create(self, request):
        chama_id = request.data.get("chama_id") or request.query_params.get("chama_id")
        if not chama_id:
            return Response({"detail": "chama_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = StartInvestmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        product = InvestmentProduct.objects.filter(id=payload["product_id"], chama_id=chama_id).first()
        if not product:
            return Response({"detail": "Investment product not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            investment = InvestmentService.start_investment(
                actor=request.user,
                chama_id=chama_id,
                product=product,
                amount=payload["amount"],
                funding_source=payload["funding_source"],
                wallet_amount=payload.get("wallet_amount"),
                mpesa_amount=payload.get("mpesa_amount"),
                phone=payload.get("phone", ""),
                auto_reinvest=payload.get("auto_reinvest", False),
                idempotency_key=payload.get("idempotency_key") or None,
            )
        except (InvestmentServiceError, PaymentServiceError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        InvestmentService.refresh_position_metrics(investment)
        return Response(MemberInvestmentPositionDetailSerializer(investment).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def refresh(self, request, pk=None):
        investment = self._get_queryset(request).filter(pk=pk).first()
        if not investment:
            return Response({"detail": "Investment not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            investment = InvestmentService.refresh_investment_funding(actor=request.user, investment=investment)
        except (InvestmentServiceError, PaymentServiceError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(MemberInvestmentPositionDetailSerializer(investment).data)

    @action(detail=True, methods=["post"], url_path="utilize")
    def utilize(self, request, pk=None):
        investment = self._get_queryset(request).filter(pk=pk).first()
        if not investment:
            return Response({"detail": "Investment not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = UtilizeReturnsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            action_record = InvestmentService.utilize_returns(
                actor=request.user,
                investment=investment,
                action_type=serializer.validated_data["action_type"],
                amount=serializer.validated_data["amount"],
                beneficiary_phone=serializer.validated_data.get("beneficiary_phone", ""),
            )
        except InvestmentServiceError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(InvestmentUtilizationActionSerializer(action_record).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="redeem")
    def redeem(self, request, pk=None):
        investment = self._get_queryset(request).filter(pk=pk).first()
        if not investment:
            return Response({"detail": "Investment not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = RedeemInvestmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            redemption = InvestmentService.redeem_investment(
                actor=request.user,
                investment=investment,
                redemption_type=serializer.validated_data["redemption_type"],
                amount=serializer.validated_data.get("amount"),
                destination=serializer.validated_data["destination"],
                beneficiary_phone=serializer.validated_data.get("beneficiary_phone", ""),
                reason=serializer.validated_data.get("reason", ""),
            )
        except InvestmentServiceError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(InvestmentRedemptionRequestSerializer(redemption).data, status=status.HTTP_201_CREATED)


class MemberInvestmentHistoryView(views.APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        chama_id = request.query_params.get("chama_id")
        if not chama_id:
            return Response({"detail": "chama_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            InvestmentService._require_membership(user=request.user, chama_id=chama_id)
        except InvestmentServiceError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        transactions = InvestmentTransactionRecord.objects.filter(
            chama_id=chama_id,
            member=request.user,
        ).order_by("-processed_at", "-created_at")
        redemptions = InvestmentRedemptionRequest.objects.filter(
            investment__chama_id=chama_id,
            investment__member=request.user,
        ).order_by("-created_at")
        payouts = InvestmentPayout.objects.filter(
            investment__chama_id=chama_id,
            investment__member=request.user,
        ).order_by("-created_at")
        return Response(
            {
                "transactions": InvestmentTransactionRecordSerializer(transactions, many=True).data,
                "redemptions": InvestmentRedemptionRequestSerializer(redemptions, many=True).data,
                "payouts": InvestmentPayoutSerializer(payouts, many=True).data,
            }
        )


class InvestmentEducationView(views.APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            {
                "sections": [
                    {
                        "title": "How MyChama investments work",
                        "body": "Choose a product, fund it from wallet or M-Pesa, track growth, and decide how to use returns as they become available.",
                    },
                    {
                        "title": "Lock periods and liquidity",
                        "body": "Each product shows its lock period, maturity timeline, and any early redemption penalties before you commit funds.",
                    },
                    {
                        "title": "Returns utilization",
                        "body": "Available returns can be moved to wallet, sent to M-Pesa, or reinvested when the selected product allows it.",
                    },
                ],
                "faq": [
                    {
                        "question": "What happens if I redeem early?",
                        "answer": "Some products apply an early redemption penalty before net payout is calculated.",
                    },
                    {
                        "question": "Can I withdraw profits only?",
                        "answer": "Yes, for products that allow returns utilization you can move profits without touching principal.",
                    },
                ],
            }
        )


class AdminRedemptionQueueView(views.APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        chama_id = request.query_params.get("chama_id")
        if not chama_id:
            return Response({"detail": "chama_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            InvestmentService._require_admin_membership(user=request.user, chama_id=chama_id)
        except InvestmentServiceError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        queryset = InvestmentRedemptionRequest.objects.select_related(
            "investment",
            "investment__member",
            "investment__product",
            "payout",
        ).filter(investment__chama_id=chama_id).order_by("-created_at")
        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        return Response(InvestmentRedemptionRequestSerializer(queryset, many=True).data)


class AdminRedemptionProcessView(views.APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        serializer = RedemptionProcessSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        redemption = InvestmentRedemptionRequest.objects.select_related("investment").filter(pk=pk).first()
        if not redemption:
            return Response({"detail": "Redemption request not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            updated = InvestmentService.process_redemption_request(
                actor=request.user,
                redemption=redemption,
                action=serializer.validated_data["action"],
                failure_reason=serializer.validated_data.get("failure_reason", ""),
            )
        except InvestmentServiceError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(InvestmentRedemptionRequestSerializer(updated).data)


class AdminInvestmentAnalyticsView(views.APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        chama_id = request.query_params.get("chama_id")
        if not chama_id:
            return Response({"detail": "chama_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            analytics = InvestmentService.admin_analytics(actor=request.user, chama_id=chama_id)
        except InvestmentServiceError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(analytics)
