"""
URL routing for wallet transfers, chama payments, and loan updates.
Add these patterns to apps/finance/urls.py
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.finance.views_new import (
    WalletTransferViewSet,
    ChamaPaymentViewSet,
    LoanUpdateRequestViewSet,
)

router = DefaultRouter()
router.register(r"transfers", WalletTransferViewSet, basename="wallet-transfer")
router.register(r"payments", ChamaPaymentViewSet, basename="chama-payment")
router.register(r"loan-updates", LoanUpdateRequestViewSet, basename="loan-update")

urlpatterns = [
    path("", include(router.urls)),
]
