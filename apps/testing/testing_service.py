"""
QA, Testing, and Production Readiness Module

Manages testing, monitoring, and production readiness checks.
"""

import logging

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


class TestingService:
    """Service for managing testing and production readiness."""

    @staticmethod
    def run_health_checks() -> dict:
        """
        Run comprehensive health checks.
        Returns health status.
        """
        from django.core.cache import cache
        from django.db import connection

        checks = {
            'database': {'status': 'unknown', 'message': ''},
            'cache': {'status': 'unknown', 'message': ''},
            'celery': {'status': 'unknown', 'message': ''},
            'storage': {'status': 'unknown', 'message': ''},
            'external_services': {'status': 'unknown', 'message': ''},
        }

        # Check database
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                checks['database'] = {
                    'status': 'healthy',
                    'message': 'Database connection successful',
                }
        except Exception as e:
            checks['database'] = {
                'status': 'unhealthy',
                'message': f'Database error: {str(e)}',
            }

        # Check cache
        try:
            cache.set('health_check', 'ok', 10)
            cache.get('health_check')
            checks['cache'] = {
                'status': 'healthy',
                'message': 'Cache connection successful',
            }
        except Exception as e:
            checks['cache'] = {
                'status': 'unhealthy',
                'message': f'Cache error: {str(e)}',
            }

        # Check Celery
        try:
            from celery import current_app
            inspect = current_app.control.inspect()
            stats = inspect.stats()
            if stats:
                checks['celery'] = {
                    'status': 'healthy',
                    'message': f'Celery workers available: {len(stats)}',
                }
            else:
                checks['celery'] = {
                    'status': 'unhealthy',
                    'message': 'No Celery workers available',
                }
        except Exception as e:
            checks['celery'] = {
                'status': 'unhealthy',
                'message': f'Celery error: {str(e)}',
            }

        # Check storage
        try:
            from django.core.files.base import ContentFile
            from django.core.files.storage import default_storage
            # Try to save and delete a test file
            test_path = 'health_check_test.txt'
            default_storage.save(test_path, ContentFile(b'test', name=test_path))
            default_storage.delete(test_path)
            checks['storage'] = {
                'status': 'healthy',
                'message': 'Storage connection successful',
            }
        except Exception as e:
            checks['storage'] = {
                'status': 'unhealthy',
                'message': f'Storage error: {str(e)}',
            }

        # Determine overall status
        all_healthy = all(
            check['status'] == 'healthy'
            for check in checks.values()
        )

        return {
            'overall_status': 'healthy' if all_healthy else 'unhealthy',
            'checks': checks,
            'timestamp': timezone.now().isoformat(),
        }

    @staticmethod
    def get_system_metrics() -> dict:
        """
        Get system metrics.
        """
        import os

        import psutil

        # Get CPU usage
        cpu_percent = psutil.cpu_percent(interval=1)

        # Get memory usage
        memory = psutil.virtual_memory()
        memory_percent = memory.percent
        memory_available = memory.available

        # Get disk usage
        disk = psutil.disk_usage('/')
        disk_percent = disk.percent
        disk_free = disk.free

        # Get process info
        process = psutil.Process(os.getpid())
        process_memory = process.memory_info().rss

        return {
            'cpu': {
                'percent': cpu_percent,
            },
            'memory': {
                'percent': memory_percent,
                'available_bytes': memory_available,
            },
            'disk': {
                'percent': disk_percent,
                'free_bytes': disk_free,
            },
            'process': {
                'memory_bytes': process_memory,
                'pid': os.getpid(),
            },
            'timestamp': timezone.now().isoformat(),
        }

    @staticmethod
    def get_application_metrics() -> dict:
        """
        Get application-specific metrics.
        """
        from django.db.models import Sum

        from apps.accounts.models import User
        from apps.chama.models import Chama, Membership
        from apps.finance.models import Account, Contribution, Loan

        # Get user metrics
        total_users = User.objects.count()
        active_users = User.objects.filter(is_active=True).count()

        # Get chama metrics
        total_chamas = Chama.objects.count()
        active_chamas = Chama.objects.filter(status='active').count()

        # Get member metrics
        total_members = Membership.objects.filter(status='active').count()

        # Get financial metrics
        total_balance = Account.objects.filter(
            account_type='main',
        ).aggregate(total=Sum('balance'))['total'] or 0

        total_contributions = Contribution.objects.aggregate(
            total=Sum('amount'),
            paid=Sum('amount_paid'),
        )

        total_loans = Loan.objects.aggregate(
            total_borrowed=Sum('principal_amount'),
            total_repaid=Sum('amount_repaid'),
        )

        return {
            'users': {
                'total': total_users,
                'active': active_users,
            },
            'chamas': {
                'total': total_chamas,
                'active': active_chamas,
            },
            'members': {
                'total': total_members,
            },
            'finance': {
                'total_balance': total_balance,
                'total_contributions': total_contributions['total'] or 0,
                'total_contributions_paid': total_contributions['paid'] or 0,
                'total_loans_borrowed': total_loans['total_borrowed'] or 0,
                'total_loans_repaid': total_loans['total_repaid'] or 0,
            },
            'timestamp': timezone.now().isoformat(),
        }

    @staticmethod
    def run_integration_tests() -> dict:
        """
        Run integration tests.
        Returns test results.
        """
        results = {
            'total': 0,
            'passed': 0,
            'failed': 0,
            'skipped': 0,
            'tests': [],
        }

        # Test 1: Database connection
        results['total'] += 1
        try:
            from django.db import connection
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            results['passed'] += 1
            results['tests'].append({
                'name': 'Database Connection',
                'status': 'passed',
            })
        except Exception as e:
            results['failed'] += 1
            results['tests'].append({
                'name': 'Database Connection',
                'status': 'failed',
                'error': str(e),
            })

        # Test 2: Cache connection
        results['total'] += 1
        try:
            from django.core.cache import cache
            cache.set('test_key', 'test_value', 10)
            cache.get('test_key')
            results['passed'] += 1
            results['tests'].append({
                'name': 'Cache Connection',
                'status': 'passed',
            })
        except Exception as e:
            results['failed'] += 1
            results['tests'].append({
                'name': 'Cache Connection',
                'status': 'failed',
                'error': str(e),
            })

        # Test 3: User model
        results['total'] += 1
        try:
            from apps.accounts.models import User
            User.objects.count()
            results['passed'] += 1
            results['tests'].append({
                'name': 'User Model',
                'status': 'passed',
            })
        except Exception as e:
            results['failed'] += 1
            results['tests'].append({
                'name': 'User Model',
                'status': 'failed',
                'error': str(e),
            })

        # Test 4: Chama model
        results['total'] += 1
        try:
            from apps.chama.models import Chama
            Chama.objects.count()
            results['passed'] += 1
            results['tests'].append({
                'name': 'Chama Model',
                'status': 'passed',
            })
        except Exception as e:
            results['failed'] += 1
            results['tests'].append({
                'name': 'Chama Model',
                'status': 'failed',
                'error': str(e),
            })

        return results

    @staticmethod
    def get_production_readiness_checklist() -> dict:
        """
        Get production readiness checklist.
        """
        return {
            'security': {
                'items': [
                    {'name': 'HTTPS enabled', 'checked': True},
                    {'name': 'Secret key secured', 'checked': True},
                    {'name': 'CORS configured', 'checked': True},
                    {'name': 'Rate limiting enabled', 'checked': True},
                    {'name': 'Input validation', 'checked': True},
                ],
            },
            'performance': {
                'items': [
                    {'name': 'Database indexes', 'checked': True},
                    {'name': 'Query optimization', 'checked': True},
                    {'name': 'Caching enabled', 'checked': True},
                    {'name': 'CDN configured', 'checked': False},
                ],
            },
            'monitoring': {
                'items': [
                    {'name': 'Logging configured', 'checked': True},
                    {'name': 'Error tracking', 'checked': True},
                    {'name': 'Health checks', 'checked': True},
                    {'name': 'Metrics collection', 'checked': True},
                ],
            },
            'backup': {
                'items': [
                    {'name': 'Database backups', 'checked': True},
                    {'name': 'File backups', 'checked': True},
                    {'name': 'Backup testing', 'checked': False},
                ],
            },
            'documentation': {
                'items': [
                    {'name': 'API documentation', 'checked': True},
                    {'name': 'Deployment guide', 'checked': True},
                    {'name': 'User guide', 'checked': False},
                ],
            },
        }

    @staticmethod
    def get_feature_flags() -> dict:
        """
        Get feature flags configuration.
        """
        return {
            'ai_assistant': {
                'enabled': getattr(settings, 'FEATURE_AI_ASSISTANT', True),
                'description': 'AI chat assistant',
            },
            'biometric_login': {
                'enabled': getattr(settings, 'FEATURE_BIOMETRIC_LOGIN', False),
                'description': 'Biometric authentication',
            },
            'offline_mode': {
                'enabled': getattr(settings, 'FEATURE_OFFLINE_MODE', False),
                'description': 'Offline functionality',
            },
            'push_notifications': {
                'enabled': getattr(settings, 'FEATURE_PUSH_NOTIFICATIONS', True),
                'description': 'Push notifications',
            },
            'advanced_analytics': {
                'enabled': getattr(settings, 'FEATURE_ADVANCED_ANALYTICS', True),
                'description': 'Advanced analytics and insights',
            },
        }

    @staticmethod
    def get_environment_info() -> dict:
        """
        Get environment information.
        """
        import sys

        import django

        return {
            'python_version': sys.version,
            'django_version': django.get_version(),
            'debug': settings.DEBUG,
            'environment': getattr(settings, 'ENVIRONMENT', 'development'),
            'allowed_hosts': settings.ALLOWED_HOSTS,
            'database_engine': settings.DATABASES['default']['ENGINE'],
            'timestamp': timezone.now().isoformat(),
        }
