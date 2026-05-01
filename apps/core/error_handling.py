"""
Error, Loading, and Empty State System

Manages error handling, loading states, and empty state patterns.
"""

import logging

from django.utils import timezone

logger = logging.getLogger(__name__)


class ErrorHandlingService:
    """Service for managing error handling and states."""

    # Error codes
    ERROR_CODES = {
        # Authentication errors
        'AUTH_001': 'Invalid credentials',
        'AUTH_002': 'Account locked',
        'AUTH_003': 'Session expired',
        'AUTH_004': 'Invalid token',
        'AUTH_005': 'Permission denied',

        # Validation errors
        'VAL_001': 'Invalid input',
        'VAL_002': 'Missing required field',
        'VAL_003': 'Invalid format',
        'VAL_004': 'Value out of range',

        # Business logic errors
        'BIZ_001': 'Insufficient funds',
        'BIZ_002': 'Loan limit exceeded',
        'BIZ_003': 'Contribution already paid',
        'BIZ_004': 'Meeting already scheduled',
        'BIZ_005': 'Member already exists',

        # System errors
        'SYS_001': 'Internal server error',
        'SYS_002': 'Service unavailable',
        'SYS_003': 'Database error',
        'SYS_004': 'External service error',

        # Payment errors
        'PAY_001': 'Payment failed',
        'PAY_002': 'Payment timeout',
        'PAY_003': 'Invalid payment method',
        'PAY_004': 'Duplicate payment',
    }

    @staticmethod
    def create_error_response(
        error_code: str,
        message: str = None,
        details: dict = None,
        status_code: int = 400,
    ) -> dict:
        """
        Create a standardized error response.
        """
        error_message = message or ErrorHandlingService.ERROR_CODES.get(
            error_code, 'Unknown error'
        )

        return {
            'success': False,
            'error': {
                'code': error_code,
                'message': error_message,
                'details': details or {},
                'timestamp': timezone.now().isoformat(),
            },
            'status_code': status_code,
        }

    @staticmethod
    def create_success_response(
        data: dict = None,
        message: str = None,
    ) -> dict:
        """
        Create a standardized success response.
        """
        return {
            'success': True,
            'data': data or {},
            'message': message or 'Operation successful',
            'timestamp': timezone.now().isoformat(),
        }

    @staticmethod
    def create_paginated_response(
        data: list,
        page: int,
        page_size: int,
        total: int,
    ) -> dict:
        """
        Create a paginated response.
        """
        total_pages = (total + page_size - 1) // page_size

        return {
            'success': True,
            'data': data,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total': total,
                'total_pages': total_pages,
                'has_next': page < total_pages,
                'has_previous': page > 1,
            },
            'timestamp': timezone.now().isoformat(),
        }

    @staticmethod
    def create_loading_response(message: str = 'Loading...') -> dict:
        """
        Create a loading state response.
        """
        return {
            'success': True,
            'loading': True,
            'message': message,
            'timestamp': timezone.now().isoformat(),
        }

    @staticmethod
    def create_empty_response(
        message: str = 'No data available',
        entity_type: str = None,
    ) -> dict:
        """
        Create an empty state response.
        """
        return {
            'success': True,
            'data': [],
            'empty': True,
            'message': message,
            'entity_type': entity_type,
            'timestamp': timezone.now().isoformat(),
        }

    @staticmethod
    def handle_exception(
        exception: Exception,
        error_code: str = 'SYS_001',
        log_error: bool = True,
    ) -> dict:
        """
        Handle an exception and return error response.
        """
        if log_error:
            logger.error(
                f"Exception occurred: {exception}",
                exc_info=True,
            )

        return ErrorHandlingService.create_error_response(
            error_code=error_code,
            message=str(exception),
            status_code=500,
        )

    @staticmethod
    def validate_required_fields(
        data: dict,
        required_fields: list[str],
    ) -> dict | None:
        """
        Validate required fields in data.
        Returns error response if validation fails, None if successful.
        """
        missing_fields = []
        for field in required_fields:
            if field not in data or data[field] is None:
                missing_fields.append(field)

        if missing_fields:
            return ErrorHandlingService.create_error_response(
                error_code='VAL_002',
                message=f"Missing required fields: {', '.join(missing_fields)}",
                details={'missing_fields': missing_fields},
            )

        return None

    @staticmethod
    def validate_field_format(
        field_name: str,
        value: str,
        format_type: str,
    ) -> dict | None:
        """
        Validate field format.
        Returns error response if validation fails, None if successful.
        """
        import re

        patterns = {
            'email': r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$',
            'phone': r'^\+?[1-9]\d{1,14}$',
            'uuid': r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        }

        if format_type in patterns:
            if not re.match(patterns[format_type], str(value)):
                return ErrorHandlingService.create_error_response(
                    error_code='VAL_003',
                    message=f"Invalid {field_name} format",
                    details={'field': field_name, 'format': format_type},
                )

        return None

    @staticmethod
    def get_error_message(error_code: str) -> str:
        """
        Get error message by code.
        """
        return ErrorHandlingService.ERROR_CODES.get(error_code, 'Unknown error')

    @staticmethod
    def log_error(
        error_code: str,
        message: str,
        details: dict = None,
        user_id: str = None,
    ) -> None:
        """
        Log an error.
        """
        logger.error(
            f"Error {error_code}: {message}",
            extra={
                'error_code': error_code,
                'details': details,
                'user_id': user_id,
            },
        )


class LoadingStateService:
    """Service for managing loading states."""

    @staticmethod
    def create_skeleton_loader(
        entity_type: str,
        count: int = 5,
    ) -> dict:
        """
        Create skeleton loader data.
        """
        skeletons = []
        for i in range(count):
            skeletons.append({
                'id': f'skeleton_{i}',
                'loading': True,
            })

        return {
            'success': True,
            'data': skeletons,
            'loading': True,
            'entity_type': entity_type,
        }

    @staticmethod
    def create_progress_response(
        progress: int,
        message: str = 'Processing...',
    ) -> dict:
        """
        Create a progress response.
        """
        return {
            'success': True,
            'loading': True,
            'progress': min(100, max(0, progress)),
            'message': message,
            'timestamp': timezone.now().isoformat(),
        }


class EmptyStateService:
    """Service for managing empty states."""

    @staticmethod
    def create_empty_state(
        entity_type: str,
        message: str = None,
        action_text: str = None,
        action_route: str = None,
    ) -> dict:
        """
        Create an empty state response.
        """
        default_messages = {
            'chamas': 'You are not a member of any chama yet',
            'contributions': 'No contributions found',
            'loans': 'No loans found',
            'meetings': 'No meetings scheduled',
            'payments': 'No payments found',
            'members': 'No members found',
            'notifications': 'No notifications',
            'documents': 'No documents found',
        }

        return {
            'success': True,
            'data': [],
            'empty': True,
            'entity_type': entity_type,
            'message': message or default_messages.get(entity_type, 'No data available'),
            'action': {
                'text': action_text,
                'route': action_route,
            } if action_text and action_route else None,
            'timestamp': timezone.now().isoformat(),
        }

    @staticmethod
    def get_empty_state_config() -> dict:
        """
        Get empty state configuration for different entities.
        """
        return {
            'chamas': {
                'icon': 'group',
                'title': 'No Chamas Yet',
                'message': 'Create or join a chama to get started',
                'action_text': 'Create Chama',
                'action_route': '/chama/create',
            },
            'contributions': {
                'icon': 'payments',
                'title': 'No Contributions',
                'message': 'Make your first contribution',
                'action_text': 'Contribute',
                'action_route': '/contribution',
            },
            'loans': {
                'icon': 'account_balance',
                'title': 'No Loans',
                'message': 'Apply for a loan',
                'action_text': 'Apply for Loan',
                'action_route': '/loan/apply',
            },
            'meetings': {
                'icon': 'event',
                'title': 'No Meetings',
                'message': 'Schedule a meeting',
                'action_text': 'Schedule Meeting',
                'action_route': '/meeting/create',
            },
            'payments': {
                'icon': 'receipt',
                'title': 'No Payments',
                'message': 'No payment history',
                'action_text': None,
                'action_route': None,
            },
            'members': {
                'icon': 'people',
                'title': 'No Members',
                'message': 'Invite members to join',
                'action_text': 'Invite Members',
                'action_route': '/invite',
            },
            'notifications': {
                'icon': 'notifications_none',
                'title': 'No Notifications',
                'message': 'You are all caught up',
                'action_text': None,
                'action_route': None,
            },
            'documents': {
                'icon': 'folder_open',
                'title': 'No Documents',
                'message': 'Upload documents',
                'action_text': 'Upload',
                'action_route': '/documents/upload',
            },
        }
