# Fines Module Views
# API endpoints for fines management

from django.db.models import Sum
from django.utils import timezone
from datetime import timedelta
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from apps.billing.gating import BillingAccessMixin

from .models import (
    AmountType,
    Fine,
    FineRule,
    FineCategory,
    FineStatus,
    FineAdjustment,
    FinePayment,
    FineReminder,
)
from .serializers import (
    FineSerializer, FineListSerializer, FineRuleSerializer,
    FineAdjustmentSerializer, FinePaymentSerializer, FineReminderSerializer,
    FineCategorySerializer, FineIssueSerializer, FineWaiveSerializer, FineAdjustSerializer,
    FinePaySerializer, FineDisputeSerializer, FineOverviewSerializer
)


class FinesBillingMixin(BillingAccessMixin):
    billing_feature_key = "fines_management"


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
        user = self.request.user
        chama_id = self.request.query_params.get('chama_id')
        member_id = self.request.query_params.get('member_id')
        status_filter = self.request.query_params.get('status')
        category = self.request.query_params.get('category')
        
        queryset = Fine.objects.select_related('member', 'issued_by', 'rule', 'chama').all()
        
        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        else:
            queryset = _restrict_to_member_chamas(queryset, user)
        
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

        from apps.chama.models import MemberStatus, Membership

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
        
        issued_fines = []
        for member_id in data['member_ids']:
            fine = Fine.objects.create(
                chama_id=chama_id,
                member_id=member_id,
                category=data['category'],
                amount=data['amount'],
                due_date=data['due_date'],
                status=FineStatus.PENDING,
                issued_by=request.user,
                issued_reason=data['reason'],
                attachments=data.get('attachments', [])
            )
            issued_fines.append(fine.id)
        
        return Response({
            'status': 'success',
            'issued_fines': issued_fines,
            'count': len(issued_fines)
        })
    
    @action(detail=True, methods=['post'])
    def waive(self, request, pk=None):
        """Waive a fine"""
        fine = self.get_object()
        serializer = FineWaiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        # Create adjustment record
        FineAdjustment.objects.create(
            fine=fine,
            before_amount=fine.amount,
            after_amount=0,
            reason=serializer.validated_data['reason'],
            adjusted_by=request.user
        )
        
        fine.status = FineStatus.WAIVED
        fine.waived_at = timezone.now()
        fine.save()
        
        return Response({'status': 'success', 'message': 'Fine waived successfully'})
    
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
        
        data = serializer.validated_data
        
        # Create payment record
        payment = FinePayment.objects.create(
            fine=fine,
            amount=data['amount'],
            method=data['method'],
            transaction_reference=data.get('transaction_reference', ''),
            recorded_by=request.user,
            notes=data.get('notes', '')
        )
        
        # Check if fine is fully paid
        outstanding = fine.outstanding_amount
        if outstanding <= 0:
            fine.status = FineStatus.PAID
            fine.paid_at = timezone.now()
            fine.save()
        
        return Response({
            'status': 'success',
            'payment_id': payment.id,
            'outstanding_amount': fine.outstanding_amount
        })
    
    @action(detail=True, methods=['post'])
    def dispute(self, request, pk=None):
        """Dispute a fine"""
        fine = self.get_object()
        serializer = FineDisputeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        fine.status = FineStatus.DISPUTED
        fine.disputed_at = timezone.now()
        fine.dispute_reason = serializer.validated_data['reason']
        fine.save()
        
        return Response({'status': 'success', 'message': 'Fine disputed successfully'})
    
    @action(detail=True, methods=['post'])
    def resolve_dispute(self, request, pk=None):
        """Resolve a dispute"""
        fine = self.get_object()
        
        if fine.status != FineStatus.DISPUTED:
            return Response({'error': 'Fine is not disputed'}, status=status.HTTP_400_BAD_REQUEST)
        
        resolution = request.data.get('resolution', '')
        
        # Restore to previous status or set to due
        fine.status = FineStatus.DUE
        fine.dispute_resolved_at = timezone.now()
        fine.dispute_resolution = resolution
        fine.save()
        
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
