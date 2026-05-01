"""
Support and Disputes Service

Manages support tickets, dispute resolution, and issue tracking.
"""

import logging

from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama

logger = logging.getLogger(__name__)


class SupportService:
    """Service for managing support and disputes."""

    @staticmethod
    @transaction.atomic
    def create_ticket(
        user: User,
        chama: Chama,
        ticket_type: str,
        subject: str,
        description: str,
        priority: str = 'normal',
        related_object_type: str = None,
        related_object_id: str = None,
    ) -> dict:
        """
        Create a support ticket.
        Returns ticket details.
        """
        from apps.support.models import SupportTicket

        # Create ticket
        ticket = SupportTicket.objects.create(
            user=user,
            chama=chama,
            ticket_type=ticket_type,
            subject=subject,
            description=description,
            priority=priority,
            related_object_type=related_object_type,
            related_object_id=related_object_id,
            status='open',
        )

        logger.info(
            f"Support ticket created: {subject} by {user.full_name}"
        )

        return {
            'id': str(ticket.id),
            'ticket_type': ticket_type,
            'subject': subject,
            'priority': priority,
            'status': 'open',
            'created_at': ticket.created_at.isoformat(),
        }

    @staticmethod
    @transaction.atomic
    def update_ticket(
        ticket_id: str,
        updater: User,
        **kwargs,
    ) -> tuple[bool, str]:
        """
        Update a support ticket.
        Returns (success, message).
        """
        from apps.support.models import SupportTicket

        try:
            ticket = SupportTicket.objects.get(id=ticket_id)

            # Check permission
            if ticket.user != updater:
                return False, "Permission denied"

            # Update fields
            for key, value in kwargs.items():
                if hasattr(ticket, key):
                    setattr(ticket, key, value)

            ticket.save()

            logger.info(
                f"Support ticket updated: {ticket_id} by {updater.full_name}"
            )

            return True, "Ticket updated"

        except SupportTicket.DoesNotExist:
            return False, "Ticket not found"

    @staticmethod
    @transaction.atomic
    def add_comment(
        ticket_id: str,
        user: User,
        comment: str,
    ) -> tuple[bool, str]:
        """
        Add a comment to a support ticket.
        Returns (success, message).
        """
        from apps.support.models import SupportTicket, TicketComment

        try:
            ticket = SupportTicket.objects.get(id=ticket_id)

            # Create comment
            TicketComment.objects.create(
                ticket=ticket,
                user=user,
                comment=comment,
            )

            logger.info(
                f"Comment added to ticket: {ticket_id} by {user.full_name}"
            )

            return True, "Comment added"

        except SupportTicket.DoesNotExist:
            return False, "Ticket not found"

    @staticmethod
    @transaction.atomic
    def resolve_ticket(
        ticket_id: str,
        resolver: User,
        resolution: str,
    ) -> tuple[bool, str]:
        """
        Resolve a support ticket.
        Returns (success, message).
        """
        from apps.support.models import SupportTicket

        try:
            ticket = SupportTicket.objects.get(id=ticket_id)

            ticket.status = 'resolved'
            ticket.resolution = resolution
            ticket.resolved_by = resolver
            ticket.resolved_at = timezone.now()
            ticket.save(update_fields=[
                'status',
                'resolution',
                'resolved_by',
                'resolved_at',
                'updated_at',
            ])

            logger.info(
                f"Support ticket resolved: {ticket_id} by {resolver.full_name}"
            )

            return True, "Ticket resolved"

        except SupportTicket.DoesNotExist:
            return False, "Ticket not found"

    @staticmethod
    def get_tickets(
        user: User = None,
        chama: Chama = None,
        status: str = None,
        ticket_type: str = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """
        Get support tickets with filtering and pagination.
        """
        from apps.support.models import SupportTicket

        queryset = SupportTicket.objects.all()

        if user:
            queryset = queryset.filter(user=user)

        if chama:
            queryset = queryset.filter(chama=chama)

        if status:
            queryset = queryset.filter(status=status)

        if ticket_type:
            queryset = queryset.filter(ticket_type=ticket_type)

        queryset = queryset.order_by('-created_at')

        # Paginate
        total = queryset.count()
        start = (page - 1) * page_size
        end = start + page_size
        tickets = queryset[start:end]

        return {
            'results': [
                {
                    'id': str(ticket.id),
                    'ticket_type': ticket.ticket_type,
                    'subject': ticket.subject,
                    'priority': ticket.priority,
                    'status': ticket.status,
                    'user_name': ticket.user.full_name,
                    'chama_name': ticket.chama.name if ticket.chama else None,
                    'created_at': ticket.created_at.isoformat(),
                    'resolved_at': ticket.resolved_at.isoformat() if ticket.resolved_at else None,
                }
                for ticket in tickets
            ],
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total': total,
                'total_pages': (total + page_size - 1) // page_size,
            },
        }

    @staticmethod
    def get_ticket_detail(ticket_id: str) -> dict | None:
        """
        Get detailed ticket information.
        """
        from apps.support.models import SupportTicket, TicketComment

        try:
            ticket = SupportTicket.objects.select_related(
                'user', 'chama', 'resolved_by'
            ).get(id=ticket_id)

            # Get comments
            comments = TicketComment.objects.filter(
                ticket=ticket,
            ).select_related('user').order_by('created_at')

            return {
                'id': str(ticket.id),
                'ticket_type': ticket.ticket_type,
                'subject': ticket.subject,
                'description': ticket.description,
                'priority': ticket.priority,
                'status': ticket.status,
                'resolution': ticket.resolution,
                'user_id': str(ticket.user.id),
                'user_name': ticket.user.full_name,
                'chama_id': str(ticket.chama.id) if ticket.chama else None,
                'chama_name': ticket.chama.name if ticket.chama else None,
                'resolved_by_id': str(ticket.resolved_by.id) if ticket.resolved_by else None,
                'resolved_by_name': ticket.resolved_by.full_name if ticket.resolved_by else None,
                'related_object_type': ticket.related_object_type,
                'related_object_id': ticket.related_object_id,
                'comments': [
                    {
                        'id': str(comment.id),
                        'user_name': comment.user.full_name,
                        'comment': comment.comment,
                        'created_at': comment.created_at.isoformat(),
                    }
                    for comment in comments
                ],
                'created_at': ticket.created_at.isoformat(),
                'resolved_at': ticket.resolved_at.isoformat() if ticket.resolved_at else None,
            }

        except SupportTicket.DoesNotExist:
            return None

    @staticmethod
    def get_ticket_types() -> list[dict]:
        """
        Get available ticket types.
        """
        return [
            {
                'id': 'payment_dispute',
                'name': 'Payment Dispute',
                'description': 'Disputes related to payments',
            },
            {
                'id': 'contribution_issue',
                'name': 'Contribution Issue',
                'description': 'Issues with contributions',
            },
            {
                'id': 'loan_issue',
                'name': 'Loan Issue',
                'description': 'Issues with loans',
            },
            {
                'id': 'account_issue',
                'name': 'Account Issue',
                'description': 'Account-related issues',
            },
            {
                'id': 'technical_issue',
                'name': 'Technical Issue',
                'description': 'Technical problems',
            },
            {
                'id': 'general_inquiry',
                'name': 'General Inquiry',
                'description': 'General questions',
            },
        ]

    @staticmethod
    def get_support_summary(chama: Chama = None) -> dict:
        """
        Get support summary.
        """
        from django.db.models import Count

        from apps.support.models import SupportTicket

        queryset = SupportTicket.objects.all()

        if chama:
            queryset = queryset.filter(chama=chama)

        summary = queryset.aggregate(
            total=Count('id'),
            open=Count('id', filter=models.Q(status='open')),
            in_progress=Count('id', filter=models.Q(status='in_progress')),
            resolved=Count('id', filter=models.Q(status='resolved')),
            closed=Count('id', filter=models.Q(status='closed')),
        )

        return {
            'total_tickets': summary['total'] or 0,
            'open_tickets': summary['open'] or 0,
            'in_progress_tickets': summary['in_progress'] or 0,
            'resolved_tickets': summary['resolved'] or 0,
            'closed_tickets': summary['closed'] or 0,
        }
