# Governance Module Views
# API endpoints for Constitution/Rules, Approvals, Role Management

from datetime import timedelta

from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.gating import BillingAccessMixin
from apps.chama.models import Membership, MembershipRole, MemberStatus
from apps.chama.permissions import get_membership
from apps.chama.services import canonicalize_role, get_effective_role
from core.algorithms.governance import quorum_required
from core.audit import create_activity_log, create_audit_log
from core.models import AuditLog

from .models import (
    ApprovalLevel,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalStep,
    ChamaRule,
    Motion,
    MotionResult,
    MotionStatus,
    MotionVote,
    MotionVoteChoice,
    MotionVoteType,
    RoleChange,
    RoleChangeStatus,
    RoleDelegation,
    RuleAcknowledgment,
    RuleStatus,
)
from .serializers import (
    AcknowledgeRuleSerializer,
    ApprovalActionSerializer,
    ApprovalRequestCreateSerializer,
    ApprovalRequestSerializer,
    ChamaRuleCreateSerializer,
    ChamaRuleSerializer,
    MotionCreateSerializer,
    MotionResultSerializer,
    MotionSerializer,
    MotionVoteCreateSerializer,
    RoleChangeCreateSerializer,
    RoleChangeSerializer,
    RoleDelegationCreateSerializer,
    RoleDelegationSerializer,
    RuleAcknowledgmentSerializer,
)


class GovernanceBillingMixin(BillingAccessMixin):
    billing_feature_key = "governance_tools"
    # Governance workflows are role-protected but should remain accessible
    # during trial/free periods.
    skip_billing_access = True


GOVERNANCE_RULE_MANAGERS = {
    MembershipRole.CHAMA_ADMIN,
}

GOVERNANCE_RULE_READERS = {
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.SECRETARY,
    MembershipRole.TREASURER,
    MembershipRole.AUDITOR,
}

GOVERNANCE_APPROVAL_OPERATORS = {
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.SECRETARY,
    MembershipRole.TREASURER,
}

GOVERNANCE_APPROVAL_READERS = GOVERNANCE_APPROVAL_OPERATORS | {
    MembershipRole.AUDITOR,
}

GOVERNANCE_ROLE_MANAGERS = {
    MembershipRole.CHAMA_ADMIN,
}

GOVERNANCE_ROLE_READERS = GOVERNANCE_ROLE_MANAGERS | {
    MembershipRole.AUDITOR,
}

GOVERNANCE_MOTION_MANAGERS = {
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.SECRETARY,
}

GOVERNANCE_MOTION_VOTERS = {
    MembershipRole.MEMBER,
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.SECRETARY,
    MembershipRole.TREASURER,
}


def _require_governance_membership(user, chama_id):
    membership = get_membership(user, chama_id)
    if not membership or not membership.is_active or not membership.is_approved:
        raise PermissionDenied("You are not an approved active member of this chama.")
    return membership


def _require_governance_roles(user, chama_id, allowed_roles: set[str], message: str):
    membership = _require_governance_membership(user, chama_id)
    effective_role = get_effective_role(user, chama_id, membership) or membership.role
    if effective_role not in allowed_roles and not getattr(user, "is_superuser", False):
        raise PermissionDenied(message)
    return membership, effective_role


def _resolve_governance_role(user, chama_id):
    membership = _require_governance_membership(user, chama_id)
    effective_role = get_effective_role(user, chama_id, membership) or membership.role
    return membership, effective_role


def _normalize_approver_role(raw_role: str | None) -> str | None:
    if not raw_role:
        return None

    raw_value = str(raw_role).strip()
    upper_value = raw_value.upper()
    valid_membership_roles = {choice for choice, _label in MembershipRole.choices}
    if upper_value in valid_membership_roles:
        return canonicalize_role(upper_value)

    return {
        "super_admin": MembershipRole.CHAMA_ADMIN,
        "superadmin": MembershipRole.CHAMA_ADMIN,
        "admin": MembershipRole.CHAMA_ADMIN,
        "chairperson": MembershipRole.CHAMA_ADMIN,
        "chama_admin": MembershipRole.CHAMA_ADMIN,
        "treasurer": MembershipRole.TREASURER,
        "secretary": MembershipRole.SECRETARY,
        "auditor": MembershipRole.AUDITOR,
        "member": MembershipRole.MEMBER,
    }.get(raw_value.lower())


class ChamaRuleViewSet(GovernanceBillingMixin, viewsets.ModelViewSet):
    """ViewSet for managing chama rules/constitution"""
    serializer_class = ChamaRuleSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None
    
    def get_queryset(self):
        user = self.request.user
        chama_id = self.request.query_params.get('chama_id')
        category = self.request.query_params.get('category')
        rule_status = self.request.query_params.get('status')
        
        queryset = ChamaRule.objects.select_related(
            'chama', 'approved_by', 'created_by'
        ).all()

        if chama_id:
            membership, effective_role = _resolve_governance_role(user, chama_id)
            queryset = queryset.filter(chama_id=chama_id)
            if effective_role not in GOVERNANCE_RULE_READERS and not getattr(user, "is_superuser", False):
                queryset = queryset.filter(status=RuleStatus.ACTIVE)
        elif hasattr(user, 'chama_members'):
            member_chamas = user.chama_members.values_list('chama_id', flat=True)
            queryset = queryset.filter(chama_id__in=member_chamas)
        
        if category:
            queryset = queryset.filter(category=category)
        
        if rule_status:
            queryset = queryset.filter(status=rule_status)
        
        return queryset

    def perform_create(self, serializer):
        chama_id = str(serializer.validated_data["chama"].id)
        _require_governance_roles(
            self.request.user,
            chama_id,
            GOVERNANCE_RULE_MANAGERS,
            "Only chama admins can create or edit governance rules.",
        )
        serializer.save(created_by=self.request.user)
    
    def get_serializer_class(self):
        if self.action == 'create':
            return ChamaRuleCreateSerializer
        return ChamaRuleSerializer
    
    @action(detail=True, methods=['post'])
    def submit_for_approval(self, request, pk=None):
        """Submit rule for approval"""
        rule = self.get_object()
        _require_governance_roles(
            request.user,
            str(rule.chama_id),
            GOVERNANCE_RULE_MANAGERS,
            "Only chama admins can submit governance rules for approval.",
        )
        
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
        _require_governance_roles(
            request.user,
            str(rule.chama_id),
            GOVERNANCE_RULE_MANAGERS,
            "Only chama admins can approve governance rules.",
        )
        
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
        _require_governance_roles(
            request.user,
            str(rule.chama_id),
            GOVERNANCE_RULE_MANAGERS,
            "Only chama admins can reject governance rules.",
        )
        
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
    pagination_class = None
    
    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return RuleAcknowledgment.objects.none()
        chama_id = self.request.query_params.get('chama_id')

        queryset = RuleAcknowledgment.objects.select_related(
            'rule', 'rule__chama', 'member'
        ).all()

        if chama_id:
            membership, effective_role = _resolve_governance_role(self.request.user, chama_id)
            queryset = queryset.filter(rule__chama_id=chama_id)
            if effective_role not in GOVERNANCE_RULE_READERS and not getattr(self.request.user, "is_superuser", False):
                queryset = queryset.filter(member=self.request.user)
        elif not getattr(self.request.user, "is_superuser", False):
            queryset = queryset.filter(member=self.request.user)

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
            membership, effective_role = _resolve_governance_role(user, chama_id)
            queryset = queryset.filter(chama_id=chama_id)
            if effective_role not in GOVERNANCE_APPROVAL_READERS and not getattr(user, "is_superuser", False):
                queryset = queryset.filter(requested_by=user)
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
        chama_id = str(serializer.validated_data["chama"].id)
        _require_governance_roles(
            self.request.user,
            chama_id,
            GOVERNANCE_APPROVAL_OPERATORS,
            "Only chama operational roles can create approval requests.",
        )
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
        _membership, effective_role = _require_governance_roles(
            user,
            str(approval.chama_id),
            GOVERNANCE_APPROVAL_OPERATORS,
            "Only approvers can decide this approval request.",
        )

        # Create/update approval step
        step, _ = ApprovalStep.objects.get_or_create(
            approval_request=approval,
            level=current_level,
            defaults={'approver_role': 'unknown'}
        )
        expected_role = _normalize_approver_role(
            step.approver_role
            or approval.first_level_approver_role
            or approval.second_level_approver_role
        )
        if expected_role and effective_role not in {expected_role, MembershipRole.CHAMA_ADMIN}:
            raise PermissionDenied("This approval is assigned to a different role level.")
        
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
        membership, effective_role = _resolve_governance_role(request.user, str(approval.chama_id))
        if not getattr(request.user, "is_superuser", False):
            if approval.requested_by_id != request.user.id and effective_role not in GOVERNANCE_APPROVAL_OPERATORS:
                raise PermissionDenied("Only the requester or governance approvers can cancel this approval.")
        
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
        role_values = {
            get_effective_role(user, str(membership.chama_id), membership) or membership.role
            for membership in memberships
        }
        reviewer_roles = {_normalize_approver_role(role) for role in role_values}
        reviewer_roles.discard(None)
        if reviewer_roles:
            pending = pending.filter(
                models.Q(first_level_approver_role__in=reviewer_roles)
                | models.Q(second_level_approver_role__in=reviewer_roles)
            )
        else:
            pending = pending.none()

        return Response(ApprovalRequestSerializer(pending, many=True).data)


class RoleChangeViewSet(GovernanceBillingMixin, viewsets.ModelViewSet):
    """ViewSet for role changes"""
    serializer_class = RoleChangeSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        chama_id = self.request.query_params.get('chama_id')
        
        queryset = RoleChange.objects.select_related(
            'chama', 'member', 'approved_by', 'approval_request'
        ).all()

        if chama_id:
            membership, effective_role = _resolve_governance_role(self.request.user, chama_id)
            queryset = queryset.filter(chama_id=chama_id)
            if effective_role not in GOVERNANCE_ROLE_READERS and not getattr(self.request.user, "is_superuser", False):
                queryset = queryset.filter(member=self.request.user)

        return queryset
    
    def get_serializer_class(self):
        if self.action == 'create':
            return RoleChangeCreateSerializer
        return RoleChangeSerializer

    def perform_create(self, serializer):
        chama_id = str(serializer.validated_data["chama"].id)
        _require_governance_roles(
            self.request.user,
            chama_id,
            GOVERNANCE_ROLE_MANAGERS,
            "Only chama admins can manage role changes.",
        )
        serializer.save()
    
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Approve a role change"""
        role_change = self.get_object()
        _require_governance_roles(
            request.user,
            str(role_change.chama_id),
            GOVERNANCE_ROLE_MANAGERS,
            "Only chama admins can approve role changes.",
        )
        
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
        _require_governance_roles(
            request.user,
            str(role_change.chama_id),
            GOVERNANCE_ROLE_MANAGERS,
            "Only chama admins can make role changes effective.",
        )
        
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

        from apps.automations.domain_services import (
            apply_membership_role_change,
            notify_role_change,
        )

        try:
            membership, previous_role, outgoing_memberships = apply_membership_role_change(
                chama=role_change.chama,
                member_user=role_change.member,
                new_role=role_change.new_role,
                actor=request.user,
            )
        except ValueError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        role_change.status = RoleChangeStatus.EFFECTIVE
        role_change.old_role = previous_role or role_change.old_role
        role_change.save()
        notify_role_change(
            chama=role_change.chama,
            membership=membership,
            old_role=previous_role or "",
            new_role=role_change.new_role,
            outgoing_memberships=outgoing_memberships,
            actor=request.user,
            reason=role_change.reason,
        )

        return Response(RoleChangeSerializer(role_change).data)
    
    @action(detail=True, methods=['post'])
    def revoke(self, request, pk=None):
        """Revoke a role change"""
        role_change = self.get_object()
        _require_governance_roles(
            request.user,
            str(role_change.chama_id),
            GOVERNANCE_ROLE_MANAGERS,
            "Only chama admins can revoke role changes.",
        )
        
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
        chama_id = request.query_params.get('chama_id')
        if chama_id:
            _require_governance_roles(
                request.user,
                chama_id,
                GOVERNANCE_ROLE_READERS,
                "Only governance reviewers can inspect expiring role changes.",
            )
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
        chama_id = self.request.query_params.get('chama_id')
        
        queryset = RoleDelegation.objects.select_related(
            'chama', 'delegator', 'delegate', 'revoked_by'
        ).all()

        if chama_id:
            membership, effective_role = _resolve_governance_role(self.request.user, chama_id)
            queryset = queryset.filter(chama_id=chama_id)
            if effective_role not in GOVERNANCE_ROLE_READERS and not getattr(self.request.user, "is_superuser", False):
                queryset = queryset.filter(
                    models.Q(delegator=self.request.user) | models.Q(delegate=self.request.user)
                )

        return queryset
    
    def get_serializer_class(self):
        if self.action == 'create':
            return RoleDelegationCreateSerializer
        return RoleDelegationSerializer

    def perform_create(self, serializer):
        chama_id = str(serializer.validated_data["chama"].id)
        _require_governance_roles(
            self.request.user,
            chama_id,
            GOVERNANCE_ROLE_MANAGERS,
            "Only chama admins can manage role delegations.",
        )
        serializer.save(delegator=self.request.user)
    
    @action(detail=True, methods=['post'])
    def revoke(self, request, pk=None):
        """Revoke a delegation"""
        delegation = self.get_object()
        _require_governance_roles(
            request.user,
            str(delegation.chama_id),
            GOVERNANCE_ROLE_MANAGERS,
            "Only chama admins can revoke role delegations.",
        )
        
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


class MotionViewSet(GovernanceBillingMixin, viewsets.ModelViewSet):
    serializer_class = MotionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Motion.objects.none()
        user = self.request.user
        chama_id = self.request.query_params.get('chama_id')

        queryset = Motion.objects.select_related(
            'chama', 'created_by', 'closed_by'
        ).prefetch_related('votes', 'result')

        if chama_id:
            _require_governance_membership(user, chama_id)
            queryset = queryset.filter(chama_id=chama_id)
        else:
            member_chamas = Membership.objects.filter(
                user=user,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
            ).values_list('chama_id', flat=True)
            queryset = queryset.filter(chama_id__in=member_chamas)

        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        return queryset

    def get_serializer_class(self):
        if self.action == 'create':
            return MotionCreateSerializer
        if self.action == 'cast_vote':
            return MotionVoteCreateSerializer
        return MotionSerializer

    @staticmethod
    def _eligible_memberships(motion: Motion):
        queryset = Membership.objects.select_related('user').filter(
            chama=motion.chama,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
        )
        if motion.eligible_roles:
            queryset = queryset.filter(role__in=motion.eligible_roles)
        return queryset

    @staticmethod
    def _notify_motion_members(motion: Motion, *, subject: str, message: str, suffix: str):
        try:
            from apps.notifications.models import NotificationType
            from apps.notifications.services import NotificationService
        except Exception:  # noqa: BLE001
            return

        for membership in MotionViewSet._eligible_memberships(motion):
            NotificationService.send_notification(
                user=membership.user,
                chama=motion.chama,
                channels=["in_app", "email"],
                message=message,
                subject=subject,
                notification_type=NotificationType.MEETING_NOTIFICATION,
                idempotency_key=f"motion:{motion.id}:{membership.user_id}:{suffix}",
                actor=motion.created_by,
            )

    @staticmethod
    def _finalize_motion(motion: Motion, *, actor=None):
        if motion.status == MotionStatus.CLOSED and hasattr(motion, 'result'):
            return motion.result

        eligible_count = MotionViewSet._eligible_memberships(motion).count()
        votes = MotionVote.objects.filter(motion=motion)
        total_votes = votes.count()
        yes_votes = votes.filter(vote=MotionVoteChoice.YES).count()
        no_votes = votes.filter(vote=MotionVoteChoice.NO).count()
        abstain_votes = votes.filter(vote=MotionVoteChoice.ABSTAIN).count()
        required_votes = quorum_required(
            total_members=eligible_count,
            quorum_percentage=motion.quorum_percent,
        )
        quorum_met = total_votes >= required_votes
        if motion.vote_type == MotionVoteType.SPECIAL:
            decisive_votes = yes_votes + no_votes
            passed = quorum_met and decisive_votes > 0 and (yes_votes / decisive_votes) >= (2 / 3)
        elif motion.vote_type == MotionVoteType.UNANIMOUS:
            passed = (
                quorum_met
                and yes_votes == eligible_count
                and no_votes == 0
                and abstain_votes == 0
            )
        else:
            passed = quorum_met and yes_votes > no_votes

        result, _created = MotionResult.objects.update_or_create(
            motion=motion,
            defaults={
                'total_votes': total_votes,
                'yes_votes': yes_votes,
                'no_votes': no_votes,
                'abstain_votes': abstain_votes,
                'eligible_voters': eligible_count,
                'quorum_met': quorum_met,
                'passed': passed,
                'calculated_at': timezone.now(),
                'created_by': actor,
                'updated_by': actor,
            },
        )

        if motion.status != MotionStatus.CLOSED:
            motion.status = MotionStatus.CLOSED
            motion.closed_at = timezone.now()
            motion.closed_by = actor
            motion.save(update_fields=['status', 'closed_at', 'closed_by', 'updated_at'])
            create_audit_log(
                actor=actor,
                chama_id=motion.chama_id,
                action='motion_closed',
                entity_type='Motion',
                entity_id=motion.id,
                metadata={
                    'quorum_met': quorum_met,
                    'passed': passed,
                    'total_votes': total_votes,
                    'vote_type': motion.vote_type,
                },
            )
            MotionViewSet._notify_motion_members(
                motion,
                subject='Motion result available',
                message=(
                    f"Voting has closed for '{motion.title}'. "
                    f"Result: {'passed' if passed else 'rejected'}."
                ),
                suffix='result',
            )
        return result

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        chama_id = str(serializer.validated_data['chama'].id)
        _require_governance_roles(
            request.user,
            chama_id,
            GOVERNANCE_MOTION_MANAGERS,
            "Only governance roles can create motions.",
        )
        motion = serializer.save(created_by=request.user, updated_by=request.user)
        create_activity_log(
            actor=request.user,
            chama_id=motion.chama_id,
            action='motion_created',
            entity_type='Motion',
            entity_id=motion.id,
            metadata={'title': motion.title, 'end_time': motion.end_time.isoformat()},
        )
        create_audit_log(
            actor=request.user,
            chama_id=motion.chama_id,
            action='motion_created',
            entity_type='Motion',
            entity_id=motion.id,
            metadata={'quorum_percent': motion.quorum_percent},
        )
        self._notify_motion_members(
            motion,
            subject='New motion created',
            message=f"A new motion is open for voting: {motion.title}",
            suffix='created',
        )
        return Response(MotionSerializer(motion, context={'request': request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def cast_vote(self, request, pk=None):
        motion = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        membership, effective_role = _require_governance_roles(
            request.user,
            str(motion.chama_id),
            GOVERNANCE_MOTION_VOTERS,
            "You are not allowed to vote on this motion.",
        )

        if motion.status != MotionStatus.OPEN or timezone.now() >= motion.end_time:
            self._finalize_motion(motion, actor=request.user)
            return Response(
                {'detail': 'Voting window is closed for this motion.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if motion.start_time > timezone.now():
            return Response({'detail': 'Voting has not started yet.'}, status=status.HTTP_400_BAD_REQUEST)
        if motion.eligible_roles and effective_role not in set(motion.eligible_roles):
            raise PermissionDenied('Your role is not eligible to vote on this motion.')

        vote, _created = MotionVote.objects.update_or_create(
            motion=motion,
            user=request.user,
            defaults={
                'vote': serializer.validated_data['vote'],
                'created_by': request.user,
                'updated_by': request.user,
            },
        )
        create_activity_log(
            actor=request.user,
            chama_id=motion.chama_id,
            action='motion_vote_cast',
            entity_type='Motion',
            entity_id=motion.id,
            metadata={'vote': vote.vote},
        )
        return Response(MotionSerializer(motion, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        motion = self.get_object()
        _require_governance_roles(
            request.user,
            str(motion.chama_id),
            GOVERNANCE_MOTION_MANAGERS,
            "Only governance approvers can close motions.",
        )
        result = self._finalize_motion(motion, actor=request.user)
        return Response(
            {
                'motion': MotionSerializer(motion, context={'request': request}).data,
                'result': MotionResultSerializer(result).data,
            }
        )

    @action(detail=True, methods=['get'])
    def results(self, request, pk=None):
        motion = self.get_object()
        if motion.status == MotionStatus.OPEN and timezone.now() >= motion.end_time:
            self._finalize_motion(motion, actor=request.user)
            motion.refresh_from_db()
        result = getattr(motion, 'result', None)
        return Response(
            {
                'motion': MotionSerializer(motion, context={'request': request}).data,
                'result': MotionResultSerializer(result).data if result else None,
            }
        )


class GovernanceOverviewView(GovernanceBillingMixin, APIView):
    """Get governance overview statistics"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        chama_id = request.query_params.get('chama_id')
        if not chama_id:
            raise ValidationError({"chama_id": "chama_id is required."})
        _membership, effective_role = _require_governance_roles(
            request.user,
            chama_id,
            GOVERNANCE_RULE_READERS,
            "You are not allowed to inspect governance overview metrics.",
        )

        queryset = ChamaRule.objects.all()
        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        if effective_role not in GOVERNANCE_RULE_READERS and not getattr(request.user, "is_superuser", False):
            queryset = queryset.filter(status=RuleStatus.ACTIVE)

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


class GovernanceAuditTrailView(GovernanceBillingMixin, APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        chama_id = request.query_params.get("chama_id")
        if not chama_id:
            raise ValidationError({"chama_id": "chama_id is required."})
        _require_governance_roles(
            request.user,
            chama_id,
            GOVERNANCE_RULE_READERS,
            "You are not allowed to inspect governance audit logs.",
        )

        queryset = AuditLog.objects.filter(chama_id=chama_id, entity_type__in=["Motion", "RoleChange", "ChamaRule"])
        action_filter = request.query_params.get("action")
        if action_filter:
            queryset = queryset.filter(action=action_filter)
        actor_id = request.query_params.get("member_id")
        if actor_id:
            queryset = queryset.filter(actor_id=actor_id)
        start_date = request.query_params.get("start_date")
        if start_date:
            queryset = queryset.filter(created_at__date__gte=start_date)
        end_date = request.query_params.get("end_date")
        if end_date:
            queryset = queryset.filter(created_at__date__lte=end_date)

        queryset = queryset.select_related("actor")[:200]
        data = [
            {
                "id": str(log.id),
                "action": log.action,
                "entity_type": log.entity_type,
                "entity_id": str(log.entity_id) if log.entity_id else None,
                "actor_id": str(log.actor_id) if log.actor_id else None,
                "actor_name": log.actor.get_full_name() if log.actor else "",
                "metadata": log.metadata,
                "created_at": log.created_at,
            }
            for log in queryset
        ]
        return Response(data)
