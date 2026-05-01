from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from rest_framework import status
from rest_framework.exceptions import (
    APIException,
    AuthenticationFailed,
    NotAuthenticated,
    NotFound,
    PermissionDenied,
    Throttled,
    ValidationError,
)
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


def _coerce_error_map(data) -> dict:
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        return {"non_field_errors": data}
    if data in (None, ""):
        return {}
    return {"detail": data}


def _stringify_detail(detail) -> str:
    if isinstance(detail, list) and detail:
        return str(detail[0])
    if isinstance(detail, dict):
        first_value = next(iter(detail.values()), "Request failed.")
        if isinstance(first_value, list) and first_value:
            return str(first_value[0])
        return str(first_value)
    if detail in (None, ""):
        return "Request failed."
    return str(detail)


def _default_message_for_exception(exc, response) -> str:
    if isinstance(exc, ValidationError):
        return "Validation failed."
    if isinstance(exc, NotAuthenticated | AuthenticationFailed):
        return "Please sign in to continue."
    if isinstance(exc, PermissionDenied | DjangoPermissionDenied):
        return "You do not have permission to perform this action."
    if isinstance(exc, NotFound):
        return "The requested resource was not found."
    if isinstance(exc, Throttled):
        return "Too many requests. Please try again later."
    if isinstance(exc, APIException):
        return _stringify_detail(getattr(exc, "detail", None))
    if response is not None and response.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        return "Something went wrong. Please try again."
    return "Request failed."


def _default_code_for_exception(exc, response) -> str:
    if isinstance(exc, ValidationError):
        return "VALIDATION_ERROR"
    if isinstance(exc, NotAuthenticated | AuthenticationFailed):
        return "UNAUTHORIZED"
    if isinstance(exc, PermissionDenied | DjangoPermissionDenied):
        return "FORBIDDEN"
    if isinstance(exc, NotFound):
        return "NOT_FOUND"
    if isinstance(exc, Throttled):
        return "RATE_LIMITED"
    if response is not None and response.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        return "SERVER_ERROR"
    return "REQUEST_FAILED"


def custom_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)

    if response is None:
        view = context.get("view")
        request = context.get("request")
        logger.exception(
            "Unhandled API exception",
            exc_info=exc,
            extra={
                "view": getattr(view, "__class__", type("x", (), {})).__name__,
                "path": getattr(request, "path", ""),
                "method": getattr(request, "method", ""),
            },
        )
        return Response(
            {
                "success": False,
                "code": "SERVER_ERROR",
                "message": "Something went wrong. Please try again.",
                "errors": {},
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    raw_data = response.data
    errors = _coerce_error_map(raw_data)
    message = _default_message_for_exception(exc, response)
    code = _default_code_for_exception(exc, response)

    if isinstance(raw_data, dict) and "detail" in raw_data:
        message = _stringify_detail(raw_data.get("detail"))
        if len(raw_data) == 1:
            errors = {}
        else:
            errors = {key: value for key, value in raw_data.items() if key != "detail"}

    if isinstance(raw_data, dict) and raw_data.get("code"):
        code = str(raw_data["code"]).upper()

    if isinstance(exc, Throttled) and getattr(exc, "wait", None):
        errors = {**errors, "retry_after_seconds": int(exc.wait)}

    response.data = {
        "success": False,
        "code": code,
        "message": message,
        "errors": errors,
    }
    return response
