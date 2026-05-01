"""
Reconciliation Service

Manages payment reconciliation, mismatch detection, and resolution workflows.
"""

import logging

from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama

logger = logging.getLogger(__name__)


class ReconciliationService:
    """Service for managing reconciliation."""

    @staticmethod
    def get_pending_reconciliations(chama: Chama = None) -> dict:
        """
        Get all pending reconciliation items.
        """
        from apps.finance.models import Contribution
        from apps.payments.models import PaymentTransaction

        result = {
            'pending_payments': [],
            'orphan_callbacks': [],
            'mismatched_amounts': [],
            'unresolved_manual_payments': [],
        }

        # Get pending payments
        pending_payments = PaymentTransaction.objects.filter(
            status='pending',
        )
        if chama:
            pending_payments = pending_payments.filter(chama=chama)

        result['pending_payments'] = [
            {
                'id': str(payment.id),
                'reference': payment.reference,
                'amount': payment.amount,
                'payment_method': payment.payment_method,
                'user_name': payment.user.full_name if payment.user else None,
                'created_at': payment.created_at.isoformat(),
            }
            for payment in pending_payments[:50]
        ]

        # Get orphan callbacks (callbacks without matching payment)
        # This would need to be implemented based on your callback storage
        result['orphan_callbacks'] = []

        # Get mismatched amounts
        contributions = Contribution.objects.filter(
            status='paid',
        )
        if chama:
            contributions = contributions.filter(membership__chama=chama)

        for contribution in contributions:
            # Check if payment amount matches contribution amount
            if contribution.amount_paid and contribution.amount_paid != contribution.amount:
                result['mismatched_amounts'].append({
                    'id': str(contribution.id),
                    'member_name': contribution.membership.user.full_name,
                    'expected_amount': contribution.amount,
                    'paid_amount': contribution.amount_paid,
                    'difference': contribution.amount - contribution.amount_paid,
                })

        return result

    @staticmethod
    @transaction.atomic
    def create_reconciliation_case(
        chama: Chama,
        case_type: str,
        reference_id: str,
        description: str,
        amount: float = None,
        created_by: User = None,
    ) -> dict:
        """
        Create a reconciliation case.
        Returns case details.
        """
        from apps.reconciliation.models import ReconciliationCase

        # Create case
        case = ReconciliationCase.objects.create(
            chama=chama,
            case_type=case_type,
            reference_id=reference_id,
            description=description,
            amount=amount,
            created_by=created_by,
            status='open',
        )

        logger.info(
            f"Reconciliation case created: {case_type} for {chama.name}"
        )

        return {
            'id': str(case.id),
            'case_type': case_type,
            'reference_id': reference_id,
            'description': description,
            'amount': amount,
            'status': 'open',
        }

    @staticmethod
    @transaction.atomic
    def resolve_reconciliation_case(
        case_id: str,
        resolution: str,
        resolved_by: User,
        notes: str = '',
    ) -> tuple[bool, str]:
        """
        Resolve a reconciliation case.
        Returns (success, message).
        """
        from apps.reconciliation.models import ReconciliationCase

        try:
            case = ReconciliationCase.objects.get(id=case_id)

            case.status = 'resolved'
            case.resolution = resolution
            case.resolved_by = resolved_by
            case.resolved_at = timezone.now()
            case.resolution_notes = notes
            case.save(update_fields=[
                'status',
                'resolution',
                'resolved_by',
                'resolved_at',
                'resolution_notes',
                'updated_at',
            ])

            logger.info(
                f"Reconciliation case resolved: {case_id} by {resolved_by.full_name}"
            )

            return True, "Case resolved"

        except ReconciliationCase.DoesNotExist:
            return False, "Case not found"

    @staticmethod
    def get_reconciliation_cases(
        chama: Chama = None,
        status: str = None,
        case_type: str = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """
        Get reconciliation cases with filtering and pagination.
        """
        from apps.reconciliation.models import ReconciliationCase

        queryset = ReconciliationCase.objects.all()

        if chama:
            queryset = queryset.filter(chama=chama)

        if status:
            queryset = queryset.filter(status=status)

        if case_type:
            queryset = queryset.filter(case_type=case_type)

        queryset = queryset.order_by('-created_at')

        # Paginate
        total = queryset.count()
        start = (page - 1) * page_size
        end = start + page_size
        cases = queryset[start:end]

        return {
            'results': [
                {
                    'id': str(case.id),
                    'case_type': case.case_type,
                    'reference_id': case.reference_id,
                    'description': case.description,
                    'amount': case.amount,
                    'status': case.status,
                    'resolution': case.resolution,
                    'created_by_name': case.created_by.full_name if case.created_by else None,
                    'resolved_by_name': case.resolved_by.full_name if case.resolved_by else None,
                    'created_at': case.created_at.isoformat(),
                    'resolved_at': case.resolved_at.isoformat() if case.resolved_at else None,
                }
                for case in cases
            ],
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total': total,
                'total_pages': (total + page_size - 1) // page_size,
            },
        }

    @staticmethod
    def get_reconciliation_summary(chama: Chama = None) -> dict:
        """
        Get reconciliation summary.
        """
        from django.db.models import Count

        from apps.reconciliation.models import ReconciliationCase

        queryset = ReconciliationCase.objects.all()

        if chama:
            queryset = queryset.filter(chama=chama)

        summary = queryset.aggregate(
            total=Count('id'),
            open=Count('id', filter=models.Q(status='open')),
            resolved=Count('id', filter=models.Q(status='resolved')),
            closed=Count('id', filter=models.Q(status='closed')),
        )

        return {
            'total_cases': summary['total'] or 0,
            'open_cases': summary['open'] or 0,
            'resolved_cases': summary['resolved'] or 0,
            'closed_cases': summary['closed'] or 0,
        }

    @staticmethod
    def run_auto_reconciliation(chama: Chama) -> dict:
        """
        Run automatic reconciliation checks.
        Returns reconciliation results.
        """

        from apps.finance.models import Contribution
        from apps.payments.models import PaymentTransaction

        results = {
            'matched': 0,
            'mismatched': 0,
            'orphaned': 0,
            'details': [],
        }

        # Get all payments for the chama
        payments = PaymentTransaction.objects.filter(
            chama=chama,
            status='completed',
        )

        for payment in payments:
            # Try to match with contribution
            contribution = Contribution.objects.filter(
                membership__chama=chama,
                payment_reference=payment.reference,
            ).first()

            if contribution:
                # Check if amounts match
                if contribution.amount_paid == payment.amount:
                    results['matched'] += 1
                else:
                    results['mismatched'] += 1
                    results['details'].append({
                        'type': 'mismatch',
                        'payment_id': str(payment.id),
                        'contribution_id': str(contribution.id),
                        'payment_amount': payment.amount,
                        'contribution_amount': contribution.amount_paid,
                    })
            else:
                # Orphan payment
                results['orphaned'] += 1
                results['details'].append({
                    'type': 'orphan',
                    'payment_id': str(payment.id),
                    'amount': payment.amount,
                    'reference': payment.reference,
                })

        return results

    @staticmethod
    def get_provider_health() -> dict:
        """
        Get health status of payment providers.
        """
        # This would check actual provider APIs
        # For now, return mock status
        return {
            'mpesa': {
                'status': 'healthy',
                'last_check': timezone.now().isoformat(),
                'response_time_ms': 150,
            },
            'card': {
                'status': 'healthy',
                'last_check': timezone.now().isoformat(),
                'response_time_ms': 200,
            },
            'bank': {
                'status': 'healthy',
                'last_check': timezone.now().isoformat(),
                'response_time_ms': 300,
            },
        }
