# Investments Module Views
# API endpoints for investments management

from django.db.models import Sum, Count, Avg
from django.utils import timezone
from django.db import models
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.billing.gating import BillingAccessMixin
from .models import (
    Investment, InvestmentTransaction, InvestmentReturn, InvestmentValuation,
    InvestmentApprovalRequest, MemberInvestment, InvestmentDistribution
)
from .serializers import (
    InvestmentSerializer, InvestmentListSerializer, InvestmentCreateSerializer,
    InvestmentTransactionSerializer, InvestmentReturnSerializer,
    InvestmentValuationSerializer, InvestmentApprovalRequestSerializer,
    AddReturnSerializer, UpdateValuationSerializer, RequestWithdrawalSerializer,
    ApproveInvestmentSerializer, InvestmentOverviewSerializer
)


class InvestmentViewSet(BillingAccessMixin, viewsets.ModelViewSet):
    """ViewSet for managing investments"""
    serializer_class = InvestmentSerializer
    permission_classes = [IsAuthenticated]
    billing_feature_key = 'full_finance_management'
    
    def get_queryset(self):
        user = self.request.user
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
