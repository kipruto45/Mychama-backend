"""
Billing URL Configuration
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    BillingCreditAdminView,
    BillingCreditAdminDetailView,
    BillingAdminDashboardView,
    BillingRuleView,
    ChamaBillingView,
    BillingEventViewSet,
    BillingOverviewView,
    CheckoutConfirmView,
    CheckoutView,
    EntitlementsView,
    FeatureCheckView,
    FeatureOverrideViewSet,
    InvoiceViewSet,
    MpesaWebhookView,
    PaymentMethodsView,
    PlanViewSet,
    SeatUsageView,
    StripeWebhookView,
    SubscriptionChangePreviewView,
    SubscriptionChangeView,
    SubscriptionViewSet,
    UsageMetricsView,
)

router = DefaultRouter()
router.register(r'plans', PlanViewSet, basename='plans')
router.register(r'subscriptions', SubscriptionViewSet, basename='subscriptions')
router.register(r'invoices', InvoiceViewSet, basename='invoices')
router.register(r'overrides', FeatureOverrideViewSet, basename='overrides')
router.register(r'events', BillingEventViewSet, basename='events')

urlpatterns = [
    # Router endpoints
    path('', include(router.urls)),
    
    # Chama billing (main endpoints)
    path('chama/', ChamaBillingView.as_view(), name='chama-billing'),
    path('chama/subscription/', ChamaBillingView.as_view(), name='chama-subscription'),
    path('chama/change-plan/', ChamaBillingView.as_view(), name='chama-change-plan'),
    path('chama/cancel/', ChamaBillingView.as_view(), name='chama-cancel'),
    path('subscription/preview/', SubscriptionChangePreviewView.as_view(), name='subscription-preview'),
    path('subscription/change/', SubscriptionChangeView.as_view(), name='subscription-change'),
    path('rules/', BillingRuleView.as_view(), name='billing-rules'),
    path('usage/', UsageMetricsView.as_view(), name='usage-metrics'),
    
    # Seat usage
    path('seats/', SeatUsageView.as_view(), name='seat-usage'),
    path('seats/recalculate/', SeatUsageView.as_view(), name='seat-recalculate'),
    
    # Entitlements & features
    path('entitlements/', EntitlementsView.as_view(), name='entitlements'),
    path('features/<str:feature_key>/', FeatureCheckView.as_view(), name='feature-check'),
    
    # Checkout
    path('checkout/', CheckoutView.as_view(), name='checkout'),
    path('checkout/confirm/', CheckoutConfirmView.as_view(), name='checkout-confirm'),
    
    # Overview
    path('overview/', BillingOverviewView.as_view(), name='billing-overview'),
    path('admin/dashboard/', BillingAdminDashboardView.as_view(), name='billing-admin-dashboard'),
    path('admin/credits/', BillingCreditAdminView.as_view(), name='billing-admin-credits'),
    path('admin/credits/<int:credit_id>/', BillingCreditAdminDetailView.as_view(), name='billing-admin-credit-detail'),
    
    # Payment methods
    path('payment-methods/', PaymentMethodsView.as_view(), name='payment-methods'),
    
    # Webhooks
    path('webhooks/stripe/', StripeWebhookView.as_view(), name='stripe-webhook'),
    path('webhooks/mpesa/', MpesaWebhookView.as_view(), name='mpesa-webhook'),
]
