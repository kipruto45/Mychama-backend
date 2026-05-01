from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from apps.payments.views import MpesaCallbackView


@method_decorator(csrf_exempt, name="dispatch")
class LegacyMpesaCallbackView(APIView):
    """Backward-compatible callback endpoint wrapper."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        view = MpesaCallbackView.as_view()
        return view(request._request, *args, **kwargs)
