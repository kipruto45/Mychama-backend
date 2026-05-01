"""
Standardized API Response utilities.

Provides consistent response formatting across all API endpoints with:
- Pagination support
- Error handling
- Success/ailure patterns
"""

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from django.db.models import QuerySet
from rest_framework.pagination import CursorPagination
from rest_framework.response import Response
from rest_framework.serializers import Serializer

T = TypeVar("T")


@dataclass
class PaginatedResponse(Generic[T]):
    """Standard paginated response structure."""
    items: list[Any]
    pagination: dict = field(default_factory=dict)
    
    def to_response(self) -> Response:
        return Response({
            "success": True,
            "data": self.items,
            "pagination": self.pagination,
        })


@dataclass
class StandardResponse:
    """Standard API response builder."""
    
    @staticmethod
    def success(
        data: Any = None,
        message: str = None,
        meta: dict = None,
        status_code: int = 200,
    ) -> Response:
        """Return a success response."""
        payload = {"success": True}
        if data is not None:
            payload["data"] = data
        if message:
            payload["message"] = message
        if meta:
            payload["meta"] = meta
        return Response(payload, status=status_code)
    
    @staticmethod
    def error(
        message: str,
        code: str = None,
        details: dict = None,
        status_code: int = 400,
    ) -> Response:
        """Return an error response."""
        payload = {
            "success": False,
            "message": message,
            "errors": {},
        }
        if code:
            payload["errors"]["code"] = code
        if details:
            payload["errors"] = {**payload["errors"], **details}
        return Response(payload, status=status_code)
    
    @staticmethod
    def paginated(
        queryset: QuerySet,
        serializer: Serializer | type[Serializer],
        request=None,
        page_size: int = 20,
        max_page_size: int = 100,
    ) -> Response:
        """Return a paginated response from a queryset."""
        paginator = StandardPaginator(page_size=page_size, max_page_size=max_page_size)
        
        if request:
            # Support both page and cursor pagination
            page = request.query_params.get("page")
            if page and page != "cursor":
                # Numbered page pagination
                try:
                    page = int(page)
                except (ValueError, TypeError):
                    page = 1
                paginator = NumberedPaginator(page_size=page_size, max_page_size=max_page_size)
                page_obj = paginator.paginate_queryset(queryset, request)
                if page_obj is not None:
                    serializer_instance = serializer(page_obj, many=True)
                    return paginator.get_paginated_response(serializer_instance.data)
            else:
                # Cursor pagination (default)
                paginator = StandardPaginator(
                    page_size=page_size,
                    max_page_size=max_page_size,
                )
                cursor = request.query_params.get("cursor")
                if cursor:
                    paginator.cursor = cursor
                    
        page_obj = paginator.paginate_queryset(queryset, request)
        if page_obj is not None:
            serializer_instance = serializer(page_obj, many=True)
            return paginator.get_paginated_response(serializer_instance.data)
        
        # Fallback for unpaginated
        serializer_instance = serializer(queryset, many=True)
        return Response({
            "success": True,
            "data": serializer_instance.data,
        })


class StandardPaginator(CursorPagination):
    """Standard cursor paginator for the API."""
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
    ordering = "-created_at"
    cursor_query_param = "cursor"


class NumberedPaginator(CursorPagination):
    """Numbered page paginator for the API."""
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
    ordering = "-created_at"
    page_query_param = "page"
    paginate_by = 20


def paginated_response(
    queryset: QuerySet,
    serializer_class: type[Serializer],
    request=None,
    page_size: int = 20,
    context: dict = None,
) -> Response:
    """
    Create a standardized paginated response.
    
    Args:
        queryset: Django QuerySet to paginate
        serializer_class: DRF Serializer class to serialize items
        request: HTTP request for pagination params
        page_size: Default page size
        context: Additional context for serializer
    
    Returns:
        Response with standardized format
    """
    # Get page size from request if available
    if request:
        try:
            page_size = int(request.query_params.get("page_size", page_size))
            page_size = min(page_size, 100)  # Cap at 100
        except (ValueError, TypeError):
            pass
    
    # Support cursor-based pagination
    cursor = request.query_params.get("cursor") if request else None
    
    if cursor:
        # Cursor pagination
        paginator = StandardPaginator()
        paginator.cursor = cursor
        page = paginator.paginate_queryset(queryset, request)
    else:
        # Simple offset pagination
        offset = int(request.query_params.get("offset", 0)) if request else 0
        limit = min(page_size, 100)
        page = queryset[offset:offset + limit]
    
    serializer = serializer_class(page, many=True, context=context)
    
    # Build pagination metadata
    pagination = {
        "limit": len(page),
        "offset": offset if request else 0,
        "total": queryset.count() if hasattr(queryset, 'count') else len(queryset),
    }
    
    # Add cursor for next page if there are more results
    if len(page) == limit and hasattr(queryset, 'count'):
        if offset + limit < queryset.count():
            pagination["next_cursor"] = str(offset + limit)
    
    return Response({
        "success": True,
        "data": serializer.data,
        "pagination": pagination,
    })


def success_response(data: Any = None, message: str = None, meta: dict = None) -> Response:
    """Create a standardized success response."""
    payload = {"success": True}
    if data is not None:
        payload["data"] = data
    if message:
        payload["message"] = message
    if meta:
        payload["meta"] = meta
    return Response(payload)


def error_response(
    message: str,
    code: str = None,
    details: dict = None,
    status_code: int = 400,
) -> Response:
    """Create a standardized error response."""
    payload = {
        "success": False,
        "message": message,
        "errors": {},
    }
    if code:
        payload["errors"]["code"] = code
    if details:
        payload["errors"] = {**payload["errors"], **details}
    return Response(payload, status=status_code)


ApiResponse = StandardResponse
