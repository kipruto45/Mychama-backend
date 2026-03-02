# Governance Module URL Configuration

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ChamaRuleViewSet, RuleAcknowledgmentViewSet, ApprovalRequestViewSet,
    RoleChangeViewSet, RoleDelegationViewSet, GovernanceOverviewView
)

router = DefaultRouter()
router.register(r'rules', ChamaRuleViewSet, basename='chama-rule')
router.register(r'acknowledgments', RuleAcknowledgmentViewSet, basename='rule-acknowledgment')
router.register(r'approvals', ApprovalRequestViewSet, basename='approval-request')
router.register(r'role-changes', RoleChangeViewSet, basename='role-change')
router.register(r'delegations', RoleDelegationViewSet, basename='role-delegation')

urlpatterns = [
    path('overview/', GovernanceOverviewView.as_view(), name='governance-overview'),
    path('', include(router.urls)),
]
