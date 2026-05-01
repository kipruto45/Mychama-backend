"""
Reports and Exports Service

Manages report generation, filtering, and export functionality.
"""

import logging

from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama

logger = logging.getLogger(__name__)


class ReportsService:
    """Service for managing reports and exports."""

    @staticmethod
    def generate_contribution_report(
        chama: Chama,
        date_from: timezone.datetime = None,
        date_to: timezone.datetime = None,
        user: User = None,
    ) -> dict:
        """
        Generate contribution report for a chama.
        """
        from django.db.models import Avg, Count, Sum

        from apps.finance.models import Contribution

        queryset = Contribution.objects.filter(
            membership__chama=chama,
        )

        if date_from:
            queryset = queryset.filter(created_at__gte=date_from)

        if date_to:
            queryset = queryset.filter(created_at__lte=date_to)

        if user:
            queryset = queryset.filter(membership__user=user)

        # Get summary
        summary = queryset.aggregate(
            total_amount=Sum('amount'),
            total_count=Count('id'),
            avg_amount=Avg('amount'),
            paid_count=Count('id', filter=models.Q(status='paid')),
            pending_count=Count('id', filter=models.Q(status='pending')),
            overdue_count=Count('id', filter=models.Q(status='overdue')),
        )

        # Get by member
        by_member = queryset.values(
            'membership__user__id',
            'membership__user__full_name',
        ).annotate(
            total=Sum('amount'),
            count=Count('id'),
        ).order_by('-total')

        # Get by month
        by_month = queryset.extra(
            select={'month': "EXTRACT(month FROM created_at)"}
        ).values('month').annotate(
            total=Sum('amount'),
            count=Count('id'),
        ).order_by('month')

        return {
            'summary': {
                'total_amount': summary['total_amount'] or 0,
                'total_count': summary['total_count'] or 0,
                'avg_amount': summary['avg_amount'] or 0,
                'paid_count': summary['paid_count'] or 0,
                'pending_count': summary['pending_count'] or 0,
                'overdue_count': summary['overdue_count'] or 0,
            },
            'by_member': [
                {
                    'user_id': str(item['membership__user__id']),
                    'user_name': item['membership__user__full_name'],
                    'total': item['total'] or 0,
                    'count': item['count'] or 0,
                }
                for item in by_member
            ],
            'by_month': [
                {
                    'month': item['month'],
                    'total': item['total'] or 0,
                    'count': item['count'] or 0,
                }
                for item in by_month
            ],
        }

    @staticmethod
    def generate_loan_report(
        chama: Chama,
        date_from: timezone.datetime = None,
        date_to: timezone.datetime = None,
        status: str = None,
    ) -> dict:
        """
        Generate loan report for a chama.
        """
        from django.db.models import Avg, Count, Sum

        from apps.finance.models import Loan

        queryset = Loan.objects.filter(chama=chama)

        if date_from:
            queryset = queryset.filter(created_at__gte=date_from)

        if date_to:
            queryset = queryset.filter(created_at__lte=date_to)

        if status:
            queryset = queryset.filter(status=status)

        # Get summary
        summary = queryset.aggregate(
            total_borrowed=Sum('principal_amount'),
            total_repaid=Sum('amount_repaid'),
            total_interest=Sum('interest_amount'),
            total_count=Count('id'),
            avg_amount=Avg('principal_amount'),
            active_count=Count('id', filter=models.Q(status='active')),
            overdue_count=Count('id', filter=models.Q(status='overdue')),
            repaid_count=Count('id', filter=models.Q(status='repaid')),
        )

        # Get by status
        by_status = queryset.values('status').annotate(
            total=Sum('principal_amount'),
            count=Count('id'),
        )

        return {
            'summary': {
                'total_borrowed': summary['total_borrowed'] or 0,
                'total_repaid': summary['total_repaid'] or 0,
                'total_interest': summary['total_interest'] or 0,
                'total_count': summary['total_count'] or 0,
                'avg_amount': summary['avg_amount'] or 0,
                'active_count': summary['active_count'] or 0,
                'overdue_count': summary['overdue_count'] or 0,
                'repaid_count': summary['repaid_count'] or 0,
                'outstanding_balance': (summary['total_borrowed'] or 0) - (summary['total_repaid'] or 0),
            },
            'by_status': [
                {
                    'status': item['status'],
                    'total': item['total'] or 0,
                    'count': item['count'] or 0,
                }
                for item in by_status
            ],
        }

    @staticmethod
    def generate_attendance_report(
        chama: Chama,
        date_from: timezone.datetime = None,
        date_to: timezone.datetime = None,
    ) -> dict:
        """
        Generate attendance report for a chama.
        """
        from django.db.models import Count

        from apps.meetings.models import Attendance, Meeting

        # Get meetings
        meetings = Meeting.objects.filter(chama=chama)
        if date_from:
            meetings = meetings.filter(start_time__gte=date_from)
        if date_to:
            meetings = meetings.filter(start_time__lte=date_to)

        total_meetings = meetings.count()

        # Get attendance
        attendance = Attendance.objects.filter(
            meeting__chama=chama,
        )
        if date_from:
            attendance = attendance.filter(meeting__start_time__gte=date_from)
        if date_to:
            attendance = attendance.filter(meeting__start_time__lte=date_to)

        summary = attendance.aggregate(
            total=Count('id'),
            present=Count('id', filter=models.Q(status='present')),
            absent=Count('id', filter=models.Q(status='absent')),
            excused=Count('id', filter=models.Q(status='excused')),
        )

        # Get by member
        by_member = attendance.values(
            'user__id',
            'user__full_name',
        ).annotate(
            total=Count('id'),
            present=Count('id', filter=models.Q(status='present')),
            absent=Count('id', filter=models.Q(status='absent')),
            excused=Count('id', filter=models.Q(status='excused')),
        ).order_by('-present')

        return {
            'summary': {
                'total_meetings': total_meetings,
                'total_attendance': summary['total'] or 0,
                'present_count': summary['present'] or 0,
                'absent_count': summary['absent'] or 0,
                'excused_count': summary['excused'] or 0,
                'attendance_rate': (
                    (summary['present'] / summary['total'] * 100)
                    if summary['total'] > 0 else 0
                ),
            },
            'by_member': [
                {
                    'user_id': str(item['user__id']),
                    'user_name': item['user__full_name'],
                    'total': item['total'] or 0,
                    'present': item['present'] or 0,
                    'absent': item['absent'] or 0,
                    'excused': item['excused'] or 0,
                    'attendance_rate': (
                        (item['present'] / item['total'] * 100)
                        if item['total'] > 0 else 0
                    ),
                }
                for item in by_member
            ],
        }

    @staticmethod
    def generate_financial_summary_report(chama: Chama) -> dict:
        """
        Generate financial summary report for a chama.
        """
        from django.db.models import Sum

        from apps.finance.models import Account, Contribution, Expense, Loan

        # Get account balance
        account = Account.objects.filter(chama=chama, account_type='main').first()
        balance = account.balance if account else 0

        # Get contributions
        contributions = Contribution.objects.filter(
            membership__chama=chama,
        ).aggregate(
            total=Sum('amount'),
            paid=Sum('amount_paid'),
        )

        # Get loans
        loans = Loan.objects.filter(chama=chama).aggregate(
            total_borrowed=Sum('principal_amount'),
            total_repaid=Sum('amount_repaid'),
        )

        # Get expenses
        expenses = Expense.objects.filter(chama=chama).aggregate(
            total=Sum('amount'),
            paid=Sum('amount', filter=models.Q(status='paid')),
        )

        return {
            'balance': balance,
            'contributions': {
                'total': contributions['total'] or 0,
                'paid': contributions['paid'] or 0,
                'outstanding': (contributions['total'] or 0) - (contributions['paid'] or 0),
            },
            'loans': {
                'total_borrowed': loans['total_borrowed'] or 0,
                'total_repaid': loans['total_repaid'] or 0,
                'outstanding': (loans['total_borrowed'] or 0) - (loans['total_repaid'] or 0),
            },
            'expenses': {
                'total': expenses['total'] or 0,
                'paid': expenses['paid'] or 0,
                'pending': (expenses['total'] or 0) - (expenses['paid'] or 0),
            },
        }

    @staticmethod
    def export_to_csv(data: list[dict], filename: str) -> str:
        """
        Export data to CSV format.
        Returns CSV content as string.
        """
        import csv
        import io

        if not data:
            return ""

        # Get headers from first row
        headers = list(data[0].keys())

        # Create CSV
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=headers)
        writer.writeheader()
        writer.writerows(data)

        return output.getvalue()

    @staticmethod
    def export_to_json(data: list[dict]) -> str:
        """
        Export data to JSON format.
        Returns JSON content as string.
        """
        import json
        return json.dumps(data, indent=2, default=str)

    @staticmethod
    @transaction.atomic
    def create_export_job(
        chama: Chama,
        user: User,
        report_type: str,
        format: str = 'csv',
        filters: dict = None,
    ) -> dict:
        """
        Create an export job for async processing.
        Returns export job details.
        """
        from apps.reports.models import ExportJob

        # Create export job
        export_job = ExportJob.objects.create(
            chama=chama,
            user=user,
            report_type=report_type,
            format=format,
            filters=filters or {},
            status='pending',
        )

        logger.info(
            f"Export job created: {report_type} for {chama.name}"
        )

        return {
            'id': str(export_job.id),
            'report_type': report_type,
            'format': format,
            'status': 'pending',
        }

    @staticmethod
    def get_export_job_status(job_id: str) -> dict | None:
        """
        Get export job status.
        """
        from apps.reports.models import ExportJob

        try:
            job = ExportJob.objects.get(id=job_id)

            return {
                'id': str(job.id),
                'report_type': job.report_type,
                'format': job.format,
                'status': job.status,
                'file_url': job.file_url if job.status == 'completed' else None,
                'error_message': job.error_message if job.status == 'failed' else None,
                'created_at': job.created_at.isoformat(),
                'completed_at': job.completed_at.isoformat() if job.completed_at else None,
            }

        except ExportJob.DoesNotExist:
            return None

    @staticmethod
    def get_available_reports() -> list[dict]:
        """
        Get list of available reports.
        """
        return [
            {
                'id': 'contribution',
                'name': 'Contribution Report',
                'description': 'Detailed contribution analysis by member and period',
                'formats': ['csv', 'pdf', 'excel'],
            },
            {
                'id': 'loan',
                'name': 'Loan Report',
                'description': 'Loan portfolio analysis and repayment status',
                'formats': ['csv', 'pdf', 'excel'],
            },
            {
                'id': 'attendance',
                'name': 'Attendance Report',
                'description': 'Meeting attendance analysis by member',
                'formats': ['csv', 'pdf', 'excel'],
            },
            {
                'id': 'financial_summary',
                'name': 'Financial Summary',
                'description': 'Overall financial position and trends',
                'formats': ['pdf', 'excel'],
            },
            {
                'id': 'member',
                'name': 'Member Report',
                'description': 'Member demographics and activity',
                'formats': ['csv', 'pdf', 'excel'],
            },
            {
                'id': 'expense',
                'name': 'Expense Report',
                'description': 'Expense analysis by category',
                'formats': ['csv', 'pdf', 'excel'],
            },
        ]
