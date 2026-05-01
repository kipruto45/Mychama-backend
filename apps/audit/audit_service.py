"""
Audit Logs and Activity History Service

Manages audit logging, activity tracking, and history retrieval.
"""

import logging

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama

logger = logging.getLogger(__name__)


class AuditService:
    """Service for managing audit logs and activity history."""

    @staticmethod
    @transaction.atomic
    def log_activity(
        user: User,
        action: str,
        entity_type: str,
        entity_id: str = None,
        chama: Chama = None,
        metadata: dict = None,
        ip_address: str = None,
        user_agent: str = None,
    ) -> dict:
        """
        Log an activity.
        Returns audit log details.
        """
        from apps.audit.models import AuditLog

        # Create audit log
        audit_log = AuditLog.objects.create(
            user=user,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            chama=chama,
            metadata=metadata or {},
            ip_address=ip_address,
            user_agent=user_agent,
        )

        logger.info(
            f"Audit log: {action} by {user.full_name} on {entity_type}:{entity_id}"
        )

        return {
            'id': str(audit_log.id),
            'action': action,
            'entity_type': entity_type,
            'entity_id': entity_id,
            'created_at': audit_log.created_at.isoformat(),
        }

    @staticmethod
    def get_activity_logs(
        user: User = None,
        chama: Chama = None,
        action: str = None,
        entity_type: str = None,
        date_from: timezone.datetime = None,
        date_to: timezone.datetime = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        """
        Get activity logs with filtering and pagination.
        Returns paginated results.
        """
        from apps.audit.models import AuditLog

        queryset = AuditLog.objects.all()

        # Apply filters
        if user:
            queryset = queryset.filter(user=user)

        if chama:
            queryset = queryset.filter(chama=chama)

        if action:
            queryset = queryset.filter(action=action)

        if entity_type:
            queryset = queryset.filter(entity_type=entity_type)

        if date_from:
            queryset = queryset.filter(created_at__gte=date_from)

        if date_to:
            queryset = queryset.filter(created_at__lte=date_to)

        # Order by
        queryset = queryset.order_by('-created_at')

        # Paginate
        total = queryset.count()
        start = (page - 1) * page_size
        end = start + page_size
        logs = queryset[start:end]

        return {
            'results': [
                {
                    'id': str(log.id),
                    'user_id': str(log.user.id) if log.user else None,
                    'user_name': log.user.full_name if log.user else None,
                    'action': log.action,
                    'entity_type': log.entity_type,
                    'entity_id': log.entity_id,
                    'chama_id': str(log.chama.id) if log.chama else None,
                    'chama_name': log.chama.name if log.chama else None,
                    'metadata': log.metadata,
                    'ip_address': log.ip_address,
                    'created_at': log.created_at.isoformat(),
                }
                for log in logs
            ],
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total': total,
                'total_pages': (total + page_size - 1) // page_size,
            },
        }

    @staticmethod
    def get_user_activity(
        user: User,
        limit: int = 50,
    ) -> list[dict]:
        """
        Get recent activity for a user.
        """
        from apps.audit.models import AuditLog

        logs = AuditLog.objects.filter(
            user=user,
        ).order_by('-created_at')[:limit]

        return [
            {
                'id': str(log.id),
                'action': log.action,
                'entity_type': log.entity_type,
                'entity_id': log.entity_id,
                'chama_name': log.chama.name if log.chama else None,
                'metadata': log.metadata,
                'created_at': log.created_at.isoformat(),
            }
            for log in logs
        ]

    @staticmethod
    def get_chama_activity(
        chama: Chama,
        limit: int = 50,
    ) -> list[dict]:
        """
        Get recent activity for a chama.
        """
        from apps.audit.models import AuditLog

        logs = AuditLog.objects.filter(
            chama=chama,
        ).select_related('user').order_by('-created_at')[:limit]

        return [
            {
                'id': str(log.id),
                'user_id': str(log.user.id) if log.user else None,
                'user_name': log.user.full_name if log.user else None,
                'action': log.action,
                'entity_type': log.entity_type,
                'entity_id': log.entity_id,
                'metadata': log.metadata,
                'created_at': log.created_at.isoformat(),
            }
            for log in logs
        ]

    @staticmethod
    def get_entity_history(
        entity_type: str,
        entity_id: str,
        limit: int = 50,
    ) -> list[dict]:
        """
        Get history for a specific entity.
        """
        from apps.audit.models import AuditLog

        logs = AuditLog.objects.filter(
            entity_type=entity_type,
            entity_id=entity_id,
        ).select_related('user').order_by('-created_at')[:limit]

        return [
            {
                'id': str(log.id),
                'user_id': str(log.user.id) if log.user else None,
                'user_name': log.user.full_name if log.user else None,
                'action': log.action,
                'metadata': log.metadata,
                'created_at': log.created_at.isoformat(),
            }
            for log in logs
        ]

    @staticmethod
    def get_audit_summary(chama: Chama = None) -> dict:
        """
        Get audit summary for a chama or globally.
        """
        from django.db.models import Count

        from apps.audit.models import AuditLog

        queryset = AuditLog.objects.all()

        if chama:
            queryset = queryset.filter(chama=chama)

        # Get summary by action
        by_action = queryset.values('action').annotate(
            count=Count('id'),
        ).order_by('-count')[:10]

        # Get summary by entity type
        by_entity = queryset.values('entity_type').annotate(
            count=Count('id'),
        ).order_by('-count')[:10]

        # Get summary by user
        by_user = queryset.filter(
            user__isnull=False,
        ).values(
            'user__id',
            'user__full_name',
        ).annotate(
            count=Count('id'),
        ).order_by('-count')[:10]

        return {
            'total_logs': queryset.count(),
            'by_action': [
                {
                    'action': item['action'],
                    'count': item['count'],
                }
                for item in by_action
            ],
            'by_entity': [
                {
                    'entity_type': item['entity_type'],
                    'count': item['count'],
                }
                for item in by_entity
            ],
            'by_user': [
                {
                    'user_id': str(item['user__id']),
                    'user_name': item['user__full_name'],
                    'count': item['count'],
                }
                for item in by_user
            ],
        }

    @staticmethod
    def get_suspicious_activity(chama: Chama = None) -> list[dict]:
        """
        Get suspicious activity patterns.
        """
        from datetime import timedelta

        from django.db.models import Count

        from apps.audit.models import AuditLog

        # Get recent failed login attempts
        one_hour_ago = timezone.now() - timedelta(hours=1)
        
        failed_logins = AuditLog.objects.filter(
            action='login_failed',
            created_at__gte=one_hour_ago,
        )

        if chama:
            failed_logins = failed_logins.filter(chama=chama)

        # Group by IP address
        suspicious_ips = failed_logins.values('ip_address').annotate(
            count=Count('id'),
        ).filter(count__gte=5).order_by('-count')

        # Get recent sensitive actions
        sensitive_actions = AuditLog.objects.filter(
            action__in=[
                'password_change',
                'password_reset',
                'role_change',
                'member_remove',
                'loan_approve',
                'withdrawal_approve',
            ],
            created_at__gte=one_hour_ago,
        )

        if chama:
            sensitive_actions = sensitive_actions.filter(chama=chama)

        return {
            'suspicious_ips': [
                {
                    'ip_address': item['ip_address'],
                    'failed_attempts': item['count'],
                }
                for item in suspicious_ips
            ],
            'recent_sensitive_actions': [
                {
                    'id': str(log.id),
                    'user_id': str(log.user.id) if log.user else None,
                    'user_name': log.user.full_name if log.user else None,
                    'action': log.action,
                    'entity_type': log.entity_type,
                    'entity_id': log.entity_id,
                    'ip_address': log.ip_address,
                    'created_at': log.created_at.isoformat(),
                }
                for log in sensitive_actions[:20]
            ],
        }

    @staticmethod
    def export_audit_logs(
        chama: Chama = None,
        date_from: timezone.datetime = None,
        date_to: timezone.datetime = None,
        format: str = 'csv',
    ) -> str:
        """
        Export audit logs to CSV or JSON.
        Returns file content as string.
        """
        from apps.audit.models import AuditLog

        queryset = AuditLog.objects.all()

        if chama:
            queryset = queryset.filter(chama=chama)

        if date_from:
            queryset = queryset.filter(created_at__gte=date_from)

        if date_to:
            queryset = queryset.filter(created_at__lte=date_to)

        queryset = queryset.order_by('-created_at')

        # Prepare data
        data = [
            {
                'timestamp': log.created_at.isoformat(),
                'user_id': str(log.user.id) if log.user else '',
                'user_name': log.user.full_name if log.user else '',
                'action': log.action,
                'entity_type': log.entity_type,
                'entity_id': log.entity_id or '',
                'chama_id': str(log.chama.id) if log.chama else '',
                'chama_name': log.chama.name if log.chama else '',
                'ip_address': log.ip_address or '',
                'metadata': str(log.metadata),
            }
            for log in queryset
        ]

        if format == 'csv':
            import csv
            import io

            output = io.StringIO()
            if data:
                writer = csv.DictWriter(output, fieldnames=data[0].keys())
                writer.writeheader()
                writer.writerows(data)
            return output.getvalue()

        elif format == 'json':
            import json
            return json.dumps(data, indent=2, default=str)

        return ""
