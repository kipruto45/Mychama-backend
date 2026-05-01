# Governance Module Serializers

from datetime import timedelta

from rest_framework import serializers

from apps.accounts.serializers import UserSerializer
from apps.automations.domain_services import ensure_no_dual_role_conflict
from apps.chama.models import Membership, MemberStatus

from .models import (
    ApprovalLevel,
    ApprovalRequest,
    ApprovalStep,
    ChamaRule,
    Motion,
    MotionResult,
    MotionVote,
    MotionVoteChoice,
    MotionVoteType,
    RoleChange,
    RoleDelegation,
    RuleAcknowledgment,
    RuleStatus,
)


class ChamaRuleSerializer(serializers.ModelSerializer):
    """Serializer for chama rules/constitution"""
    acknowledgment_rate = serializers.FloatField(read_only=True)
    approved_by_name = serializers.CharField(source='approved_by.get_full_name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    category_display = serializers.CharField(source='get_category_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = ChamaRule
        fields = [
            'id', 'chama', 'category', 'category_display', 'title', 'description',
            'content', 'version', 'status', 'status_display', 'effective_date',
            'expiry_date', 'previous_version', 'requires_acknowledgment',
            'acknowledgment_deadline_days', 'acknowledgment_rate',
            'approved_by', 'approved_by_name', 'approved_at',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['version', 'created_at', 'updated_at']


class ChamaRuleCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating new rules with version management"""
    
    class Meta:
        model = ChamaRule
        fields = [
            'chama', 'category', 'title', 'description', 'content',
            'effective_date', 'expiry_date', 'requires_acknowledgment',
            'acknowledgment_deadline_days'
        ]

    def create(self, validated_data):
        chama = validated_data['chama']
        category = validated_data['category']
        
        # Get latest version for this category
        latest = ChamaRule.objects.filter(
            chama=chama, 
            category=category
        ).order_by('-version').first()
        
        new_version = (latest.version + 1) if latest else 1
        
        # If there's an active rule, archive it
        if latest and latest.status == RuleStatus.ACTIVE:
            latest.status = RuleStatus.ARCHIVED
            latest.save()
        
        validated_data['version'] = new_version
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)


class RuleAcknowledgmentSerializer(serializers.ModelSerializer):
    """Serializer for rule acknowledgments"""
    member = serializers.SerializerMethodField()
    member_name = serializers.CharField(source='member.get_full_name', read_only=True)
    rule_title = serializers.CharField(source='rule.title', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = RuleAcknowledgment
        fields = [
            'id', 'rule', 'rule_title', 'member', 'member_name', 
            'status', 'status_display', 'acknowledged_at', 
            'ip_address', 'device_info', 'created_at'
        ]
        read_only_fields = ['acknowledged_at', 'ip_address', 'device_info']

    def get_member(self, obj) -> str:
        return str(obj.member_id)


class AcknowledgeRuleSerializer(serializers.Serializer):
    """Serializer for acknowledging a rule"""
    rule_id = serializers.UUIDField()
    ip_address = serializers.IPAddressField(required=False)
    device_info = serializers.CharField(required=False)


class ApprovalStepSerializer(serializers.ModelSerializer):
    """Serializer for approval steps"""
    decided_by_name = serializers.CharField(source='decided_by.get_full_name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    level_display = serializers.CharField(source='get_level_display', read_only=True)

    class Meta:
        model = ApprovalStep
        fields = [
            'id', 'approval_request', 'level', 'level_display', 
            'approver_role', 'status', 'status_display',
            'decision_at', 'decided_by', 'decided_by_name', 'comment', 'conditions',
            'created_at'
        ]


class ApprovalRequestSerializer(serializers.ModelSerializer):
    """Serializer for approval requests"""
    requested_by_name = serializers.CharField(source='requested_by.get_full_name', read_only=True)
    approval_type_display = serializers.CharField(source='get_approval_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    current_level_display = serializers.CharField(source='get_current_level_display', read_only=True)
    steps = ApprovalStepSerializer(many=True, read_only=True)
    approvers_needed = serializers.SerializerMethodField()

    class Meta:
        model = ApprovalRequest
        fields = [
            'id', 'chama', 'approval_type', 'approval_type_display',
            'reference_type', 'reference_id', 'reference_display',
            'title', 'description', 'amount', 'currency',
            'requested_by', 'requested_by_name',
            'status', 'status_display', 'required_level', 'current_level', 
            'current_level_display', 'first_level_approver_role', 
            'second_level_approver_role', 'first_level_threshold',
            'due_date', 'resolved_at', 'resolved_by', 'meeting',
            'steps', 'approvers_needed', 'created_at', 'updated_at'
        ]
        read_only_fields = ['status', 'current_level', 'resolved_at', 'resolved_by']

    def get_approvers_needed(self, obj) -> dict:
        return obj.get_approvers_needed()


class ApprovalRequestCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating approval requests"""
    
    class Meta:
        model = ApprovalRequest
        fields = [
            'chama', 'approval_type', 'reference_type', 'reference_id',
            'reference_display', 'title', 'description', 'amount', 'currency',
            'first_level_approver_role', 'second_level_approver_role',
            'first_level_threshold', 'due_date', 'meeting'
        ]

    def create(self, validated_data):
        validated_data['requested_by'] = self.context['request'].user
        
        # Determine required level based on amount
        amount = validated_data.get('amount')
        threshold = validated_data.get('first_level_threshold')
        
        if amount and threshold and amount > threshold:
            validated_data['required_level'] = ApprovalLevel.SECOND
        else:
            validated_data['required_level'] = ApprovalLevel.FIRST
        
        validated_data['current_level'] = ApprovalLevel.FIRST
        return super().create(validated_data)


class ApprovalActionSerializer(serializers.Serializer):
    """Serializer for approving/rejecting approval requests"""
    action = serializers.ChoiceField(choices=['approve', 'reject'])
    comment = serializers.CharField(required=False, allow_blank=True)
    conditions = serializers.JSONField(required=False, default=dict)


class RoleChangeSerializer(serializers.ModelSerializer):
    """Serializer for role changes"""
    member_name = serializers.CharField(source='member.get_full_name', read_only=True)
    change_type_display = serializers.CharField(source='get_change_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    approved_by_name = serializers.CharField(source='approved_by.get_full_name', read_only=True)

    class Meta:
        model = RoleChange
        fields = [
            'id', 'chama', 'member', 'member_name', 'change_type', 
            'change_type_display', 'old_role', 'new_role',
            'effective_date', 'expiry_date', 'status', 'status_display',
            'approval_request', 'reason', 'approved_by', 'approved_by_name',
            'approved_at', 'is_acting', 'acting_reason',
            'revoked_at', 'revoked_by', 'revocation_reason',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['status', 'approved_by', 'approved_at']


class RoleChangeCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating role changes"""
    
    class Meta:
        model = RoleChange
        fields = [
            'chama', 'member', 'change_type', 'old_role', 'new_role',
            'effective_date', 'expiry_date', 'reason', 'is_acting', 'acting_reason'
        ]

    def validate(self, data):
        # Validate effective date is in the future or today
        from django.utils import timezone
        today = timezone.now().date()
        
        if data['effective_date'] < today:
            raise serializers.ValidationError({
                'effective_date': 'Effective date must be today or in the future'
            })
        
        # For acting roles, expiry date is required
        if data.get('is_acting') and not data.get('expiry_date'):
            raise serializers.ValidationError({
                'expiry_date': 'Expiry date is required for acting roles'
            })

        active_membership = Membership.objects.filter(
            chama=data["chama"],
            user=data["member"],
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        ).first()
        if not active_membership:
            raise serializers.ValidationError(
                {"member": "Role changes require an active approved member in this chama."}
            )

        try:
            ensure_no_dual_role_conflict(
                chama=data["chama"],
                user=data["member"],
                new_role=data["new_role"],
            )
        except ValueError as exc:
            raise serializers.ValidationError({"new_role": str(exc)}) from exc
        
        return data


class RoleDelegationSerializer(serializers.ModelSerializer):
    """Serializer for role delegations"""
    delegator_name = serializers.CharField(source='delegator.get_full_name', read_only=True)
    delegate_name = serializers.CharField(source='delegate.get_full_name', read_only=True)
    is_valid = serializers.BooleanField(read_only=True)

    class Meta:
        model = RoleDelegation
        fields = [
            'id', 'chama', 'delegator', 'delegator_name', 'delegate', 
            'delegate_name', 'role', 'start_date', 'end_date',
            'is_active', 'is_valid', 'revoked_at', 'revoked_by',
            'can_delegate_further', 'restrictions', 'created_at'
        ]
        read_only_fields = ['is_active', 'revoked_at', 'revoked_by']


class RoleDelegationCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating delegations"""
    
    class Meta:
        model = RoleDelegation
        fields = [
            'chama', 'delegate', 'role', 'start_date', 'end_date',
            'can_delegate_further', 'restrictions'
        ]


class GovernanceOverviewSerializer(serializers.Serializer):
    """Overview statistics for governance dashboard"""
    total_rules = serializers.IntegerField()
    active_rules = serializers.IntegerField()
    pending_acknowledgments = serializers.IntegerField()
    pending_approvals = serializers.IntegerField()
    pending_role_changes = serializers.IntegerField()
    expiring_acting_roles = serializers.IntegerField()


class MotionResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = MotionResult
        fields = [
            'id',
            'motion',
            'total_votes',
            'yes_votes',
            'no_votes',
            'abstain_votes',
            'eligible_voters',
            'quorum_met',
            'passed',
            'calculated_at',
        ]


class MotionVoteSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)

    class Meta:
        model = MotionVote
        fields = ['id', 'motion', 'user', 'vote', 'created_at', 'updated_at']
        read_only_fields = ['created_at', 'updated_at']


class MotionSerializer(serializers.ModelSerializer):
    created_by = UserSerializer(read_only=True)
    closed_by = UserSerializer(read_only=True)
    result = MotionResultSerializer(read_only=True)
    my_vote = serializers.SerializerMethodField()
    vote_summary = serializers.SerializerMethodField()

    class Meta:
        model = Motion
        fields = [
            'id',
            'chama',
            'title',
            'description',
            'created_by',
            'status',
            'start_time',
            'end_time',
            'quorum_percent',
            'vote_type',
            'closed_at',
            'closed_by',
            'eligible_roles',
            'result',
            'my_vote',
            'vote_summary',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at', 'result', 'my_vote', 'vote_summary']

    def get_my_vote(self, obj) -> dict | None:
        request = self.context.get('request')
        if not request or not getattr(request, 'user', None) or not request.user.is_authenticated:
            return None
        vote = obj.votes.filter(user=request.user).first()
        return MotionVoteSerializer(vote).data if vote else None

    def get_vote_summary(self, obj) -> dict:
        votes = obj.votes.all()
        return {
            'total_votes': votes.count(),
            'yes_votes': votes.filter(vote=MotionVoteChoice.YES).count(),
            'no_votes': votes.filter(vote=MotionVoteChoice.NO).count(),
            'abstain_votes': votes.filter(vote=MotionVoteChoice.ABSTAIN).count(),
        }


class MotionCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Motion
        fields = [
            'chama',
            'title',
            'description',
            'start_time',
            'end_time',
            'quorum_percent',
            'vote_type',
            'eligible_roles',
        ]

    def validate(self, attrs):
        start_time = attrs.get('start_time')
        end_time = attrs.get('end_time')
        if start_time and end_time and end_time <= start_time:
            raise serializers.ValidationError({'end_time': 'end_time must be after start_time'})
        if start_time and end_time:
            duration = end_time - start_time
            if duration < timedelta(hours=24):
                raise serializers.ValidationError(
                    {'end_time': 'Voting period must be at least 24 hours.'}
                )
            if duration > timedelta(days=7):
                raise serializers.ValidationError(
                    {'end_time': 'Voting period cannot exceed 7 days.'}
                )
        vote_type = attrs.get("vote_type", MotionVoteType.ORDINARY)
        if vote_type not in {choice for choice, _ in MotionVoteType.choices}:
            raise serializers.ValidationError({'vote_type': 'Unsupported vote type.'})
        return attrs


class MotionVoteCreateSerializer(serializers.Serializer):
    vote = serializers.ChoiceField(choices=MotionVoteChoice.choices)
