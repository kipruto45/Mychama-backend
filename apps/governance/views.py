# Governance Module Views
# API endpoints for Constitution/Rules, Approvals, Role Management

from django.db.models import Count, Q
from django.utils import timezone
from datetime import timedelta
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.billing.gating import BillingAccessMixin
from .models import (
    ChamaRule, RuleAcknowledgment, ApprovalRequest, ApprovalStep,
    RoleChange, RoleDelegation, RuleStatus, ApprovalStatus, 
    ApprovalLevel, RoleChangeStatus
)
from .serializers import (
    ChamaRuleSerializer, ChamaRuleCreateSerializer,
    RuleAcknowledgmentSerializer, AcknowledgeRuleSerializer,
    ApprovalRequestSerializer, ApprovalRequestCreateSerializer,
    ApprovalActionSerializer, ApprovalStepSerializer,
    RoleChangeSerializer, RoleChangeCreateSerializer,
    RoleDelegationSerializer, RoleDelegationCreateSerializer,
    GovernanceOverviewSerializer
)


class GovernanceBillingMixin(BillingAccessMixin):
    billing_feature_key = "governance_tools"


class ChamaRuleViewSet(GovernanceBillingMixin, viewsets.ModelViewSet):
    """ViewSet for managing chama rules/constitution"""
    serializer_class = ChamaRuleSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        chama_id = self.request.query_params.get('chama_id')
        category = self.request.query_params.get('category')
        rule_status = self.request.query_params.get('status')
        
        queryset = ChamaRule.objects.select_related(
            'chama', 'approved_by', 'created_by'
        ).all()
        
        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        elif hasattr(user, 'chama_members'):
            member_chamas = user.chama_members.values_list('chama_id', flat=True)
            queryset = queryset.filter(chama_id__in=member_chamas)
        
        if category:
            queryset = queryset.filter(category=category)
        
        if rule_status:
            queryset = queryset.filter(status=rule_status)
        
        return queryset
    
    def get_serializer_class(self):
        if self.action == 'create':
            return ChamaRuleCreateSerializer
        return ChamaRuleSerializer
    
    @action(detail=True, methods=['post'])
    def submit_for_approval(self, request, pk=None):
        """Submit rule for approval"""
        rule = self.get_object()
        
        if rule.status != RuleStatus.DRAFT:
            return Response(
                {'error': 'Only draft rules can be submitted for approval'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        rule.status = RuleStatus.PENDING_APPROVAL
        rule.save()
        
        return Response(ChamaRuleSerializer(rule).data)
    
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Approve a rule"""
        rule = self.get_object()
        
        if rule.status != RuleStatus.PENDING_APPROVAL:
            return Response(
                {'error': 'Only pending rules can be approved'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        rule.status = RuleStatus.ACTIVE
        rule.approved_by = request.user
        rule.approved_at = timezone.now()
        rule.save()
        
        # Create acknowledgments for all members if required
        if rule.requires_acknowledgment:
            from apps.chama.models import Membership
            memberships = Membership.objects.filter(
                chama=rule.chama,
                status='active'
            )
            for membership in memberships:
                RuleAcknowledgment.objects.get_or_create(
                    rule=rule,
                    member=membership.user,
                    defaults={'status': 'pending'}
                )
        
        return Response(ChamaRuleSerializer(rule).data)
    
    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        """Reject a rule"""
        rule = self.get_object()
        
        if rule.status != RuleStatus.PENDING_APPROVAL:
            return Response(
                {'error': 'Only pending rules can be rejected'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        rule.status = RuleStatus.REJECTED
        rule.save()
        
        return Response(ChamaRuleSerializer(rule).data)
    
    @action(detail=False, methods=['get'])
    def my_acknowledgments(self, request):
        """Get rules pending acknowledgment for current user"""
        user = request.user
        
        acknowledgments = RuleAcknowledgment.objects.filter(
            member=user,
            status='pending'
        ).select_related('rule', 'rule__chama')
        
        return Response(RuleAcknowledgmentSerializer(acknowledgments, many=True).data)


class RuleAcknowledgmentViewSet(GovernanceBillingMixin, viewsets.ModelViewSet):
    """ViewSet for managing rule acknowledgments"""
    serializer_class = RuleAcknowledgmentSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        chama_id = self.request.query_params.get('chama_id')
        
        queryset = RuleAcknowledgment.objects.select_related(
            'rule', 'rule__chama', 'member'
        ).all()
        
        if chama_id:
            queryset = queryset.filter(rule__chama_id=chama_id)
        
        return queryset
    
    @action(detail=False, methods=['post'])
    def acknowledge(self, request):
        """Acknowledge a rule"""
        serializer = AcknowledgeRuleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        try:
            acknowledgment = RuleAcknowledgment.objects.get(
                rule_id=serializer.validated_data['rule_id'],
                member=request.user
            )
        except RuleAcknowledgment.DoesNotExist:
            return Response(
                {'error': 'No acknowledgment record found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        acknowledgment.status = 'acknowledged'
        acknowledgment.acknowledged_at = timezone.now()
        acknowledgment.ip_address = serializer.validated_data.get('ip_address')
        acknowledgment.device_info = serializer.validated_data.get('device_info', '')
        acknowledgment.save()
        
        return Response(RuleAcknowledgmentSerializer(acknowledgment).data)


class ApprovalRequestViewSet(GovernanceBillingMixin, viewsets.ModelViewSet):
    """ViewSet for approval requests"""
    serializer_class = ApprovalRequestSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        chama_id = self.request.query_params.get('chama_id')
        approval_type = self.request.query_params.get('type')
        req_status = self.request.query_params.get('status')
        
        queryset = ApprovalRequest.objects.select_related(
            'chama', 'requested_by', 'resolved_by', 'meeting'
        ).prefetch_related('steps').all()
        
        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        elif hasattr(user, 'chama_members'):
            member_chamas = user.chama_members.values_list('chama_id', flat=True)
            queryset = queryset.filter(chama_id__in=member_chamas)
        
        if approval_type:
            queryset = queryset.filter(approval_type=approval_type)
        
        if req_status:
            queryset = queryset.filter(status=req_status)
        
        return queryset
    
    def get_serializer_class(self):
        if self.action == 'create':
            return ApprovalRequestCreateSerializer
        return ApprovalRequestSerializer
    
    def perform_create(self, serializer):
        approval = serializer.save()
        
        # Create initial approval step
        ApprovalStep.objects.create(
            approval_request=approval,
            level=ApprovalLevel.FIRST,
            approver_role=approval.first_level_approver_role or 'admin',
            status=ApprovalStatus.PENDING
        )
    
    @action(detail=True, methods=['post'])
    def decide(self, request, pk=None):
        """Approve or reject an approval request"""
        approval = self.get_object()
        serializer = ApprovalActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        action = serializer.validated_data['action']
        comment = serializer.validated_data.get('comment', '')
        conditions = serializer.validated_data.get('conditions', {})
        
        if approval.status != ApprovalStatus.PENDING:
            return Response(
                {'error': 'Only pending requests can be decided'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        user = request.user
        current_level = approval.current_level
        
        # Create/update approval step
        step, _ = ApprovalStep.objects.get_or_create(
            approval_request=approval,
            level=current_level,
            defaults={'approver_role': 'unknown'}
        )
        
        step.status = ApprovalStatus.APPROVED if action == 'approve' else ApprovalStatus.REJECTED
        step.decided_by = user
        step.decision_at = timezone.now()
        step.comment = comment
        step.conditions = conditions
        step.save()
        
        if action == 'reject':
            approval.status = ApprovalStatus.REJECTED
            approval.resolved_by = user
            approval.resolved_at = timezone.now()
            approval.save()
            return Response(ApprovalRequestSerializer(approval).data)
        
        # Check if more levels needed
        next_level = None
        if current_level == ApprovalLevel.FIRST and approval.second_level_approver_role:
            next_level = ApprovalLevel.SECOND
        elif current_level == ApprovalLevel.SECOND:
            next_level = ApprovalLevel.FINAL
        
        if next_level:
            approval.current_level = next_level
            approval.save()
            
            # Create next step
            ApprovalStep.objects.create(
                approval_request=approval,
                level=next_level,
                approver_role=approval.second_level_approver_role,
                status=ApprovalStatus.PENDING
            )
        else:
            # All approvals complete
            approval.status = ApprovalStatus.APPROVED
            approval.resolved_by = user
            approval.resolved_at = timezone.now()
            approval.save()
        
        return Response(ApprovalRequestSerializer(approval).data)
    
    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Cancel an approval request"""
        approval = self.get_object()
        
        if approval.status != ApprovalStatus.PENDING:
            return Response(
                {'error': 'Only pending requests can be cancelled'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        approval.status = ApprovalStatus.CANCELLED
        approval.save()
        
        return Response(ApprovalRequestSerializer(approval).data)
    
    @action(detail=False, methods=['get'])
    def my_pending(self, request):
        """Get pending approvals for current user"""
        user = request.user
        chama_id = request.query_params.get('chama_id')
        
        # Get chamas where user has relevant roles
        from apps.chama.models import Membership
        if chama_id:
            memberships = Membership.objects.filter(
                chama_id=chama_id,
                user=user,
                status='active'
            )
        else:
            memberships = Membership.objects.filter(
                user=user,
                status='active'
            )
        
        # Get pending approvals for these roles
        pending = ApprovalRequest.objects.filter(
            chama__in=memberships.values_list('chama_id', flat=True),
            status=ApprovalStatus.PENDING
        ).select_related('chama', 'requested_by')
        
        return Response(ApprovalRequestSerializer(pending, many=True).data)


class RoleChangeViewSet(GovernanceBillingMixin, viewsets.ModelViewSet):
    """ViewSet for role changes"""
    serializer_class = RoleChangeSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        chama_id = self.request.query_params.get('chama_id')
        
        queryset = RoleChange.objects.select_related(
            'chama', 'member', 'approved_by', 'approval_request'
        ).all()
        
        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        
        return queryset
    
    def get_serializer_class(self):
        if self.action == 'create':
            return RoleChangeCreateSerializer
        return RoleChangeSerializer
    
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Approve a role change"""
        role_change = self.get_object()
        
        if role_change.status != RoleChangeStatus.PENDING:
            return Response(
                {'error': 'Only pending role changes can be approved'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        role_change.status = RoleChangeStatus.APPROVED
        role_change.approved_by = request.user
        role_change.approved_at = timezone.now()
        role_change.save()
        
        return Response(RoleChangeSerializer(role_change).data)
    
    @action(detail=True, methods=['post'])
    def make_effective(self, request, pk=None):
        """Make an approved role change effective"""
        role_change = self.get_object()
        
        if role_change.status != RoleChangeStatus.APPROVED:
            return Response(
                {'error': 'Only approved role changes can be made effective'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        from django.utils import timezone
        today = timezone.now().date()
        
        if role_change.effective_date > today:
            return Response(
                {'error': 'Effective date is in the future'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        role_change.status = RoleChangeStatus.EFFECTIVE
        role_change.save()
        
        return Response(RoleChangeSerializer(role_change).data)
    
    @action(detail=True, methods=['post'])
    def revoke(self, request, pk=None):
        """Revoke a role change"""
        role_change = self.get_object()
        
        if role_change.status != RoleChangeStatus.EFFECTIVE:
            return Response(
                {'error': 'Only effective role changes can be revoked'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        revocation_reason = request.data.get('reason', '')
        
        role_change.status = RoleChangeStatus.REJECTED
        role_change.revoked_at = timezone.now()
        role_change.revoked_by = request.user
        role_change.revocation_reason = revocation_reason
        role_change.save()
        
        return Response(RoleChangeSerializer(role_change).data)
    
    @action(detail=False, methods=['get'])
    def expiring_soon(self, request):
        """Get acting roles expiring soon"""
        from django.utils import timezone
        today = timezone.now().date()
        future = today + timedelta(days=7)
        
        expiring = RoleChange.objects.filter(
            chama_id=request.query_params.get('chama_id'),
            is_acting=True,
            status=RoleChangeStatus.EFFECTIVE,
            expiry_date__gte=today,
            expiry_date__lte=future
        ).select_related('chama', 'member')
        
        return Response(RoleChangeSerializer(expiring, many=True).data)


class RoleDelegationViewSet(GovernanceBillingMixin, viewsets.ModelViewSet):
    """ViewSet for role delegations"""
    serializer_class = RoleDelegationSerializer
    permission_classes = [IsAuthenticated]
    billing_feature_key = "role_delegation"
    
    def get_queryset(self):
        user = self.request.user
        chama_id = self.request.query_params.get('chama_id')
        
        queryset = RoleDelegation.objects.select_related(
            'chama', 'delegator', 'delegate', 'revoked_by'
        ).all()
        
        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        
        return queryset
    
    def get_serializer_class(self):
        if self.action == 'create':
            return RoleDelegationCreateSerializer
        return RoleDelegationSerializer
    
    @action(detail=True, methods=['post'])
    def revoke(self, request, pk=None):
        """Revoke a delegation"""
        delegation = self.get_object()
        
        if not delegation.is_active:
            return Response(
                {'error': 'Delegation is already inactive'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        delegation.is_active = False
        delegation.revoked_at = timezone.now()
        delegation.revoked_by = request.user
        delegation.save()
        
        return Response(RoleDelegationSerializer(delegation).data)


class GovernanceOverviewView(GovernanceBillingMixin, APIView):
    """Get governance overview statistics"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        chama_id = request.query_params.get('chama_id')
        
        queryset = ChamaRule.objects.all()
        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        
        data = {
            'total_rules': queryset.count(),
            'active_rules': queryset.filter(status=RuleStatus.ACTIVE).count(),
            'pending_acknowledgments': RuleAcknowledgment.objects.filter(
                rule__chama_id=chama_id if chama_id else None,
                status='pending'
            ).count(),
            'pending_approvals': ApprovalRequest.objects.filter(
                chama_id=chama_id if chama_id else None,
                status=ApprovalStatus.PENDING
            ).count(),
            'pending_role_changes': RoleChange.objects.filter(
                chama_id=chama_id if chama_id else None,
                status=RoleChangeStatus.PENDING
            ).count(),
            'expiring_acting_roles': RoleChange.objects.filter(
                chama_id=chama_id if chama_id else None,
                is_acting=True,
                status=RoleChangeStatus.EFFECTIVE,
                expiry_date__lte=timezone.now().date() + timedelta(days=7)
            ).count(),
        }
        
        return Response(data)
