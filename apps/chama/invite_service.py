"""
Invite System Service

Manages secure invite tokens, expiry rules, and acceptance flow.
"""

import hashlib
import logging
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama, Membership, MemberStatus

logger = logging.getLogger(__name__)


class InviteService:
    """Service for managing chama invites."""

    # Configuration
    INVITE_TOKEN_LENGTH = 32
    INVITE_CODE_LENGTH = 8
    DEFAULT_EXPIRY_DAYS = 7
    MAX_INVITES_PER_CHAMA = 100

    @staticmethod
    def generate_invite_token() -> str:
        """Generate a secure invite token."""
        return secrets.token_urlsafe(InviteService.INVITE_TOKEN_LENGTH)

    @staticmethod
    def generate_invite_code() -> str:
        """Generate a short invite code."""
        return secrets.token_urlsafe(InviteService.INVITE_CODE_LENGTH)[:8].upper()

    @staticmethod
    def hash_token(token: str) -> str:
        """Hash an invite token for storage."""
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    @transaction.atomic
    def create_invite(
        chama: Chama,
        inviter: User,
        email: str | None = None,
        phone: str | None = None,
        role: str = 'member',
        expiry_days: int = None,
        max_uses: int = 1,
        message: str = '',
    ) -> dict:
        """
        Create a new invite for a chama.
        Returns invite details including token and code.
        """
        from apps.chama.models import Invite, InviteStatus

        # Check invite limit
        active_invites = Invite.objects.filter(
            chama=chama,
            status=InviteStatus.PENDING,
        ).count()

        if active_invites >= InviteService.MAX_INVITES_PER_CHAMA:
            raise ValueError(f"Maximum invite limit ({InviteService.MAX_INVITES_PER_CHAMA}) reached")

        # Check if user is already a member
        if email:
            existing_user = User.objects.filter(email=email).first()
            if existing_user:
                if Membership.objects.filter(user=existing_user, chama=chama).exists():
                    raise ValueError("User is already a member of this chama")

        if phone:
            existing_user = User.objects.filter(phone=phone).first()
            if existing_user:
                if Membership.objects.filter(user=existing_user, chama=chama).exists():
                    raise ValueError("User is already a member of this chama")

        # Generate tokens
        token = InviteService.generate_invite_token()
        code = InviteService.generate_invite_code()
        token_hash = InviteService.hash_token(token)

        # Set expiry
        expiry_days = expiry_days or InviteService.DEFAULT_EXPIRY_DAYS
        expires_at = timezone.now() + timedelta(days=expiry_days)

        # Create invite
        invite = Invite.objects.create(
            chama=chama,
            inviter=inviter,
            email=email,
            phone=phone,
            role=role,
            token_hash=token_hash,
            code=code,
            expires_at=expires_at,
            max_uses=max_uses,
            uses=0,
            message=message,
            status=InviteStatus.PENDING,
        )

        logger.info(f"Invite created for chama {chama.id} by user {inviter.id}")

        return {
            'id': str(invite.id),
            'token': token,
            'code': code,
            'expires_at': expires_at.isoformat(),
            'max_uses': max_uses,
        }

    @staticmethod
    def get_invite_by_token(token: str) -> dict | None:
        """
        Get invite details by token.
        Returns None if invite is invalid or expired.
        """
        from apps.chama.models import Invite, InviteStatus

        token_hash = InviteService.hash_token(token)
        
        try:
            invite = Invite.objects.select_related('chama', 'inviter').get(
                token_hash=token_hash,
                status=InviteStatus.PENDING,
            )

            # Check expiry
            if invite.expires_at < timezone.now():
                invite.status = InviteStatus.EXPIRED
                invite.save(update_fields=['status'])
                return None

            # Check max uses
            if invite.uses >= invite.max_uses:
                invite.status = InviteStatus.EXPIRED
                invite.save(update_fields=['status'])
                return None

            return {
                'id': str(invite.id),
                'chama_id': str(invite.chama.id),
                'chama_name': invite.chama.name,
                'inviter_name': invite.inviter.full_name,
                'role': invite.role,
                'email': invite.email,
                'phone': invite.phone,
                'message': invite.message,
                'expires_at': invite.expires_at.isoformat(),
                'uses': invite.uses,
                'max_uses': invite.max_uses,
            }

        except Invite.DoesNotExist:
            return None

    @staticmethod
    def get_invite_by_code(code: str) -> dict | None:
        """
        Get invite details by code.
        Returns None if invite is invalid or expired.
        """
        from apps.chama.models import Invite, InviteStatus

        try:
            invite = Invite.objects.select_related('chama', 'inviter').get(
                code=code.upper(),
                status=InviteStatus.PENDING,
            )

            # Check expiry
            if invite.expires_at < timezone.now():
                invite.status = InviteStatus.EXPIRED
                invite.save(update_fields=['status'])
                return None

            # Check max uses
            if invite.uses >= invite.max_uses:
                invite.status = InviteStatus.EXPIRED
                invite.save(update_fields=['status'])
                return None

            return {
                'id': str(invite.id),
                'chama_id': str(invite.chama.id),
                'chama_name': invite.chama.name,
                'inviter_name': invite.inviter.full_name,
                'role': invite.role,
                'email': invite.email,
                'phone': invite.phone,
                'message': invite.message,
                'expires_at': invite.expires_at.isoformat(),
                'uses': invite.uses,
                'max_uses': invite.max_uses,
            }

        except Invite.DoesNotExist:
            return None

    @staticmethod
    @transaction.atomic
    def accept_invite(
        token: str,
        user: User,
    ) -> tuple[bool, str]:
        """
        Accept an invite and create membership.
        Returns (success, message).
        """
        from apps.chama.models import Invite, InviteStatus

        token_hash = InviteService.hash_token(token)
        
        try:
            invite = Invite.objects.select_for_update().get(
                token_hash=token_hash,
                status=InviteStatus.PENDING,
            )

            # Check expiry
            if invite.expires_at < timezone.now():
                invite.status = InviteStatus.EXPIRED
                invite.save(update_fields=['status'])
                return False, "Invite has expired"

            # Check max uses
            if invite.uses >= invite.max_uses:
                invite.status = InviteStatus.EXPIRED
                invite.save(update_fields=['status'])
                return False, "Invite has reached maximum uses"

            # Check if user is already a member
            if Membership.objects.filter(user=user, chama=invite.chama).exists():
                return False, "You are already a member of this chama"

            # Check email/phone match if restricted
            if invite.email and user.email != invite.email:
                return False, "This invite is for a different email address"

            if invite.phone and user.phone != invite.phone:
                return False, "This invite is for a different phone number"

            # Create membership
            Membership.objects.create(
                user=user,
                chama=invite.chama,
                role=invite.role,
                status=MemberStatus.ACTIVE,
                is_active=True,
                is_approved=True,
                joined_at=timezone.now(),
            )

            # Update invite
            invite.uses += 1
            if invite.uses >= invite.max_uses:
                invite.status = InviteStatus.ACCEPTED
            invite.save(update_fields=['uses', 'status'])

            logger.info(f"Invite accepted by user {user.id} for chama {invite.chama.id}")

            # TODO: Send notification to inviter
            # from apps.notifications.services import NotificationService
            # NotificationService.send_invite_accepted(invite.inviter, user, invite.chama)

            return True, "Successfully joined chama"

        except Invite.DoesNotExist:
            return False, "Invalid invite token"

    @staticmethod
    @transaction.atomic
    def revoke_invite(invite_id: str, user: User) -> tuple[bool, str]:
        """
        Revoke an invite.
        Returns (success, message).
        """
        from apps.chama.models import Invite, InviteStatus

        try:
            invite = Invite.objects.get(id=invite_id)

            # Check if user can revoke (inviter or admin)
            if invite.inviter != user:
                from apps.chama.permissions import Permission, PermissionChecker
                if not PermissionChecker.has_permission(
                    user,
                    Permission.CAN_INVITE_MEMBERS,
                    str(invite.chama.id),
                ):
                    return False, "Permission denied"

            invite.status = InviteStatus.REVOKED
            invite.save(update_fields=['status'])

            logger.info(f"Invite {invite_id} revoked by user {user.id}")
            return True, "Invite revoked"

        except Invite.DoesNotExist:
            return False, "Invite not found"

    @staticmethod
    def get_chama_invites(
        chama: Chama,
        status: str = None,
        limit: int = 50,
    ) -> list[dict]:
        """
        Get invites for a chama.
        """
        from apps.chama.models import Invite

        queryset = Invite.objects.filter(chama=chama).select_related('inviter')

        if status:
            queryset = queryset.filter(status=status)

        invites = queryset.order_by('-created_at')[:limit]

        return [
            {
                'id': str(invite.id),
                'inviter_name': invite.inviter.full_name,
                'email': invite.email,
                'phone': invite.phone,
                'role': invite.role,
                'status': invite.status,
                'uses': invite.uses,
                'max_uses': invite.max_uses,
                'expires_at': invite.expires_at.isoformat(),
                'created_at': invite.created_at.isoformat(),
            }
            for invite in invites
        ]

    @staticmethod
    def cleanup_expired_invites() -> int:
        """
        Clean up expired invites.
        Returns number of invites cleaned up.
        """
        from apps.chama.models import Invite, InviteStatus

        count = Invite.objects.filter(
            status=InviteStatus.PENDING,
            expires_at__lt=timezone.now(),
        ).update(status=InviteStatus.EXPIRED)

        if count > 0:
            logger.info(f"Cleaned up {count} expired invites")

        return count
