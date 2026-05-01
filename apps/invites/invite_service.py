"""
Invite System Service

Manages invite tokens, codes, and acceptance flow.
"""

import logging
import secrets

from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama, Membership

logger = logging.getLogger(__name__)


class InviteService:
    """Service for managing invites."""

    @staticmethod
    def generate_invite_code() -> str:
        """
        Generate a unique invite code.
        """
        return secrets.token_urlsafe(8).upper()

    @staticmethod
    def generate_invite_token() -> str:
        """
        Generate a unique invite token.
        """
        return secrets.token_urlsafe(32)

    @staticmethod
    @transaction.atomic
    def create_invite(
        chama: Chama,
        inviter: User,
        email: str = None,
        phone: str = None,
        role: str = 'member',
        expires_in_days: int = 7,
    ) -> dict:
        """
        Create an invite for a chama.
        Returns invite details.
        """
        from apps.invites.models import Invite

        # Generate unique code and token
        code = InviteService.generate_invite_code()
        token = InviteService.generate_invite_token()

        # Calculate expiry
        expires_at = timezone.now() + timezone.timedelta(days=expires_in_days)

        # Create invite
        invite = Invite.objects.create(
            chama=chama,
            inviter=inviter,
            email=email,
            phone=phone,
            code=code,
            token=token,
            role=role,
            expires_at=expires_at,
            status='pending',
        )

        logger.info(f"Invite created for chama {chama.name} by {inviter.full_name}")

        return {
            'id': str(invite.id),
            'code': code,
            'token': token,
            'email': email,
            'phone': phone,
            'role': role,
            'expires_at': expires_at.isoformat(),
            'status': 'pending',
        }

    @staticmethod
    def get_invite_by_code(code: str) -> dict | None:
        """
        Get invite by code.
        """
        from apps.invites.models import Invite

        try:
            invite = Invite.objects.select_related('chama', 'inviter').get(
                code=code,
                status='pending',
            )

            # Check if expired
            if invite.expires_at < timezone.now():
                invite.status = 'expired'
                invite.save(update_fields=['status', 'updated_at'])
                return None

            return {
                'id': str(invite.id),
                'chama_id': str(invite.chama.id),
                'chama_name': invite.chama.name,
                'inviter_name': invite.inviter.full_name,
                'role': invite.role,
                'expires_at': invite.expires_at.isoformat(),
            }

        except Invite.DoesNotExist:
            return None

    @staticmethod
    def get_invite_by_token(token: str) -> dict | None:
        """
        Get invite by token.
        """
        from apps.invites.models import Invite

        try:
            invite = Invite.objects.select_related('chama', 'inviter').get(
                token=token,
                status='pending',
            )

            # Check if expired
            if invite.expires_at < timezone.now():
                invite.status = 'expired'
                invite.save(update_fields=['status', 'updated_at'])
                return None

            return {
                'id': str(invite.id),
                'chama_id': str(invite.chama.id),
                'chama_name': invite.chama.name,
                'inviter_name': invite.inviter.full_name,
                'role': invite.role,
                'expires_at': invite.expires_at.isoformat(),
            }

        except Invite.DoesNotExist:
            return None

    @staticmethod
    @transaction.atomic
    def accept_invite(
        invite_id: str,
        user: User,
    ) -> tuple[bool, str]:
        """
        Accept an invite.
        Returns (success, message).
        """
        from apps.invites.models import Invite

        try:
            invite = Invite.objects.get(id=invite_id, status='pending')

            # Check if expired
            if invite.expires_at < timezone.now():
                invite.status = 'expired'
                invite.save(update_fields=['status', 'updated_at'])
                return False, "Invite has expired"

            # Check if user already a member
            if Membership.objects.filter(chama=invite.chama, user=user).exists():
                return False, "You are already a member of this chama"

            # Create membership
            Membership.objects.create(
                user=user,
                chama=invite.chama,
                role=invite.role,
                status='active',
                is_active=True,
                is_approved=True,
                joined_at=timezone.now(),
            )

            # Update invite
            invite.status = 'accepted'
            invite.accepted_by = user
            invite.accepted_at = timezone.now()
            invite.save(update_fields=[
                'status',
                'accepted_by',
                'accepted_at',
                'updated_at',
            ])

            logger.info(f"Invite {invite_id} accepted by {user.full_name}")

            return True, "Invite accepted"

        except Invite.DoesNotExist:
            return False, "Invite not found"

    @staticmethod
    @transaction.atomic
    def revoke_invite(
        invite_id: str,
        revoked_by: User,
    ) -> tuple[bool, str]:
        """
        Revoke an invite.
        Returns (success, message).
        """
        from apps.invites.models import Invite

        try:
            invite = Invite.objects.get(id=invite_id, status='pending')

            # Check if revoker is inviter or has permission
            if invite.inviter != revoked_by:
                from apps.chama.permissions import Permission, PermissionChecker
                if not PermissionChecker.has_permission(
                    revoked_by,
                    Permission.CAN_INVITE_MEMBERS,
                    str(invite.chama.id),
                ):
                    return False, "Permission denied"

            invite.status = 'revoked'
            invite.save(update_fields=['status', 'updated_at'])

            logger.info(f"Invite {invite_id} revoked by {revoked_by.full_name}")

            return True, "Invite revoked"

        except Invite.DoesNotExist:
            return False, "Invite not found"

    @staticmethod
    def get_chama_invites(chama: Chama) -> list[dict]:
        """
        Get all invites for a chama.
        """
        from apps.invites.models import Invite

        invites = Invite.objects.filter(chama=chama).order_by('-created_at')

        return [
            {
                'id': str(invite.id),
                'email': invite.email,
                'phone': invite.phone,
                'role': invite.role,
                'status': invite.status,
                'inviter_name': invite.inviter.full_name,
                'created_at': invite.created_at.isoformat(),
                'expires_at': invite.expires_at.isoformat(),
                'accepted_at': invite.accepted_at.isoformat() if invite.accepted_at else None,
            }
            for invite in invites
        ]

    @staticmethod
    def get_user_invites(user: User) -> list[dict]:
        """
        Get all invites for a user (by email or phone).
        """
        from apps.invites.models import Invite

        invites = Invite.objects.filter(
            models.Q(email=user.email) | models.Q(phone=user.phone),
            status='pending',
        ).select_related('chama', 'inviter')

        return [
            {
                'id': str(invite.id),
                'chama_id': str(invite.chama.id),
                'chama_name': invite.chama.name,
                'inviter_name': invite.inviter.full_name,
                'role': invite.role,
                'expires_at': invite.expires_at.isoformat(),
            }
            for invite in invites
        ]

    @staticmethod
    def cleanup_expired_invites() -> int:
        """
        Clean up expired invites.
        Returns number of invites cleaned up.
        """
        from apps.invites.models import Invite

        expired = Invite.objects.filter(
            status='pending',
            expires_at__lt=timezone.now(),
        )

        count = expired.count()
        expired.update(status='expired')

        return count
