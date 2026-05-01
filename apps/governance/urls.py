# Governance Module URL Configuration

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    ApprovalRequestViewSet,
    ChamaRuleViewSet,
    GovernanceAuditTrailView,
    GovernanceOverviewView,
    MotionViewSet,
    RoleChangeViewSet,
    RoleDelegationViewSet,
    RuleAcknowledgmentViewSet,
)

router = DefaultRouter()
router.register(r'rules', ChamaRuleViewSet, basename='chama-rule')
router.register(r'acknowledgments', RuleAcknowledgmentViewSet, basename='rule-acknowledgment')
router.register(r'approvals', ApprovalRequestViewSet, basename='approval-request')
router.register(r'role-changes', RoleChangeViewSet, basename='role-change')
router.register(r'delegations', RoleDelegationViewSet, basename='role-delegation')
router.register(r'motions', MotionViewSet, basename='motion')

urlpatterns = [
    path('overview/', GovernanceOverviewView.as_view(), name='governance-overview'),
    path('audit-trail/', GovernanceAuditTrailView.as_view(), name='governance-audit-trail'),
    path('', include(router.urls)),
]
