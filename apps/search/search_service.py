"""
Search, Filters, and Pagination Service

Manages search functionality, filtering, and pagination across the platform.
"""

import logging

from django.db.models import Q

from apps.accounts.models import User
from apps.chama.models import Chama, Membership

logger = logging.getLogger(__name__)


class SearchService:
    """Service for managing search, filters, and pagination."""

    @staticmethod
    def search_members(
        chama: Chama,
        query: str = None,
        filters: dict = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """
        Search and filter members in a chama.
        Returns paginated results.
        """
        queryset = Membership.objects.filter(
            chama=chama,
        ).select_related('user')

        # Apply search query
        if query:
            queryset = queryset.filter(
                Q(user__full_name__icontains=query) |
                Q(user__phone__icontains=query) |
                Q(user__email__icontains=query)
            )

        # Apply filters
        if filters:
            if 'role' in filters:
                queryset = queryset.filter(role=filters['role'])

            if 'status' in filters:
                queryset = queryset.filter(status=filters['status'])

            if 'is_active' in filters:
                queryset = queryset.filter(is_active=filters['is_active'])

        # Order by
        queryset = queryset.order_by('-joined_at')

        # Paginate
        total = queryset.count()
        start = (page - 1) * page_size
        end = start + page_size
        members = queryset[start:end]

        return {
            'results': [
                {
                    'id': str(member.id),
                    'user_id': str(member.user.id),
                    'user_name': member.user.full_name,
                    'user_phone': member.user.phone,
                    'user_email': member.user.email,
                    'role': member.role,
                    'status': member.status,
                    'is_active': member.is_active,
                    'joined_at': member.joined_at.isoformat() if member.joined_at else None,
                }
                for member in members
            ],
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total': total,
                'total_pages': (total + page_size - 1) // page_size,
            },
        }

    @staticmethod
    def search_contributions(
        chama: Chama,
        query: str = None,
        filters: dict = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """
        Search and filter contributions in a chama.
        Returns paginated results.
        """
        from apps.finance.models import Contribution

        queryset = Contribution.objects.filter(
            membership__chama=chama,
        ).select_related('membership__user')

        # Apply search query
        if query:
            queryset = queryset.filter(
                Q(membership__user__full_name__icontains=query) |
                Q(reference__icontains=query)
            )

        # Apply filters
        if filters:
            if 'status' in filters:
                queryset = queryset.filter(status=filters['status'])

            if 'contribution_type' in filters:
                queryset = queryset.filter(contribution_type=filters['contribution_type'])

            if 'date_from' in filters:
                queryset = queryset.filter(created_at__gte=filters['date_from'])

            if 'date_to' in filters:
                queryset = queryset.filter(created_at__lte=filters['date_to'])

            if 'amount_min' in filters:
                queryset = queryset.filter(amount__gte=filters['amount_min'])

            if 'amount_max' in filters:
                queryset = queryset.filter(amount__lte=filters['amount_max'])

        # Order by
        queryset = queryset.order_by('-created_at')

        # Paginate
        total = queryset.count()
        start = (page - 1) * page_size
        end = start + page_size
        contributions = queryset[start:end]

        return {
            'results': [
                {
                    'id': str(contrib.id),
                    'member_name': contrib.membership.user.full_name,
                    'amount': contrib.amount,
                    'amount_paid': contrib.amount_paid,
                    'contribution_type': contrib.contribution_type,
                    'status': contrib.status,
                    'reference': contrib.reference,
                    'created_at': contrib.created_at.isoformat(),
                }
                for contrib in contributions
            ],
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total': total,
                'total_pages': (total + page_size - 1) // page_size,
            },
        }

    @staticmethod
    def search_loans(
        chama: Chama,
        query: str = None,
        filters: dict = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """
        Search and filter loans in a chama.
        Returns paginated results.
        """
        from apps.finance.models import Loan

        queryset = Loan.objects.filter(
            chama=chama,
        ).select_related('user')

        # Apply search query
        if query:
            queryset = queryset.filter(
                Q(user__full_name__icontains=query) |
                Q(reference__icontains=query)
            )

        # Apply filters
        if filters:
            if 'status' in filters:
                queryset = queryset.filter(status=filters['status'])

            if 'date_from' in filters:
                queryset = queryset.filter(created_at__gte=filters['date_from'])

            if 'date_to' in filters:
                queryset = queryset.filter(created_at__lte=filters['date_to'])

            if 'amount_min' in filters:
                queryset = queryset.filter(principal_amount__gte=filters['amount_min'])

            if 'amount_max' in filters:
                queryset = queryset.filter(principal_amount__lte=filters['amount_max'])

        # Order by
        queryset = queryset.order_by('-created_at')

        # Paginate
        total = queryset.count()
        start = (page - 1) * page_size
        end = start + page_size
        loans = queryset[start:end]

        return {
            'results': [
                {
                    'id': str(loan.id),
                    'borrower_name': loan.user.full_name,
                    'principal_amount': loan.principal_amount,
                    'total_amount': loan.total_amount,
                    'amount_repaid': loan.amount_repaid,
                    'status': loan.status,
                    'reference': loan.reference,
                    'created_at': loan.created_at.isoformat(),
                }
                for loan in loans
            ],
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total': total,
                'total_pages': (total + page_size - 1) // page_size,
            },
        }

    @staticmethod
    def search_meetings(
        chama: Chama,
        query: str = None,
        filters: dict = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """
        Search and filter meetings in a chama.
        Returns paginated results.
        """
        from apps.meetings.models import Meeting

        queryset = Meeting.objects.filter(chama=chama)

        # Apply search query
        if query:
            queryset = queryset.filter(
                Q(title__icontains=query) |
                Q(description__icontains=query) |
                Q(location__icontains=query)
            )

        # Apply filters
        if filters:
            if 'status' in filters:
                queryset = queryset.filter(status=filters['status'])

            if 'meeting_type' in filters:
                queryset = queryset.filter(meeting_type=filters['meeting_type'])

            if 'date_from' in filters:
                queryset = queryset.filter(start_time__gte=filters['date_from'])

            if 'date_to' in filters:
                queryset = queryset.filter(start_time__lte=filters['date_to'])

        # Order by
        queryset = queryset.order_by('-start_time')

        # Paginate
        total = queryset.count()
        start = (page - 1) * page_size
        end = start + page_size
        meetings = queryset[start:end]

        return {
            'results': [
                {
                    'id': str(meeting.id),
                    'title': meeting.title,
                    'description': meeting.description,
                    'start_time': meeting.start_time.isoformat(),
                    'end_time': meeting.end_time.isoformat(),
                    'location': meeting.location,
                    'meeting_type': meeting.meeting_type,
                    'status': meeting.status,
                    'attendee_count': meeting.attendees.count(),
                }
                for meeting in meetings
            ],
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total': total,
                'total_pages': (total + page_size - 1) // page_size,
            },
        }

    @staticmethod
    def search_transactions(
        chama: Chama,
        query: str = None,
        filters: dict = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """
        Search and filter transactions in a chama.
        Returns paginated results.
        """
        from apps.finance.models import Transaction

        queryset = Transaction.objects.filter(chama=chama)

        # Apply search query
        if query:
            queryset = queryset.filter(
                Q(reference__icontains=query) |
                Q(description__icontains=query) |
                Q(notes__icontains=query)
            )

        # Apply filters
        if filters:
            if 'transaction_type' in filters:
                queryset = queryset.filter(transaction_type=filters['transaction_type'])

            if 'payment_method' in filters:
                queryset = queryset.filter(payment_method=filters['payment_method'])

            if 'date_from' in filters:
                queryset = queryset.filter(created_at__gte=filters['date_from'])

            if 'date_to' in filters:
                queryset = queryset.filter(created_at__lte=filters['date_to'])

            if 'amount_min' in filters:
                queryset = queryset.filter(amount__gte=filters['amount_min'])

            if 'amount_max' in filters:
                queryset = queryset.filter(amount__lte=filters['amount_max'])

        # Order by
        queryset = queryset.order_by('-created_at')

        # Paginate
        total = queryset.count()
        start = (page - 1) * page_size
        end = start + page_size
        transactions = queryset[start:end]

        return {
            'results': [
                {
                    'id': str(txn.id),
                    'transaction_type': txn.transaction_type,
                    'amount': txn.amount,
                    'payment_method': txn.payment_method,
                    'reference': txn.reference,
                    'description': txn.description,
                    'created_at': txn.created_at.isoformat(),
                }
                for txn in transactions
            ],
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total': total,
                'total_pages': (total + page_size - 1) // page_size,
            },
        }

    @staticmethod
    def universal_search(
        user: User,
        query: str,
        chama: Chama = None,
        limit: int = 10,
    ) -> dict:
        """
        Universal search across all entities for a user.
        Returns results from different categories.
        """
        results = {
            'members': [],
            'contributions': [],
            'loans': [],
            'meetings': [],
            'transactions': [],
        }

        # Get user's chamas
        user_chamas = Membership.objects.filter(
            user=user,
            status='active',
        ).values_list('chama_id', flat=True)

        if chama:
            if chama.id not in user_chamas:
                return results
            chamas = [chama]
        else:
            chamas = Chama.objects.filter(id__in=user_chamas)

        # Search members
        for chama in chamas:
            members = SearchService.search_members(
                chama=chama,
                query=query,
                page=1,
                page_size=limit,
            )
            results['members'].extend(members['results'])

        # Search contributions
        for chama in chamas:
            contributions = SearchService.search_contributions(
                chama=chama,
                query=query,
                page=1,
                page_size=limit,
            )
            results['contributions'].extend(contributions['results'])

        # Search loans
        for chama in chamas:
            loans = SearchService.search_loans(
                chama=chama,
                query=query,
                page=1,
                page_size=limit,
            )
            results['loans'].extend(loans['results'])

        # Search meetings
        for chama in chamas:
            meetings = SearchService.search_meetings(
                chama=chama,
                query=query,
                page=1,
                page_size=limit,
            )
            results['meetings'].extend(meetings['results'])

        # Limit results per category
        for key in results:
            results[key] = results[key][:limit]

        return results

    @staticmethod
    def get_filter_options(chama: Chama) -> dict:
        """
        Get available filter options for a chama.
        """

        return {
            'contribution_statuses': ['pending', 'paid', 'overdue', 'partial'],
            'contribution_types': ['regular', 'special', 'emergency', 'late_fee'],
            'loan_statuses': ['pending', 'approved', 'active', 'overdue', 'repaid', 'defaulted'],
            'meeting_statuses': ['scheduled', 'completed', 'cancelled'],
            'meeting_types': ['regular', 'special', 'emergency', 'annual'],
            'transaction_types': ['contribution', 'loan_disbursement', 'loan_repayment', 'expense', 'fine', 'withdrawal'],
            'payment_methods': ['mpesa', 'card', 'bank', 'cash'],
            'member_roles': ['creator', 'admin', 'treasurer', 'secretary', 'member'],
            'member_statuses': ['active', 'suspended', 'removed'],
        }
