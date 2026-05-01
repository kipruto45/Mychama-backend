"""
Feature Gating Decorators
Use these decorators to protect endpoints based on subscription features
"""
import functools
from collections.abc import Callable

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response

from .services import (
    check_seat_limit,
    get_access_status,
    get_active_chama_from_request,
    get_entitlements,
    has_feature,
)


class PaymentRequiredException(APIException):
    status_code = status.HTTP_402_PAYMENT_REQUIRED
    default_code = 'payment_required'
    default_detail = 'An active paid subscription is required for this action.'


def _build_payment_required_payload(chama, access, feature_key: str | None = None):
    payload = {
        'error': 'payment_required',
        'message': 'Your billing access does not cover this action.',
        'reason': access.get('reason'),
        'trial_ends_at': access.get('trial_ends_at'),
        'trial_days_remaining': access.get('trial_days_remaining', 0),
    }
    if chama is not None:
        payload['chama_id'] = str(chama.id)
    if feature_key:
        payload['feature'] = feature_key
    return payload


def enforce_billing_access(request):
    """
    Ensure the active chama has a valid trial or paid subscription.
    Returns the active chama when available.
    """
    chama = get_active_chama_from_request(request)
    if not chama:
        return None

    access = get_access_status(chama)
    if access.get('requires_payment'):
        raise PaymentRequiredException(detail=_build_payment_required_payload(chama, access))
    return chama


class BillingAccessMixin:
    """
    Mixin for APIView/ViewSet classes that need billing enforcement.
    """
    billing_feature_key: str | None = None
    billing_or_features: list[str] | None = None
    skip_billing_access: bool = False

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)

        if self.skip_billing_access:
            return

        chama = enforce_billing_access(request)
        if not chama or not self.billing_feature_key:
            return

        if has_feature(chama, self.billing_feature_key):
            return

        if self.billing_or_features:
            for feature_key in self.billing_or_features:
                if has_feature(chama, feature_key):
                    return

        access = get_access_status(chama)
        raise PaymentRequiredException(
            detail=_build_payment_required_payload(
                chama,
                access,
                feature_key=self.billing_feature_key,
            )
        )


def require_billing_access():
    """
    Decorator that blocks access when a trial has expired and no paid plan is active.
    """
    def decorator(view_func: Callable):
        @functools.wraps(view_func)
        def wrapper(request, *args, **kwargs):
            enforce_billing_access(request)
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def require_feature(feature_key: str, or_features: list[str] | None = None):
    """
    Decorator that requires a specific feature to access an endpoint.
    
    Usage:
        @require_feature('exports_pdf')
        def my_view(request):
            ...
    
    Or require ANY of multiple features:
        @require_feature('exports_pdf', or_features=['exports_excel'])
        def my_view(request):
            ...
    """
    def decorator(view_func: Callable):
        @functools.wraps(view_func)
        def wrapper(request_or_self, *args, **kwargs):
            # Handle both function-based views (request) and methods (self)
            # For schema generation, swagger_fake_view will be set
            is_method = not isinstance(request_or_self, type(request_or_self)) or hasattr(request_or_self, 'request')
            
            if is_method and getattr(request_or_self, "swagger_fake_view", False):
                # Skip feature check during schema generation
                return view_func(request_or_self, *args, **kwargs)
            
            request = request_or_self if not hasattr(request_or_self, 'request') else request_or_self.request
            
            # Get chama from request
            chama = enforce_billing_access(request)
            
            if not chama:
                return Response(
                    {'error': 'No active chama selected'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Check primary feature
            if has_feature(chama, feature_key):
                return view_func(request_or_self, *args, **kwargs)
            
            # Check OR features if provided
            if or_features:
                for or_feature in or_features:
                    if has_feature(chama, or_feature):
                        return view_func(request_or_self, *args, **kwargs)
            
            # Feature not available - return upgrade required
            entitlements = get_entitlements(chama)
            
            return Response(
                {
                    'error': 'upgrade_required',
                    'message': 'This feature requires a higher subscription plan',
                    'feature': feature_key,
                    'current_plan': entitlements.get('plan_code', 'FREE'),
                },
                status=status.HTTP_402_PAYMENT_REQUIRED
            )
        
        return wrapper
    return decorator


def require_feature_async(feature_key: str, or_features: list[str] | None = None):
    """
    Async version of require_feature for async views
    """
    def decorator(view_func: Callable):
        @functools.wraps(view_func)
        async def wrapper(request, *args, **kwargs):
            chama = enforce_billing_access(request)
            
            if not chama:
                return Response(
                    {'error': 'No active chama selected'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            if has_feature(chama, feature_key):
                return await view_func(request, *args, **kwargs)
            
            if or_features:
                for or_feature in or_features:
                    if has_feature(chama, or_feature):
                        return await view_func(request, *args, **kwargs)
            
            entitlements = get_entitlements(chama)
            
            return Response(
                {
                    'error': 'upgrade_required',
                    'message': 'This feature requires a higher subscription plan',
                    'feature': feature_key,
                    'current_plan': entitlements.get('plan_code', 'FREE'),
                },
                status=status.HTTP_402_PAYMENT_REQUIRED
            )
        
        return wrapper
    return decorator


def require_seat_limit():
    """
    Decorator that enforces seat limits.
    Used for membership creation endpoints.
    """
    def decorator(view_func: Callable):
        @functools.wraps(view_func)
        def wrapper(request, *args, **kwargs):
            chama = enforce_billing_access(request)
            
            if not chama:
                return Response(
                    {'error': 'No active chama selected'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            seat_info = check_seat_limit(chama)
            
            if not seat_info['is_valid']:
                return Response(
                    {
                        'error': 'seat_limit_exceeded',
                        'message': f'Seat limit exceeded. Current: {seat_info["current"]}, Limit: {seat_info["limit"]}',
                        'current': seat_info['current'],
                        'limit': seat_info['limit'],
                    },
                    status=status.HTTP_402_PAYMENT_REQUIRED
                )
            
            return view_func(request, *args, **kwargs)
        
        return wrapper
    return decorator


def require_seat_limit_async():
    """Async version of require_seat_limit"""
    def decorator(view_func: Callable):
        @functools.wraps(view_func)
        async def wrapper(request, *args, **kwargs):
            chama = enforce_billing_access(request)
            
            if not chama:
                return Response(
                    {'error': 'No active chama selected'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            seat_info = check_seat_limit(chama)
            
            if not seat_info['is_valid']:
                return Response(
                    {
                        'error': 'seat_limit_exceeded',
                        'message': f'Seat limit exceeded. Current: {seat_info["current"]}, Limit: {seat_info["limit"]}',
                        'current': seat_info['current'],
                        'limit': seat_info['limit'],
                    },
                    status=status.HTTP_402_PAYMENT_REQUIRED
                )
            
            return await view_func(request, *args, **kwargs)
        
        return wrapper
    return decorator


def require_plan(minimum_plan: str):
    """
    Decorator that requires a minimum plan level.
    
    Usage:
        @require_plan('PRO')
        def my_view(request):
            ...
    """
    PLAN_LEVELS = {'FREE': 0, 'PRO': 1, 'ENTERPRISE': 2}
    
    def decorator(view_func: Callable):
        @functools.wraps(view_func)
        def wrapper(request, *args, **kwargs):
            chama = enforce_billing_access(request)
            
            if not chama:
                return Response(
                    {'error': 'No active chama selected'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            entitlements = get_entitlements(chama)
            current_plan = entitlements.get('plan_code', 'FREE')
            
            current_level = PLAN_LEVELS.get(current_plan, 0)
            required_level = PLAN_LEVELS.get(minimum_plan, 0)
            
            if current_level < required_level:
                return Response(
                    {
                        'error': 'upgrade_required',
                        'message': f'This feature requires {minimum_plan} plan or higher',
                        'current_plan': current_plan,
                        'required_plan': minimum_plan,
                    },
                    status=status.HTTP_402_PAYMENT_REQUIRED
                )
            
            return view_func(request, *args, **kwargs)
        
        return wrapper
    return decorator


def require_plan_async(minimum_plan: str):
    """Async version of require_plan"""
    PLAN_LEVELS = {'FREE': 0, 'PRO': 1, 'ENTERPRISE': 2}
    
    def decorator(view_func: Callable):
        @functools.wraps(view_func)
        async def wrapper(request, *args, **kwargs):
            chama = enforce_billing_access(request)
            
            if not chama:
                return Response(
                    {'error': 'No active chama selected'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            entitlements = get_entitlements(chama)
            current_plan = entitlements.get('plan_code', 'FREE')
            
            current_level = PLAN_LEVELS.get(current_plan, 0)
            required_level = PLAN_LEVELS.get(minimum_plan, 0)
            
            if current_level < required_level:
                return Response(
                    {
                        'error': 'upgrade_required',
                        'message': f'This feature requires {minimum_plan} plan or higher',
                        'current_plan': current_plan,
                        'required_plan': minimum_plan,
                    },
                    status=status.HTTP_402_PAYMENT_REQUIRED
                )
            
            return await view_func(request, *args, **kwargs)
        
        return wrapper
    return decorator


def add_entitlements_to_response(response, chama):
    """
    Add entitlements info to response headers for debugging/UI
    """
    entitlements = get_entitlements(chama, use_cache=True)
    
    response['X-Plan-Seat-Limit'] = str(entitlements.get('seat_limit', 25))
    response['X-Plan-Support-Level'] = entitlements.get('support_level', 'community')
    
    return response
