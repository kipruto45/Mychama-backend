"""
Announcements and Communication Service

Manages announcements, broadcasts, and communication history.
"""

import logging

from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama

logger = logging.getLogger(__name__)


class CommunicationService:
    """Service for managing announcements and communication."""

    @staticmethod
    @transaction.atomic
    def create_announcement(
        chama: Chama,
        title: str,
        content: str,
        announcement_type: str = 'general',
        priority: str = 'normal',
        is_pinned: bool = False,
        created_by: User = None,
    ) -> dict:
        """
        Create a new announcement.
        Returns announcement details.
        """
        from apps.messaging.models import Announcement

        # Create announcement
        announcement = Announcement.objects.create(
            chama=chama,
            title=title,
            content=content,
            announcement_type=announcement_type,
            priority=priority,
            is_pinned=is_pinned,
            created_by=created_by,
            status='published',
        )

        logger.info(
            f"Announcement created: {title} for {chama.name}"
        )

        return {
            'id': str(announcement.id),
            'title': title,
            'content': content,
            'announcement_type': announcement_type,
            'priority': priority,
            'is_pinned': is_pinned,
            'status': 'published',
        }

    @staticmethod
    @transaction.atomic
    def update_announcement(
        announcement_id: str,
        updater: User,
        **kwargs,
    ) -> tuple[bool, str]:
        """
        Update an announcement.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker
        from apps.messaging.models import Announcement

        try:
            announcement = Announcement.objects.get(id=announcement_id)

            # Check if updater has permission
            if not PermissionChecker.has_permission(
                updater,
                Permission.CAN_SEND_ANNOUNCEMENTS,
                str(announcement.chama.id),
            ):
                return False, "Permission denied"

            # Update fields
            for key, value in kwargs.items():
                if hasattr(announcement, key):
                    setattr(announcement, key, value)

            announcement.save()

            logger.info(
                f"Announcement updated: {announcement_id} by {updater.full_name}"
            )

            return True, "Announcement updated"

        except Announcement.DoesNotExist:
            return False, "Announcement not found"

    @staticmethod
    @transaction.atomic
    def delete_announcement(
        announcement_id: str,
        deleter: User,
    ) -> tuple[bool, str]:
        """
        Delete an announcement.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker
        from apps.messaging.models import Announcement

        try:
            announcement = Announcement.objects.get(id=announcement_id)

            # Check if deleter has permission
            if not PermissionChecker.has_permission(
                deleter,
                Permission.CAN_SEND_ANNOUNCEMENTS,
                str(announcement.chama.id),
            ):
                return False, "Permission denied"

            # Soft delete
            announcement.status = 'deleted'
            announcement.deleted_by = deleter
            announcement.deleted_at = timezone.now()
            announcement.save(update_fields=[
                'status',
                'deleted_by',
                'deleted_at',
                'updated_at',
            ])

            logger.info(
                f"Announcement deleted: {announcement_id} by {deleter.full_name}"
            )

            return True, "Announcement deleted"

        except Announcement.DoesNotExist:
            return False, "Announcement not found"

    @staticmethod
    def get_announcements(
        chama: Chama = None,
        user: User = None,
        announcement_type: str = None,
        priority: str = None,
        is_pinned: bool = None,
    ) -> list[dict]:
        """
        Get announcements with filtering.
        """
        from apps.messaging.models import Announcement

        queryset = Announcement.objects.filter(status='published')

        if chama:
            queryset = queryset.filter(chama=chama)

        if user:
            # Get announcements for user's chamas
            from apps.chama.models import Membership
            user_chamas = Membership.objects.filter(
                user=user,
                status='active',
            ).values_list('chama_id', flat=True)
            queryset = queryset.filter(chama_id__in=user_chamas)

        if announcement_type:
            queryset = queryset.filter(announcement_type=announcement_type)

        if priority:
            queryset = queryset.filter(priority=priority)

        if is_pinned is not None:
            queryset = queryset.filter(is_pinned=is_pinned)

        # Order by pinned first, then by created date
        announcements = queryset.order_by('-is_pinned', '-created_at')

        return [
            {
                'id': str(announcement.id),
                'title': announcement.title,
                'content': announcement.content,
                'announcement_type': announcement.announcement_type,
                'priority': announcement.priority,
                'is_pinned': announcement.is_pinned,
                'chama_name': announcement.chama.name,
                'created_by_name': announcement.created_by.full_name if announcement.created_by else None,
                'created_at': announcement.created_at.isoformat(),
                'updated_at': announcement.updated_at.isoformat(),
            }
            for announcement in announcements
        ]

    @staticmethod
    def get_announcement_detail(announcement_id: str) -> dict | None:
        """
        Get detailed announcement information.
        """
        from apps.messaging.models import Announcement

        try:
            announcement = Announcement.objects.select_related(
                'chama', 'created_by'
            ).get(id=announcement_id)

            return {
                'id': str(announcement.id),
                'title': announcement.title,
                'content': announcement.content,
                'announcement_type': announcement.announcement_type,
                'priority': announcement.priority,
                'is_pinned': announcement.is_pinned,
                'status': announcement.status,
                'chama_id': str(announcement.chama.id),
                'chama_name': announcement.chama.name,
                'created_by_id': str(announcement.created_by.id) if announcement.created_by else None,
                'created_by_name': announcement.created_by.full_name if announcement.created_by else None,
                'created_at': announcement.created_at.isoformat(),
                'updated_at': announcement.updated_at.isoformat(),
            }

        except Announcement.DoesNotExist:
            return None

    @staticmethod
    @transaction.atomic
    def send_broadcast(
        chama: Chama,
        title: str,
        content: str,
        sender: User,
        recipient_type: str = 'all',  # 'all', 'role', 'specific'
        recipient_ids: list[str] = None,
    ) -> dict:
        """
        Send a broadcast message to members.
        Returns broadcast details.
        """
        from apps.messaging.models import Broadcast, BroadcastRecipient

        # Create broadcast
        broadcast = Broadcast.objects.create(
            chama=chama,
            title=title,
            content=content,
            sender=sender,
            recipient_type=recipient_type,
            status='sent',
        )

        # Get recipients
        from apps.chama.models import Membership
        if recipient_type == 'all':
            recipients = Membership.objects.filter(
                chama=chama,
                status='active',
            ).select_related('user')
        elif recipient_type == 'role' and recipient_ids:
            recipients = Membership.objects.filter(
                chama=chama,
                status='active',
                role__in=recipient_ids,
            ).select_related('user')
        elif recipient_type == 'specific' and recipient_ids:
            recipients = Membership.objects.filter(
                chama=chama,
                status='active',
                user_id__in=recipient_ids,
            ).select_related('user')
        else:
            recipients = []

        # Create recipient records
        recipient_count = 0
        for membership in recipients:
            BroadcastRecipient.objects.create(
                broadcast=broadcast,
                user=membership.user,
                status='pending',
            )
            recipient_count += 1

        logger.info(
            f"Broadcast sent: {title} to {recipient_count} recipients in {chama.name}"
        )

        return {
            'id': str(broadcast.id),
            'title': title,
            'content': content,
            'recipient_count': recipient_count,
            'status': 'sent',
        }

    @staticmethod
    def get_broadcasts(
        chama: Chama = None,
        user: User = None,
    ) -> list[dict]:
        """
        Get broadcasts with filtering.
        """
        from apps.messaging.models import Broadcast, BroadcastRecipient

        queryset = Broadcast.objects.all()

        if chama:
            queryset = queryset.filter(chama=chama)

        if user:
            # Get broadcasts where user is recipient
            user_broadcasts = BroadcastRecipient.objects.filter(
                user=user,
            ).values_list('broadcast_id', flat=True)
            queryset = queryset.filter(id__in=user_broadcasts)

        broadcasts = queryset.order_by('-created_at')

        return [
            {
                'id': str(broadcast.id),
                'title': broadcast.title,
                'content': broadcast.content,
                'sender_name': broadcast.sender.full_name if broadcast.sender else None,
                'chama_name': broadcast.chama.name,
                'recipient_count': broadcast.recipients.count(),
                'status': broadcast.status,
                'created_at': broadcast.created_at.isoformat(),
            }
            for broadcast in broadcasts
        ]

    @staticmethod
    def get_communication_history(
        chama: Chama = None,
        user: User = None,
        communication_type: str = None,
    ) -> list[dict]:
        """
        Get communication history.
        """
        from apps.messaging.models import Announcement, Broadcast, BroadcastRecipient

        history = []

        # Get announcements
        announcements = Announcement.objects.filter(status='published')
        if chama:
            announcements = announcements.filter(chama=chama)
        if user:
            from apps.chama.models import Membership
            user_chamas = Membership.objects.filter(
                user=user,
                status='active',
            ).values_list('chama_id', flat=True)
            announcements = announcements.filter(chama_id__in=user_chamas)

        for announcement in announcements:
            history.append({
                'type': 'announcement',
                'id': str(announcement.id),
                'title': announcement.title,
                'content': announcement.content[:100] + '...' if len(announcement.content) > 100 else announcement.content,
                'chama_name': announcement.chama.name,
                'created_by_name': announcement.created_by.full_name if announcement.created_by else None,
                'created_at': announcement.created_at.isoformat(),
            })

        # Get broadcasts
        broadcasts = Broadcast.objects.all()
        if chama:
            broadcasts = broadcasts.filter(chama=chama)
        if user:
            user_broadcasts = BroadcastRecipient.objects.filter(
                user=user,
            ).values_list('broadcast_id', flat=True)
            broadcasts = broadcasts.filter(id__in=user_broadcasts)

        for broadcast in broadcasts:
            history.append({
                'type': 'broadcast',
                'id': str(broadcast.id),
                'title': broadcast.title,
                'content': broadcast.content[:100] + '...' if len(broadcast.content) > 100 else broadcast.content,
                'chama_name': broadcast.chama.name,
                'sender_name': broadcast.sender.full_name if broadcast.sender else None,
                'created_at': broadcast.created_at.isoformat(),
            })

        # Sort by created_at
        history.sort(key=lambda x: x['created_at'], reverse=True)

        return history

    @staticmethod
    def get_communication_summary(chama: Chama) -> dict:
        """
        Get communication summary for a chama.
        """
        from django.db.models import Count

        from apps.messaging.models import Announcement, Broadcast

        # Get announcement summary
        announcements = Announcement.objects.filter(chama=chama).aggregate(
            total=Count('id'),
            published=Count('id', filter=models.Q(status='published')),
            pinned=Count('id', filter=models.Q(is_pinned=True)),
        )

        # Get broadcast summary
        broadcasts = Broadcast.objects.filter(chama=chama).aggregate(
            total=Count('id'),
            sent=Count('id', filter=models.Q(status='sent')),
        )

        return {
            'total_announcements': announcements['total'] or 0,
            'published_announcements': announcements['published'] or 0,
            'pinned_announcements': announcements['pinned'] or 0,
            'total_broadcasts': broadcasts['total'] or 0,
            'sent_broadcasts': broadcasts['sent'] or 0,
        }
