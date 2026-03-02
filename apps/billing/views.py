"""
Billing Views
API endpoints for subscription management and feature gating
"""
from rest_framework import viewsets, status
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from decimal import Decimal
from urllib.parse import quote
from django.conf import settings
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db.models import Q

from apps.chama.models import Chama
from .credits import (
    get_credit_admin_summary,
    issue_billing_credit,
    list_recent_credits,
    revoke_billing_credit,
    update_billing_credit,
)
from .invoicing import generate_invoice_pdf
from .metering import increment_usage, sync_usage_limits
from .models import (
    BillingCredit,
    BillingEvent,
    BillingRule,
    BillingWebhookEvent,
    FeatureOverride,
    Invoice,
    Plan,
    SeatUsage,
    Subscription,
    UsageMetric,
)
from .policy import get_billing_rule, schedule_plan_change, validate_plan_change
from .security import decrypt_billing_metadata
from .serializers import (
    BillingCreditIssueSerializer,
    BillingCreditSerializer,
    BillingCreditUpdateSerializer,
    BillingEventSerializer,
    BillingRuleSerializer,
    CheckoutRequestSerializer,
    EntitlementsSerializer,
    FeatureOverrideSerializer,
    InvoiceSerializer,
    PlanListSerializer,
    PlanSerializer,
    SeatUsageSerializer,
    SubscriptionDetailSerializer,
    SubscriptionSerializer,
    UsageMetricSerializer,
)
from .services import (
    cancel_subscription,
    check_seat_limit,
    confirm_checkout_subscription,
    create_checkout_invoice,
    ensure_default_plans,
    get_access_status,
    get_active_chama_from_request,
    get_admin_billing_dashboard,
    get_billing_overview,
    get_credit_summary,
    get_entitlements,
    get_invoice_by_provider_reference,
    get_latest_subscription,
    get_plan_for_chama,
    get_usage_summary,
    has_feature,
    mark_invoice_payment_state,
    preview_checkout_totals,
    update_seat_usage,
)
from .entitlements import get_all_features, FEATURE_DESCRIPTIONS


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _has_chama_admin_access(user, chama) -> bool:
    if not user or not user.is_authenticated or not chama:
        return False
    if user.is_staff or user.is_superuser:
        return True

    from apps.chama.models import Membership, MembershipRole, MemberStatus

    return Membership.objects.filter(
        chama=chama,
        user=user,
        role__in=[
            MembershipRole.ADMIN,
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.SUPERADMIN,
            MembershipRole.TREASURER,
        ],
        status=MemberStatus.ACTIVE,
        is_active=True,
        is_approved=True,
    ).exists()


class PlanViewSet(viewsets.ModelViewSet):
    """Public plan listing with admin-only plan management."""

    queryset = Plan.objects.all().order_by('sort_order', 'monthly_price', 'name')

    def get_queryset(self):
        ensure_default_plans()
        if self.action in {'list', 'retrieve', 'comparison'}:
            return Plan.objects.filter(is_active=True).order_by('sort_order', 'monthly_price', 'name')
        return self.queryset
    
    def get_serializer_class(self):
        if self.action == 'list':
            return PlanListSerializer
        return PlanSerializer

    def get_permissions(self):
        if self.action in {'list', 'retrieve', 'comparison'}:
            return [AllowAny()]
        return [IsAdminUser()]
    
    @action(detail=False, methods=['get'])
    def comparison(self, request):
        """Get plan comparison with all features"""
        plans = self.get_queryset()
        features = get_all_features()
        
        return Response({
            'plans': PlanListSerializer(plans, many=True).data,
            'features': features,
            'feature_descriptions': FEATURE_DESCRIPTIONS,
        })


class ChamaBillingView(APIView):
    """Billing endpoints for a specific chama"""
    permission_classes = [IsAuthenticated]
    
    def get_chama(self, request):
        """Get chama from request"""
        chama = get_active_chama_from_request(request)
        if not chama:
            raise PermissionError("No active chama selected")
        return chama
    
    def get(self, request):
        """Get billing status for current chama"""
        try:
            chama = self.get_chama(request)
        except PermissionError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        
        plan = get_plan_for_chama(chama)
        subscription = get_latest_subscription(chama)
        
        seat_info = check_seat_limit(chama)
        entitlements = get_entitlements(chama)
        access = get_access_status(chama)
        usage = get_usage_summary(chama)
        latest_invoice = Invoice.objects.filter(chama=chama).order_by('-created_at').first()
        
        return Response({
            'chama': {
                'id': str(chama.id),
                'name': chama.name,
            },
            'plan': {
                'code': plan.code if plan else 'FREE',
                'name': plan.name if plan else 'Free Trial',
            } if plan else None,
            'subscription': SubscriptionDetailSerializer(subscription).data if subscription else None,
            'entitlements': {
                'seat_limit': seat_info['limit'],
                'seat_used': seat_info['current'],
                'seat_available': seat_info['available'],
                'support_level': entitlements.get('support_level', 'community'),
            },
            'features': entitlements,
            'access': access,
            'usage': usage,
            'credits': get_credit_summary(chama),
            'credit_ledger': BillingCreditSerializer(list_recent_credits(chama), many=True).data,
            'billing_rule': BillingRuleSerializer(get_billing_rule(chama)).data,
            'latest_invoice': InvoiceSerializer(latest_invoice).data if latest_invoice else None,
        })
    
    def post(self, request):
        """Create or update subscription (manual/bypass)"""
        try:
            chama = self.get_chama(request)
        except PermissionError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        
        plan_id = request.data.get('plan_id')
        if not plan_id:
            return Response({'error': 'plan_id required'}, status=status.HTTP_400_BAD_REQUEST)

        billing_cycle = request.data.get('billing_cycle', 'monthly')
        if billing_cycle not in {'monthly', 'yearly'}:
            return Response({'error': 'billing_cycle must be monthly or yearly'}, status=status.HTTP_400_BAD_REQUEST)
        
        plan = get_object_or_404(Plan, id=plan_id, is_active=True)
        access = get_access_status(chama)

        if plan.code != Plan.FREE:
            return Response(
                {'error': 'Use the checkout flow to activate a paid subscription.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not access['is_trialing']:
            return Response(
                {'error': 'The free plan is only available during the initial 30-day trial. Choose a paid plan to continue.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        subscription = get_latest_subscription(chama)
        
        return Response({
            'message': 'Free trial is already active',
            'subscription': SubscriptionDetailSerializer(subscription).data,
        })
    
    def delete(self, request):
        """Cancel subscription"""
        try:
            chama = self.get_chama(request)
        except PermissionError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        
        cancel_at_period_end = request.data.get('cancel_at_period_end', True)
        
        subscription = cancel_subscription(
            chama,
            cancel_at_period_end=cancel_at_period_end,
            performed_by=request.user
        )
        
        return Response({
            'message': 'Subscription cancelled',
            'subscription': SubscriptionDetailSerializer(subscription).data,
        })


class SubscriptionViewSet(viewsets.ModelViewSet):
    """Manage subscriptions"""
    serializer_class = SubscriptionSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        try:
            chama = get_active_chama_from_request(self.request)
            if not chama:
                return Subscription.objects.none()
            return Subscription.objects.filter(chama=chama)
        except:
            return Subscription.objects.none()
    
    @action(detail=False, methods=['get'])
    def current(self, request):
        """Get current active subscription"""
        try:
            chama = get_active_chama_from_request(request)
            if not chama:
                return Response({'error': 'No active chama'}, status=status.HTTP_400_BAD_REQUEST)
            
            subscription = Subscription.objects.filter(
                chama=chama,
                status__in=[Subscription.TRIALING, Subscription.ACTIVE]
            ).first()
            
            if not subscription:
                return Response({'subscription': None})
            
            return Response({
                'subscription': SubscriptionDetailSerializer(subscription).data
            })
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class SeatUsageView(APIView):
    """Seat usage for current chama"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        try:
            chama = get_active_chama_from_request(request)
            if not chama:
                return Response({'error': 'No active chama'}, status=status.HTTP_400_BAD_REQUEST)
            
            seat_usage, _ = SeatUsage.objects.get_or_create(
                chama=chama,
                defaults={'active_members_count': 0}
            )
            
            seat_info = check_seat_limit(chama)
            
            # Add seat_limit to serializer context
            serializer = SeatUsageSerializer(seat_usage)
            data = serializer.data
            data['limit'] = seat_info['limit']
            data['percentage'] = round(seat_info['current'] / seat_info['limit'] * 100, 1) if seat_info['limit'] > 0 else 100
            
            return Response(data)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def post(self, request):
        """Manually recalculate seat usage"""
        try:
            chama = get_active_chama_from_request(request)
            if not chama:
                return Response({'error': 'No active chama'}, status=status.HTTP_400_BAD_REQUEST)
            
            seat_usage = update_seat_usage(chama)
            
            return Response({
                'message': 'Seat usage updated',
                'active_members_count': seat_usage.active_members_count,
            })
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class EntitlementsView(APIView):
    """Get entitlements for current chama"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        try:
            chama = get_active_chama_from_request(request)
            if not chama:
                return Response({'error': 'No active chama'}, status=status.HTTP_400_BAD_REQUEST)
            
            plan = get_plan_for_chama(chama)
            entitlements = get_entitlements(chama)
            seat_info = check_seat_limit(chama)
            
            return Response({
                'plan_code': plan.code if plan else 'FREE',
                'plan_name': plan.name if plan else 'Free Trial',
                'seat_limit': seat_info['limit'],
                'storage_limit_mb': entitlements.get('storage_limit_mb', 250),
                'support_level': entitlements.get('support_level', 'community'),
                'features': entitlements,
            })
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class FeatureCheckView(APIView):
    """Check if a specific feature is available"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request, feature_key):
        try:
            chama = get_active_chama_from_request(request)
            if not chama:
                return Response({'error': 'No active chama'}, status=status.HTTP_400_BAD_REQUEST)
            
            has_feature_access = has_feature(chama, feature_key)
            entitlements = get_entitlements(chama)
            
            return Response({
                'feature': feature_key,
                'available': has_feature_access,
                'description': FEATURE_DESCRIPTIONS.get(feature_key, {}).get('description', ''),
            })
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class FeatureOverrideViewSet(viewsets.ModelViewSet):
    """Manage feature overrides (admin only)"""
    serializer_class = FeatureOverrideSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        try:
            chama = get_active_chama_from_request(self.request)
            if not chama:
                return FeatureOverride.objects.none()
            
            # Only admins can manage overrides
            from apps.chama.models import Membership, MembershipRole, MemberStatus
            membership = Membership.objects.filter(
                chama=chama,
                user=self.request.user,
                role__in=[
                    MembershipRole.ADMIN,
                    MembershipRole.CHAMA_ADMIN,
                    MembershipRole.SUPERADMIN,
                ],
                status=MemberStatus.ACTIVE,
                is_active=True,
                is_approved=True,
            ).first()
            
            if not membership:
                return FeatureOverride.objects.none()
            
            return FeatureOverride.objects.filter(chama=chama)
        except:
            return FeatureOverride.objects.none()
    
    def perform_create(self, serializer):
        chama = get_active_chama_from_request(self.request)
        serializer.save(chama=chama, created_by=self.request.user)


class BillingEventViewSet(viewsets.ReadOnlyModelViewSet):
    """View billing events"""
    serializer_class = BillingEventSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        try:
            chama = get_active_chama_from_request(self.request)
            if not chama:
                return BillingEvent.objects.none()
            
            return BillingEvent.objects.filter(chama=chama).order_by('-created_at')[:50]
        except:
            return BillingEvent.objects.none()


class BillingRuleView(APIView):
    """Read the effective billing rule, or update it as a platform admin."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        chama = get_active_chama_from_request(request)
        if not chama and not request.user.is_staff:
            return Response({'error': 'No active chama'}, status=status.HTTP_400_BAD_REQUEST)

        scope = None if request.user.is_staff and request.query_params.get('scope') == 'global' else chama
        rule = get_billing_rule(scope)
        return Response(BillingRuleSerializer(rule).data)

    def put(self, request):
        if not request.user.is_staff:
            return Response({'error': 'Admin access required'}, status=status.HTTP_403_FORBIDDEN)

        chama_id = request.data.get('chama_id')
        chama = None
        if chama_id:
            chama = get_object_or_404(Chama, id=chama_id)

        instance = BillingRule.objects.filter(chama=chama).first()
        serializer = BillingRuleSerializer(instance=instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(chama=chama)
        return Response(serializer.data)


class UsageMetricsView(APIView):
    """Expose current usage counters and limits for the active chama."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        chama = get_active_chama_from_request(request)
        if not chama:
            return Response({'error': 'No active chama'}, status=status.HTTP_400_BAD_REQUEST)

        sync_usage_limits(chama)
        metrics = UsageMetric.objects.filter(chama=chama).order_by('metric_key')
        return Response(
            {
                'summary': get_usage_summary(chama),
                'metrics': UsageMetricSerializer(metrics, many=True).data,
            }
        )


class InvoiceViewSet(viewsets.ReadOnlyModelViewSet):
    """Invoice history and downloads for the active chama."""

    serializer_class = InvoiceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        chama = get_active_chama_from_request(self.request)
        if not chama:
            return Invoice.objects.none()
        return Invoice.objects.filter(chama=chama).select_related('plan').prefetch_related('line_items')

    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        invoice = self.get_object()
        if not invoice.pdf_file:
            generate_invoice_pdf(invoice)
        invoice.pdf_file.open('rb')
        return FileResponse(
            invoice.pdf_file,
            as_attachment=True,
            filename=invoice.pdf_file.name.rsplit('/', 1)[-1],
        )


class SubscriptionChangePreviewView(APIView):
    """Preview upgrade charge or validate a downgrade."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        chama = get_active_chama_from_request(request)
        if not chama:
            return Response({'error': 'No active chama'}, status=status.HTTP_400_BAD_REQUEST)
        if not _has_chama_admin_access(request.user, chama):
            return Response({'error': 'Admin access required'}, status=status.HTTP_403_FORBIDDEN)

        plan_id = request.data.get('plan_id')
        billing_cycle = request.data.get('billing_cycle', Subscription.MONTHLY)
        if billing_cycle not in {Subscription.MONTHLY, Subscription.YEARLY}:
            return Response({'error': 'Invalid billing_cycle'}, status=status.HTTP_400_BAD_REQUEST)

        plan = get_object_or_404(Plan, id=plan_id, is_active=True)
        preview = preview_checkout_totals(chama, plan, billing_cycle)
        proration = preview['proration']
        validation = None
        error = None
        try:
            validation = validate_plan_change(chama, plan)
        except ValueError as exc:
            error = str(exc)

        return Response(
            {
                'plan': PlanSerializer(plan).data,
                'proration': {
                    'charge_amount': str(proration['charge_amount']),
                    'credit_amount': str(proration['credit_amount']),
                    'referral_credit_amount': str(preview['referral_credit_amount']),
                    'net_charge_amount': str(preview['net_charge_amount']),
                    'full_amount': str(proration['full_amount']),
                    'prorated': proration['prorated'],
                    'requires_approval': proration['requires_approval'],
                },
                'validation_error': error,
                'usage': validation['usage'] if validation else get_usage_summary(chama),
                'credits': get_credit_summary(chama),
            }
        )


class SubscriptionChangeView(APIView):
    """Schedule compatible downgrades or redirect upgrades into checkout."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        chama = get_active_chama_from_request(request)
        if not chama:
            return Response({'error': 'No active chama'}, status=status.HTTP_400_BAD_REQUEST)
        if not _has_chama_admin_access(request.user, chama):
            return Response({'error': 'Admin access required'}, status=status.HTTP_403_FORBIDDEN)

        plan_id = request.data.get('plan_id')
        billing_cycle = request.data.get('billing_cycle', Subscription.MONTHLY)
        plan = get_object_or_404(Plan, id=plan_id, is_active=True)
        try:
            result = schedule_plan_change(
                chama,
                plan,
                performed_by=request.user,
                billing_cycle=billing_cycle,
            )
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if result['scheduled']:
            return Response(
                {
                    'message': 'Plan downgrade scheduled for the end of the current billing cycle.',
                    'scheduled': True,
                    'effective_at': result['effective_at'].isoformat() if result['effective_at'] else None,
                }
            )

        return Response(
            {
                'message': 'This is an upgrade path. Use the checkout endpoint to complete payment.',
                'scheduled': False,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )


class BillingAdminDashboardView(APIView):
    """Platform-level SaaS billing analytics."""

    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response(get_admin_billing_dashboard())


class BillingCreditAdminView(APIView):
    """Platform admin credit issuance and inspection."""

    permission_classes = [IsAdminUser]

    def _resolve_chama(self, request, explicit_chama_id=None):
        chama_id = explicit_chama_id or request.query_params.get('chama_id')
        if chama_id:
            return get_object_or_404(Chama, id=chama_id)
        return get_active_chama_from_request(request)

    def get(self, request):
        chama = self._resolve_chama(request)
        query = (request.query_params.get('q') or '').strip()
        summary = get_credit_admin_summary(chama=chama)
        credits_qs = BillingCredit.objects.select_related('chama')
        if chama:
            credits_qs = credits_qs.filter(chama=chama)
        credits = credits_qs.order_by('-created_at')[:20]
        chama_qs = Chama.objects.all().order_by('name')
        if query:
            chama_qs = chama_qs.filter(name__icontains=query)
        available_chamas = [
            {
                'id': str(item.id),
                'name': item.name,
            }
            for item in chama_qs[:100]
        ]
        return Response(
            {
                'summary': summary,
                'credits': BillingCreditSerializer(credits, many=True).data,
                'chama': (
                    {
                        'id': str(chama.id),
                        'name': chama.name,
                    }
                    if chama
                    else None
                ),
                'available_chamas': available_chamas,
            }
        )

    def post(self, request):
        serializer = BillingCreditIssueSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        chama = self._resolve_chama(
            request,
            explicit_chama_id=serializer.validated_data.get('chama_id'),
        )
        if not chama:
            return Response({'error': 'chama_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        credit = issue_billing_credit(
            chama=chama,
            amount=serializer.validated_data['amount'],
            source_type=BillingCredit.MANUAL,
            source_reference=serializer.validated_data.get('source_reference', ''),
            description=serializer.validated_data.get('description', ''),
            expires_at=serializer.validated_data.get('expires_at'),
            metadata={
                'issued_via': 'admin_api',
            },
            performed_by=request.user,
        )
        if not credit:
            return Response(
                {'error': 'Credit amount must be greater than zero.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                'message': 'Billing credit issued successfully.',
                'credit': BillingCreditSerializer(credit).data,
                'summary': get_credit_admin_summary(chama=chama),
            },
            status=status.HTTP_201_CREATED,
        )


class BillingCreditAdminDetailView(APIView):
    """Admin actions for an individual billing credit."""

    permission_classes = [IsAdminUser]

    def patch(self, request, credit_id: int):
        credit = get_object_or_404(BillingCredit.objects.select_related('chama'), id=credit_id)
        serializer = BillingCreditUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        action = serializer.validated_data.get('action', 'update')
        if action == 'revoke':
            try:
                revoke_billing_credit(credit, performed_by=request.user)
            except ValueError as exc:
                return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

            return Response(
                {
                    'message': 'Billing credit revoked successfully.',
                    'credit': BillingCreditSerializer(credit).data,
                    'summary': get_credit_admin_summary(chama=credit.chama),
                }
            )

        if not serializer.validated_data:
            return Response(
                {'error': 'Provide at least one field to update.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        updated_credit = update_billing_credit(
            credit,
            update_remaining='remaining_amount' in serializer.validated_data,
            remaining_amount=serializer.validated_data.get('remaining_amount'),
            update_description='description' in serializer.validated_data,
            description=serializer.validated_data.get('description', ''),
            update_expires_at='expires_at' in serializer.validated_data,
            expires_at=serializer.validated_data.get('expires_at'),
            performed_by=request.user,
        )
        return Response(
            {
                'message': 'Billing credit updated successfully.',
                'credit': BillingCreditSerializer(updated_credit).data,
                'summary': get_credit_admin_summary(chama=updated_credit.chama),
            }
        )


class CheckoutView(APIView):
    """Initiate checkout with payment provider"""
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        try:
            from .payments import PaymentProviderFactory
            
            chama = get_active_chama_from_request(request)
            if not chama:
                return Response({'error': 'No active chama'}, status=status.HTTP_400_BAD_REQUEST)
            
            serializer = CheckoutRequestSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            
            plan = get_object_or_404(
                Plan,
                id=serializer.validated_data['plan_id'],
                is_active=True
            )
            if plan.code == Plan.FREE:
                return Response(
                    {'error': 'The free trial plan does not require checkout.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            billing_cycle = serializer.validated_data['billing_cycle']
            provider_id = serializer.validated_data['provider']
            customer_phone = serializer.validated_data.get('phone') or getattr(request.user, 'phone', '')
            
            preview = preview_checkout_totals(chama, plan, billing_cycle)
            proration = preview['proration']
            if proration['charge_amount'] <= 0:
                return Response(
                    {
                        'error': 'This plan change is a downgrade. Use the plan change endpoint to schedule it safely.',
                        'requires_downgrade_flow': True,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if proration['requires_approval'] and not request.user.is_staff:
                return Response(
                    {
                        'error': 'approval_required',
                        'message': 'This upgrade exceeds the configured approval threshold and requires platform review before checkout.',
                        'charge_amount': str(proration['charge_amount']),
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )
            if provider_id == Subscription.MPESA and preview['net_charge_amount'] > 0:
                usage = get_usage_summary(chama).get(UsageMetric.STK_PUSHES, {})
                if usage and usage.get('limit', 0) and usage.get('remaining', 0) <= 0:
                    return Response(
                        {
                            'error': 'usage_limit_exceeded',
                            'message': 'Your current plan has exhausted its monthly billing STK allocation.',
                            'metric': UsageMetric.STK_PUSHES,
                            'current': usage.get('used', 0),
                            'limit': usage.get('limit', 0),
                        },
                        status=status.HTTP_402_PAYMENT_REQUIRED,
                    )
            
            # Get success/cancel URLs
            success_base_url = request.data.get('success_url', '') or getattr(settings, 'BASE_URL', '')
            cancel_base_url = request.data.get('cancel_url', '') or success_base_url
            next_path = serializer.validated_data.get('next') or request.data.get('next', '')
            next_param = (
                f"&next={quote(str(next_path), safe='/')}"
                if next_path and str(next_path).startswith('/')
                else ''
            )
            provider_param = f"&provider={provider_id}"
            cycle_param = f"&cycle={billing_cycle}"

            if provider_id == 'stripe':
                success_url = (
                    f"{success_base_url}/billing/success?chama={chama.id}&plan={plan.id}"
                    f"{cycle_param}{provider_param}&session_id={{CHECKOUT_SESSION_ID}}{next_param}"
                )
            else:
                success_url = (
                    f"{success_base_url}/billing/success?chama={chama.id}&plan={plan.id}"
                    f"{cycle_param}{provider_param}{next_param}"
                )
            cancel_url = (
                f"{cancel_base_url}/billing/cancel?chama={chama.id}&plan={plan.id}"
                f"{cycle_param}{provider_param}{next_param}"
            )

            invoice_context = create_checkout_invoice(
                chama=chama,
                plan=plan,
                provider=provider_id,
                billing_cycle=billing_cycle,
                customer_email=request.user.email or '',
                provider_transaction_id='',
                payment_metadata={
                    'billing_cycle': billing_cycle,
                    'provider': provider_id,
                    'phone': customer_phone,
                    'auto_renew': serializer.validated_data.get('auto_renew', True),
                },
            )
            invoice = invoice_context['invoice']
            payable_amount = Decimal(invoice.total_amount or 0)

            if payable_amount <= 0:
                invoice.provider = Subscription.MANUAL
                invoice.save(update_fields=['provider', 'updated_at'])
                subscription = confirm_checkout_subscription(
                    chama,
                    plan,
                    billing_cycle=billing_cycle,
                    provider=Subscription.MANUAL,
                    provider_subscription_id=None,
                    payment_reference='REFERRAL-CREDIT',
                    payment_metadata={
                        'billing_cycle': billing_cycle,
                        'provider': provider_id,
                        'settled_by': 'billing_credit',
                    },
                    invoice=invoice,
                    performed_by=request.user,
                )
                return Response({
                    'checkout_url': success_url,
                    'session_id': None,
                    'provider': Subscription.MANUAL,
                    'auto_applied': True,
                    'message': 'Your referral credit covered this upgrade, so access has been activated immediately.',
                    'plan': PlanSerializer(plan).data,
                    'subscription': SubscriptionSerializer(subscription).data,
                    'invoice': InvoiceSerializer(invoice).data,
                    'proration': {
                        'charge_amount': str(invoice_context['proration']['charge_amount']),
                        'credit_amount': str(invoice_context['proration']['credit_amount']),
                        'referral_credit_amount': str(invoice_context['referral_credit_amount']),
                        'net_charge_amount': str(invoice_context['net_charge_amount']),
                        'full_amount': str(invoice_context['proration']['full_amount']),
                        'prorated': invoice_context['proration']['prorated'],
                        'requires_approval': invoice_context['proration']['requires_approval'],
                    },
                    'credits': invoice_context['credit_summary'],
                })

            result = PaymentProviderFactory.create_checkout(
                provider_id=provider_id,
                plan_id=plan.id,
                plan_name=plan.name,
                amount=payable_amount,
                currency='KES',
                billing_cycle=billing_cycle,
                customer_email=request.user.email or '',
                customer_phone=customer_phone,
                chama_id=str(chama.id),
                success_url=success_url,
                cancel_url=cancel_url,
            )

            if not result.success:
                mark_invoice_payment_state(invoice=invoice, paid=False)
                return Response({
                    'error': result.error_message,
                    'provider': provider_id,
                }, status=status.HTTP_400_BAD_REQUEST)

            if result.transaction_id and invoice.provider_transaction_id != result.transaction_id:
                invoice.provider_transaction_id = result.transaction_id
                invoice.save(update_fields=['provider_transaction_id', 'updated_at'])

            if provider_id == Subscription.MPESA:
                increment_usage(chama, UsageMetric.STK_PUSHES, 1)
            
            return Response({
                'checkout_url': result.checkout_url,
                'session_id': result.transaction_id,
                'provider': provider_id,
                'auto_applied': False,
                'plan': PlanSerializer(plan).data,
                'invoice': InvoiceSerializer(invoice).data,
                'proration': {
                    'charge_amount': str(invoice_context['proration']['charge_amount']),
                    'credit_amount': str(invoice_context['proration']['credit_amount']),
                    'referral_credit_amount': str(invoice_context['referral_credit_amount']),
                    'net_charge_amount': str(invoice_context['net_charge_amount']),
                    'full_amount': str(invoice_context['proration']['full_amount']),
                    'prorated': invoice_context['proration']['prorated'],
                    'requires_approval': invoice_context['proration']['requires_approval'],
                },
                'credits': invoice_context['credit_summary'],
            })
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CheckoutConfirmView(APIView):
    """Activate a paid subscription after a successful checkout callback."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            chama = get_active_chama_from_request(request)
            if not chama:
                return Response({'error': 'No active chama'}, status=status.HTTP_400_BAD_REQUEST)

            plan_id = request.data.get('plan_id')
            if not plan_id:
                return Response({'error': 'plan_id required'}, status=status.HTTP_400_BAD_REQUEST)

            billing_cycle = request.data.get('billing_cycle', 'monthly')
            if billing_cycle not in {'monthly', 'yearly'}:
                return Response({'error': 'billing_cycle must be monthly or yearly'}, status=status.HTTP_400_BAD_REQUEST)

            provider = request.data.get('provider', Subscription.MANUAL)
            provider_subscription_id = request.data.get('session_id') or request.data.get('provider_subscription_id')
            invoice = get_invoice_by_provider_reference(
                provider=provider,
                provider_transaction_id=provider_subscription_id or '',
            )

            plan = get_object_or_404(Plan, id=plan_id, is_active=True)
            if plan.code == Plan.FREE:
                return Response(
                    {'error': 'The free trial plan does not require checkout confirmation.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if provider != Subscription.MANUAL:
                return Response(
                    {'error': 'Online checkout confirmations are processed by provider webhooks.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            subscription = confirm_checkout_subscription(
                chama,
                plan,
                billing_cycle=billing_cycle,
                provider=provider,
                provider_subscription_id=provider_subscription_id,
                payment_reference=request.data.get('payment_reference') or provider_subscription_id,
                payment_metadata={'phone': request.data.get('phone') or getattr(request.user, 'phone', '')},
                invoice=invoice,
                charge_amount=invoice.subtotal if invoice else None,
                performed_by=request.user,
            )

            return Response({
                'message': f'{plan.name} subscription activated',
                'subscription': SubscriptionDetailSerializer(subscription).data,
            })
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class BillingOverviewView(APIView):
    """Get billing overview for all user's chamas"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        try:
            overview = get_billing_overview(request.user)
            return Response(overview)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class PaymentMethodsView(APIView):
    """Get available payment methods"""
    permission_classes = [AllowAny]
    
    def get(self, request):
        from .payments import PaymentProviderFactory
        providers = PaymentProviderFactory.get_available_providers()
        return Response({'payment_methods': providers})


class StripeWebhookView(APIView):
    """Handle Stripe webhooks"""
    permission_classes = [AllowAny]
    authentication_classes = []  # No auth - verify signature instead
    
    def post(self, request):
        """Process Stripe webhook"""
        from .payments import PaymentProviderFactory

        payload = request.body
        stripe_signature = request.headers.get('Stripe-Signature', '')
        provider = PaymentProviderFactory.get_provider('stripe')
        event = provider.handle_webhook(payload, stripe_signature)

        if not event:
            return Response({'status': 'ignored'})

        data = event['data']
        metadata = data.get('metadata', {}) or {}
        subscription_id = data.get('id') or data.get('subscription')
        invoice = get_invoice_by_provider_reference(
            provider=Subscription.STRIPE,
            provider_transaction_id=subscription_id or '',
        )
        webhook, _ = BillingWebhookEvent.objects.get_or_create(
            idempotency_key=f"stripe:{subscription_id or timezone.now().timestamp()}",
            defaults={
                'provider': Subscription.STRIPE,
                'event_type': event['event'],
                'external_event_id': str(subscription_id or ''),
                'verified': True,
                'signature_valid': True,
                'amount': invoice.total_amount if invoice else None,
                'currency': 'KES',
                'chama': invoice.chama if invoice else None,
                'invoice': invoice,
                'payload': data,
                'headers': {k: v for k, v in request.headers.items()},
                'processing_status': 'received',
            },
        )
        if webhook.processed_at:
            return Response({'status': 'webhook_received'})

        if event['event'] == 'payment_succeeded':
            chama = invoice.chama if invoice else get_object_or_404(Chama, id=metadata.get('chama_id'))
            plan = invoice.plan if invoice else get_object_or_404(Plan, id=metadata.get('plan_id'), is_active=True)
            billing_cycle = metadata.get('billing_cycle', Subscription.MONTHLY)
            confirm_checkout_subscription(
                chama,
                plan,
                billing_cycle=billing_cycle,
                provider=Subscription.STRIPE,
                provider_subscription_id=subscription_id,
                payment_reference=subscription_id,
                payment_metadata=decrypt_billing_metadata(invoice.metadata_encrypted) if invoice else None,
                invoice=invoice,
                charge_amount=invoice.subtotal if invoice else None,
            )
            webhook.processing_status = 'processed'
        elif event['event'] == 'payment_failed':
            mark_invoice_payment_state(
                invoice=invoice,
                paid=False,
                payment_reference=subscription_id or '',
                provider_transaction_id=subscription_id or '',
            )
            if invoice and invoice.subscription:
                invoice.subscription.failed_payment_count += 1
                invoice.subscription.status = Subscription.PAST_DUE
                invoice.subscription.save(
                    update_fields=['failed_payment_count', 'status', 'updated_at']
                )
            webhook.processing_status = 'failed'
        else:
            webhook.processing_status = 'ignored'
        webhook.processed_at = timezone.now()
        webhook.save(update_fields=['processing_status', 'processed_at'])

        return Response({'status': 'webhook_received'})


class MpesaWebhookView(APIView):
    """Verified M-Pesa billing callback that never trusts frontend confirmation."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        from apps.payments.services import PaymentWorkflowService
        from .payments import PaymentProviderFactory

        source_ip = _client_ip(request)
        raw_payload = request.body
        signature = request.headers.get(
            getattr(settings, 'MPESA_CALLBACK_SIGNATURE_HEADER', 'X-MPESA-SIGNATURE')
        )
        verified, reason = PaymentWorkflowService.verify_callback_request(
            source_ip=source_ip,
            payload_bytes=raw_payload,
            signature=signature,
        )

        provider = PaymentProviderFactory.get_provider('mpesa')
        parsed_event = provider.handle_webhook(raw_payload, signature or '')
        data = parsed_event['data'] if parsed_event else {}
        checkout_request_id = data.get('id') or ''
        webhook = BillingWebhookEvent.objects.filter(
            idempotency_key=f'mpesa:{checkout_request_id}'
        ).first()
        if webhook and webhook.processed_at:
            return Response({'ResultCode': 0, 'ResultDesc': 'Accepted'})

        invoice = get_invoice_by_provider_reference(
            provider=Subscription.MPESA,
            provider_transaction_id=checkout_request_id,
        )
        webhook = webhook or BillingWebhookEvent(
            provider=Subscription.MPESA,
            event_type=parsed_event['event'] if parsed_event else 'invalid',
            external_event_id=checkout_request_id,
            idempotency_key=f'mpesa:{checkout_request_id or timezone.now().timestamp()}',
        )
        webhook.signature_valid = verified
        webhook.verified = verified
        webhook.amount = data.get('amount')
        webhook.currency = 'KES'
        webhook.chama = invoice.chama if invoice else None
        webhook.invoice = invoice
        webhook.payload = data.get('raw') if parsed_event else request.data
        webhook.headers = {k: v for k, v in request.headers.items()}

        if not verified or not parsed_event:
            webhook.processing_status = 'rejected'
            webhook.failure_reason = reason if not verified else 'Invalid callback payload'
            webhook.processed_at = timezone.now()
            webhook.save()
            return Response({'ResultCode': 0, 'ResultDesc': 'Accepted'})

        if invoice and data.get('amount') is not None:
            received_amount = Decimal(str(data['amount']))
            if received_amount not in {invoice.total_amount, invoice.subtotal}:
                webhook.processing_status = 'rejected'
                webhook.failure_reason = 'Callback amount does not match the expected invoice amount.'
                webhook.processed_at = timezone.now()
                webhook.save()
                return Response({'ResultCode': 0, 'ResultDesc': 'Accepted'})

        if parsed_event['event'] == 'payment_succeeded' and invoice:
            invoice_metadata = decrypt_billing_metadata(invoice.metadata_encrypted)
            confirm_checkout_subscription(
                invoice.chama,
                invoice.plan,
                billing_cycle=invoice_metadata.get('billing_cycle', Subscription.MONTHLY),
                provider=Subscription.MPESA,
                provider_subscription_id=checkout_request_id,
                payment_reference=data.get('payment_reference') or checkout_request_id,
                payment_metadata=invoice_metadata,
                invoice=invoice,
                charge_amount=invoice.subtotal,
            )
            webhook.processing_status = 'processed'
        elif invoice:
            mark_invoice_payment_state(
                invoice=invoice,
                paid=False,
                payment_reference=data.get('payment_reference') or checkout_request_id,
                provider_transaction_id=checkout_request_id,
            )
            if invoice.subscription:
                invoice.subscription.failed_payment_count += 1
                invoice.subscription.status = Subscription.PAST_DUE
                invoice.subscription.save(
                    update_fields=['failed_payment_count', 'status', 'updated_at']
                )
            webhook.processing_status = 'failed'
        else:
            webhook.processing_status = 'ignored'
            webhook.failure_reason = 'Invoice not found for callback reference.'

        webhook.processed_at = timezone.now()
        webhook.save()
        return Response({'ResultCode': 0, 'ResultDesc': 'Accepted'})
