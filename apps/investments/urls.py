# Investments Module URL Configuration

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AdminInvestmentAnalyticsView,
    AdminRedemptionProcessView,
    AdminRedemptionQueueView,
    CalculateROIView,
    InvestmentApprovalViewSet,
    InvestmentDistributionViewSet,
    InvestmentOverviewView,
    InvestmentProductDetailView,
    InvestmentProductProjectionView,
    InvestmentProductViewSet,
    InvestmentReturnViewSet,
    InvestmentTransactionViewSet,
    InvestmentValuationViewSet,
    InvestmentViewSet,
    InvestmentEducationView,
    MemberInvestmentHistoryView,
    MemberInvestmentPositionViewSet,
    MemberPortfolioAnalyticsView,
    MemberPortfolioSummaryView,
    MemberInvestmentViewSet,
    UpcomingMaturitiesView,
)

router = DefaultRouter()
router.register(r'', InvestmentViewSet, basename='investment')
router.register(r'transactions', InvestmentTransactionViewSet, basename='investment-transaction')
router.register(r'returns', InvestmentReturnViewSet, basename='investment-return')
router.register(r'valuations', InvestmentValuationViewSet, basename='investment-valuation')
router.register(r'approvals', InvestmentApprovalViewSet, basename='investment-approval')
router.register(r'member-investments', MemberInvestmentViewSet, basename='member-investment')
router.register(r'distributions', InvestmentDistributionViewSet, basename='investment-distribution')
router.register(r'member/positions', MemberInvestmentPositionViewSet, basename='member-investment-position')

urlpatterns = [
    path('overview/', InvestmentOverviewView.as_view(), name='investment-overview'),
    path('calculate-roi/', CalculateROIView.as_view(), name='calculate-roi'),
    path('upcoming-maturities/', UpcomingMaturitiesView.as_view(), name='upcoming-maturities'),
    path('products/', InvestmentProductViewSet.as_view(), name='investment-products'),
    path('products/<uuid:pk>/', InvestmentProductDetailView.as_view(), name='investment-product-detail'),
    path('products/<uuid:pk>/simulate/', InvestmentProductProjectionView.as_view(), name='investment-product-simulate'),
    path('member/portfolio/summary/', MemberPortfolioSummaryView.as_view(), name='member-investment-portfolio-summary'),
    path('member/portfolio/analytics/', MemberPortfolioAnalyticsView.as_view(), name='member-investment-portfolio-analytics'),
    path('member/history/', MemberInvestmentHistoryView.as_view(), name='member-investment-history'),
    path('member/education/', InvestmentEducationView.as_view(), name='member-investment-education'),
    path('admin/redemptions/', AdminRedemptionQueueView.as_view(), name='admin-investment-redemptions'),
    path('admin/redemptions/<uuid:pk>/process/', AdminRedemptionProcessView.as_view(), name='admin-investment-redemption-process'),
    path('admin/analytics/', AdminInvestmentAnalyticsView.as_view(), name='admin-investment-analytics'),
    path('', include(router.urls)),
]
