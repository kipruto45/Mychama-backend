"""
Loans Service

Manages loan policy, eligibility engine, application, approval, and repayment.
"""

import logging

from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama

logger = logging.getLogger(__name__)


class LoansService:
    """Service for managing loans."""

    @staticmethod
    def check_eligibility(chama: Chama, user: User, amount: float) -> dict:
        """
        Check loan eligibility for a user.
        Returns eligibility details.
        """
        from django.db.models import Count, Sum

        from apps.finance.models import Contribution, Loan

        # Get user's contribution history
        contributions = Contribution.objects.filter(
            membership__chama=chama,
            membership__user=user,
            status='paid',
        ).aggregate(
            total=Sum('amount'),
            count=Count('id'),
        )

        total_contributions = contributions['total'] or 0
        contribution_count = contributions['count'] or 0

        # Get active loans
        active_loans = Loan.objects.filter(
            chama=chama,
            user=user,
            status__in=['active', 'overdue'],
        ).count()

        # Get loan policy
        from apps.settings.models import ChamaSettings
        settings = ChamaSettings.objects.filter(chama=chama).first()

        max_multiplier = settings.max_loan_multiplier if settings else 3
        max_amount = total_contributions * max_multiplier

        # Check eligibility
        is_eligible = (
            contribution_count >= 3 and  # Minimum 3 contributions
            active_loans == 0 and  # No active loans
            amount <= max_amount  # Within limit
        )

        return {
            'is_eligible': is_eligible,
            'max_amount': max_amount,
            'total_contributions': total_contributions,
            'contribution_count': contribution_count,
            'active_loans': active_loans,
            'reasons': [] if is_eligible else [
                'Minimum 3 contributions required' if contribution_count < 3 else None,
                'You have active loans' if active_loans > 0 else None,
                f'Amount exceeds maximum of {max_amount}' if amount > max_amount else None,
            ],
        }

    @staticmethod
    @transaction.atomic
    def apply_for_loan(
        chama: Chama,
        user: User,
        amount: float,
        term_months: int,
        purpose: str,
        guarantors: list[str] = None,
    ) -> dict:
        """
        Apply for a loan.
        Returns loan application details.
        """
        from apps.finance.models import Loan

        # Check eligibility
        eligibility = LoansService.check_eligibility(chama, user, amount)
        if not eligibility['is_eligible']:
            raise ValueError(f"Not eligible for loan: {', '.join(eligibility['reasons'])}")

        # Get interest rate
        from apps.settings.models import ChamaSettings
        settings = ChamaSettings.objects.filter(chama=chama).first()
        interest_rate = settings.interest_rate if settings else 10

        # Calculate interest
        interest_amount = amount * (interest_rate / 100) * (term_months / 12)
        total_amount = amount + interest_amount

        # Create loan
        loan = Loan.objects.create(
            chama=chama,
            user=user,
            principal_amount=amount,
            interest_rate=interest_rate,
            interest_amount=interest_amount,
            total_amount=total_amount,
            term_months=term_months,
            purpose=purpose,
            status='pending',
        )

        # Add guarantors
        if guarantors:
            for guarantor_id in guarantors:
                try:
                    guarantor = User.objects.get(id=guarantor_id)
                    loan.guarantors.add(guarantor)
                except User.DoesNotExist:
                    pass

        logger.info(
            f"Loan application created: {amount} for {user.full_name} "
            f"in {chama.name}"
        )

        return {
            'id': str(loan.id),
            'principal_amount': amount,
            'interest_rate': interest_rate,
            'interest_amount': interest_amount,
            'total_amount': total_amount,
            'term_months': term_months,
            'purpose': purpose,
            'status': 'pending',
            'created_at': loan.created_at.isoformat(),
        }

    @staticmethod
    @transaction.atomic
    def approve_loan(
        loan_id: str,
        approver: User,
        notes: str = '',
    ) -> tuple[bool, str]:
        """
        Approve a loan application.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker
        from apps.finance.models import Loan

        try:
            loan = Loan.objects.get(id=loan_id)

            # Check if approver has permission
            if not PermissionChecker.has_permission(
                approver,
                Permission.CAN_APPROVE_LOAN,
                str(loan.chama.id),
            ):
                return False, "Permission denied"

            # Cannot approve your own loan
            if loan.user == approver:
                return False, "Cannot approve your own loan"

            if loan.status != 'pending':
                return False, "Loan is not pending"

            # Update loan
            loan.status = 'approved'
            loan.approved_by = approver
            loan.approved_at = timezone.now()
            loan.approval_notes = notes
            loan.save(update_fields=[
                'status',
                'approved_by',
                'approved_at',
                'approval_notes',
                'updated_at',
            ])

            logger.info(
                f"Loan approved: {loan_id} by {approver.full_name}"
            )

            return True, "Loan approved"

        except Loan.DoesNotExist:
            return False, "Loan not found"

    @staticmethod
    @transaction.atomic
    def reject_loan(
        loan_id: str,
        rejector: User,
        reason: str,
    ) -> tuple[bool, str]:
        """
        Reject a loan application.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker
        from apps.finance.models import Loan

        try:
            loan = Loan.objects.get(id=loan_id)

            # Check if rejector has permission
            if not PermissionChecker.has_permission(
                rejector,
                Permission.CAN_APPROVE_LOAN,
                str(loan.chama.id),
            ):
                return False, "Permission denied"

            # Update loan
            loan.status = 'rejected'
            loan.rejected_by = rejector
            loan.rejected_at = timezone.now()
            loan.rejection_reason = reason
            loan.save(update_fields=[
                'status',
                'rejected_by',
                'rejected_at',
                'rejection_reason',
                'updated_at',
            ])

            logger.info(
                f"Loan rejected: {loan_id} by {rejector.full_name}"
            )

            return True, "Loan rejected"

        except Loan.DoesNotExist:
            return False, "Loan not found"

    @staticmethod
    @transaction.atomic
    def disburse_loan(
        loan_id: str,
        payment_method: str,
        reference: str = '',
        notes: str = '',
    ) -> tuple[bool, str]:
        """
        Disburse an approved loan.
        Returns (success, message).
        """
        from apps.finance.models import Account, Loan, Transaction

        try:
            loan = Loan.objects.get(id=loan_id)

            if loan.status != 'approved':
                return False, "Loan is not approved"

            # Create transaction
            Transaction.objects.create(
                chama=loan.chama,
                transaction_type='loan_disbursement',
                amount=loan.principal_amount,
                payment_method=payment_method,
                reference=reference,
                notes=notes,
                created_by=loan.user,
            )

            # Update loan
            loan.status = 'active'
            loan.disbursed_at = timezone.now()
            loan.disbursement_reference = reference
            loan.disbursement_notes = notes
            loan.due_date = timezone.now() + timezone.timedelta(days=loan.term_months * 30)
            loan.save(update_fields=[
                'status',
                'disbursed_at',
                'disbursement_reference',
                'disbursement_notes',
                'due_date',
                'updated_at',
            ])

            # Update account balance
            account = Account.objects.get(
                chama=loan.chama,
                account_type='main',
            )
            account.balance -= loan.principal_amount
            account.save(update_fields=['balance', 'updated_at'])

            logger.info(
                f"Loan disbursed: {loan_id} via {payment_method}"
            )

            return True, "Loan disbursed"

        except Loan.DoesNotExist:
            return False, "Loan not found"

    @staticmethod
    @transaction.atomic
    def record_repayment(
        loan_id: str,
        amount: float,
        payment_method: str,
        reference: str = '',
        notes: str = '',
    ) -> tuple[bool, str]:
        """
        Record a loan repayment.
        Returns (success, message).
        """
        from apps.finance.models import Account, Loan, Transaction

        try:
            loan = Loan.objects.get(id=loan_id)

            if loan.status not in ['active', 'overdue']:
                return False, "Loan is not active"

            # Create transaction
            Transaction.objects.create(
                chama=loan.chama,
                transaction_type='loan_repayment',
                amount=amount,
                payment_method=payment_method,
                reference=reference,
                notes=notes,
                created_by=loan.user,
            )

            # Update loan
            loan.amount_repaid += amount
            loan.last_repayment_date = timezone.now()

            # Check if fully repaid
            if loan.amount_repaid >= loan.total_amount:
                loan.status = 'repaid'
                loan.repaid_at = timezone.now()

            loan.save(update_fields=[
                'amount_repaid',
                'last_repayment_date',
                'status',
                'repaid_at',
                'updated_at',
            ])

            # Update account balance
            account = Account.objects.get(
                chama=loan.chama,
                account_type='main',
            )
            account.balance += amount
            account.save(update_fields=['balance', 'updated_at'])

            logger.info(
                f"Loan repayment recorded: {amount} for loan {loan_id}"
            )

            return True, "Repayment recorded"

        except Loan.DoesNotExist:
            return False, "Loan not found"

    @staticmethod
    def get_pending_loans(chama: Chama = None) -> list[dict]:
        """
        Get pending loan applications.
        """
        from apps.finance.models import Loan

        queryset = Loan.objects.filter(status='pending')

        if chama:
            queryset = queryset.filter(chama=chama)

        loans = queryset.order_by('-created_at')

        return [
            {
                'id': str(loan.id),
                'principal_amount': loan.principal_amount,
                'interest_rate': loan.interest_rate,
                'total_amount': loan.total_amount,
                'term_months': loan.term_months,
                'purpose': loan.purpose,
                'user_name': loan.user.full_name,
                'guarantors': [
                    {'id': str(g.id), 'name': g.full_name}
                    for g in loan.guarantors.all()
                ],
                'created_at': loan.created_at.isoformat(),
            }
            for loan in loans
        ]

    @staticmethod
    def get_loan_history(
        chama: Chama = None,
        user: User = None,
        status: str = None,
    ) -> list[dict]:
        """
        Get loan history with filtering.
        """
        from apps.finance.models import Loan

        queryset = Loan.objects.all()

        if chama:
            queryset = queryset.filter(chama=chama)

        if user:
            queryset = queryset.filter(user=user)

        if status:
            queryset = queryset.filter(status=status)

        loans = queryset.order_by('-created_at')

        return [
            {
                'id': str(loan.id),
                'principal_amount': loan.principal_amount,
                'interest_rate': loan.interest_rate,
                'interest_amount': loan.interest_amount,
                'total_amount': loan.total_amount,
                'amount_repaid': loan.amount_repaid,
                'term_months': loan.term_months,
                'purpose': loan.purpose,
                'user_name': loan.user.full_name,
                'approved_by_name': loan.approved_by.full_name if loan.approved_by else None,
                'status': loan.status,
                'created_at': loan.created_at.isoformat(),
                'approved_at': loan.approved_at.isoformat() if loan.approved_at else None,
                'disbursed_at': loan.disbursed_at.isoformat() if loan.disbursed_at else None,
                'due_date': loan.due_date.isoformat() if loan.due_date else None,
                'repaid_at': loan.repaid_at.isoformat() if loan.repaid_at else None,
            }
            for loan in loans
        ]

    @staticmethod
    def get_loan_summary(chama: Chama) -> dict:
        """
        Get loan summary for a chama.
        """
        from django.db.models import Count, Sum

        from apps.finance.models import Loan

        summary = Loan.objects.filter(chama=chama).aggregate(
            total=Count('id'),
            pending=Count('id', filter=models.Q(status='pending')),
            active=Count('id', filter=models.Q(status='active')),
            overdue=Count('id', filter=models.Q(status='overdue')),
            repaid=Count('id', filter=models.Q(status='repaid')),
            total_borrowed=Sum('principal_amount'),
            total_repaid=Sum('amount_repaid'),
            total_interest=Sum('interest_amount'),
        )

        return {
            'total_loans': summary['total'] or 0,
            'pending_loans': summary['pending'] or 0,
            'active_loans': summary['active'] or 0,
            'overdue_loans': summary['overdue'] or 0,
            'repaid_loans': summary['repaid'] or 0,
            'total_borrowed': summary['total_borrowed'] or 0,
            'total_repaid': summary['total_repaid'] or 0,
            'total_interest': summary['total_interest'] or 0,
            'outstanding_balance': (summary['total_borrowed'] or 0) - (summary['total_repaid'] or 0),
            'approval_rate': (
                (summary['active'] + summary['overdue'] + summary['repaid']) / summary['total'] * 100
                if summary['total'] > 0 else 0
            ),
        }

    @staticmethod
    def check_overdue_loans(chama: Chama = None) -> int:
        """
        Check and update overdue loans.
        Returns number of loans marked as overdue.
        """
        from apps.finance.models import Loan

        now = timezone.now()

        # Get active loans past due date
        overdue = Loan.objects.filter(
            status='active',
            due_date__lt=now,
        )

        if chama:
            overdue = overdue.filter(chama=chama)

        count = overdue.count()

        if count > 0:
            overdue.update(status='overdue')
            logger.info(f"Marked {count} loans as overdue")

        return count
