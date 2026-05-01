"""URL routing for Payout views."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import PayoutRotationViewSet, PayoutViewSet

router = DefaultRouter()
router.register(r"payouts", PayoutViewSet, basename="payout")
router.register(r"rotations", PayoutRotationViewSet, basename="rotation")

urlpatterns = [
    path("", include(router.urls)),
]
