"""
Deep Links and Navigation Service

Manages deep link configuration, routing, and navigation.
"""

import logging

from django.conf import settings

logger = logging.getLogger(__name__)


class DeepLinksService:
    """Service for managing deep links and navigation."""

    # Deep link configuration
    DEEP_LINK_SCHEME = getattr(settings, 'DEEP_LINK_SCHEME', 'mychama')
    DEEP_LINK_DOMAIN = getattr(settings, 'DEEP_LINK_DOMAIN', 'mychama.app')

    @staticmethod
    def generate_deep_link(
        route: str,
        params: dict = None,
        chama_id: str = None,
    ) -> str:
        """
        Generate a deep link URL.
        Returns deep link string.
        """
        # Build query parameters
        query_params = []
        if params:
            for key, value in params.items():
                query_params.append(f"{key}={value}")

        if chama_id:
            query_params.append(f"chama_id={chama_id}")

        query_string = "&".join(query_params)

        # Generate deep link
        if query_string:
            deep_link = f"{DeepLinksService.DEEP_LINK_SCHEME}://{route}?{query_string}"
        else:
            deep_link = f"{DeepLinksService.DEEP_LINK_SCHEME}://{route}"

        return deep_link

    @staticmethod
    def generate_universal_link(
        route: str,
        params: dict = None,
        chama_id: str = None,
    ) -> str:
        """
        Generate a universal link (HTTPS).
        Returns universal link string.
        """
        # Build query parameters
        query_params = []
        if params:
            for key, value in params.items():
                query_params.append(f"{key}={value}")

        if chama_id:
            query_params.append(f"chama_id={chama_id}")

        query_string = "&".join(query_params)

        # Generate universal link
        if query_string:
            universal_link = f"https://{DeepLinksService.DEEP_LINK_DOMAIN}/{route}?{query_string}"
        else:
            universal_link = f"https://{DeepLinksService.DEEP_LINK_DOMAIN}/{route}"

        return universal_link

    @staticmethod
    def parse_deep_link(deep_link: str) -> dict:
        """
        Parse a deep link URL.
        Returns parsed components.
        """
        import urllib.parse

        try:
            # Parse URL
            parsed = urllib.parse.urlparse(deep_link)

            # Extract components
            scheme = parsed.scheme
            netloc = parsed.netloc
            path = parsed.path
            query = parsed.query

            # Parse query parameters
            params = urllib.parse.parse_qs(query)

            # Convert single-value lists to values
            parsed_params = {}
            for key, value in params.items():
                if len(value) == 1:
                    parsed_params[key] = value[0]
                else:
                    parsed_params[key] = value

            return {
                'scheme': scheme,
                'netloc': netloc,
                'path': path,
                'params': parsed_params,
                'route': netloc or path.lstrip('/'),
            }

        except Exception as e:
            logger.error(f"Failed to parse deep link: {e}")
            return None

    @staticmethod
    def get_route_config() -> dict:
        """
        Get route configuration for deep links.
        """
        return {
            'home': {
                'route': 'home',
                'description': 'Home screen',
                'params': [],
            },
            'chama_detail': {
                'route': 'chama/detail',
                'description': 'Chama detail screen',
                'params': ['chama_id'],
            },
            'contribution': {
                'route': 'contribution',
                'description': 'Make contribution screen',
                'params': ['chama_id', 'contribution_id'],
            },
            'loan_detail': {
                'route': 'loan/detail',
                'description': 'Loan detail screen',
                'params': ['loan_id'],
            },
            'meeting_detail': {
                'route': 'meeting/detail',
                'description': 'Meeting detail screen',
                'params': ['meeting_id'],
            },
            'payment_detail': {
                'route': 'payment/detail',
                'description': 'Payment detail screen',
                'params': ['payment_id'],
            },
            'member_detail': {
                'route': 'member/detail',
                'description': 'Member detail screen',
                'params': ['chama_id', 'user_id'],
            },
            'invite': {
                'route': 'invite',
                'description': 'Invite preview screen',
                'params': ['invite_code'],
            },
            'notification': {
                'route': 'notification',
                'description': 'Notification detail screen',
                'params': ['notification_id'],
            },
        }

    @staticmethod
    def generate_invite_link(invite_code: str) -> dict:
        """
        Generate invite deep link.
        Returns deep link and universal link.
        """
        deep_link = DeepLinksService.generate_deep_link(
            route='invite',
            params={'invite_code': invite_code},
        )

        universal_link = DeepLinksService.generate_universal_link(
            route='invite',
            params={'invite_code': invite_code},
        )

        return {
            'deep_link': deep_link,
            'universal_link': universal_link,
            'invite_code': invite_code,
        }

    @staticmethod
    def generate_contribution_link(chama_id: str, contribution_id: str = None) -> dict:
        """
        Generate contribution deep link.
        """
        params = {'chama_id': chama_id}
        if contribution_id:
            params['contribution_id'] = contribution_id

        deep_link = DeepLinksService.generate_deep_link(
            route='contribution',
            params=params,
        )

        universal_link = DeepLinksService.generate_universal_link(
            route='contribution',
            params=params,
        )

        return {
            'deep_link': deep_link,
            'universal_link': universal_link,
        }

    @staticmethod
    def generate_meeting_link(meeting_id: str) -> dict:
        """
        Generate meeting deep link.
        """
        deep_link = DeepLinksService.generate_deep_link(
            route='meeting/detail',
            params={'meeting_id': meeting_id},
        )

        universal_link = DeepLinksService.generate_universal_link(
            route='meeting/detail',
            params={'meeting_id': meeting_id},
        )

        return {
            'deep_link': deep_link,
            'universal_link': universal_link,
        }

    @staticmethod
    def generate_payment_link(payment_id: str) -> dict:
        """
        Generate payment deep link.
        """
        deep_link = DeepLinksService.generate_deep_link(
            route='payment/detail',
            params={'payment_id': payment_id},
        )

        universal_link = DeepLinksService.generate_universal_link(
            route='payment/detail',
            params={'payment_id': payment_id},
        )

        return {
            'deep_link': deep_link,
            'universal_link': universal_link,
        }

    @staticmethod
    def validate_deep_link(deep_link: str) -> dict:
        """
        Validate a deep link.
        Returns validation result.
        """
        parsed = DeepLinksService.parse_deep_link(deep_link)

        if not parsed:
            return {
                'valid': False,
                'error': 'Invalid deep link format',
            }

        # Check scheme
        if parsed['scheme'] != DeepLinksService.DEEP_LINK_SCHEME:
            return {
                'valid': False,
                'error': f"Invalid scheme: {parsed['scheme']}",
            }

        # Check route
        route_config = DeepLinksService.get_route_config()
        route = parsed['route']

        if route not in route_config:
            return {
                'valid': False,
                'error': f"Unknown route: {route}",
            }

        # Check required params
        required_params = route_config[route]['params']
        for param in required_params:
            if param not in parsed['params']:
                return {
                    'valid': False,
                    'error': f"Missing required parameter: {param}",
                }

        return {
            'valid': True,
            'route': route,
            'params': parsed['params'],
        }

    @staticmethod
    def get_shareable_link(
        entity_type: str,
        entity_id: str,
        chama_id: str = None,
    ) -> dict:
        """
        Get shareable link for an entity.
        """
        if entity_type == 'chama':
            return DeepLinksService.generate_deep_link(
                route='chama/detail',
                params={'chama_id': entity_id},
            )
        elif entity_type == 'meeting':
            return DeepLinksService.generate_meeting_link(entity_id)
        elif entity_type == 'payment':
            return DeepLinksService.generate_payment_link(entity_id)
        elif entity_type == 'invite':
            return DeepLinksService.generate_invite_link(entity_id)
        else:
            return {
                'deep_link': None,
                'universal_link': None,
                'error': f"Unknown entity type: {entity_type}",
            }
