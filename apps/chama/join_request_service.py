"""
Join Request Service

Manages join requests, approval workflow, and notifications.
"""

import logging

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama, Membership, MemberStatus

logger = logging.getLogger(__name__)


class JoinRequestService:
    """Service for managing join requests."""

    @staticmethod
    @transaction.atomic
    def create_request(
        chama: Chama,
        user: User,
        message: str = '',
    ) -> tuple[bool, str]:
        """
        Create a join request for a chama.
        Returns (success, message).
        """
        from apps.chama.models import JoinRequest, JoinRequestStatus

        # Check if user is already a member
        if Membership.objects.filter(user=user, chama=chama).exists():
            return False, "You are already a member of this chama"

        # Check if there's already a pending request
        existing_request = JoinRequest.objects.filter(
            user=user,
            chama=chama,
            status=JoinRequestStatus.PENDING,
        ).first()

        if existing_request:
            return False, "You already have a pending join request"

        # Check if chama allows join requests
        if chama.join_policy == 'invite_only':
            return False, "This chama is invite-only. Please use an invite link."

        # Check member limit
        if chama.max_members:
            current_members = Membership.objects.filter(
                chama=chama,
                status=MemberStatus.ACTIVE,
            ).count()
            if current_members >= chama.max_members:
                return False, "This chama has reached its member limit"

        # Create join request
        JoinRequest.objects.create(
            chama=chama,
            user=user,
            message=message,
            status=JoinRequestStatus.PENDING,
        )

        logger.info(f"Join request created by user {user.id} for chama {chama.id}")

        # TODO: Send notification to admins
        # from apps.notifications.services import NotificationService
        # NotificationService.send_join_request_notification(chama, user, join_request)

        return True, "Join request submitted successfully"

    @staticmethod
    @transaction.atomic
    def approve_request(
        request_id: str,
        approver: User,
        role: str = 'member',
    ) -> tuple[bool, str]:
        """
        Approve a join request and create membership.
        Returns (success, message).
        """
        from apps.chama.models import JoinRequest, JoinRequestStatus
        from apps.chama.permissions import Permission, PermissionChecker

        try:
            join_request = JoinRequest.objects.select_for_update().get(
                id=request_id,
                status=JoinRequestStatus.PENDING,
            )

            # Check if approver has permission
            if not PermissionChecker.has_permission(
                approver,
                Permission.CAN_APPROVE_MEMBERS,
                str(join_request.chama.id),
            ):
                return False, "Permission denied"

            # Check member limit
            if join_request.chama.max_members:
                current_members = Membership.objects.filter(
                    chama=join_request.chama,
                    status=MemberStatus.ACTIVE,
                ).count()
                if current_members >= join_request.chama.max_members:
                    return False, "This chama has reached its member limit"

            # Create membership
            Membership.objects.create(
                user=join_request.user,
                chama=join_request.chama,
                role=role,
                status=MemberStatus.ACTIVE,
                is_active=True,
                is_approved=True,
                joined_at=timezone.now(),
            )

            # Update join request
            join_request.status = JoinRequestStatus.APPROVED
            join_request.reviewed_by = approver
            join_request.reviewed_at = timezone.now()
            join_request.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])

            logger.info(
                f"Join request {request_id} approved by {approver.id} "
                f"for user {join_request.user.id}"
            )

            # TODO: Send approval notification
            # from apps.notifications.services import NotificationService
            # NotificationService.send_join_request_approved(join_request.user, join_request.chama)

            return True, "Join request approved"

        except JoinRequest.DoesNotExist:
            return False, "Join request not found"

    @staticmethod
    @transaction.atomic
    def reject_request(
        request_id: str,
        rejector: User,
        reason: str = '',
    ) -> tuple[bool, str]:
        """
        Reject a join request.
        Returns (success, message).
        """
        from apps.chama.models import JoinRequest, JoinRequestStatus
        from apps.chama.permissions import Permission, PermissionChecker

        try:
            join_request = JoinRequest.objects.get(
                id=request_id,
                status=JoinRequestStatus.PENDING,
            )

            # Check if rejector has permission
            if not PermissionChecker.has_permission(
                rejector,
                Permission.CAN_APPROVE_MEMBERS,
                str(join_request.chama.id),
            ):
                return False, "Permission denied"

            # Update join request
            join_request.status = JoinRequestStatus.REJECTED
            join_request.reviewed_by = rejector
            join_request.reviewed_at = timezone.now()
            join_request.rejection_reason = reason
            join_request.save(update_fields=[
                'status',
                'reviewed_by',
                'reviewed_at',
                'rejection_reason',
            ])

            logger.info(
                f"Join request {request_id} rejected by {rejector.id} "
                f"for user {join_request.user.id}"
            )

            # TODO: Send rejection notification
            # from apps.notifications.services import NotificationService
            # NotificationService.send_join_request_rejected(
            #     join_request.user, join_request.chama, reason
            # )

            return True, "Join request rejected"

        except JoinRequest.DoesNotExist:
            return False, "Join request not found"

    @staticmethod
    def get_chama_requests(
        chama: Chama,
        status: str = None,
        limit: int = 50,
    ) -> list[dict]:
        """
        Get join requests for a chama.
        """
        from apps.chama.models import JoinRequest

        queryset = JoinRequest.objects.filter(chama=chama).select_related('user')

        if status:
            queryset = queryset.filter(status=status)

        requests = queryset.order_by('-created_at')[:limit]

        return [
            {
                'id': str(req.id),
                'user_id': str(req.user.id),
                'user_name': req.user.full_name,
                'user_phone': req.user.phone,
                'message': req.message,
                'status': req.status,
                'created_at': req.created_at.isoformat(),
                'reviewed_by': req.reviewed_by.full_name if req.reviewed_by else None,
                'reviewed_at': req.reviewed_at.isoformat() if req.reviewed_at else None,
                'rejection_reason': req.rejection_reason,
            }
            for req in requests
        ]

    @staticmethod
    def get_user_requests(
        user: User,
        status: str = None,
        limit: int = 50,
    ) -> list[dict]:
        """
        Get join requests for a user.
        """
        from apps.chama.models import JoinRequest

        queryset = JoinRequest.objects.filter(user=user).select_related('chama')

        if status:
            queryset = queryset.filter(status=status)

        requests = queryset.order_by('-created_at')[:limit]

        return [
            {
                'id': str(req.id),
                'chama_id': str(req.chama.id),
                'chama_name': req.chama.name,
                'message': req.message,
                'status': req.status,
                'created_at': req.created_at.isoformat(),
                'reviewed_by': req.reviewed_by.full_name if req.reviewed_by else None,
                'reviewed_at': req.reviewed_at.isoformat() if req.reviewed_at else None,
                'rejection_reason': req.rejection_reason,
            }
            for req in requests
        ]

    @staticmethod
    def cleanup_old_requests(days: int = 30) -> int:
        """
        Clean up old pending requests.
        Returns number of requests cleaned up.
        """
        from apps.chama.models import JoinRequest, JoinRequestStatus

        cutoff_date = timezone.now() - timezone.timedelta(days=days)
        
        count = JoinRequest.objects.filter(
            status=JoinRequestStatus.PENDING,
            created_at__lt=cutoff_date,
        ).update(status=JoinRequestStatus.EXPIRED)

        if count > 0:
            logger.info(f"Cleaned up {count} old join requests")

        return count
