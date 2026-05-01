from __future__ import annotations

"""
Onboarding Service

Manages onboarding flows, checklists, and progress tracking
for new members and chama creators.
"""

import logging
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama, Membership

if TYPE_CHECKING:
    from apps.accounts.models import OnboardingProgress

logger = logging.getLogger(__name__)


class OnboardingService:
    """Service for managing user onboarding."""

    # Onboarding step definitions
    MEMBER_STEPS = [
        'complete_profile',
        'verify_phone',
        'view_chama_details',
        'make_first_contribution',
        'attend_first_meeting',
        'explore_dashboard',
    ]

    CREATOR_STEPS = [
        'basic_details',
        'contribution_setup',
        'finance_settings',
        'meeting_settings',
        'membership_rules',
        'invite_members',
        'create_first_meeting',
        'review_settings',
    ]

    @staticmethod
    def get_onboarding_progress(
        user: User,
        chama: Chama | None = None,
    ) -> dict:
        """
        Get onboarding progress for user.
        Returns completed steps and progress percentage.
        """
        from apps.accounts.models import OnboardingProgress

        # Get or create progress record
        progress, created = OnboardingProgress.objects.get_or_create(
            user=user,
            chama=chama,
            defaults={
                'completed_steps': [],
                'skipped': False,
            }
        )

        # Determine which steps to use
        if chama:
            # Check if user is creator
            is_creator = Membership.objects.filter(
                user=user,
                chama=chama,
                role='creator',
            ).exists()

            steps = OnboardingService.CREATOR_STEPS if is_creator else OnboardingService.MEMBER_STEPS
        else:
            steps = OnboardingService.MEMBER_STEPS

        # Calculate progress
        completed = progress.completed_steps or []
        total_steps = len(steps)
        completed_count = len([s for s in steps if s in completed])
        progress_pct = round((completed_count / total_steps) * 100) if total_steps > 0 else 0

        return {
            'completed_steps': completed,
            'total_steps': total_steps,
            'completed_count': completed_count,
            'progress': progress_pct,
            'is_complete': progress_pct >= 100,
            'skipped': progress.skipped,
        }

    @staticmethod
    @transaction.atomic
    def complete_step(
        user: User,
        step_id: str,
        chama: Chama | None = None,
    ) -> bool:
        """
        Mark an onboarding step as completed.
        """
        from apps.accounts.models import OnboardingProgress

        progress, created = OnboardingProgress.objects.get_or_create(
            user=user,
            chama=chama,
            defaults={
                'completed_steps': [],
                'skipped': False,
            }
        )

        # Add step to completed list if not already there
        completed = progress.completed_steps or []
        if step_id not in completed:
            completed.append(step_id)
            progress.completed_steps = completed
            progress.save(update_fields=['completed_steps', 'updated_at'])

            logger.info(f"Onboarding step '{step_id}' completed for user {user.id}")

            # Check if all steps are completed
            OnboardingService._check_completion(user, chama, progress)

        return True

    @staticmethod
    def _check_completion(
        user: User,
        chama: Chama | None,
        progress: OnboardingProgress,
    ) -> None:
        """Check if onboarding is complete and send notification."""
        # Determine which steps to use
        if chama:
            is_creator = Membership.objects.filter(
                user=user,
                chama=chama,
                role='creator',
            ).exists()
            steps = OnboardingService.CREATOR_STEPS if is_creator else OnboardingService.MEMBER_STEPS
        else:
            steps = OnboardingService.MEMBER_STEPS

        completed = progress.completed_steps or []
        all_completed = all(step in completed for step in steps)

        if all_completed and not progress.completed_at:
            progress.completed_at = timezone.now()
            progress.save(update_fields=['completed_at', 'updated_at'])

            logger.info(f"Onboarding completed for user {user.id}")

            # TODO: Send completion notification
            # from apps.notifications.services import NotificationService
            # NotificationService.send_onboarding_complete(user, chama)

    @staticmethod
    @transaction.atomic
    def skip_onboarding(
        user: User,
        chama: Chama | None = None,
    ) -> bool:
        """
        Skip onboarding for user.
        """
        from apps.accounts.models import OnboardingProgress

        progress, created = OnboardingProgress.objects.get_or_create(
            user=user,
            chama=chama,
            defaults={
                'completed_steps': [],
                'skipped': True,
                'skipped_at': timezone.now(),
            }
        )

        if not created:
            progress.skipped = True
            progress.skipped_at = timezone.now()
            progress.save(update_fields=['skipped', 'skipped_at', 'updated_at'])

        logger.info(f"Onboarding skipped for user {user.id}")
        return True

    @staticmethod
    def reset_onboarding(
        user: User,
        chama: Chama | None = None,
    ) -> bool:
        """
        Reset onboarding progress for user.
        """
        from apps.accounts.models import OnboardingProgress

        try:
            progress = OnboardingProgress.objects.get(
                user=user,
                chama=chama,
            )
            progress.completed_steps = []
            progress.skipped = False
            progress.completed_at = None
            progress.skipped_at = None
            progress.save(update_fields=[
                'completed_steps',
                'skipped',
                'completed_at',
                'skipped_at',
                'updated_at',
            ])

            logger.info(f"Onboarding reset for user {user.id}")
            return True
        except OnboardingProgress.DoesNotExist:
            return False

    @staticmethod
    def get_next_action(
        user: User,
        chama: Chama | None = None,
    ) -> dict | None:
        """
        Get the next recommended onboarding action for user.
        """
        progress = OnboardingService.get_onboarding_progress(user, chama)

        if progress['is_complete']:
            return None

        # Determine which steps to use
        if chama:
            is_creator = Membership.objects.filter(
                user=user,
                chama=chama,
                role='creator',
            ).exists()
            steps = OnboardingService.CREATOR_STEPS if is_creator else OnboardingService.MEMBER_STEPS
        else:
            steps = OnboardingService.MEMBER_STEPS

        # Find first incomplete step
        completed = progress['completed_steps']
        for step in steps:
            if step not in completed:
                return {
                    'step_id': step,
                    'title': OnboardingService._get_step_title(step),
                    'description': OnboardingService._get_step_description(step),
                    'screen': OnboardingService._get_step_screen(step),
                }

        return None

    @staticmethod
    def _get_step_title(step_id: str) -> str:
        """Get human-readable title for step."""
        titles = {
            'complete_profile': 'Complete Your Profile',
            'verify_phone': 'Verify Your Phone Number',
            'view_chama_details': 'View Chama Details',
            'make_first_contribution': 'Make Your First Contribution',
            'attend_first_meeting': 'Attend Your First Meeting',
            'explore_dashboard': 'Explore Your Dashboard',
            'basic_details': 'Add Basic Details',
            'contribution_setup': 'Set Up Contributions',
            'finance_settings': 'Configure Finance Settings',
            'meeting_settings': 'Set Meeting Preferences',
            'membership_rules': 'Define Membership Rules',
            'invite_members': 'Invite Your First Members',
            'create_first_meeting': 'Schedule First Meeting',
            'review_settings': 'Review All Settings',
        }
        return titles.get(step_id, step_id.replace('_', ' ').title())

    @staticmethod
    def _get_step_description(step_id: str) -> str:
        """Get description for step."""
        descriptions = {
            'complete_profile': 'Add your personal information and photo',
            'verify_phone': 'Confirm your phone number with OTP',
            'view_chama_details': 'Learn about your chama\'s rules and structure',
            'make_first_contribution': 'Start building your savings with your first contribution',
            'attend_first_meeting': 'Join an upcoming meeting to meet other members',
            'explore_dashboard': 'See your savings, contributions, and chama insights',
            'basic_details': 'Set chama name, description, and profile image',
            'contribution_setup': 'Define contribution amounts, frequency, and due dates',
            'finance_settings': 'Set up accounts, categories, and financial rules',
            'meeting_settings': 'Configure meeting frequency, reminders, and agenda',
            'membership_rules': 'Set join policies, roles, and member limits',
            'invite_members': 'Send invitations to people you want to join',
            'create_first_meeting': 'Set up your first chama meeting',
            'review_settings': 'Double-check all configurations before launch',
        }
        return descriptions.get(step_id, '')

    @staticmethod
    def _get_step_screen(step_id: str) -> str:
        """Get screen name for step."""
        screens = {
            'complete_profile': 'EditProfile',
            'verify_phone': 'OTPVerification',
            'view_chama_details': 'ChamaDetail',
            'make_first_contribution': 'MakeContribution',
            'attend_first_meeting': 'Meetings',
            'explore_dashboard': 'Dashboard',
            'basic_details': 'CreateChama',
            'contribution_setup': 'ChamaSettings',
            'finance_settings': 'ChamaSettings',
            'meeting_settings': 'ChamaSettings',
            'membership_rules': 'ChamaSettings',
            'invite_members': 'InviteMember',
            'create_first_meeting': 'CreateMeeting',
            'review_settings': 'ChamaSettings',
        }
        return screens.get(step_id, 'Dashboard')
