"""
Chama Creation and Setup Service

Manages chama creation wizard, setup, and configuration.
"""

import logging

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama, Membership

logger = logging.getLogger(__name__)


class ChamaCreationService:
    """Service for managing chama creation and setup."""

    @staticmethod
    @transaction.atomic
    def create_chama(
        creator: User,
        name: str,
        description: str,
        chama_type: str,
        contribution_amount: float,
        contribution_frequency: str,
        meeting_frequency: str,
        max_members: int = 20,
        **kwargs,
    ) -> dict:
        """
        Create a new chama with all necessary setup.
        Returns chama details.
        """
        from apps.chama.models import Chama, ChamaStatus
        from apps.finance.models import Account, ContributionSettings
        from apps.settings.models import ChamaSettings

        # Create chama
        chama = Chama.objects.create(
            name=name,
            description=description,
            chama_type=chama_type,
            created_by=creator,
            status=ChamaStatus.ACTIVE,
            max_members=max_members,
        )

        # Create membership for creator
        Membership.objects.create(
            user=creator,
            chama=chama,
            role='creator',
            status='active',
            is_active=True,
            is_approved=True,
            joined_at=timezone.now(),
        )

        # Create main account
        Account.objects.create(
            chama=chama,
            name='Main Account',
            account_type='main',
            balance=0,
        )

        # Create contribution settings
        ContributionSettings.objects.create(
            chama=chama,
            amount=contribution_amount,
            frequency=contribution_frequency,
            due_day=kwargs.get('due_day', 1),
            grace_period_days=kwargs.get('grace_period_days', 7),
            late_fee_percentage=kwargs.get('late_fee_percentage', 5),
        )

        # Create chama settings
        ChamaSettings.objects.create(
            chama=chama,
            meeting_frequency=meeting_frequency,
            meeting_day_of_week=kwargs.get('meeting_day_of_week', 6),
            meeting_time=kwargs.get('meeting_time', '10:00'),
            meeting_duration=kwargs.get('meeting_duration', 120),
            loans_enabled=kwargs.get('loans_enabled', True),
            max_loan_multiplier=kwargs.get('max_loan_multiplier', 3),
            interest_rate=kwargs.get('interest_rate', 10),
            max_term_months=kwargs.get('max_term_months', 12),
            join_policy=kwargs.get('join_policy', 'approval_required'),
            require_kyc=kwargs.get('require_kyc', True),
        )

        logger.info(f"Chama created: {name} by {creator.full_name}")

        return {
            'id': str(chama.id),
            'name': name,
            'description': description,
            'chama_type': chama_type,
            'status': 'active',
            'created_at': chama.created_at.isoformat(),
        }

    @staticmethod
    def get_creation_wizard_steps() -> list[dict]:
        """
        Get chama creation wizard steps.
        """
        return [
            {
                'id': 'basic_info',
                'title': 'Basic Information',
                'description': 'Enter chama name and description',
                'order': 1,
                'fields': ['name', 'description', 'chama_type'],
            },
            {
                'id': 'contribution',
                'title': 'Contribution Setup',
                'description': 'Set contribution amounts and schedule',
                'order': 2,
                'fields': ['contribution_amount', 'contribution_frequency', 'due_day'],
            },
            {
                'id': 'meetings',
                'title': 'Meeting Schedule',
                'description': 'Configure meeting frequency and time',
                'order': 3,
                'fields': ['meeting_frequency', 'meeting_day_of_week', 'meeting_time'],
            },
            {
                'id': 'loans',
                'title': 'Loan Settings',
                'description': 'Configure loan policies',
                'order': 4,
                'fields': ['loans_enabled', 'max_loan_multiplier', 'interest_rate'],
            },
            {
                'id': 'membership',
                'title': 'Membership Rules',
                'description': 'Set membership policies',
                'order': 5,
                'fields': ['max_members', 'join_policy', 'require_kyc'],
            },
            {
                'id': 'review',
                'title': 'Review & Create',
                'description': 'Review settings and create chama',
                'order': 6,
                'fields': [],
            },
        ]

    @staticmethod
    def get_chama_types() -> list[dict]:
        """
        Get available chama types.
        """
        return [
            {
                'id': 'savings',
                'name': 'Savings Group',
                'description': 'Regular savings and contributions',
            },
            {
                'id': 'investment',
                'name': 'Investment Club',
                'description': 'Investment-focused group',
            },
            {
                'id': 'welfare',
                'name': 'Welfare Group',
                'description': 'Social welfare and support',
            },
            {
                'id': 'merry_go_round',
                'name': 'Merry-Go-Round',
                'description': 'Rotating savings and credit',
            },
        ]

    @staticmethod
    def get_contribution_frequencies() -> list[dict]:
        """
        Get available contribution frequencies.
        """
        return [
            {'id': 'weekly', 'name': 'Weekly'},
            {'id': 'biweekly', 'name': 'Bi-weekly'},
            {'id': 'monthly', 'name': 'Monthly'},
            {'id': 'quarterly', 'name': 'Quarterly'},
        ]

    @staticmethod
    def get_meeting_frequencies() -> list[dict]:
        """
        Get available meeting frequencies.
        """
        return [
            {'id': 'weekly', 'name': 'Weekly'},
            {'id': 'biweekly', 'name': 'Bi-weekly'},
            {'id': 'monthly', 'name': 'Monthly'},
            {'id': 'quarterly', 'name': 'Quarterly'},
        ]

    @staticmethod
    def get_join_policies() -> list[dict]:
        """
        Get available join policies.
        """
        return [
            {
                'id': 'open',
                'name': 'Open',
                'description': 'Anyone can join',
            },
            {
                'id': 'approval_required',
                'name': 'Approval Required',
                'description': 'Admin approval needed',
            },
            {
                'id': 'invite_only',
                'name': 'Invite Only',
                'description': 'Only invited members can join',
            },
        ]

    @staticmethod
    def validate_chama_data(data: dict) -> tuple[bool, list[str]]:
        """
        Validate chama creation data.
        Returns (is_valid, errors).
        """
        errors = []

        # Required fields
        required_fields = ['name', 'description', 'chama_type', 'contribution_amount', 'contribution_frequency']
        for field in required_fields:
            if field not in data or not data[field]:
                errors.append(f"Missing required field: {field}")

        # Validate name
        if 'name' in data and len(data['name']) < 3:
            errors.append("Chama name must be at least 3 characters")

        # Validate contribution amount
        if 'contribution_amount' in data:
            try:
                amount = float(data['contribution_amount'])
                if amount <= 0:
                    errors.append("Contribution amount must be greater than 0")
            except (ValueError, TypeError):
                errors.append("Invalid contribution amount")

        # Validate max members
        if 'max_members' in data:
            try:
                max_members = int(data['max_members'])
                if max_members < 2:
                    errors.append("Maximum members must be at least 2")
            except (ValueError, TypeError):
                errors.append("Invalid max members")

        return len(errors) == 0, errors

    @staticmethod
    def get_suggested_defaults(chama_type: str) -> dict:
        """
        Get suggested defaults for a chama type.
        """
        defaults = {
            'savings': {
                'contribution_amount': 1000,
                'contribution_frequency': 'monthly',
                'meeting_frequency': 'monthly',
                'max_members': 20,
                'loans_enabled': True,
                'max_loan_multiplier': 3,
                'interest_rate': 10,
                'grace_period_days': 7,
                'late_fee_percentage': 5,
            },
            'investment': {
                'contribution_amount': 5000,
                'contribution_frequency': 'monthly',
                'meeting_frequency': 'monthly',
                'max_members': 15,
                'loans_enabled': False,
                'max_loan_multiplier': 0,
                'interest_rate': 0,
                'grace_period_days': 7,
                'late_fee_percentage': 5,
            },
            'welfare': {
                'contribution_amount': 500,
                'contribution_frequency': 'monthly',
                'meeting_frequency': 'quarterly',
                'max_members': 50,
                'loans_enabled': True,
                'max_loan_multiplier': 2,
                'interest_rate': 5,
                'grace_period_days': 14,
                'late_fee_percentage': 0,
            },
            'merry_go_round': {
                'contribution_amount': 2000,
                'contribution_frequency': 'monthly',
                'meeting_frequency': 'monthly',
                'max_members': 12,
                'loans_enabled': False,
                'max_loan_multiplier': 0,
                'interest_rate': 0,
                'grace_period_days': 7,
                'late_fee_percentage': 10,
            },
        }

        return defaults.get(chama_type, defaults['savings'])

    @staticmethod
    def get_chama_summary(chama: Chama) -> dict:
        """
        Get chama summary for review.
        """
        from apps.finance.models import Account, ContributionSettings
        from apps.settings.models import ChamaSettings

        account = Account.objects.filter(chama=chama, account_type='main').first()
        contribution_settings = ContributionSettings.objects.filter(chama=chama).first()
        chama_settings = ChamaSettings.objects.filter(chama=chama).first()

        return {
            'id': str(chama.id),
            'name': chama.name,
            'description': chama.description,
            'chama_type': chama.chama_type,
            'status': chama.status,
            'member_count': chama.memberships.filter(status='active').count(),
            'balance': account.balance if account else 0,
            'contribution': {
                'amount': contribution_settings.amount if contribution_settings else 0,
                'frequency': contribution_settings.frequency if contribution_settings else 'monthly',
            },
            'settings': {
                'meeting_frequency': chama_settings.meeting_frequency if chama_settings else 'monthly',
                'loans_enabled': chama_settings.loans_enabled if chama_settings else True,
                'max_members': chama.max_members,
            },
            'created_at': chama.created_at.isoformat(),
        }
