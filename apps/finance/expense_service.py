"""
Expenses Service

Manages expense requests, approval flow, and payment tracking.
"""

import logging

from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama

logger = logging.getLogger(__name__)


class ExpenseService:
    """Service for managing expenses."""

    @staticmethod
    @transaction.atomic
    def create_expense_request(
        chama: Chama,
        user: User,
        amount: float,
        category: str,
        description: str,
        receipt_document=None,
        notes: str = '',
    ) -> dict:
        """
        Create an expense request.
        Returns expense details.
        """
        from apps.finance.models import Expense

        # Validate amount
        if amount <= 0:
            raise ValueError("Expense amount must be greater than 0")

        # Create expense request
        expense = Expense.objects.create(
            chama=chama,
            user=user,
            amount=amount,
            category=category,
            description=description,
            receipt_document=receipt_document,
            notes=notes,
            status='pending',
        )

        logger.info(
            f"Expense request created: {amount} for {category} by {user.full_name}"
        )

        return {
            'id': str(expense.id),
            'amount': amount,
            'category': category,
            'description': description,
            'status': 'pending',
            'created_at': expense.created_at.isoformat(),
        }

    @staticmethod
    @transaction.atomic
    def approve_expense(
        expense_id: str,
        approver: User,
        notes: str = '',
    ) -> tuple[bool, str]:
        """
        Approve an expense request.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker
        from apps.finance.models import Expense

        try:
            expense = Expense.objects.get(id=expense_id)

            # Check if approver has permission
            if not PermissionChecker.has_permission(
                approver,
                Permission.CAN_APPROVE_LOAN,  # Using loan approval permission
                str(expense.chama.id),
            ):
                return False, "Permission denied"

            # Cannot approve your own expense
            if expense.user == approver:
                return False, "Cannot approve your own expense"

            # Update expense
            expense.status = 'approved'
            expense.approved_by = approver
            expense.approved_at = timezone.now()
            expense.approval_notes = notes
            expense.save(update_fields=[
                'status',
                'approved_by',
                'approved_at',
                'approval_notes',
                'updated_at',
            ])

            logger.info(
                f"Expense approved: {expense_id} by {approver.full_name}"
            )

            return True, "Expense approved"

        except Expense.DoesNotExist:
            return False, "Expense not found"

    @staticmethod
    @transaction.atomic
    def reject_expense(
        expense_id: str,
        rejector: User,
        reason: str,
    ) -> tuple[bool, str]:
        """
        Reject an expense request.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker
        from apps.finance.models import Expense

        try:
            expense = Expense.objects.get(id=expense_id)

            # Check if rejector has permission
            if not PermissionChecker.has_permission(
                rejector,
                Permission.CAN_APPROVE_LOAN,
                str(expense.chama.id),
            ):
                return False, "Permission denied"

            # Update expense
            expense.status = 'rejected'
            expense.rejected_by = rejector
            expense.rejected_at = timezone.now()
            expense.rejection_reason = reason
            expense.save(update_fields=[
                'status',
                'rejected_by',
                'rejected_at',
                'rejection_reason',
                'updated_at',
            ])

            logger.info(
                f"Expense rejected: {expense_id} by {rejector.full_name}"
            )

            return True, "Expense rejected"

        except Expense.DoesNotExist:
            return False, "Expense not found"

    @staticmethod
    @transaction.atomic
    def pay_expense(
        expense_id: str,
        payment_method: str,
        reference: str = '',
        notes: str = '',
    ) -> tuple[bool, str]:
        """
        Mark an expense as paid.
        Returns (success, message).
        """
        from apps.finance.models import Expense, Transaction

        try:
            expense = Expense.objects.get(id=expense_id)

            if expense.status != 'approved':
                return False, "Expense is not approved"

            # Create transaction
            Transaction.objects.create(
                chama=expense.chama,
                transaction_type='expense',
                amount=expense.amount,
                payment_method=payment_method,
                reference=reference,
                notes=notes,
                created_by=expense.user,
            )

            # Update expense
            expense.status = 'paid'
            expense.paid_at = timezone.now()
            expense.payment_reference = reference
            expense.payment_notes = notes
            expense.save(update_fields=[
                'status',
                'paid_at',
                'payment_reference',
                'payment_notes',
                'updated_at',
            ])

            # Update account balance
            from apps.finance.models import Account
            account = Account.objects.get(
                chama=expense.chama,
                account_type='main',
            )
            account.balance -= expense.amount
            account.save(update_fields=['balance', 'updated_at'])

            logger.info(
                f"Expense paid: {expense_id} via {payment_method}"
            )

            return True, "Expense paid"

        except Expense.DoesNotExist:
            return False, "Expense not found"

    @staticmethod
    def get_pending_expenses(chama: Chama = None) -> list[dict]:
        """
        Get pending expense requests.
        """
        from apps.finance.models import Expense

        queryset = Expense.objects.filter(status='pending')

        if chama:
            queryset = queryset.filter(chama=chama)

        expenses = queryset.order_by('-created_at')

        return [
            {
                'id': str(expense.id),
                'amount': expense.amount,
                'category': expense.category,
                'description': expense.description,
                'user_name': expense.user.full_name,
                'notes': expense.notes,
                'receipt_url': expense.receipt_document.url if expense.receipt_document else None,
                'created_at': expense.created_at.isoformat(),
            }
            for expense in expenses
        ]

    @staticmethod
    def get_expense_history(
        chama: Chama = None,
        user: User = None,
        status: str = None,
        category: str = None,
    ) -> list[dict]:
        """
        Get expense history with filtering.
        """
        from apps.finance.models import Expense

        queryset = Expense.objects.all()

        if chama:
            queryset = queryset.filter(chama=chama)

        if user:
            queryset = queryset.filter(user=user)

        if status:
            queryset = queryset.filter(status=status)

        if category:
            queryset = queryset.filter(category=category)

        expenses = queryset.order_by('-created_at')

        return [
            {
                'id': str(expense.id),
                'amount': expense.amount,
                'category': expense.category,
                'description': expense.description,
                'user_name': expense.user.full_name,
                'approved_by_name': expense.approved_by.full_name if expense.approved_by else None,
                'status': expense.status,
                'notes': expense.notes,
                'approval_notes': expense.approval_notes,
                'rejection_reason': expense.rejection_reason,
                'payment_reference': expense.payment_reference,
                'created_at': expense.created_at.isoformat(),
                'approved_at': expense.approved_at.isoformat() if expense.approved_at else None,
                'paid_at': expense.paid_at.isoformat() if expense.paid_at else None,
            }
            for expense in expenses
        ]

    @staticmethod
    def get_expense_summary(chama: Chama) -> dict:
        """
        Get expense summary for a chama.
        """
        from django.db.models import Count, Sum

        from apps.finance.models import Expense

        summary = Expense.objects.filter(chama=chama).aggregate(
            total=Count('id'),
            pending=Count('id', filter=models.Q(status='pending')),
            approved=Count('id', filter=models.Q(status='approved')),
            paid=Count('id', filter=models.Q(status='paid')),
            rejected=Count('id', filter=models.Q(status='rejected')),
            total_amount=Sum('amount'),
            paid_amount=Sum('amount', filter=models.Q(status='paid')),
        )

        # Get by category
        by_category = Expense.objects.filter(
            chama=chama,
            status='paid',
        ).values('category').annotate(
            total=Sum('amount'),
            count=Count('id'),
        )

        return {
            'total_expenses': summary['total'] or 0,
            'pending_expenses': summary['pending'] or 0,
            'approved_expenses': summary['approved'] or 0,
            'paid_expenses': summary['paid'] or 0,
            'rejected_expenses': summary['rejected'] or 0,
            'total_amount': summary['total_amount'] or 0,
            'paid_amount': summary['paid_amount'] or 0,
            'approval_rate': (
                (summary['approved'] / summary['total'] * 100)
                if summary['total'] > 0 else 0
            ),
            'by_category': {
                item['category']: {
                    'total': item['total'] or 0,
                    'count': item['count'] or 0,
                }
                for item in by_category
            },
        }

    @staticmethod
    def get_expense_categories() -> list[dict]:
        """
        Get available expense categories.
        """
        return [
            {'id': 'utilities', 'name': 'Utilities', 'description': 'Electricity, water, internet'},
            {'id': 'rent', 'name': 'Rent', 'description': 'Office or meeting space rent'},
            {'id': 'supplies', 'name': 'Supplies', 'description': 'Office supplies and materials'},
            {'id': 'transport', 'name': 'Transport', 'description': 'Travel and transportation'},
            {'id': 'food', 'name': 'Food', 'description': 'Meeting refreshments and meals'},
            {'id': 'maintenance', 'name': 'Maintenance', 'description': 'Equipment and facility maintenance'},
            {'id': 'professional', 'name': 'Professional Services', 'description': 'Legal, accounting, consulting'},
            {'id': 'insurance', 'name': 'Insurance', 'description': 'Insurance premiums'},
            {'id': 'taxes', 'name': 'Taxes', 'description': 'Government taxes and fees'},
            {'id': 'other', 'name': 'Other', 'description': 'Other miscellaneous expenses'},
        ]
