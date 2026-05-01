"""
Meetings Service

Manages meeting CRUD, reminders, agenda, and minutes.
"""

import logging

from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama

logger = logging.getLogger(__name__)


class MeetingsService:
    """Service for managing meetings."""

    @staticmethod
    @transaction.atomic
    def create_meeting(
        chama: Chama,
        title: str,
        description: str,
        start_time: timezone.datetime,
        end_time: timezone.datetime,
        location: str = '',
        meeting_type: str = 'regular',
        agenda: list[str] = None,
        created_by: User = None,
    ) -> dict:
        """
        Create a new meeting.
        Returns meeting details.
        """
        from apps.meetings.models import Meeting

        # Validate times
        if start_time >= end_time:
            raise ValueError("Start time must be before end time")

        # Create meeting
        meeting = Meeting.objects.create(
            chama=chama,
            title=title,
            description=description,
            start_time=start_time,
            end_time=end_time,
            location=location,
            meeting_type=meeting_type,
            agenda=agenda or [],
            created_by=created_by,
            status='scheduled',
        )

        logger.info(
            f"Meeting created: {title} for {chama.name}"
        )

        return {
            'id': str(meeting.id),
            'title': title,
            'description': description,
            'start_time': start_time.isoformat(),
            'end_time': end_time.isoformat(),
            'location': location,
            'meeting_type': meeting_type,
            'agenda': agenda or [],
            'status': 'scheduled',
        }

    @staticmethod
    @transaction.atomic
    def update_meeting(
        meeting_id: str,
        updater: User,
        **kwargs,
    ) -> tuple[bool, str]:
        """
        Update a meeting.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker
        from apps.meetings.models import Meeting

        try:
            meeting = Meeting.objects.get(id=meeting_id)

            # Check if updater has permission
            if not PermissionChecker.has_permission(
                updater,
                Permission.CAN_EDIT_MEETINGS,
                str(meeting.chama.id),
            ):
                return False, "Permission denied"

            # Update fields
            for key, value in kwargs.items():
                if hasattr(meeting, key):
                    setattr(meeting, key, value)

            meeting.save()

            logger.info(
                f"Meeting updated: {meeting_id} by {updater.full_name}"
            )

            return True, "Meeting updated"

        except Meeting.DoesNotExist:
            return False, "Meeting not found"

    @staticmethod
    @transaction.atomic
    def cancel_meeting(
        meeting_id: str,
        canceller: User,
        reason: str = '',
    ) -> tuple[bool, str]:
        """
        Cancel a meeting.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker
        from apps.meetings.models import Meeting

        try:
            meeting = Meeting.objects.get(id=meeting_id)

            # Check if canceller has permission
            if not PermissionChecker.has_permission(
                canceller,
                Permission.CAN_DELETE_MEETINGS,
                str(meeting.chama.id),
            ):
                return False, "Permission denied"

            # Update meeting
            meeting.status = 'cancelled'
            meeting.cancellation_reason = reason
            meeting.cancelled_by = canceller
            meeting.cancelled_at = timezone.now()
            meeting.save(update_fields=[
                'status',
                'cancellation_reason',
                'cancelled_by',
                'cancelled_at',
                'updated_at',
            ])

            logger.info(
                f"Meeting cancelled: {meeting_id} by {canceller.full_name}"
            )

            return True, "Meeting cancelled"

        except Meeting.DoesNotExist:
            return False, "Meeting not found"

    @staticmethod
    def get_meetings(
        chama: Chama = None,
        user: User = None,
        status: str = None,
        meeting_type: str = None,
        date_from: timezone.datetime = None,
        date_to: timezone.datetime = None,
    ) -> list[dict]:
        """
        Get meetings with filtering.
        """
        from apps.meetings.models import Meeting

        queryset = Meeting.objects.all()

        if chama:
            queryset = queryset.filter(chama=chama)

        if user:
            # Get meetings for user's chamas
            from apps.chama.models import Membership
            user_chamas = Membership.objects.filter(
                user=user,
                status='active',
            ).values_list('chama_id', flat=True)
            queryset = queryset.filter(chama_id__in=user_chamas)

        if status:
            queryset = queryset.filter(status=status)

        if meeting_type:
            queryset = queryset.filter(meeting_type=meeting_type)

        if date_from:
            queryset = queryset.filter(start_time__gte=date_from)

        if date_to:
            queryset = queryset.filter(start_time__lte=date_to)

        meetings = queryset.order_by('start_time')

        return [
            {
                'id': str(meeting.id),
                'title': meeting.title,
                'description': meeting.description,
                'start_time': meeting.start_time.isoformat(),
                'end_time': meeting.end_time.isoformat(),
                'location': meeting.location,
                'meeting_type': meeting.meeting_type,
                'status': meeting.status,
                'chama_name': meeting.chama.name,
                'created_by_name': meeting.created_by.full_name if meeting.created_by else None,
                'agenda': meeting.agenda,
                'attendee_count': meeting.attendees.count(),
            }
            for meeting in meetings
        ]

    @staticmethod
    def get_meeting_detail(meeting_id: str) -> dict | None:
        """
        Get detailed meeting information.
        """
        from apps.meetings.models import Meeting

        try:
            meeting = Meeting.objects.select_related(
                'chama', 'created_by'
            ).prefetch_related('attendees').get(id=meeting_id)

            return {
                'id': str(meeting.id),
                'title': meeting.title,
                'description': meeting.description,
                'start_time': meeting.start_time.isoformat(),
                'end_time': meeting.end_time.isoformat(),
                'location': meeting.location,
                'meeting_type': meeting.meeting_type,
                'status': meeting.status,
                'chama_id': str(meeting.chama.id),
                'chama_name': meeting.chama.name,
                'created_by_id': str(meeting.created_by.id) if meeting.created_by else None,
                'created_by_name': meeting.created_by.full_name if meeting.created_by else None,
                'agenda': meeting.agenda,
                'minutes': meeting.minutes,
                'resolutions': meeting.resolutions,
                'attendees': [
                    {
                        'id': str(attendee.id),
                        'name': attendee.full_name,
                        'status': attendee.attendance_status,
                    }
                    for attendee in meeting.attendees.all()
                ],
                'created_at': meeting.created_at.isoformat(),
                'updated_at': meeting.updated_at.isoformat(),
            }

        except Meeting.DoesNotExist:
            return None

    @staticmethod
    @transaction.atomic
    def record_minutes(
        meeting_id: str,
        minutes: str,
        resolutions: list[str] = None,
        recorder: User = None,
    ) -> tuple[bool, str]:
        """
        Record minutes for a meeting.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker
        from apps.meetings.models import Meeting

        try:
            meeting = Meeting.objects.get(id=meeting_id)

            # Check if recorder has permission
            if not PermissionChecker.has_permission(
                recorder,
                Permission.CAN_RECORD_MINUTES,
                str(meeting.chama.id),
            ):
                return False, "Permission denied"

            # Update meeting
            meeting.minutes = minutes
            meeting.resolutions = resolutions or []
            meeting.minutes_recorded_by = recorder
            meeting.minutes_recorded_at = timezone.now()
            meeting.save(update_fields=[
                'minutes',
                'resolutions',
                'minutes_recorded_by',
                'minutes_recorded_at',
                'updated_at',
            ])

            logger.info(
                f"Minutes recorded for meeting: {meeting_id} by {recorder.full_name}"
            )

            return True, "Minutes recorded"

        except Meeting.DoesNotExist:
            return False, "Meeting not found"

    @staticmethod
    def get_upcoming_meetings(chama: Chama = None, user: User = None) -> list[dict]:
        """
        Get upcoming meetings.
        """
        from apps.meetings.models import Meeting

        now = timezone.now()
        queryset = Meeting.objects.filter(
            start_time__gt=now,
            status='scheduled',
        )

        if chama:
            queryset = queryset.filter(chama=chama)

        if user:
            from apps.chama.models import Membership
            user_chamas = Membership.objects.filter(
                user=user,
                status='active',
            ).values_list('chama_id', flat=True)
            queryset = queryset.filter(chama_id__in=user_chamas)

        meetings = queryset.order_by('start_time')

        return [
            {
                'id': str(meeting.id),
                'title': meeting.title,
                'start_time': meeting.start_time.isoformat(),
                'end_time': meeting.end_time.isoformat(),
                'location': meeting.location,
                'chama_name': meeting.chama.name,
                'attendee_count': meeting.attendees.count(),
            }
            for meeting in meetings
        ]

    @staticmethod
    def get_meeting_summary(chama: Chama) -> dict:
        """
        Get meeting summary for a chama.
        """
        from django.db.models import Count

        from apps.meetings.models import Meeting

        now = timezone.now()

        summary = Meeting.objects.filter(chama=chama).aggregate(
            total=Count('id'),
            upcoming=Count('id', filter=models.Q(start_time__gt=now, status='scheduled')),
            completed=Count('id', filter=models.Q(status='completed')),
            cancelled=Count('id', filter=models.Q(status='cancelled')),
        )

        return {
            'total_meetings': summary['total'] or 0,
            'upcoming_meetings': summary['upcoming'] or 0,
            'completed_meetings': summary['completed'] or 0,
            'cancelled_meetings': summary['cancelled'] or 0,
        }

    @staticmethod
    def send_meeting_reminders(meeting_id: str) -> int:
        """
        Send reminders for a meeting.
        Returns number of reminders sent.
        """
        from apps.meetings.models import Meeting
        from apps.notifications.services import NotificationService

        try:
            meeting = Meeting.objects.get(id=meeting_id)

            # Get chama members
            from apps.chama.models import Membership
            members = Membership.objects.filter(
                chama=meeting.chama,
                status='active',
            ).select_related('user')

            reminder_count = 0
            for member in members:
                try:
                    NotificationService.send_meeting_reminder(
                        user=member.user,
                        meeting=meeting,
                    )
                    reminder_count += 1
                except Exception as e:
                    logger.error(
                        f"Failed to send reminder to {member.user.full_name}: {e}"
                    )

            logger.info(
                f"Sent {reminder_count} reminders for meeting: {meeting_id}"
            )

            return reminder_count

        except Meeting.DoesNotExist:
            return 0
