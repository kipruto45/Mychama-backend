"""
Constitution and Policy Center Service

Manages policy models, versioning, and member acknowledgement.
"""

import logging

from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama, Membership

logger = logging.getLogger(__name__)


class PolicyService:
    """Service for managing constitution and policies."""

    @staticmethod
    @transaction.atomic
    def create_policy(
        chama: Chama,
        policy_type: str,
        title: str,
        content: str,
        created_by: User,
        effective_date: timezone.datetime = None,
    ) -> dict:
        """
        Create a new policy.
        Returns policy details.
        """
        from apps.policy.models import Policy

        # Create policy
        policy = Policy.objects.create(
            chama=chama,
            policy_type=policy_type,
            title=title,
            content=content,
            created_by=created_by,
            effective_date=effective_date or timezone.now(),
            version=1,
            status='draft',
        )

        logger.info(
            f"Policy created: {title} for {chama.name}"
        )

        return {
            'id': str(policy.id),
            'policy_type': policy_type,
            'title': title,
            'version': 1,
            'status': 'draft',
            'effective_date': policy.effective_date.isoformat(),
        }

    @staticmethod
    @transaction.atomic
    def update_policy(
        policy_id: str,
        updater: User,
        **kwargs,
    ) -> tuple[bool, str]:
        """
        Update a policy (creates new version).
        Returns (success, message).
        """
        from apps.policy.models import Policy

        try:
            old_policy = Policy.objects.get(id=policy_id)

            # Check permission
            if old_policy.created_by != updater:
                return False, "Permission denied"

            # Create new version
            new_policy = Policy.objects.create(
                chama=old_policy.chama,
                policy_type=old_policy.policy_type,
                title=kwargs.get('title', old_policy.title),
                content=kwargs.get('content', old_policy.content),
                created_by=updater,
                effective_date=kwargs.get('effective_date', old_policy.effective_date),
                version=old_policy.version + 1,
                status='draft',
                previous_version=old_policy,
            )

            # Mark old policy as superseded
            old_policy.status = 'superseded'
            old_policy.save(update_fields=['status', 'updated_at'])

            logger.info(
                f"Policy updated: {policy_id} - new version {new_policy.version}"
            )

            return True, f"Policy updated to version {new_policy.version}"

        except Policy.DoesNotExist:
            return False, "Policy not found"

    @staticmethod
    @transaction.atomic
    def publish_policy(
        policy_id: str,
        publisher: User,
    ) -> tuple[bool, str]:
        """
        Publish a policy.
        Returns (success, message).
        """
        from apps.policy.models import Policy

        try:
            policy = Policy.objects.get(id=policy_id)

            # Check permission
            if policy.created_by != publisher:
                return False, "Permission denied"

            if policy.status != 'draft':
                return False, "Policy is not in draft status"

            # Publish policy
            policy.status = 'published'
            policy.published_at = timezone.now()
            policy.save(update_fields=['status', 'published_at', 'updated_at'])

            logger.info(
                f"Policy published: {policy_id}"
            )

            return True, "Policy published"

        except Policy.DoesNotExist:
            return False, "Policy not found"

    @staticmethod
    def get_policies(
        chama: Chama,
        policy_type: str = None,
        status: str = None,
    ) -> list[dict]:
        """
        Get policies for a chama.
        """
        from apps.policy.models import Policy

        queryset = Policy.objects.filter(chama=chama)

        if policy_type:
            queryset = queryset.filter(policy_type=policy_type)

        if status:
            queryset = queryset.filter(status=status)

        policies = queryset.order_by('-version')

        return [
            {
                'id': str(policy.id),
                'policy_type': policy.policy_type,
                'title': policy.title,
                'version': policy.version,
                'status': policy.status,
                'effective_date': policy.effective_date.isoformat(),
                'published_at': policy.published_at.isoformat() if policy.published_at else None,
                'created_by_name': policy.created_by.full_name,
                'created_at': policy.created_at.isoformat(),
            }
            for policy in policies
        ]

    @staticmethod
    def get_policy_detail(policy_id: str) -> dict | None:
        """
        Get detailed policy information.
        """
        from apps.policy.models import Policy

        try:
            policy = Policy.objects.select_related(
                'chama', 'created_by'
            ).get(id=policy_id)

            return {
                'id': str(policy.id),
                'policy_type': policy.policy_type,
                'title': policy.title,
                'content': policy.content,
                'version': policy.version,
                'status': policy.status,
                'effective_date': policy.effective_date.isoformat(),
                'published_at': policy.published_at.isoformat() if policy.published_at else None,
                'chama_id': str(policy.chama.id),
                'chama_name': policy.chama.name,
                'created_by_id': str(policy.created_by.id),
                'created_by_name': policy.created_by.full_name,
                'created_at': policy.created_at.isoformat(),
                'updated_at': policy.updated_at.isoformat(),
            }

        except Policy.DoesNotExist:
            return None

    @staticmethod
    @transaction.atomic
    def acknowledge_policy(
        policy_id: str,
        user: User,
    ) -> tuple[bool, str]:
        """
        Acknowledge a policy.
        Returns (success, message).
        """
        from apps.policy.models import Policy, PolicyAcknowledgement

        try:
            policy = Policy.objects.get(id=policy_id)

            if policy.status != 'published':
                return False, "Policy is not published"

            # Create or update acknowledgement
            acknowledgement, created = PolicyAcknowledgement.objects.get_or_create(
                policy=policy,
                user=user,
                defaults={'acknowledged_at': timezone.now()},
            )

            if not created:
                # Already acknowledged
                return False, "Policy already acknowledged"

            logger.info(
                f"Policy acknowledged: {policy_id} by {user.full_name}"
            )

            return True, "Policy acknowledged"

        except Policy.DoesNotExist:
            return False, "Policy not found"

    @staticmethod
    def get_policy_acknowledgements(policy_id: str) -> list[dict]:
        """
        Get acknowledgements for a policy.
        """
        from apps.policy.models import PolicyAcknowledgement

        acknowledgements = PolicyAcknowledgement.objects.filter(
            policy_id=policy_id,
        ).select_related('user')

        return [
            {
                'user_id': str(ack.user.id),
                'user_name': ack.user.full_name,
                'acknowledged_at': ack.acknowledged_at.isoformat(),
            }
            for ack in acknowledgements
        ]

    @staticmethod
    def get_user_acknowledgements(chama: Chama, user: User) -> list[dict]:
        """
        Get policy acknowledgements for a user.
        """
        from apps.policy.models import Policy, PolicyAcknowledgement

        # Get published policies
        policies = Policy.objects.filter(
            chama=chama,
            status='published',
        )

        result = []
        for policy in policies:
            acknowledged = PolicyAcknowledgement.objects.filter(
                policy=policy,
                user=user,
            ).exists()

            result.append({
                'policy_id': str(policy.id),
                'policy_title': policy.title,
                'policy_type': policy.policy_type,
                'version': policy.version,
                'acknowledged': acknowledged,
            })

        return result

    @staticmethod
    def get_policy_types() -> list[dict]:
        """
        Get available policy types.
        """
        return [
            {
                'id': 'constitution',
                'name': 'Constitution',
                'description': 'Chama constitution and bylaws',
            },
            {
                'id': 'loan_policy',
                'name': 'Loan Policy',
                'description': 'Loan eligibility, terms, and conditions',
            },
            {
                'id': 'contribution_policy',
                'name': 'Contribution Policy',
                'description': 'Contribution rules and schedules',
            },
            {
                'id': 'fine_policy',
                'name': 'Fine Policy',
                'description': 'Fines and penalties',
            },
            {
                'id': 'meeting_policy',
                'name': 'Meeting Policy',
                'description': 'Meeting rules and procedures',
            },
            {
                'id': 'voting_policy',
                'name': 'Voting Policy',
                'description': 'Voting rules and procedures',
            },
        ]

    @staticmethod
    def get_policy_summary(chama: Chama) -> dict:
        """
        Get policy summary for a chama.
        """
        from django.db.models import Count

        from apps.policy.models import Policy, PolicyAcknowledgement

        policies = Policy.objects.filter(chama=chama)

        summary = policies.aggregate(
            total=Count('id'),
            published=Count('id', filter=models.Q(status='published')),
            draft=Count('id', filter=models.Q(status='draft')),
        )

        # Get acknowledgement stats
        published_policies = policies.filter(status='published')
        total_acknowledgements = 0
        total_members = Membership.objects.filter(chama=chama, status='active').count()

        for policy in published_policies:
            ack_count = PolicyAcknowledgement.objects.filter(policy=policy).count()
            total_acknowledgements += ack_count

        return {
            'total_policies': summary['total'] or 0,
            'published_policies': summary['published'] or 0,
            'draft_policies': summary['draft'] or 0,
            'total_acknowledgements': total_acknowledgements,
            'total_members': total_members,
            'acknowledgement_rate': (
                (total_acknowledgements / (summary['published'] * total_members) * 100)
                if summary['published'] > 0 and total_members > 0 else 0
            ),
        }
