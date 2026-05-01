# Fines Module Views
# API endpoints for fines management

from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone
from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.types import OpenApiTypes
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.gating import BillingAccessMixin
from apps.chama.models import Membership, MembershipRole, MemberStatus
from apps.chama.permissions import get_membership
from apps.chama.services import get_effective_role
from core.audit import create_activity_log, create_audit_log

from .models import (
    AmountType,
    Fine,
    FineAdjustment,
    FineCategory,
    FinePayment,
    FineReminder,
    FineRule,
    FineStatus,
)
from .serializers import (
    FineAdjustmentSerializer,
    FineAdjustSerializer,
    FineCategorySerializer,
    FineDisputeSerializer,
    FineIssueSerializer,
    FineListSerializer,
    FineOverviewSerializer,
    FinePaymentSerializer,
    FinePaySerializer,
    FineReminderSerializer,
    FineRuleSerializer,
    FineSerializer,
    FineWaiveSerializer,
)
from .services import FinesService, FinesServiceError


class FinesBillingMixin(BillingAccessMixin):
    billing_feature_key = "fines_management"


FINE_MANAGERS = {
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.TREASURER,
    MembershipRole.SECRETARY,
}
FINE_WAIVERS = {MembershipRole.CHAMA_ADMIN}
FINE_REPORTERS = {
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.TREASURER,
    MembershipRole.SECRETARY,
    MembershipRole.AUDITOR,
}


def _require_chama_membership(user, chama_id):
    membership = get_membership(user, chama_id)
    if not membership or not membership.is_active or not membership.is_approved:
        raise PermissionDenied("You are not an approved active member of this chama.")
    return membership


def _require_fine_roles(user, chama_id, allowed_roles: set[str], message: str):
    membership = _require_chama_membership(user, chama_id)
    effective_role = get_effective_role(user, chama_id, membership) or membership.role
    if effective_role not in allowed_roles and not getattr(user, "is_superuser", False):
        raise PermissionDenied(message)
    return membership, effective_role


def _restrict_to_member_chamas(queryset, user, chama_lookup='chama_id'):
    if hasattr(user, 'chama_members'):
        member_chamas = user.chama_members.values_list('chama_id', flat=True)
        return queryset.filter(**{f'{chama_lookup}__in': member_chamas})
    return queryset.none()


class FineRuleViewSet(FinesBillingMixin, viewsets.ModelViewSet):
    """ViewSet for managing fine rules"""
    serializer_class = FineRuleSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        chama_id = self.request.query_params.get('chama_id')
        
        queryset = FineRule.objects.all()
        
        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        else:
            queryset = _restrict_to_member_chamas(queryset, user)
        
        return queryset
    
    @action(detail=True, methods=['post'])
    def toggle(self, request, pk=None):
        """Toggle rule enabled status"""
        rule = self.get_object()
        rule.enabled = not rule.enabled
        rule.save()
        return Response({'status': 'success', 'enabled': rule.enabled})


class FineCategoryViewSet(FinesBillingMixin, viewsets.ViewSet):
    """Expose fine categories as API metadata"""
    permission_classes = [IsAuthenticated]

    def list(self, request):
        serializer = FineCategorySerializer(
            [{'value': value, 'label': label} for value, label in FineCategory.choices],
            many=True,
        )
        return Response(serializer.data)

    @extend_schema(
        parameters=[
            OpenApiParameter(name='id', location=OpenApiParameter.PATH, type=OpenApiTypes.STR),
        ]
    )
    def retrieve(self, request, pk=None):
        for value, label in FineCategory.choices:
            if value == pk:
                serializer = FineCategorySerializer({'value': value, 'label': label})
                return Response(serializer.data)
        return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)


class FineAdjustmentViewSet(FinesBillingMixin, viewsets.ReadOnlyModelViewSet):
    """Read-only access to fine adjustment history"""
    serializer_class = FineAdjustmentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        chama_id = self.request.query_params.get('chama_id')
        fine_id = self.request.query_params.get('fine_id')

        queryset = FineAdjustment.objects.select_related('fine', 'fine__chama', 'adjusted_by')

        if fine_id:
            queryset = queryset.filter(fine_id=fine_id)

        if chama_id:
            queryset = queryset.filter(fine__chama_id=chama_id)
        else:
            queryset = _restrict_to_member_chamas(queryset, user, 'fine__chama_id')

        return queryset


class FinePaymentViewSet(FinesBillingMixin, viewsets.ReadOnlyModelViewSet):
    """Read-only access to fine payment history"""
    serializer_class = FinePaymentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        chama_id = self.request.query_params.get('chama_id')
        fine_id = self.request.query_params.get('fine_id')

        queryset = FinePayment.objects.select_related('fine', 'fine__chama', 'recorded_by')

        if fine_id:
            queryset = queryset.filter(fine_id=fine_id)

        if chama_id:
            queryset = queryset.filter(fine__chama_id=chama_id)
        else:
            queryset = _restrict_to_member_chamas(queryset, user, 'fine__chama_id')

        return queryset


class FineReminderViewSet(FinesBillingMixin, viewsets.ReadOnlyModelViewSet):
    """Read-only access to fine reminder history"""
    serializer_class = FineReminderSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        chama_id = self.request.query_params.get('chama_id')
        fine_id = self.request.query_params.get('fine_id')

        queryset = FineReminder.objects.select_related('fine', 'fine__chama', 'sent_to')

        if fine_id:
            queryset = queryset.filter(fine_id=fine_id)

        if chama_id:
            queryset = queryset.filter(fine__chama_id=chama_id)
        else:
            queryset = _restrict_to_member_chamas(queryset, user, 'fine__chama_id')

        return queryset


class FineViewSet(FinesBillingMixin, viewsets.ModelViewSet):
    """ViewSet for managing fines"""
    serializer_class = FineSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Fine.objects.none()
        user = self.request.user
        chama_id = self.request.query_params.get('chama_id')
        member_id = self.request.query_params.get('member_id')
        status_filter = self.request.query_params.get('status')
        category = self.request.query_params.get('category')
        
        queryset = Fine.objects.select_related('member', 'issued_by', 'rule', 'chama').all()
        
        if chama_id:
            membership, effective_role = _require_fine_roles(
                user,
                chama_id,
                FINE_REPORTERS.union(FINE_MANAGERS).union({MembershipRole.MEMBER}),
                "You do not have access to this chama's fines.",
            )
            queryset = queryset.filter(chama_id=chama_id)
            if effective_role not in FINE_REPORTERS and effective_role not in FINE_MANAGERS:
                queryset = queryset.filter(member=user)
        else:
            queryset = queryset.filter(member=user)
        
        # Filter by member
        if member_id:
            queryset = queryset.filter(member_id=member_id)
        
        # Filter by status
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Filter by category
        if category:
            queryset = queryset.filter(category=category)
        
        return queryset
    
    def get_serializer_class(self):
        if self.action == 'list':
            return FineListSerializer
        return FineSerializer
    
    @action(detail=False, methods=['get'])
    def overview(self, request):
        """Get fines overview statistics"""
        chama_id = request.query_params.get('chama_id')
        
        if not chama_id:
            return Response({'error': 'chama_id required'}, status=status.HTTP_400_BAD_REQUEST)
        
        _require_fine_roles(
            request.user,
            chama_id,
            FINE_REPORTERS,
            "Only governance and finance roles can view fine reports.",
        )

        today = timezone.now().date()
        
        # Get all fines for the chama
        fines = Fine.objects.filter(chama_id=chama_id)
        
        # Calculate totals
        total_outstanding = fines.exclude(status__in=[FineStatus.PAID, FineStatus.WAIVED]).aggregate(
            total=Sum('amount')
        )['total'] or 0
        
        # This month
        month_start = today.replace(day=1)
        collected_this_month = fines.filter(
            status=FineStatus.PAID,
            paid_at__gte=month_start
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        waived_this_month = fines.filter(
            status=FineStatus.WAIVED,
            waived_at__gte=month_start
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        # Counts
        overdue_count = fines.filter(
            status=FineStatus.OVERDUE
        ).count()
        
        pending_count = fines.filter(
            status=FineStatus.PENDING
        ).count()
        
        total_fines_count = fines.count()
        
        # By category
        by_category = {}
        for cat in FineCategory:
            cat_total = fines.filter(category=cat).aggregate(total=Sum('amount'))['total'] or 0
            by_category[cat] = float(cat_total)
        
        # Monthly collections (last 6 months)
        monthly_collections = []
        for i in range(5, -1, -1):
            month_date = today - timedelta(days=30 * i)
            month_start = month_date.replace(day=1)
            if month_date.month == 12:
                month_end = month_date.replace(year=month_date.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                month_end = month_date.replace(month=month_date.month + 1, day=1) - timedelta(days=1)
            
            total = fines.filter(
                status=FineStatus.PAID,
                paid_at__gte=month_start,
                paid_at__lte=month_end
            ).aggregate(total=Sum('amount'))['total'] or 0
            
            monthly_collections.append({
                'month': month_start.strftime('%b %Y'),
                'amount': float(total)
            })
        
        data = {
            'total_outstanding': total_outstanding,
            'collected_this_month': collected_this_month,
            'waived_this_month': waived_this_month,
            'overdue_count': overdue_count,
            'pending_count': pending_count,
            'total_fines_count': total_fines_count,
            'by_category': by_category,
            'monthly_collections': monthly_collections
        }
        
        serializer = FineOverviewSerializer(data)
        return Response(serializer.data)

    @action(detail=False, methods=['post'], url_path='auto-generate', url_name='auto-generate')
    def auto_generate(self, request):
        """Generate fines from enabled flat-amount rules for active members"""
        chama_id = request.query_params.get('chama_id') or request.data.get('chama_id')

        _require_fine_roles(
            request.user,
            chama_id,
            FINE_MANAGERS,
            "Only authorized finance roles can auto-generate fines.",
        )

        if not chama_id:
            return Response({'error': 'chama_id required'}, status=status.HTTP_400_BAD_REQUEST)

        rules = FineRule.objects.filter(chama_id=chama_id, enabled=True).order_by('created_at')
        if not rules.exists():
            return Response({
                'status': 'success',
                'issued_fines': [],
                'count': 0,
                'skipped': 0,
                'message': 'No enabled fine rules found for this chama.',
            })

        member_ids = request.data.get('member_ids') or []
        memberships = Membership.objects.filter(
            chama_id=chama_id,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        ).select_related('user')

        if member_ids:
            memberships = memberships.filter(user_id__in=member_ids)

        memberships = list(memberships)
        if not memberships:
            return Response({
                'status': 'success',
                'issued_fines': [],
                'count': 0,
                'skipped': 0,
                'message': 'No eligible active members found for auto-generation.',
            })

        today = timezone.now().date()
        issued_fines = []
        skipped = 0

        for rule in rules:
            if rule.amount_type != AmountType.FLAT:
                skipped += len(memberships)
                continue

            due_date = today + timedelta(days=rule.grace_days or 0)

            for membership in memberships:
                duplicate_exists = Fine.objects.filter(
                    chama_id=chama_id,
                    member=membership.user,
                    rule=rule,
                    due_date=due_date,
                ).exclude(status__in=[FineStatus.PAID, FineStatus.WAIVED]).exists()

                if duplicate_exists:
                    skipped += 1
                    continue

                fine = Fine.objects.create(
                    chama_id=chama_id,
                    member=membership.user,
                    category=rule.category,
                    rule=rule,
                    amount=rule.amount_value,
                    due_date=due_date,
                    status=FineStatus.PENDING,
                    issued_by=request.user,
                    issued_reason=f'Auto-generated from rule: {rule.name}',
                )
                issued_fines.append(fine.id)
                create_activity_log(
                    actor=request.user,
                    chama_id=chama_id,
                    action="fine_auto_generated",
                    entity_type="Fine",
                    entity_id=fine.id,
                    metadata={"rule_id": str(rule.id), "member_id": str(membership.user_id)},
                )

        return Response({
            'status': 'success',
            'issued_fines': issued_fines,
            'count': len(issued_fines),
            'skipped': skipped,
        })
    
    @action(detail=False, methods=['post'])
    def issue(self, request):
        """Issue a new fine to members"""
        serializer = FineIssueSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        data = serializer.validated_data
        chama_id = request.query_params.get('chama_id')
        
        if not chama_id:
            return Response({'error': 'chama_id required'}, status=status.HTTP_400_BAD_REQUEST)

        _require_fine_roles(
            request.user,
            chama_id,
            FINE_MANAGERS,
            "Only authorized finance roles can issue fines.",
        )

        memberships = {
            str(item.user_id): item
            for item in Membership.objects.filter(
                chama_id=chama_id,
                user_id__in=data['member_ids'],
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
            ).select_related('user')
        }

        issued_fines = []
        for member_id in data['member_ids']:
            membership = memberships.get(str(member_id))
            if not membership:
                continue
            fine = FinesService.issue_fine(
                chama=membership.chama,
                member=membership.user,
                payload=data,
                actor=request.user,
            )
            issued_fines.append(fine.id)
        
        return Response({
            'status': 'success',
            'issued_fines': issued_fines,
            'count': len(issued_fines),
            'fines': FineSerializer(
                Fine.objects.filter(id__in=issued_fines).select_related('member', 'issued_by', 'rule', 'chama'),
                many=True,
            ).data,
        })
    
    @action(detail=True, methods=['post'])
    def waive(self, request, pk=None):
        """Waive a fine"""
        fine = self.get_object()
        serializer = FineWaiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _require_fine_roles(
            request.user,
            str(fine.chama_id),
            FINE_WAIVERS,
            "Only chama admins can waive fines.",
        )

        try:
            fine = FinesService.waive_fine(
                fine_id=fine.id,
                reason=serializer.validated_data['reason'],
                actor=request.user,
            )
        except FinesServiceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'status': 'success', 'message': 'Fine waived successfully', 'fine': FineSerializer(fine).data})
    
    @action(detail=True, methods=['post'])
    def adjust(self, request, pk=None):
        """Adjust a fine amount"""
        fine = self.get_object()
        serializer = FineAdjustSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        new_amount = serializer.validated_data['new_amount']
        
        # Create adjustment record
        FineAdjustment.objects.create(
            fine=fine,
            before_amount=fine.amount,
            after_amount=new_amount,
            reason=serializer.validated_data['reason'],
            adjusted_by=request.user
        )
        
        fine.amount = new_amount
        fine.save()
        
        return Response({'status': 'success', 'new_amount': str(new_amount)})
    
    @action(detail=True, methods=['post'])
    def pay(self, request, pk=None):
        """Record payment for a fine"""
        fine = self.get_object()
        serializer = FinePaySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        membership, effective_role = _require_fine_roles(
            request.user,
            str(fine.chama_id),
            FINE_MANAGERS.union({MembershipRole.MEMBER}),
            "You do not have permission to pay this fine.",
        )
        if effective_role == MembershipRole.MEMBER and fine.member_id != request.user.id:
            raise PermissionDenied("Members can only pay their own fines.")

        data = serializer.validated_data
        try:
            fine, payment = FinesService.pay_fine(
                fine_id=fine.id,
                payload=data,
                actor=request.user,
            )
        except FinesServiceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'status': 'success',
            'payment_id': payment.id,
            'fine': FineSerializer(fine).data,
            'payment': FinePaymentSerializer(payment).data,
            'outstanding_amount': fine.outstanding_amount,
        })
    
    @action(detail=True, methods=['post'])
    def dispute(self, request, pk=None):
        """Dispute a fine"""
        fine = self.get_object()
        serializer = FineDisputeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        membership, effective_role = _require_fine_roles(
            request.user,
            str(fine.chama_id),
            FINE_MANAGERS.union({MembershipRole.MEMBER}),
            "You do not have permission to dispute this fine.",
        )
        if effective_role == MembershipRole.MEMBER and fine.member_id != request.user.id:
            raise PermissionDenied("Members can only dispute their own fines.")
        
        fine.status = FineStatus.DISPUTED
        fine.disputed_at = timezone.now()
        fine.dispute_reason = serializer.validated_data['reason']
        fine.save()

        create_activity_log(
            actor=request.user,
            chama_id=fine.chama_id,
            action="fine_disputed",
            entity_type="Fine",
            entity_id=fine.id,
            metadata={"reason": fine.dispute_reason},
        )
        create_audit_log(
            actor=request.user,
            chama_id=fine.chama_id,
            action="fine_disputed",
            entity_type="Fine",
            entity_id=fine.id,
            metadata={"reason": fine.dispute_reason},
        )
        
        return Response({'status': 'success', 'message': 'Fine disputed successfully'})
    
    @action(detail=True, methods=['post'])
    def resolve_dispute(self, request, pk=None):
        """Resolve a dispute"""
        fine = self.get_object()
        _require_fine_roles(
            request.user,
            str(fine.chama_id),
            FINE_MANAGERS,
            "Only authorized finance roles can resolve fine disputes.",
        )
        
        if fine.status != FineStatus.DISPUTED:
            return Response({'error': 'Fine is not disputed'}, status=status.HTTP_400_BAD_REQUEST)
        
        resolution = request.data.get('resolution', '')
        
        # Restore to previous status or set to due
        fine.status = FineStatus.DUE
        fine.dispute_resolved_at = timezone.now()
        fine.dispute_resolution = resolution
        fine.save()

        create_activity_log(
            actor=request.user,
            chama_id=fine.chama_id,
            action="fine_dispute_resolved",
            entity_type="Fine",
            entity_id=fine.id,
            metadata={"resolution": resolution},
        )
        create_audit_log(
            actor=request.user,
            chama_id=fine.chama_id,
            action="fine_dispute_resolved",
            entity_type="Fine",
            entity_id=fine.id,
            metadata={"resolution": resolution},
        )
        
        return Response({'status': 'success', 'message': 'Dispute resolved successfully'})
    
    @action(detail=True, methods=['get'])
    def timeline(self, request, pk=None):
        """Get fine timeline"""
        fine = self.get_object()
        
        timeline = []
        
        # Issued
        timeline.append({
            'event': 'Fine Issued',
            'date': fine.created_at,
            'details': fine.issued_reason
        })
        
        # Adjustments
        for adj in fine.adjustments.all():
            timeline.append({
                'event': 'Amount Adjusted',
                'date': adj.created_at,
                'details': f'{adj.before_amount} → {adj.after_amount}. Reason: {adj.reason}'
            })
        
        # Payments
        for payment in fine.payments.all():
            timeline.append({
                'event': 'Payment Received',
                'date': payment.created_at,
                'details': f'{payment.amount} via {payment.get_method_display()}'
            })
        
        # Dispute
        if fine.disputed_at:
            timeline.append({
                'event': 'Disputed',
                'date': fine.disputed_at,
                'details': fine.dispute_reason
            })
        
        # Waived
        if fine.waived_at:
            timeline.append({
                'event': 'Waived',
                'date': fine.waived_at,
                'details': 'Fine waived'
            })
        
        # Sort by date
        timeline.sort(key=lambda x: x['date'], reverse=True)
        
        return Response(timeline)
    
    @action(detail=False, methods=['get'])
    def my_fines(self, request):
        """Get fines for the current user"""
        user = request.user
        
        # Get fines where user is the member
        fines = Fine.objects.filter(
            member=user
        ).select_related('chama', 'issued_by').order_by('-created_at')
        
        serializer = self.get_serializer(fines, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def my_fines_stats(self, request):
        """Get fine statistics for the current user"""
        user = request.user
        
        fines = Fine.objects.filter(member=user)
        
        total_outstanding = fines.exclude(status__in=[FineStatus.PAID, FineStatus.WAIVED]).aggregate(
            total=Sum('amount')
        )['total'] or 0
        
        total_paid = fines.filter(status=FineStatus.PAID).aggregate(
            total=Sum('amount')
        )['total'] or 0
        
        return Response({
            'total_outstanding': total_outstanding,
            'total_paid': total_paid,
            'overdue_count': fines.filter(status=FineStatus.OVERDUE).count(),
            'pending_count': fines.filter(status=FineStatus.PENDING).count()
        })


class MemberFinesView(APIView):
    """Standalone view for members to get their own fines"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        """Get fines for the current user"""
        user = request.user
        
        # Get fines where user is the member
        fines = Fine.objects.filter(
            member=user
        ).select_related('chama', 'issued_by').order_by('-created_at')
        
        serializer = FineListSerializer(fines, many=True)
        return Response(serializer.data)


class MemberFineStatsView(APIView):
    """Standalone view for members to get their fine statistics"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        """Get fine statistics for the current user"""
        user = request.user
        
        fines = Fine.objects.filter(member=user)
        
        total_outstanding = fines.exclude(status__in=[FineStatus.PAID, FineStatus.WAIVED]).aggregate(
            total=Sum('amount')
        )['total'] or 0
        
        total_paid = fines.filter(status=FineStatus.PAID).aggregate(
            total=Sum('amount')
        )['total'] or 0
        
        return Response({
            'total_outstanding': float(total_outstanding),
            'total_paid': float(total_paid),
            'overdue_count': fines.filter(status=FineStatus.OVERDUE).count(),
            'pending_count': fines.filter(status=FineStatus.PENDING).count()
        })
