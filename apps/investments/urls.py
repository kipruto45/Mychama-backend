# Investments Module URL Configuration

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    InvestmentViewSet, InvestmentTransactionViewSet, InvestmentReturnViewSet,
    InvestmentValuationViewSet, InvestmentApprovalRequestViewSet,
    MemberInvestmentViewSet, InvestmentDistributionViewSet,
    InvestmentOverviewView, CalculateROIView, UpcomingMaturitiesView
)

router = DefaultRouter()
router.register(r'', InvestmentViewSet, basename='investment')
router.register(r'transactions', InvestmentTransactionViewSet, basename='investment-transaction')
router.register(r'returns', InvestmentReturnViewSet, basename='investment-return')
router.register(r'valuations', InvestmentValuationViewSet, basename='investment-valuation')
router.register(r'approvals', InvestmentApprovalRequestViewSet, basename='investment-approval')
router.register(r'member-investments', MemberInvestmentViewSet, basename='member-investment')
router.register(r'distributions', InvestmentDistributionViewSet, basename='investment-distribution')

urlpatterns = [
    path('overview/', InvestmentOverviewView.as_view(), name='investment-overview'),
    path('calculate-roi/', CalculateROIView.as_view(), name='calculate-roi'),
    path('upcoming-maturities/', UpcomingMaturitiesView.as_view(), name='upcoming-maturities'),
    path('', include(router.urls)),
]
