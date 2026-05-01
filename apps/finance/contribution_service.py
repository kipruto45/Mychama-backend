"""
Contributions Service

Manages contribution schedules, dues tracking, and compliance.
"""

import logging

from django.db import models, transaction
from django.utils import timezone

from apps.chama.models import Chama, Membership

logger = logging.getLogger(__name__)


class ContributionService:
    """Service for managing contributions."""

    @staticmethod
    @transaction.atomic
    def create_contribution(
        membership: Membership,
        amount: float,
        due_date: timezone.datetime,
        contribution_type: str = 'regular',
    ) -> dict:
        """
        Create a new contribution record.
        Returns contribution details.
        """
        from apps.finance.models import Contribution

        # Validate amount
        if amount <= 0:
            raise ValueError("Contribution amount must be greater than 0")

        # Create contribution
        contribution = Contribution.objects.create(
            membership=membership,
            amount=amount,
            due_date=due_date,
            contribution_type=contribution_type,
            status='pending',
        )

        logger.info(
            f"Contribution created: {amount} for {membership.user.full_name} "
            f"in {membership.chama.name}"
        )

        return {
            'id': str(contribution.id),
            'amount': amount,
            'due_date': due_date.isoformat(),
            'contribution_type': contribution_type,
            'status': 'pending',
        }

    @staticmethod
    @transaction.atomic
    def record_payment(
        contribution_id: str,
        amount: float,
        payment_method: str,
        reference: str = '',
        notes: str = '',
    ) -> tuple[bool, str]:
        """
        Record a contribution payment.
        Returns (success, message).
        """
        from apps.finance.models import Contribution, Transaction

        try:
            contribution = Contribution.objects.get(id=contribution_id)

            # Validate amount
            if amount <= 0:
                return False, "Payment amount must be greater than 0"

            # Create transaction
            Transaction.objects.create(
                chama=contribution.membership.chama,
                transaction_type='contribution',
                amount=amount,
                payment_method=payment_method,
                reference=reference,
                notes=notes,
                created_by=contribution.membership.user,
            )

            # Update contribution
            contribution.amount_paid += amount
            contribution.payment_date = timezone.now()
            contribution.payment_reference = reference

            # Check if fully paid
            if contribution.amount_paid >= contribution.amount:
                contribution.status = 'paid'
            else:
                contribution.status = 'partial'

            contribution.save(update_fields=[
                'amount_paid',
                'payment_date',
                'payment_reference',
                'status',
                'updated_at',
            ])

            # Update account balance
            from apps.finance.models import Account
            account = Account.objects.get(
                chama=contribution.membership.chama,
                account_type='main',
            )
            account.balance += amount
            account.save(update_fields=['balance', 'updated_at'])

            logger.info(
                f"Payment recorded: {amount} for contribution {contribution_id}"
            )

            return True, "Payment recorded"

        except Contribution.DoesNotExist:
            return False, "Contribution not found"

    @staticmethod
    def get_member_contributions(
        membership: Membership,
        status: str = None,
    ) -> list[dict]:
        """
        Get contributions for a member.
        """
        from apps.finance.models import Contribution

        queryset = Contribution.objects.filter(membership=membership)

        if status:
            queryset = queryset.filter(status=status)

        contributions = queryset.order_by('-due_date')

        return [
            {
                'id': str(contrib.id),
                'amount': contrib.amount,
                'amount_paid': contrib.amount_paid,
                'due_date': contrib.due_date.isoformat(),
                'payment_date': contrib.payment_date.isoformat() if contrib.payment_date else None,
                'status': contrib.status,
                'contribution_type': contrib.contribution_type,
                'payment_reference': contrib.payment_reference,
            }
            for contrib in contributions
        ]

    @staticmethod
    def get_chama_contributions(
        chama: Chama,
        status: str = None,
        due_date_from: timezone.datetime = None,
        due_date_to: timezone.datetime = None,
    ) -> tuple[list[dict], dict]:
        """
        Get contributions for a chama with summary.
        Returns (contributions, summary).
        """
        from django.db.models import Count, Sum

        from apps.finance.models import Contribution

        queryset = Contribution.objects.filter(
            membership__chama=chama,
        ).select_related('membership__user')

        if status:
            queryset = queryset.filter(status=status)

        if due_date_from:
            queryset = queryset.filter(due_date__gte=due_date_from)

        if due_date_to:
            queryset = queryset.filter(due_date__lte=due_date_to)

        contributions = queryset.order_by('-due_date')

        # Calculate summary
        summary = queryset.aggregate(
            total_amount=Sum('amount'),
            total_paid=Sum('amount_paid'),
            count=Count('id'),
            pending_count=Count('id', filter=models.Q(status='pending')),
            overdue_count=Count('id', filter=models.Q(status='overdue')),
            paid_count=Count('id', filter=models.Q(status='paid')),
        )

        return [
            {
                'id': str(contrib.id),
                'member_name': contrib.membership.user.full_name,
                'amount': contrib.amount,
                'amount_paid': contrib.amount_paid,
                'due_date': contrib.due_date.isoformat(),
                'status': contrib.status,
                'contribution_type': contrib.contribution_type,
            }
            for contrib in contributions
        ], {
            'total_amount': summary['total_amount'] or 0,
            'total_paid': summary['total_paid'] or 0,
            'count': summary['count'] or 0,
            'pending_count': summary['pending_count'] or 0,
            'overdue_count': summary['overdue_count'] or 0,
            'paid_count': summary['paid_count'] or 0,
        }

    @staticmethod
    def check_overdue_contributions(chama: Chama) -> int:
        """
        Check and update overdue contributions.
        Returns number of contributions marked as overdue.
        """
        from apps.finance.models import Contribution

        now = timezone.now()

        # Get pending contributions past due date
        overdue = Contribution.objects.filter(
            membership__chama=chama,
            status='pending',
            due_date__lt=now,
        )

        count = overdue.count()

        if count > 0:
            overdue.update(status='overdue')
            logger.info(f"Marked {count} contributions as overdue for chama {chama.id}")

        return count

    @staticmethod
    def get_contribution_compliance(chama: Chama) -> list[dict]:
        """
        Get contribution compliance report for all members.
        """
        from django.db.models import Count, Sum

        from apps.finance.models import Contribution

        members = Membership.objects.filter(
            chama=chama,
            status='active',
        ).select_related('user')

        compliance_data = []

        for member in members:
            contributions = Contribution.objects.filter(
                membership=member,
            ).aggregate(
                total=Count('id'),
                paid=Count('id', filter=models.Q(status='paid')),
                pending=Count('id', filter=models.Q(status='pending')),
                overdue=Count('id', filter=models.Q(status='overdue')),
                total_amount=Sum('amount'),
                total_paid=Sum('amount_paid'),
            )

            compliance_rate = (
                (contributions['paid'] / contributions['total'] * 100)
                if contributions['total'] > 0 else 0
            )

            compliance_data.append({
                'member_id': str(member.user.id),
                'member_name': member.user.full_name,
                'total_contributions': contributions['total'],
                'paid_contributions': contributions['paid'],
                'pending_contributions': contributions['pending'],
                'overdue_contributions': contributions['overdue'],
                'total_amount': contributions['total_amount'] or 0,
                'total_paid': contributions['total_paid'] or 0,
                'compliance_rate': compliance_rate,
                'status': (
                    'excellent' if compliance_rate >= 95 else
                    'good' if compliance_rate >= 80 else
                    'fair' if compliance_rate >= 60 else
                    'poor'
                ),
            })

        # Sort by compliance rate
        compliance_data.sort(key=lambda x: x['compliance_rate'], reverse=True)

        return compliance_data

    @staticmethod
    def generate_due_dates(
        chama: Chama,
        start_date: timezone.datetime,
        end_date: timezone.datetime,
    ) -> list[dict]:
        """
        Generate contribution due dates based on chama settings.
        """
        from datetime import timedelta

        from apps.finance.models import ContributionSettings

        settings = ContributionSettings.objects.filter(chama=chama).first()

        if not settings:
            return []

        due_dates = []
        current_date = start_date

        while current_date <= end_date:
            if settings.frequency == 'weekly':
                due_date = current_date + timedelta(days=7)
            elif settings.frequency == 'biweekly':
                due_date = current_date + timedelta(days=14)
            elif settings.frequency == 'monthly':
                # Move to next month
                if current_date.month == 12:
                    due_date = current_date.replace(year=current_date.year + 1, month=1)
                else:
                    due_date = current_date.replace(month=current_date.month + 1)
            elif settings.frequency == 'quarterly':
                # Move to next quarter
                quarter_month = ((current_date.month - 1) // 3) * 3 + 1
                if current_date.month <= quarter_month:
                    due_date = current_date.replace(month=quarter_month + 3)
                else:
                    due_date = current_date.replace(month=quarter_month + 6)
            else:
                break

            if due_date <= end_date:
                due_dates.append({
                    'due_date': due_date,
                    'amount': settings.amount,
                })

            current_date = due_date

        return due_dates
