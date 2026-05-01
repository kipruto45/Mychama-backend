"""
Notifications and Reminders Service

Manages in-app notifications, email/SMS notifications, and reminder scheduling.
"""

import logging

from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama

logger = logging.getLogger(__name__)


class NotificationService:
    """Service for managing notifications and reminders."""

    @staticmethod
    @transaction.atomic
    def create_notification(
        user: User,
        title: str,
        message: str,
        notification_type: str = 'general',
        priority: str = 'normal',
        chama: Chama = None,
        reference_id: str = None,
        reference_type: str = None,
    ) -> dict:
        """
        Create a new notification.
        Returns notification details.
        """
        from apps.notifications.models import Notification

        # Create notification
        notification = Notification.objects.create(
            user=user,
            title=title,
            message=message,
            notification_type=notification_type,
            priority=priority,
            chama=chama,
            reference_id=reference_id,
            reference_type=reference_type,
            status='unread',
        )

        logger.info(
            f"Notification created: {title} for {user.full_name}"
        )

        return {
            'id': str(notification.id),
            'title': title,
            'message': message,
            'notification_type': notification_type,
            'priority': priority,
            'status': 'unread',
        }

    @staticmethod
    @transaction.atomic
    def mark_as_read(
        notification_id: str,
        user: User,
    ) -> tuple[bool, str]:
        """
        Mark a notification as read.
        Returns (success, message).
        """
        from apps.notifications.models import Notification

        try:
            notification = Notification.objects.get(
                id=notification_id,
                user=user,
            )

            notification.status = 'read'
            notification.read_at = timezone.now()
            notification.save(update_fields=['status', 'read_at', 'updated_at'])

            return True, "Notification marked as read"

        except Notification.DoesNotExist:
            return False, "Notification not found"

    @staticmethod
    @transaction.atomic
    def mark_all_as_read(
        user: User,
        chama: Chama = None,
    ) -> int:
        """
        Mark all notifications as read.
        Returns number of notifications marked.
        """
        from apps.notifications.models import Notification

        queryset = Notification.objects.filter(
            user=user,
            status='unread',
        )

        if chama:
            queryset = queryset.filter(chama=chama)

        count = queryset.update(
            status='read',
            read_at=timezone.now(),
        )

        return count

    @staticmethod
    def get_notifications(
        user: User,
        chama: Chama = None,
        notification_type: str = None,
        status: str = None,
    ) -> list[dict]:
        """
        Get notifications for a user.
        """
        from apps.notifications.models import Notification

        queryset = Notification.objects.filter(user=user)

        if chama:
            queryset = queryset.filter(chama=chama)

        if notification_type:
            queryset = queryset.filter(notification_type=notification_type)

        if status:
            queryset = queryset.filter(status=status)

        notifications = queryset.order_by('-created_at')

        return [
            {
                'id': str(notification.id),
                'title': notification.title,
                'message': notification.message,
                'notification_type': notification.notification_type,
                'priority': notification.priority,
                'status': notification.status,
                'chama_name': notification.chama.name if notification.chama else None,
                'reference_id': notification.reference_id,
                'reference_type': notification.reference_type,
                'created_at': notification.created_at.isoformat(),
                'read_at': notification.read_at.isoformat() if notification.read_at else None,
            }
            for notification in notifications
        ]

    @staticmethod
    def get_unread_count(
        user: User,
        chama: Chama = None,
    ) -> int:
        """
        Get count of unread notifications.
        """
        from apps.notifications.models import Notification

        queryset = Notification.objects.filter(
            user=user,
            status='unread',
        )

        if chama:
            queryset = queryset.filter(chama=chama)

        return queryset.count()

    @staticmethod
    @transaction.atomic
    def send_email_notification(
        user: User,
        subject: str,
        message: str,
        notification_type: str = 'general',
    ) -> bool:
        """
        Send email notification.
        Returns success status.
        """
        from apps.notifications.models import EmailNotification

        try:
            # Create email notification record
            email_notification = EmailNotification.objects.create(
                user=user,
                subject=subject,
                message=message,
                notification_type=notification_type,
                status='pending',
            )

            # TODO: Integrate with email service (e.g., SendGrid, AWS SES)
            # For now, just mark as sent
            email_notification.status = 'sent'
            email_notification.sent_at = timezone.now()
            email_notification.save(update_fields=['status', 'sent_at', 'updated_at'])

            logger.info(
                f"Email notification sent: {subject} to {user.email}"
            )

            return True

        except Exception as e:
            logger.error(
                f"Failed to send email notification: {e}"
            )
            return False

    @staticmethod
    @transaction.atomic
    def send_sms_notification(
        user: User,
        message: str,
        notification_type: str = 'general',
    ) -> bool:
        """
        Send SMS notification.
        Returns success status.
        """
        from apps.notifications.models import SMSNotification

        try:
            # Create SMS notification record
            sms_notification = SMSNotification.objects.create(
                user=user,
                message=message,
                notification_type=notification_type,
                status='pending',
            )

            # TODO: Integrate with SMS service (e.g., Africa's Talking)
            # For now, just mark as sent
            sms_notification.status = 'sent'
            sms_notification.sent_at = timezone.now()
            sms_notification.save(update_fields=['status', 'sent_at', 'updated_at'])

            logger.info(
                f"SMS notification sent: {message[:50]}... to {user.phone}"
            )

            return True

        except Exception as e:
            logger.error(
                f"Failed to send SMS notification: {e}"
            )
            return False

    @staticmethod
    def send_contribution_reminder(
        user: User,
        chama: Chama,
        contribution_amount: float,
        due_date: timezone.datetime,
    ) -> bool:
        """
        Send contribution reminder notification.
        """
        title = "Contribution Reminder"
        message = f"Your contribution of {contribution_amount} for {chama.name} is due on {due_date.strftime('%Y-%m-%d')}."

        # Create in-app notification
        NotificationService.create_notification(
            user=user,
            title=title,
            message=message,
            notification_type='contribution_reminder',
            priority='high',
            chama=chama,
        )

        # Send email
        NotificationService.send_email_notification(
            user=user,
            subject=title,
            message=message,
            notification_type='contribution_reminder',
        )

        # Send SMS
        NotificationService.send_sms_notification(
            user=user,
            message=message,
            notification_type='contribution_reminder',
        )

        return True

    @staticmethod
    def send_meeting_reminder(
        user: User,
        meeting,
    ) -> bool:
        """
        Send meeting reminder notification.
        """
        title = "Meeting Reminder"
        message = f"Reminder: {meeting.title} is scheduled for {meeting.start_time.strftime('%Y-%m-%d %H:%M')}."

        # Create in-app notification
        NotificationService.create_notification(
            user=user,
            title=title,
            message=message,
            notification_type='meeting_reminder',
            priority='normal',
            chama=meeting.chama,
            reference_id=str(meeting.id),
            reference_type='meeting',
        )

        # Send email
        NotificationService.send_email_notification(
            user=user,
            subject=title,
            message=message,
            notification_type='meeting_reminder',
        )

        return True

    @staticmethod
    def send_loan_reminder(
        user: User,
        loan,
    ) -> bool:
        """
        Send loan repayment reminder notification.
        """
        title = "Loan Repayment Reminder"
        message = f"Your loan repayment of {loan.total_amount} is due."

        # Create in-app notification
        NotificationService.create_notification(
            user=user,
            title=title,
            message=message,
            notification_type='loan_reminder',
            priority='high',
            chama=loan.chama,
            reference_id=str(loan.id),
            reference_type='loan',
        )

        # Send email
        NotificationService.send_email_notification(
            user=user,
            subject=title,
            message=message,
            notification_type='loan_reminder',
        )

        # Send SMS
        NotificationService.send_sms_notification(
            user=user,
            message=message,
            notification_type='loan_reminder',
        )

        return True

    @staticmethod
    def get_notification_preferences(user: User) -> dict:
        """
        Get notification preferences for a user.
        """
        from apps.notifications.models import NotificationPreference

        try:
            preferences = NotificationPreference.objects.get(user=user)
            return {
                'email_enabled': preferences.email_enabled,
                'sms_enabled': preferences.sms_enabled,
                'in_app_enabled': preferences.in_app_enabled,
                'contribution_reminders': preferences.contribution_reminders,
                'meeting_reminders': preferences.meeting_reminders,
                'loan_reminders': preferences.loan_reminders,
                'announcement_notifications': preferences.announcement_notifications,
            }
        except NotificationPreference.DoesNotExist:
            # Return default preferences
            return {
                'email_enabled': True,
                'sms_enabled': True,
                'in_app_enabled': True,
                'contribution_reminders': True,
                'meeting_reminders': True,
                'loan_reminders': True,
                'announcement_notifications': True,
            }

    @staticmethod
    @transaction.atomic
    def update_notification_preferences(
        user: User,
        **kwargs,
    ) -> tuple[bool, str]:
        """
        Update notification preferences for a user.
        Returns (success, message).
        """
        from apps.notifications.models import NotificationPreference

        try:
            preferences, created = NotificationPreference.objects.get_or_create(
                user=user,
                defaults=kwargs,
            )

            if not created:
                for key, value in kwargs.items():
                    if hasattr(preferences, key):
                        setattr(preferences, key, value)
                preferences.save()

            return True, "Preferences updated"

        except Exception as e:
            logger.error(
                f"Failed to update notification preferences: {e}"
            )
            return False, "Failed to update preferences"

    @staticmethod
    def get_notification_summary(user: User) -> dict:
        """
        Get notification summary for a user.
        """
        from django.db.models import Count

        from apps.notifications.models import Notification

        summary = Notification.objects.filter(user=user).aggregate(
            total=Count('id'),
            unread=Count('id', filter=models.Q(status='unread')),
            read=Count('id', filter=models.Q(status='read')),
        )

        return {
            'total_notifications': summary['total'] or 0,
            'unread_notifications': summary['unread'] or 0,
            'read_notifications': summary['read'] or 0,
        }
