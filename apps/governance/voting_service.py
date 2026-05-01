"""
Voting and Governance Service

Manages motions, voting, quorum, and governance archive.
"""

import logging

from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama

logger = logging.getLogger(__name__)


class VotingService:
    """Service for managing voting and governance."""

    @staticmethod
    @transaction.atomic
    def create_motion(
        chama: Chama,
        title: str,
        description: str,
        motion_type: str = 'general',
        voting_start: timezone.datetime = None,
        voting_end: timezone.datetime = None,
        created_by: User = None,
    ) -> dict:
        """
        Create a new motion.
        Returns motion details.
        """
        from apps.governance.models import Motion

        # Validate times
        if voting_start and voting_end and voting_start >= voting_end:
            raise ValueError("Voting start must be before voting end")

        # Create motion
        motion = Motion.objects.create(
            chama=chama,
            title=title,
            description=description,
            motion_type=motion_type,
            voting_start=voting_start,
            voting_end=voting_end,
            created_by=created_by,
            status='pending',
        )

        logger.info(
            f"Motion created: {title} for {chama.name}"
        )

        return {
            'id': str(motion.id),
            'title': title,
            'description': description,
            'motion_type': motion_type,
            'voting_start': voting_start.isoformat() if voting_start else None,
            'voting_end': voting_end.isoformat() if voting_end else None,
            'status': 'pending',
        }

    @staticmethod
    @transaction.atomic
    def open_voting(
        motion_id: str,
        opener: User,
    ) -> tuple[bool, str]:
        """
        Open voting for a motion.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker
        from apps.governance.models import Motion

        try:
            motion = Motion.objects.get(id=motion_id)

            # Check if opener has permission
            if not PermissionChecker.has_permission(
                opener,
                Permission.CAN_CREATE_MEETINGS,  # Using meeting permission
                str(motion.chama.id),
            ):
                return False, "Permission denied"

            if motion.status != 'pending':
                return False, "Motion is not pending"

            # Update motion
            motion.status = 'voting'
            motion.voting_opened_by = opener
            motion.voting_opened_at = timezone.now()
            motion.save(update_fields=[
                'status',
                'voting_opened_by',
                'voting_opened_at',
                'updated_at',
            ])

            logger.info(
                f"Voting opened for motion: {motion_id} by {opener.full_name}"
            )

            return True, "Voting opened"

        except Motion.DoesNotExist:
            return False, "Motion not found"

    @staticmethod
    @transaction.atomic
    def cast_vote(
        motion_id: str,
        user: User,
        vote: str,  # 'yes', 'no', 'abstain'
        reason: str = '',
    ) -> tuple[bool, str]:
        """
        Cast a vote on a motion.
        Returns (success, message).
        """
        from apps.governance.models import Motion, Vote

        try:
            motion = Motion.objects.get(id=motion_id)

            # Check if voting is open
            if motion.status != 'voting':
                return False, "Voting is not open"

            # Check if voting period is valid
            now = timezone.now()
            if motion.voting_start and now < motion.voting_start:
                return False, "Voting has not started yet"

            if motion.voting_end and now > motion.voting_end:
                return False, "Voting has ended"

            # Check if user is a member
            from apps.chama.models import Membership
            if not Membership.objects.filter(
                chama=motion.chama,
                user=user,
                status='active',
            ).exists():
                return False, "You are not a member of this chama"

            # Check if user already voted
            if Vote.objects.filter(motion=motion, user=user).exists():
                return False, "You have already voted"

            # Create vote
            Vote.objects.create(
                motion=motion,
                user=user,
                vote=vote,
                reason=reason,
            )

            logger.info(
                f"Vote cast: {user.full_name} voted {vote} on motion {motion_id}"
            )

            return True, "Vote cast"

        except Motion.DoesNotExist:
            return False, "Motion not found"

    @staticmethod
    @transaction.atomic
    def close_voting(
        motion_id: str,
        closer: User,
    ) -> tuple[bool, str]:
        """
        Close voting for a motion and calculate results.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker
        from apps.governance.models import Motion, Vote

        try:
            motion = Motion.objects.get(id=motion_id)

            # Check if closer has permission
            if not PermissionChecker.has_permission(
                closer,
                Permission.CAN_APPROVE_MINUTES,  # Using minutes permission
                str(motion.chama.id),
            ):
                return False, "Permission denied"

            if motion.status != 'voting':
                return False, "Voting is not open"

            # Calculate results
            votes = Vote.objects.filter(motion=motion)

            yes_count = votes.filter(vote='yes').count()
            no_count = votes.filter(vote='no').count()
            abstain_count = votes.filter(vote='abstain').count()
            total_votes = votes.count()

            # Get total eligible voters
            from apps.chama.models import Membership
            total_members = Membership.objects.filter(
                chama=motion.chama,
                status='active',
            ).count()

            # Calculate quorum (typically 50% + 1)
            quorum_required = (total_members // 2) + 1
            has_quorum = total_votes >= quorum_required

            # Determine result
            if has_quorum:
                if yes_count > no_count:
                    result = 'passed'
                elif no_count > yes_count:
                    result = 'failed'
                else:
                    result = 'tied'
            else:
                result = 'no_quorum'

            # Update motion
            motion.status = 'closed'
            motion.voting_closed_by = closer
            motion.voting_closed_at = timezone.now()
            motion.yes_count = yes_count
            motion.no_count = no_count
            motion.abstain_count = abstain_count
            motion.total_votes = total_votes
            motion.quorum_required = quorum_required
            motion.has_quorum = has_quorum
            motion.result = result
            motion.save(update_fields=[
                'status',
                'voting_closed_by',
                'voting_closed_at',
                'yes_count',
                'no_count',
                'abstain_count',
                'total_votes',
                'quorum_required',
                'has_quorum',
                'result',
                'updated_at',
            ])

            logger.info(
                f"Voting closed for motion: {motion_id} - Result: {result}"
            )

            return True, f"Voting closed - Result: {result}"

        except Motion.DoesNotExist:
            return False, "Motion not found"

    @staticmethod
    def get_motions(
        chama: Chama = None,
        status: str = None,
        motion_type: str = None,
    ) -> list[dict]:
        """
        Get motions with filtering.
        """
        from apps.governance.models import Motion

        queryset = Motion.objects.all()

        if chama:
            queryset = queryset.filter(chama=chama)

        if status:
            queryset = queryset.filter(status=status)

        if motion_type:
            queryset = queryset.filter(motion_type=motion_type)

        motions = queryset.order_by('-created_at')

        return [
            {
                'id': str(motion.id),
                'title': motion.title,
                'description': motion.description,
                'motion_type': motion.motion_type,
                'status': motion.status,
                'result': motion.result,
                'chama_name': motion.chama.name,
                'created_by_name': motion.created_by.full_name if motion.created_by else None,
                'voting_start': motion.voting_start.isoformat() if motion.voting_start else None,
                'voting_end': motion.voting_end.isoformat() if motion.voting_end else None,
                'yes_count': motion.yes_count,
                'no_count': motion.no_count,
                'abstain_count': motion.abstain_count,
                'total_votes': motion.total_votes,
                'created_at': motion.created_at.isoformat(),
            }
            for motion in motions
        ]

    @staticmethod
    def get_motion_detail(motion_id: str) -> dict | None:
        """
        Get detailed motion information.
        """
        from apps.governance.models import Motion, Vote

        try:
            motion = Motion.objects.select_related(
                'chama', 'created_by'
            ).get(id=motion_id)

            # Get votes
            votes = Vote.objects.filter(
                motion=motion,
            ).select_related('user')

            return {
                'id': str(motion.id),
                'title': motion.title,
                'description': motion.description,
                'motion_type': motion.motion_type,
                'status': motion.status,
                'result': motion.result,
                'chama_id': str(motion.chama.id),
                'chama_name': motion.chama.name,
                'created_by_id': str(motion.created_by.id) if motion.created_by else None,
                'created_by_name': motion.created_by.full_name if motion.created_by else None,
                'voting_start': motion.voting_start.isoformat() if motion.voting_start else None,
                'voting_end': motion.voting_end.isoformat() if motion.voting_end else None,
                'yes_count': motion.yes_count,
                'no_count': motion.no_count,
                'abstain_count': motion.abstain_count,
                'total_votes': motion.total_votes,
                'quorum_required': motion.quorum_required,
                'has_quorum': motion.has_quorum,
                'votes': [
                    {
                        'user_id': str(vote.user.id),
                        'user_name': vote.user.full_name,
                        'vote': vote.vote,
                        'reason': vote.reason,
                        'voted_at': vote.created_at.isoformat(),
                    }
                    for vote in votes
                ],
                'created_at': motion.created_at.isoformat(),
                'updated_at': motion.updated_at.isoformat(),
            }

        except Motion.DoesNotExist:
            return None

    @staticmethod
    def get_governance_summary(chama: Chama) -> dict:
        """
        Get governance summary for a chama.
        """
        from django.db.models import Count

        from apps.governance.models import Motion

        summary = Motion.objects.filter(chama=chama).aggregate(
            total=Count('id'),
            pending=Count('id', filter=models.Q(status='pending')),
            voting=Count('id', filter=models.Q(status='voting')),
            closed=Count('id', filter=models.Q(status='closed')),
            passed=Count('id', filter=models.Q(result='passed')),
            failed=Count('id', filter=models.Q(result='failed')),
        )

        return {
            'total_motions': summary['total'] or 0,
            'pending_motions': summary['pending'] or 0,
            'voting_motions': summary['voting'] or 0,
            'closed_motions': summary['closed'] or 0,
            'passed_motions': summary['passed'] or 0,
            'failed_motions': summary['failed'] or 0,
            'pass_rate': (
                (summary['passed'] / summary['closed'] * 100)
                if summary['closed'] > 0 else 0
            ),
        }
