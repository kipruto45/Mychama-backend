"""
Guarantor and Recovery Service

Manages guarantor approval, exposure tracking, and recovery actions.
"""

import logging

from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama

logger = logging.getLogger(__name__)


class GuarantorService:
    """Service for managing guarantors and recovery."""

    @staticmethod
    def check_guarantor_capacity(chama: Chama, user: User, loan_amount: float) -> dict:
        """
        Check if user can guarantee a loan.
        Returns capacity details.
        """
        from django.db.models import Sum

        from apps.finance.models import Contribution, Loan

        # Get user's total contributions
        contributions = Contribution.objects.filter(
            membership__chama=chama,
            membership__user=user,
            status='paid',
        ).aggregate(
            total=Sum('amount'),
        )

        total_contributions = contributions['total'] or 0

        # Get existing guarantees
        existing_guarantees = Loan.objects.filter(
            chama=chama,
            guarantors=user,
            status__in=['active', 'overdue'],
        ).aggregate(
            total=Sum('principal_amount'),
        )

        total_guaranteed = existing_guarantees['total'] or 0

        # Calculate capacity (e.g., 3x contributions)
        max_guarantee_capacity = total_contributions * 3
        available_capacity = max_guarantee_capacity - total_guaranteed

        can_guarantee = available_capacity >= loan_amount

        return {
            'can_guarantee': can_guarantee,
            'total_contributions': total_contributions,
            'total_guaranteed': total_guaranteed,
            'max_guarantee_capacity': max_guarantee_capacity,
            'available_capacity': available_capacity,
            'requested_amount': loan_amount,
        }

    @staticmethod
    @transaction.atomic
    def approve_guarantee(
        loan_id: str,
        guarantor: User,
        approved: bool,
        notes: str = '',
    ) -> tuple[bool, str]:
        """
        Approve or reject a guarantee request.
        Returns (success, message).
        """
        from apps.finance.models import GuaranteeApproval, Loan

        try:
            loan = Loan.objects.get(id=loan_id)

            # Check if guarantor is assigned to this loan
            if guarantor not in loan.guarantors.all():
                return False, "You are not a guarantor for this loan"

            # Check capacity
            capacity = GuarantorService.check_guarantor_capacity(
                loan.chama, guarantor, loan.principal_amount
            )

            if approved and not capacity['can_guarantee']:
                return False, "Insufficient guarantee capacity"

            # Create guarantee approval record
            GuaranteeApproval.objects.create(
                loan=loan,
                guarantor=guarantor,
                approved=approved,
                notes=notes,
            )

            logger.info(
                f"Guarantee {'approved' if approved else 'rejected'}: "
                f"loan {loan_id} by {guarantor.full_name}"
            )

            return True, f"Guarantee {'approved' if approved else 'rejected'}"

        except Loan.DoesNotExist:
            return False, "Loan not found"

    @staticmethod
    def get_guarantor_exposure(chama: Chama = None, user: User = None) -> list[dict]:
        """
        Get guarantor exposure details.
        """
        from apps.finance.models import Loan

        queryset = Loan.objects.filter(
            status__in=['active', 'overdue'],
        ).exclude(guarantors__isnull=True)

        if chama:
            queryset = queryset.filter(chama=chama)

        if user:
            queryset = queryset.filter(guarantors=user)

        loans = queryset.prefetch_related('guarantors')

        exposure_data = []
        for loan in loans:
            for guarantor in loan.guarantors.all():
                exposure_data.append({
                    'loan_id': str(loan.id),
                    'borrower_name': loan.user.full_name,
                    'guarantor_name': guarantor.full_name,
                    'guarantor_id': str(guarantor.id),
                    'loan_amount': loan.principal_amount,
                    'loan_status': loan.status,
                    'due_date': loan.due_date.isoformat() if loan.due_date else None,
                })

        return exposure_data

    @staticmethod
    def get_guarantor_summary(chama: Chama) -> dict:
        """
        Get guarantor summary for a chama.
        """
        from django.db.models import Sum

        from apps.finance.models import Loan

        # Get loans with guarantors
        loans_with_guarantors = Loan.objects.filter(
            chama=chama,
            status__in=['active', 'overdue'],
        ).exclude(guarantors__isnull=True)

        # Count unique guarantors
        guarantor_ids = set()
        for loan in loans_with_guarantors:
            for guarantor in loan.guarantors.all():
                guarantor_ids.add(guarantor.id)

        # Get total guaranteed amount
        total_guaranteed = loans_with_guarantors.aggregate(
            total=Sum('principal_amount')
        )['total'] or 0

        # Get overdue loans with guarantors
        overdue_with_guarantors = loans_with_guarantors.filter(
            status='overdue'
        ).count()

        return {
            'total_guarantors': len(guarantor_ids),
            'total_guaranteed_amount': total_guaranteed,
            'loans_with_guarantors': loans_with_guarantors.count(),
            'overdue_loans_with_guarantors': overdue_with_guarantors,
        }

    @staticmethod
    @transaction.atomic
    def initiate_recovery(
        loan_id: str,
        recovery_type: str,
        amount: float,
        notes: str = '',
        initiated_by: User = None,
    ) -> dict:
        """
        Initiate a recovery action for a loan.
        Returns recovery details.
        """
        from apps.finance.models import Loan, RecoveryAction

        try:
            loan = Loan.objects.get(id=loan_id)

            if loan.status not in ['active', 'overdue']:
                raise ValueError("Loan is not active or overdue")

            # Create recovery action
            recovery = RecoveryAction.objects.create(
                loan=loan,
                recovery_type=recovery_type,
                amount=amount,
                notes=notes,
                initiated_by=initiated_by,
                status='pending',
            )

            logger.info(
                f"Recovery action initiated: {recovery_type} for loan {loan_id}"
            )

            return {
                'id': str(recovery.id),
                'loan_id': str(loan.id),
                'recovery_type': recovery_type,
                'amount': amount,
                'status': 'pending',
                'created_at': recovery.created_at.isoformat(),
            }

        except Loan.DoesNotExist:
            raise ValueError("Loan not found")

    @staticmethod
    @transaction.atomic
    def process_recovery(
        recovery_id: str,
        amount_recovered: float,
        payment_method: str,
        reference: str = '',
        notes: str = '',
    ) -> tuple[bool, str]:
        """
        Process a recovery action.
        Returns (success, message).
        """
        from apps.finance.models import RecoveryAction, Transaction

        try:
            recovery = RecoveryAction.objects.get(id=recovery_id)

            if recovery.status != 'pending':
                return False, "Recovery action is not pending"

            # Create transaction
            Transaction.objects.create(
                chama=recovery.loan.chama,
                transaction_type='recovery',
                amount=amount_recovered,
                payment_method=payment_method,
                reference=reference,
                notes=notes,
                created_by=recovery.loan.user,
            )

            # Update recovery action
            recovery.status = 'completed'
            recovery.amount_recovered = amount_recovered
            recovery.recovered_at = timezone.now()
            recovery.payment_reference = reference
            recovery.notes = notes
            recovery.save(update_fields=[
                'status',
                'amount_recovered',
                'recovered_at',
                'payment_reference',
                'notes',
                'updated_at',
            ])

            # Update loan
            recovery.loan.amount_repaid += amount_recovered
            recovery.loan.last_repayment_date = timezone.now()

            if recovery.loan.amount_repaid >= recovery.loan.total_amount:
                recovery.loan.status = 'repaid'
                recovery.loan.repaid_at = timezone.now()

            recovery.loan.save(update_fields=[
                'amount_repaid',
                'last_repayment_date',
                'status',
                'repaid_at',
                'updated_at',
            ])

            # Update account balance
            from apps.finance.models import Account
            account = Account.objects.get(
                chama=recovery.loan.chama,
                account_type='main',
            )
            account.balance += amount_recovered
            account.save(update_fields=['balance', 'updated_at'])

            logger.info(
                f"Recovery processed: {recovery_id} - {amount_recovered}"
            )

            return True, "Recovery processed"

        except RecoveryAction.DoesNotExist:
            return False, "Recovery action not found"

    @staticmethod
    def get_recovery_actions(
        chama: Chama = None,
        loan_id: str = None,
        status: str = None,
    ) -> list[dict]:
        """
        Get recovery actions with filtering.
        """
        from apps.finance.models import RecoveryAction

        queryset = RecoveryAction.objects.all()

        if chama:
            queryset = queryset.filter(loan__chama=chama)

        if loan_id:
            queryset = queryset.filter(loan_id=loan_id)

        if status:
            queryset = queryset.filter(status=status)

        recoveries = queryset.order_by('-created_at')

        return [
            {
                'id': str(recovery.id),
                'loan_id': str(recovery.loan.id),
                'borrower_name': recovery.loan.user.full_name,
                'recovery_type': recovery.recovery_type,
                'amount': recovery.amount,
                'amount_recovered': recovery.amount_recovered,
                'status': recovery.status,
                'notes': recovery.notes,
                'initiated_by_name': recovery.initiated_by.full_name if recovery.initiated_by else None,
                'created_at': recovery.created_at.isoformat(),
                'recovered_at': recovery.recovered_at.isoformat() if recovery.recovered_at else None,
            }
            for recovery in recoveries
        ]

    @staticmethod
    def get_recovery_summary(chama: Chama) -> dict:
        """
        Get recovery summary for a chama.
        """
        from django.db.models import Count, Sum

        from apps.finance.models import RecoveryAction

        summary = RecoveryAction.objects.filter(
            loan__chama=chama,
        ).aggregate(
            total=Count('id'),
            pending=Count('id', filter=models.Q(status='pending')),
            completed=Count('id', filter=models.Q(status='completed')),
            total_amount=Sum('amount'),
            total_recovered=Sum('amount_recovered'),
        )

        return {
            'total_recoveries': summary['total'] or 0,
            'pending_recoveries': summary['pending'] or 0,
            'completed_recoveries': summary['completed'] or 0,
            'total_amount': summary['total_amount'] or 0,
            'total_recovered': summary['total_recovered'] or 0,
            'recovery_rate': (
                (summary['total_recovered'] / summary['total_amount'] * 100)
                if summary['total_amount'] > 0 else 0
            ),
        }
