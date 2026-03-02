import uuid

from django.conf import settings

from core.request_context import clear_correlation_id, set_correlation_id


class CorrelationIdMiddleware:
    request_header = "HTTP_X_REQUEST_ID"
    response_header = "X-Request-ID"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        correlation_id = str(request.META.get(self.request_header, "")).strip() or str(
            uuid.uuid4()
        )
        request.correlation_id = correlation_id
        set_correlation_id(correlation_id)

        try:
            response = self.get_response(request)
        finally:
            clear_correlation_id()

        response[self.response_header] = correlation_id
        return response


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        csp = str(getattr(settings, "CONTENT_SECURITY_POLICY", "")).strip()
        if csp:
            response.setdefault("Content-Security-Policy", csp)

        permissions_policy = str(getattr(settings, "PERMISSIONS_POLICY", "")).strip()
        if permissions_policy:
            response.setdefault("Permissions-Policy", permissions_policy)

        referrer_policy = str(getattr(settings, "SECURE_REFERRER_POLICY", "")).strip()
        if referrer_policy:
            response.setdefault("Referrer-Policy", referrer_policy)

        response.setdefault(
            "X-Frame-Options",
            str(getattr(settings, "X_FRAME_OPTIONS", "DENY")),
        )
        response.setdefault("X-Content-Type-Options", "nosniff")
        return response
