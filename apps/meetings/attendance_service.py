"""
Attendance Service

Manages attendance marking, summaries, and attendance-linked fines.
"""

import logging

from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama

logger = logging.getLogger(__name__)


class AttendanceService:
    """Service for managing attendance."""

    @staticmethod
    @transaction.atomic
    def mark_attendance(
        meeting_id: str,
        user: User,
        status: str = 'present',
        notes: str = '',
        marked_by: User = None,
    ) -> tuple[bool, str]:
        """
        Mark attendance for a meeting.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker
        from apps.meetings.models import Attendance, Meeting

        try:
            meeting = Meeting.objects.get(id=meeting_id)

            # Check if marker has permission
            if not PermissionChecker.has_permission(
                marked_by,
                Permission.CAN_RECORD_ATTENDANCE,
                str(meeting.chama.id),
            ):
                return False, "Permission denied"

            # Check if user is a member of the chama
            from apps.chama.models import Membership
            if not Membership.objects.filter(
                chama=meeting.chama,
                user=user,
                status='active',
            ).exists():
                return False, "User is not a member of this chama"

            # Create or update attendance
            attendance, created = Attendance.objects.update_or_create(
                meeting=meeting,
                user=user,
                defaults={
                    'status': status,
                    'notes': notes,
                    'marked_by': marked_by,
                    'marked_at': timezone.now(),
                },
            )

            logger.info(
                f"Attendance marked: {user.full_name} - {status} "
                f"for meeting {meeting_id}"
            )

            return True, "Attendance marked"

        except Meeting.DoesNotExist:
            return False, "Meeting not found"

    @staticmethod
    def get_meeting_attendance(meeting_id: str) -> list[dict]:
        """
        Get attendance for a meeting.
        """
        from apps.meetings.models import Attendance

        attendance_records = Attendance.objects.filter(
            meeting_id=meeting_id,
        ).select_related('user', 'marked_by')

        return [
            {
                'id': str(record.id),
                'user_id': str(record.user.id),
                'user_name': record.user.full_name,
                'status': record.status,
                'notes': record.notes,
                'marked_by_name': record.marked_by.full_name if record.marked_by else None,
                'marked_at': record.marked_at.isoformat() if record.marked_at else None,
            }
            for record in attendance_records
        ]

    @staticmethod
    def get_member_attendance(chama: Chama, user: User) -> list[dict]:
        """
        Get attendance history for a member.
        """
        from apps.meetings.models import Attendance

        attendance_records = Attendance.objects.filter(
            meeting__chama=chama,
            user=user,
        ).select_related('meeting').order_by('-meeting__start_time')

        return [
            {
                'id': str(record.id),
                'meeting_id': str(record.meeting.id),
                'meeting_title': record.meeting.title,
                'meeting_date': record.meeting.start_time.isoformat(),
                'status': record.status,
                'notes': record.notes,
            }
            for record in attendance_records
        ]

    @staticmethod
    def get_attendance_summary(chama: Chama, user: User = None) -> dict:
        """
        Get attendance summary for a chama or user.
        """
        from django.db.models import Count

        from apps.meetings.models import Attendance, Meeting

        # Get total meetings
        total_meetings = Meeting.objects.filter(
            chama=chama,
            status='completed',
        ).count()

        if user:
            # Get user's attendance
            attendance = Attendance.objects.filter(
                meeting__chama=chama,
                user=user,
            ).aggregate(
                total=Count('id'),
                present=Count('id', filter=models.Q(status='present')),
                absent=Count('id', filter=models.Q(status='absent')),
                excused=Count('id', filter=models.Q(status='excused')),
            )

            return {
                'total_meetings': total_meetings,
                'attended': attendance['present'] or 0,
                'absent': attendance['absent'] or 0,
                'excused': attendance['excused'] or 0,
                'attendance_rate': (
                    (attendance['present'] / total_meetings * 100)
                    if total_meetings > 0 else 0
                ),
            }
        else:
            # Get overall attendance summary
            attendance = Attendance.objects.filter(
                meeting__chama=chama,
            ).aggregate(
                total=Count('id'),
                present=Count('id', filter=models.Q(status='present')),
                absent=Count('id', filter=models.Q(status='absent')),
                excused=Count('id', filter=models.Q(status='excused')),
            )

            # Get member count
            from apps.chama.models import Membership
            member_count = Membership.objects.filter(
                chama=chama,
                status='active',
            ).count()

            return {
                'total_meetings': total_meetings,
                'total_attendance_records': attendance['total'] or 0,
                'present_count': attendance['present'] or 0,
                'absent_count': attendance['absent'] or 0,
                'excused_count': attendance['excused'] or 0,
                'average_attendance': (
                    (attendance['present'] / total_meetings / member_count * 100)
                    if total_meetings > 0 and member_count > 0 else 0
                ),
            }

    @staticmethod
    def get_attendance_report(chama: Chama) -> list[dict]:
        """
        Get attendance report for all members.
        """
        from django.db.models import Count

        from apps.meetings.models import Attendance, Meeting

        # Get total meetings
        total_meetings = Meeting.objects.filter(
            chama=chama,
            status='completed',
        ).count()

        # Get attendance for each member
        from apps.chama.models import Membership
        members = Membership.objects.filter(
            chama=chama,
            status='active',
        ).select_related('user')

        report = []
        for member in members:
            attendance = Attendance.objects.filter(
                meeting__chama=chama,
                user=member.user,
            ).aggregate(
                present=Count('id', filter=models.Q(status='present')),
                absent=Count('id', filter=models.Q(status='absent')),
                excused=Count('id', filter=models.Q(status='excused')),
            )

            attendance_rate = (
                (attendance['present'] / total_meetings * 100)
                if total_meetings > 0 else 0
            )

            report.append({
                'user_id': str(member.user.id),
                'user_name': member.user.full_name,
                'total_meetings': total_meetings,
                'present': attendance['present'] or 0,
                'absent': attendance['absent'] or 0,
                'excused': attendance['excused'] or 0,
                'attendance_rate': attendance_rate,
                'status': (
                    'excellent' if attendance_rate >= 90 else
                    'good' if attendance_rate >= 75 else
                    'fair' if attendance_rate >= 60 else
                    'poor'
                ),
            })

        # Sort by attendance rate
        report.sort(key=lambda x: x['attendance_rate'], reverse=True)

        return report

    @staticmethod
    def check_absence_fines(chama: Chama) -> list[dict]:
        """
        Check and generate fines for absences.
        """
        from apps.finance.models import Fine, FineRule
        from apps.meetings.models import Attendance, Meeting

        # Get absence fine rule
        fine_rule = FineRule.objects.filter(
            chama=chama,
            category='absence',
            is_automatic=True,
            is_active=True,
        ).first()

        if not fine_rule:
            return []

        # Get completed meetings in the last 30 days
        thirty_days_ago = timezone.now() - timezone.timedelta(days=30)
        meetings = Meeting.objects.filter(
            chama=chama,
            status='completed',
            start_time__gte=thirty_days_ago,
        )

        fines_issued = []
        for meeting in meetings:
            # Get absent members
            absent_members = Attendance.objects.filter(
                meeting=meeting,
                status='absent',
            ).select_related('user')

            for attendance in absent_members:
                # Check if fine already issued
                existing_fine = Fine.objects.filter(
                    user=attendance.user,
                    fine_rule=fine_rule,
                    reason__contains=str(meeting.id),
                ).exists()

                if not existing_fine:
                    # Issue fine
                    Fine.objects.create(
                        chama=chama,
                        user=attendance.user,
                        fine_rule=fine_rule,
                        amount=fine_rule.amount,
                        reason=f"Absence from meeting: {meeting.title}",
                        notes=f"Meeting ID: {meeting.id}",
                        issued_by=None,  # System-generated
                        status='pending',
                    )

                    fines_issued.append({
                        'user_id': str(attendance.user.id),
                        'user_name': attendance.user.full_name,
                        'amount': fine_rule.amount,
                        'meeting_title': meeting.title,
                    })

        return fines_issued

    @staticmethod
    def get_quorum_status(meeting_id: str) -> dict:
        """
        Check if meeting has quorum.
        """
        from apps.meetings.models import Attendance, Meeting

        try:
            meeting = Meeting.objects.get(id=meeting_id)

            # Get total members
            from apps.chama.models import Membership
            total_members = Membership.objects.filter(
                chama=meeting.chama,
                status='active',
            ).count()

            # Get present members
            present_members = Attendance.objects.filter(
                meeting=meeting,
                status='present',
            ).count()

            # Calculate quorum (typically 50% + 1)
            quorum_required = (total_members // 2) + 1
            has_quorum = present_members >= quorum_required

            return {
                'total_members': total_members,
                'present_members': present_members,
                'quorum_required': quorum_required,
                'has_quorum': has_quorum,
                'quorum_percentage': (
                    (present_members / total_members * 100)
                    if total_members > 0 else 0
                ),
            }

        except Meeting.DoesNotExist:
            return None
