"""
Fines and Penalties Service

Manages fine rules, issued fines, and payment tracking.
"""

import logging

from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama

logger = logging.getLogger(__name__)


class FinesService:
    """Service for managing fines and penalties."""

    @staticmethod
    @transaction.atomic
    def create_fine_rule(
        chama: Chama,
        name: str,
        description: str,
        amount: float,
        category: str,
        is_automatic: bool = False,
        trigger_condition: str = '',
    ) -> dict:
        """
        Create a fine rule.
        Returns fine rule details.
        """
        from apps.finance.models import FineRule

        # Validate amount
        if amount <= 0:
            raise ValueError("Fine amount must be greater than 0")

        # Create fine rule
        fine_rule = FineRule.objects.create(
            chama=chama,
            name=name,
            description=description,
            amount=amount,
            category=category,
            is_automatic=is_automatic,
            trigger_condition=trigger_condition,
            is_active=True,
        )

        logger.info(
            f"Fine rule created: {name} for {chama.name}"
        )

        return {
            'id': str(fine_rule.id),
            'name': name,
            'description': description,
            'amount': amount,
            'category': category,
            'is_automatic': is_automatic,
            'trigger_condition': trigger_condition,
        }

    @staticmethod
    @transaction.atomic
    def issue_fine(
        chama: Chama,
        user: User,
        fine_rule_id: str,
        reason: str,
        issued_by: User,
        notes: str = '',
    ) -> dict:
        """
        Issue a fine to a member.
        Returns fine details.
        """
        from apps.finance.models import Fine, FineRule

        try:
            fine_rule = FineRule.objects.get(id=fine_rule_id, chama=chama)

            # Create fine
            fine = Fine.objects.create(
                chama=chama,
                user=user,
                fine_rule=fine_rule,
                amount=fine_rule.amount,
                reason=reason,
                notes=notes,
                issued_by=issued_by,
                status='pending',
            )

            logger.info(
                f"Fine issued: {fine_rule.amount} to {user.full_name} "
                f"in {chama.name}"
            )

            return {
                'id': str(fine.id),
                'amount': fine_rule.amount,
                'reason': reason,
                'user_name': user.full_name,
                'issued_by_name': issued_by.full_name,
                'status': 'pending',
                'created_at': fine.created_at.isoformat(),
            }

        except FineRule.DoesNotExist:
            raise ValueError("Fine rule not found")

    @staticmethod
    @transaction.atomic
    def waive_fine(
        fine_id: str,
        waiver_user: User,
        reason: str,
    ) -> tuple[bool, str]:
        """
        Waive a fine.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker
        from apps.finance.models import Fine

        try:
            fine = Fine.objects.get(id=fine_id)

            # Check if waiver user has permission
            if not PermissionChecker.has_permission(
                waiver_user,
                Permission.CAN_ISSUE_PENALTY,
                str(fine.chama.id),
            ):
                return False, "Permission denied"

            if fine.status != 'pending':
                return False, "Fine is not pending"

            # Update fine
            fine.status = 'waived'
            fine.waived_by = waiver_user
            fine.waived_at = timezone.now()
            fine.waiver_reason = reason
            fine.save(update_fields=[
                'status',
                'waived_by',
                'waived_at',
                'waiver_reason',
                'updated_at',
            ])

            logger.info(
                f"Fine waived: {fine_id} by {waiver_user.full_name}"
            )

            return True, "Fine waived"

        except Fine.DoesNotExist:
            return False, "Fine not found"

    @staticmethod
    @transaction.atomic
    def pay_fine(
        fine_id: str,
        payment_method: str,
        reference: str = '',
        notes: str = '',
    ) -> tuple[bool, str]:
        """
        Mark a fine as paid.
        Returns (success, message).
        """
        from apps.finance.models import Fine, Transaction

        try:
            fine = Fine.objects.get(id=fine_id)

            if fine.status != 'pending':
                return False, "Fine is not pending"

            # Create transaction
            Transaction.objects.create(
                chama=fine.chama,
                transaction_type='fine',
                amount=fine.amount,
                payment_method=payment_method,
                reference=reference,
                notes=notes,
                created_by=fine.user,
            )

            # Update fine
            fine.status = 'paid'
            fine.paid_at = timezone.now()
            fine.payment_reference = reference
            fine.payment_notes = notes
            fine.save(update_fields=[
                'status',
                'paid_at',
                'payment_reference',
                'payment_notes',
                'updated_at',
            ])

            # Update account balance
            from apps.finance.models import Account
            account = Account.objects.get(
                chama=fine.chama,
                account_type='main',
            )
            account.balance += fine.amount
            account.save(update_fields=['balance', 'updated_at'])

            logger.info(
                f"Fine paid: {fine_id} via {payment_method}"
            )

            return True, "Fine paid"

        except Fine.DoesNotExist:
            return False, "Fine not found"

    @staticmethod
    def get_pending_fines(chama: Chama = None, user: User = None) -> list[dict]:
        """
        Get pending fines.
        """
        from apps.finance.models import Fine

        queryset = Fine.objects.filter(status='pending')

        if chama:
            queryset = queryset.filter(chama=chama)

        if user:
            queryset = queryset.filter(user=user)

        fines = queryset.order_by('-created_at')

        return [
            {
                'id': str(fine.id),
                'amount': fine.amount,
                'reason': fine.reason,
                'user_name': fine.user.full_name,
                'fine_rule_name': fine.fine_rule.name if fine.fine_rule else None,
                'issued_by_name': fine.issued_by.full_name if fine.issued_by else None,
                'notes': fine.notes,
                'created_at': fine.created_at.isoformat(),
            }
            for fine in fines
        ]

    @staticmethod
    def get_fine_history(
        chama: Chama = None,
        user: User = None,
        status: str = None,
        category: str = None,
    ) -> list[dict]:
        """
        Get fine history with filtering.
        """
        from apps.finance.models import Fine

        queryset = Fine.objects.all()

        if chama:
            queryset = queryset.filter(chama=chama)

        if user:
            queryset = queryset.filter(user=user)

        if status:
            queryset = queryset.filter(status=status)

        if category:
            queryset = queryset.filter(fine_rule__category=category)

        fines = queryset.order_by('-created_at')

        return [
            {
                'id': str(fine.id),
                'amount': fine.amount,
                'reason': fine.reason,
                'user_name': fine.user.full_name,
                'issued_by_name': fine.issued_by.full_name if fine.issued_by else None,
                'waived_by_name': fine.waived_by.full_name if fine.waived_by else None,
                'status': fine.status,
                'fine_rule_name': fine.fine_rule.name if fine.fine_rule else None,
                'fine_rule_category': fine.fine_rule.category if fine.fine_rule else None,
                'notes': fine.notes,
                'waiver_reason': fine.waiver_reason,
                'payment_reference': fine.payment_reference,
                'created_at': fine.created_at.isoformat(),
                'paid_at': fine.paid_at.isoformat() if fine.paid_at else None,
                'waived_at': fine.waived_at.isoformat() if fine.waived_at else None,
            }
            for fine in fines
        ]

    @staticmethod
    def get_fine_summary(chama: Chama) -> dict:
        """
        Get fine summary for a chama.
        """
        from django.db.models import Count, Sum

        from apps.finance.models import Fine

        summary = Fine.objects.filter(chama=chama).aggregate(
            total=Count('id'),
            pending=Count('id', filter=models.Q(status='pending')),
            paid=Count('id', filter=models.Q(status='paid')),
            waived=Count('id', filter=models.Q(status='waived')),
            total_amount=Sum('amount'),
            paid_amount=Sum('amount', filter=models.Q(status='paid')),
        )

        # Get by category
        by_category = Fine.objects.filter(
            chama=chama,
            status='paid',
        ).values('fine_rule__category').annotate(
            total=Sum('amount'),
            count=Count('id'),
        )

        return {
            'total_fines': summary['total'] or 0,
            'pending_fines': summary['pending'] or 0,
            'paid_fines': summary['paid'] or 0,
            'waived_fines': summary['waived'] or 0,
            'total_amount': summary['total_amount'] or 0,
            'paid_amount': summary['paid_amount'] or 0,
            'collection_rate': (
                (summary['paid'] / summary['total'] * 100)
                if summary['total'] > 0 else 0
            ),
            'by_category': {
                item['fine_rule__category']: {
                    'total': item['total'] or 0,
                    'count': item['count'] or 0,
                }
                for item in by_category
            },
        }

    @staticmethod
    def get_fine_rules(chama: Chama) -> list[dict]:
        """
        Get fine rules for a chama.
        """
        from apps.finance.models import FineRule

        rules = FineRule.objects.filter(chama=chama, is_active=True)

        return [
            {
                'id': str(rule.id),
                'name': rule.name,
                'description': rule.description,
                'amount': rule.amount,
                'category': rule.category,
                'is_automatic': rule.is_automatic,
                'trigger_condition': rule.trigger_condition,
            }
            for rule in rules
        ]
