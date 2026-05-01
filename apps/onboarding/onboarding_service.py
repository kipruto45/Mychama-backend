"""
Onboarding and Post-Join Setup Service

Manages onboarding flows, checklists, and guided setup.
"""

import logging

from django.db import transaction

from apps.accounts.models import User
from apps.chama.models import Chama, Membership

logger = logging.getLogger(__name__)


class OnboardingService:
    """Service for managing onboarding and post-join setup."""

    @staticmethod
    def get_onboarding_steps(user: User, chama: Chama = None) -> list[dict]:
        """
        Get onboarding steps for a user.
        Returns list of steps with completion status.
        """
        from apps.onboarding.models import OnboardingProgress

        steps = [
            {
                'id': 'welcome',
                'title': 'Welcome to Digital Chama',
                'description': 'Learn about the platform features',
                'order': 1,
                'completed': False,
            },
            {
                'id': 'profile',
                'title': 'Complete Your Profile',
                'description': 'Add your personal information',
                'order': 2,
                'completed': False,
            },
            {
                'id': 'kyc',
                'title': 'Verify Your Identity',
                'description': 'Complete KYC verification',
                'order': 3,
                'completed': False,
            },
            {
                'id': 'chama',
                'title': 'Join or Create a Chama',
                'description': 'Join an existing chama or create your own',
                'order': 4,
                'completed': False,
            },
            {
                'id': 'contribution',
                'title': 'Make Your First Contribution',
                'description': 'Start building your savings',
                'order': 5,
                'completed': False,
            },
        ]

        # Get progress
        progress = OnboardingProgress.objects.filter(user=user).first()

        if progress:
            for step in steps:
                if step['id'] in progress.completed_steps:
                    step['completed'] = True

        return steps

    @staticmethod
    @transaction.atomic
    def complete_step(
        user: User,
        step_id: str,
        chama: Chama = None,
    ) -> tuple[bool, str]:
        """
        Mark an onboarding step as completed.
        Returns (success, message).
        """
        from apps.onboarding.models import OnboardingProgress

        # Get or create progress
        progress, created = OnboardingProgress.objects.get_or_create(
            user=user,
            defaults={'completed_steps': []},
        )

        # Add step to completed
        if step_id not in progress.completed_steps:
            progress.completed_steps.append(step_id)
            progress.save(update_fields=['completed_steps', 'updated_at'])

        logger.info(
            f"Onboarding step completed: {step_id} by {user.full_name}"
        )

        return True, "Step completed"

    @staticmethod
    def get_onboarding_progress(user: User) -> dict:
        """
        Get onboarding progress for a user.
        """
        from apps.onboarding.models import OnboardingProgress

        steps = OnboardingService.get_onboarding_steps(user)
        total_steps = len(steps)
        completed_steps = sum(1 for step in steps if step['completed'])

        progress = OnboardingProgress.objects.filter(user=user).first()

        return {
            'total_steps': total_steps,
            'completed_steps': completed_steps,
            'progress_percentage': (completed_steps / total_steps * 100) if total_steps > 0 else 0,
            'is_complete': completed_steps >= total_steps,
            'steps': steps,
            'last_updated': progress.updated_at.isoformat() if progress else None,
        }

    @staticmethod
    def get_post_join_checklist(chama: Chama, user: User) -> list[dict]:
        """
        Get post-join checklist for a new member.
        """
        checklist = [
            {
                'id': 'view_chama',
                'title': 'View Chama Details',
                'description': 'Learn about your chama',
                'completed': False,
            },
            {
                'id': 'read_rules',
                'title': 'Read Chama Rules',
                'description': 'Understand the chama constitution',
                'completed': False,
            },
            {
                'id': 'make_contribution',
                'title': 'Make First Contribution',
                'description': 'Start contributing to your chama',
                'completed': False,
            },
            {
                'id': 'attend_meeting',
                'title': 'Attend First Meeting',
                'description': 'Join your first chama meeting',
                'completed': False,
            },
            {
                'id': 'invite_members',
                'title': 'Invite Other Members',
                'description': 'Grow your chama',
                'completed': False,
            },
        ]

        # Check completion status
        from apps.finance.models import Contribution
        from apps.meetings.models import Attendance

        # Check if user has made a contribution
        has_contribution = Contribution.objects.filter(
            membership__chama=chama,
            membership__user=user,
        ).exists()

        # Check if user has attended a meeting
        has_attendance = Attendance.objects.filter(
            meeting__chama=chama,
            user=user,
        ).exists()

        for item in checklist:
            if item['id'] == 'make_contribution' and has_contribution:
                item['completed'] = True
            elif item['id'] == 'attend_meeting' and has_attendance:
                item['completed'] = True

        return checklist

    @staticmethod
    def get_creator_onboarding_steps(chama: Chama) -> list[dict]:
        """
        Get onboarding steps for a chama creator.
        """
        steps = [
            {
                'id': 'setup_chama',
                'title': 'Set Up Your Chama',
                'description': 'Configure basic chama settings',
                'order': 1,
                'completed': True,  # Already created
            },
            {
                'id': 'invite_members',
                'title': 'Invite Members',
                'description': 'Invite people to join your chama',
                'order': 2,
                'completed': False,
            },
            {
                'id': 'set_contribution',
                'title': 'Set Contribution Rules',
                'description': 'Define contribution amounts and schedules',
                'order': 3,
                'completed': False,
            },
            {
                'id': 'schedule_meeting',
                'title': 'Schedule First Meeting',
                'description': 'Plan your first chama meeting',
                'order': 4,
                'completed': False,
            },
            {
                'id': 'review_settings',
                'title': 'Review Settings',
                'description': 'Finalize your chama configuration',
                'order': 5,
                'completed': False,
            },
        ]

        # Check completion status
        from apps.finance.models import ContributionSettings
        from apps.meetings.models import Meeting

        # Check if members have been invited
        member_count = Membership.objects.filter(chama=chama).count()
        if member_count > 1:
            steps[1]['completed'] = True

        # Check if contribution settings exist
        has_contribution_settings = ContributionSettings.objects.filter(chama=chama).exists()
        if has_contribution_settings:
            steps[2]['completed'] = True

        # Check if meeting has been scheduled
        has_meeting = Meeting.objects.filter(chama=chama).exists()
        if has_meeting:
            steps[3]['completed'] = True

        return steps

    @staticmethod
    def get_guided_setup_wizard(chama: Chama, user: User) -> dict:
        """
        Get guided setup wizard data.
        """
        from apps.chama.permissions import Permission, PermissionChecker

        # Check if user is creator
        is_creator = PermissionChecker.has_permission(
            user,
            Permission.CAN_MANAGE_CHAMA_SETTINGS,
            str(chama.id),
        )

        if is_creator:
            steps = OnboardingService.get_creator_onboarding_steps(chama)
        else:
            steps = OnboardingService.get_post_join_checklist(chama, user)

        completed = sum(1 for step in steps if step['completed'])
        total = len(steps)

        return {
            'steps': steps,
            'progress': {
                'completed': completed,
                'total': total,
                'percentage': (completed / total * 100) if total > 0 else 0,
            },
            'is_complete': completed >= total,
        }

    @staticmethod
    def get_suggested_defaults(chama_type: str) -> dict:
        """
        Get suggested defaults for chama setup.
        """
        defaults = {
            'savings': {
                'contribution_amount': 1000,
                'contribution_frequency': 'monthly',
                'meeting_frequency': 'monthly',
                'max_members': 20,
                'loan_enabled': True,
                'loan_multiplier': 3,
            },
            'investment': {
                'contribution_amount': 5000,
                'contribution_frequency': 'monthly',
                'meeting_frequency': 'monthly',
                'max_members': 15,
                'loan_enabled': False,
                'loan_multiplier': 0,
            },
            'welfare': {
                'contribution_amount': 500,
                'contribution_frequency': 'monthly',
                'meeting_frequency': 'quarterly',
                'max_members': 50,
                'loan_enabled': True,
                'loan_multiplier': 2,
            },
        }

        return defaults.get(chama_type, defaults['savings'])

    @staticmethod
    def get_tooltips() -> dict:
        """
        Get tooltips for onboarding.
        """
        return {
            'contribution_amount': 'The amount each member contributes per period',
            'contribution_frequency': 'How often contributions are made',
            'meeting_frequency': 'How often meetings are held',
            'max_members': 'Maximum number of members allowed',
            'loan_enabled': 'Whether members can take loans',
            'loan_multiplier': 'Maximum loan amount as multiple of contributions',
            'grace_period': 'Days after due date before late fee applies',
            'late_fee': 'Fee charged for late contributions',
        }

    @staticmethod
    def get_next_action(user: User, chama: Chama = None) -> dict | None:
        """
        Get the next recommended action for a user.
        """
        # Get onboarding progress
        progress = OnboardingService.get_onboarding_progress(user)

        if not progress['is_complete']:
            # Find next incomplete step
            for step in progress['steps']:
                if not step['completed']:
                    return {
                        'type': 'onboarding',
                        'step_id': step['id'],
                        'title': step['title'],
                        'description': step['description'],
                    }

        # If onboarding is complete, suggest next action
        if chama:
            checklist = OnboardingService.get_post_join_checklist(chama, user)
            for item in checklist:
                if not item['completed']:
                    return {
                        'type': 'checklist',
                        'item_id': item['id'],
                        'title': item['title'],
                        'description': item['description'],
                    }

        return None
