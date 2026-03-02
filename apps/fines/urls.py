# Fines Module URL Configuration

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    FineRuleViewSet,
    FineViewSet,
    FineCategoryViewSet,
    FineAdjustmentViewSet,
    FinePaymentViewSet,
    FineReminderViewSet,
)

router = DefaultRouter()
router.register(r'rules', FineRuleViewSet, basename='fine-rule')
router.register(r'categories', FineCategoryViewSet, basename='fine-category')
router.register(r'adjustments', FineAdjustmentViewSet, basename='fine-adjustment')
router.register(r'payments', FinePaymentViewSet, basename='fine-payment')
router.register(r'reminders', FineReminderViewSet, basename='fine-reminder')
router.register(r'', FineViewSet, basename='fine')

urlpatterns = [
    path('members/my-fines/', FineViewSet.as_view({'get': 'my_fines'}), name='member-fines'),
    path('members/my-fines/stats/', FineViewSet.as_view({'get': 'my_fines_stats'}), name='member-fines-stats'),
    path('', include(router.urls)),
]
