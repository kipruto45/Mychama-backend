"""
Join Request Service

Manages join requests, approval workflow, and notifications.
"""

import logging

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama, Membership

logger = logging.getLogger(__name__)


class JoinRequestService:
    """Service for managing join requests."""

    @staticmethod
    @transaction.atomic
    def create_request(
        chama: Chama,
        user: User,
        message: str = '',
    ) -> dict:
        """
        Create a join request.
        Returns request details.
        """
        from apps.join_requests.models import JoinRequest

        # Check if already a member
        if Membership.objects.filter(chama=chama, user=user).exists():
            raise ValueError("You are already a member of this chama")

        # Check if already has pending request
        if JoinRequest.objects.filter(chama=chama, user=user, status='pending').exists():
            raise ValueError("You already have a pending join request")

        # Create request
        request = JoinRequest.objects.create(
            chama=chama,
            user=user,
            message=message,
            status='pending',
        )

        logger.info(f"Join request created for chama {chama.name} by {user.full_name}")

        return {
            'id': str(request.id),
            'chama_id': str(chama.id),
            'chama_name': chama.name,
            'user_id': str(user.id),
            'user_name': user.full_name,
            'message': message,
            'status': 'pending',
            'created_at': request.created_at.isoformat(),
        }

    @staticmethod
    def get_chama_requests(chama: Chama, status: str = None) -> list[dict]:
        """
        Get join requests for a chama.
        """
        from apps.join_requests.models import JoinRequest

        queryset = JoinRequest.objects.filter(chama=chama).select_related('user')

        if status:
            queryset = queryset.filter(status=status)

        requests = queryset.order_by('-created_at')

        return [
            {
                'id': str(req.id),
                'user_id': str(req.user.id),
                'user_name': req.user.full_name,
                'user_phone': req.user.phone,
                'message': req.message,
                'status': req.status,
                'created_at': req.created_at.isoformat(),
                'reviewed_at': req.reviewed_at.isoformat() if req.reviewed_at else None,
                'reviewed_by': req.reviewed_by.full_name if req.reviewed_by else None,
            }
            for req in requests
        ]

    @staticmethod
    def get_user_requests(user: User, status: str = None) -> list[dict]:
        """
        Get join requests for a user.
        """
        from apps.join_requests.models import JoinRequest

        queryset = JoinRequest.objects.filter(user=user).select_related('chama')

        if status:
            queryset = queryset.filter(status=status)

        requests = queryset.order_by('-created_at')

        return [
            {
                'id': str(req.id),
                'chama_id': str(req.chama.id),
                'chama_name': req.chama.name,
                'message': req.message,
                'status': req.status,
                'created_at': req.created_at.isoformat(),
                'reviewed_at': req.reviewed_at.isoformat() if req.reviewed_at else None,
            }
            for req in requests
        ]

    @staticmethod
    @transaction.atomic
    def approve_request(
        request_id: str,
        approver: User,
        role: str = 'member',
    ) -> tuple[bool, str]:
        """
        Approve a join request.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker
        from apps.join_requests.models import JoinRequest

        try:
            request = JoinRequest.objects.get(id=request_id, status='pending')

            # Check permission
            if not PermissionChecker.has_permission(
                approver,
                Permission.CAN_APPROVE_MEMBERS,
                str(request.chama.id),
            ):
                return False, "Permission denied"

            # Check if already a member
            if Membership.objects.filter(chama=request.chama, user=request.user).exists():
                request.status = 'rejected'
                request.rejection_reason = 'Already a member'
                request.reviewed_by = approver
                request.reviewed_at = timezone.now()
                request.save(update_fields=[
                    'status',
                    'rejection_reason',
                    'reviewed_by',
                    'reviewed_at',
                    'updated_at',
                ])
                return False, "User is already a member"

            # Create membership
            Membership.objects.create(
                user=request.user,
                chama=request.chama,
                role=role,
                status='active',
                is_active=True,
                is_approved=True,
                joined_at=timezone.now(),
            )

            # Update request
            request.status = 'approved'
            request.reviewed_by = approver
            request.reviewed_at = timezone.now()
            request.save(update_fields=[
                'status',
                'reviewed_by',
                'reviewed_at',
                'updated_at',
            ])

            logger.info(f"Join request {request_id} approved by {approver.full_name}")

            return True, "Request approved"

        except JoinRequest.DoesNotExist:
            return False, "Request not found"

    @staticmethod
    @transaction.atomic
    def reject_request(
        request_id: str,
        rejector: User,
        reason: str,
    ) -> tuple[bool, str]:
        """
        Reject a join request.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker
        from apps.join_requests.models import JoinRequest

        try:
            request = JoinRequest.objects.get(id=request_id, status='pending')

            # Check permission
            if not PermissionChecker.has_permission(
                rejector,
                Permission.CAN_APPROVE_MEMBERS,
                str(request.chama.id),
            ):
                return False, "Permission denied"

            # Update request
            request.status = 'rejected'
            request.rejection_reason = reason
            request.reviewed_by = rejector
            request.reviewed_at = timezone.now()
            request.save(update_fields=[
                'status',
                'rejection_reason',
                'reviewed_by',
                'reviewed_at',
                'updated_at',
            ])

            logger.info(f"Join request {request_id} rejected by {rejector.full_name}")

            return True, "Request rejected"

        except JoinRequest.DoesNotExist:
            return False, "Request not found"

    @staticmethod
    @transaction.atomic
    def cancel_request(
        request_id: str,
        user: User,
    ) -> tuple[bool, str]:
        """
        Cancel a join request.
        Returns (success, message).
        """
        from apps.join_requests.models import JoinRequest

        try:
            request = JoinRequest.objects.get(id=request_id, user=user, status='pending')

            request.status = 'cancelled'
            request.save(update_fields=['status', 'updated_at'])

            logger.info(f"Join request {request_id} cancelled by {user.full_name}")

            return True, "Request cancelled"

        except JoinRequest.DoesNotExist:
            return False, "Request not found"

    @staticmethod
    def get_request_detail(request_id: str) -> dict | None:
        """
        Get detailed request information.
        """
        from apps.join_requests.models import JoinRequest

        try:
            request = JoinRequest.objects.select_related(
                'chama', 'user', 'reviewed_by'
            ).get(id=request_id)

            return {
                'id': str(request.id),
                'chama_id': str(request.chama.id),
                'chama_name': request.chama.name,
                'user_id': str(request.user.id),
                'user_name': request.user.full_name,
                'user_phone': request.user.phone,
                'message': request.message,
                'status': request.status,
                'rejection_reason': request.rejection_reason,
                'reviewed_by': request.reviewed_by.full_name if request.reviewed_by else None,
                'reviewed_at': request.reviewed_at.isoformat() if request.reviewed_at else None,
                'created_at': request.created_at.isoformat(),
            }

        except JoinRequest.DoesNotExist:
            return None

    @staticmethod
    def cleanup_old_requests(days: int = 30) -> int:
        """
        Clean up old pending requests.
        Returns number of requests cleaned up.
        """
        from apps.join_requests.models import JoinRequest

        cutoff_date = timezone.now() - timezone.timedelta(days=days)

        old_requests = JoinRequest.objects.filter(
            status='pending',
            created_at__lt=cutoff_date,
        )

        count = old_requests.count()
        old_requests.update(status='expired')

        return count
