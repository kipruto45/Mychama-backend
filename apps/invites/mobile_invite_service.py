"""
Mobile Invitation System

Enhanced invitation service for mobile-only app with:
- Deep link support
- Share message generation
- Multi-use and single-use invites
- APK flow support
- Auth continuation
"""

import logging
import secrets
import hashlib
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.chama.models import Chama, Invite, InviteLink, Membership
from apps.accounts.models import User

logger = logging.getLogger(__name__)


# Configuration
INVITE_CODE_LENGTH = 6
INVITE_TOKEN_LENGTH = 32
DEFAULT_EXPIRY_DAYS = 7
MAX_INVITE_USES = 100


def generate_invite_code() -> str:
    """Generate a user-friendly invite code."""
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(chars) for _ in range(INVITE_CODE_LENGTH))


def generate_invite_token() -> str:
    """Generate a secure invite token."""
    return secrets.token_urlsafe(INVITE_TOKEN_LENGTH)


@dataclass
class InviteLinkData:
    """Invite link data for sharing."""
    token: str
    code: str
    chama_id: str
    chama_name: str
    inviter_name: str
    role: str
    expires_at: str
    max_uses: Optional[int]
    current_uses: int
    is_active: bool
    deep_link: str
    short_url: str


@dataclass
class ShareMessage:
    """Generated share message."""
    whatsapp: str
    sms: str
    full_message: str


class MobileInviteService:
    """Service for mobile invitation management."""

    @staticmethod
    def get_apk_link() -> str:
        """Get APK download link."""
        return getattr(settings, "APK_DOWNLOAD_URL", "https://my-cham-a.app/app.apk")

    @staticmethod
    def get_deep_link_base() -> str:
        """Get deep link base."""
        return getattr(settings, "DEEP_LINK_BASE", "mychama://")

    @staticmethod
    @transaction.atomic
    def create_invite_link(
        chama: Chama,
        created_by: User,
        role: str = "member",
        max_uses: int = 1,
        expires_in_days: int = DEFAULT_EXPIRY_DAYS,
        note: str = "",
    ) -> InviteLinkData:
        """Create a new invite link."""
        token = generate_invite_token()
        code = generate_invite_code()
        
        # Ensure code uniqueness
        while InviteLink.objects.filter(
            chama=chama,
            token__contains=code
        ).exists():
            code = generate_invite_code()
        
        expires_at = timezone.now() + timedelta(days=expires_in_days)
        
        link = InviteLink.objects.create(
            chama=chama,
            token=token,
            created_by=created_by,
            preassigned_role=role,
            max_uses=max_uses if max_uses > 1 else None,
            expires_at=expires_at,
            is_active=True,
        )
        
        logger.info(f"Invite link created for {chama.name} by {created_by.full_name}")
        
        return MobileInviteService._build_link_data(link, code)

    @staticmethod
    def _build_link_data(link: InviteLink, code: str = "") -> InviteLinkData:
        """Build InviteLinkData from model."""
        deep_link = f"{MobileInviteService.get_deep_link_base()}invite/{link.token}"
        short_url = f"https://my-cham-a.app/invite/{code}" if code else deep_link
        
        return InviteLinkData(
            token=link.token,
            code=code,
            chama_id=str(link.chama_id),
            chama_name=link.chama.name,
            inviter_name=link.created_by.full_name,
            role=link.preassigned_role or "member",
            expires_at=link.expires_at.isoformat(),
            max_uses=link.max_uses,
            current_uses=link.current_uses,
            is_active=link.is_active,
            deep_link=deep_link,
            short_url=short_url,
        )

    @staticmethod
    def resolve_invite_token(token: str) -> Optional[InviteLinkData]:
        """Resolve an invite by token."""
        link = InviteLink.objects.select_related(
            "chama", "created_by"
        ).filter(
            token=token,
            is_active=True,
        ).first()
        
        if not link:
            return None
        
        if link.expires_at < timezone.now():
            link.is_active = False
            link.save(update_fields=["is_active"])
            return None
        
        if link.max_uses and link.current_uses >= link.max_uses:
            return None
        
        return MobileInviteService._build_link_data(link)

    @staticmethod
    def resolve_invite_code(code: str) -> Optional[InviteLinkData]:
        """Resolve an invite by short code."""
        # Handle edge: short codes could be confused, so we search by token prefix
        links = InviteLink.objects.select_related(
            "chama", "created_by"
        ).filter(
            chama__is_active=True,
            is_active=True,
            token__startswith=code.upper(),
        ).first()
        
        if not links:
            # Try matching a token-like code directly
            links = InviteLink.objects.select_related(
                "chama", "created_by"
            ).filter(
                token=code.upper(),
                is_active=True,
            ).first()
        
        if not links:
            return None
        
        if links.expires_at < timezone.now():
            links.is_active = False
            links.save(update_fields=["is_active"])
            return None
        
        if links.max_uses and links.current_uses >= links.max_uses:
            return None
        
        # Generate code from short token for display
        display_code = links.token[:INVITE_CODE_LENGTH].upper()
        return MobileInviteService._build_link_data(links, display_code)

    @staticmethod
    def validate_invite(token: str) -> tuple[bool, str, Optional[InviteLinkData]]:
        """Validate an invite token."""
        if not token or len(token) < 4:
            return False, "Invalid invite token", None
        
        link_data = MobileInviteService.resolve_invite_token(token)
        
        if not link_data:
            # Try by code
            link_data = MobileInviteService.resolve_invite_code(token)
        
        if not link_data:
            return False, "Invalid or expired invitation", None
        
        if not link_data.is_active:
            return False, "This invitation has been revoked", None
        
        return True, "Valid invitation", link_data

    @staticmethod
    @transaction.atomic
    def accept_invite(
        token: str,
        user: User,
    ) -> tuple[bool, str]:
        """Accept an invite and create membership."""
        valid, message, link_data = MobileInviteService.validate_invite(token)
        
        if not valid:
            return False, message
        
        link = InviteLink.objects.filter(token=token).first()
        if not link:
            return False, "Invitation not found"
        
        # Check if user already a member
        if Membership.objects.filter(
            chama=link.chama,
            user=user,
            is_active=True,
        ).exists():
            return False, "You are already a member of this chama"
        
        # Create membership
        role = link.preassigned_role or "member"
        Membership.objects.create(
            user=user,
            chama=link.chama,
            role=role,
            is_active=True,
            is_approved=True,
            joined_at=timezone.now(),
        )
        
        # Increment use count
        link.current_uses += 1
        if link.max_uses and link.current_uses >= link.max_uses:
            link.is_active = False
        link.save(update_fields=["current_uses", "is_active"])
        
        logger.info(f"Invite {token} accepted by {user.id} for chama {link.chama_id}")
        
        return True, f"Successfully joined {link.chama.name}"

    @staticmethod
    @transaction.atomic
    def revoke_invite_link(
        token: str,
        revoked_by: User,
        reason: str = "",
    ) -> tuple[bool, str]:
        """Revoke an invite link."""
        link = InviteLink.objects.filter(token=token).first()
        
        if not link:
            return False, "Invitation not found"
        
        if not link.is_active:
            return False, "Invitation already revoked"
        
        link.is_active = False
        link.revoked_at = timezone.now()
        link.revoke_reason = reason or "Revoked by admin"
        link.save()
        
        logger.info(f"Invite {token} revoked by {revoked_by.id}")
        
        return True, "Invitation revoked"

    @staticmethod
    @transaction.atomic
    def regenerate_invite_link(
        token: str,
        regenerated_by: User,
    ) -> tuple[bool, str, Optional[InviteLinkData]]:
        """Regenerate an invite link."""
        link = InviteLink.objects.filter(token=token).first()
        
        if not link:
            return False, "Invitation not found", None
        
        # Create new token
        new_token = generate_invite_token()
        old_code = link.token[:INVITE_CODE_LENGTH]
        link.token = new_token
        link.current_uses = 0
        link.is_active = True
        link.revoked_at = None
        link.revoke_reason = ""
        link.save()
        
        logger.info(f"Invite {token} regenerated by {regenerated_by.id}")
        
        new_data = MobileInviteService._build_link_data(link, old_code)
        
        return True, "Invitation regenerated", new_data

    @staticmethod
    def get_share_message(
        link_data: InviteLinkData,
        note: str = "",
    ) -> ShareMessage:
        """Generate share messages for WhatsApp and SMS."""
        
        apk_link = MobileInviteService.get_apk_link()
        
        # Message components
        chama_part = f"to join {link_data.chama_name}"
        inviter_part = f"by {link_data.inviter_name}"
        
        # WhatsApp message (supports longer text with formatting)
        whatsapp = f"""You've been invited {chama_part} on MyChama! 🎉

{inviter_part}

1. Download the app: {apk_link}
2. Open the app
3. Tap "I have an invite" 
4. Enter this code: {link_data.code}

If you already have the app, open directly:
{link_data.deep_link}

"""
        
        if note:
            whatsapp += f"Note: {note}\n"
        
        whatsapp += f"\nJoin our chama on MyChama - the easy way to save and grow together! 💰"
        
        # SMS message (shorter, cleaner)
        sms = f"""MyChama Invite: You've been invited {chama_part}. 
        
Download: {apk_link}
Code: {link_data.code}
Or open: {link_data.deep_link}

"""
        
        if note:
            sms += f"Note: {note}\n"
        
        return ShareMessage(
            whatsapp=whatsapp,
            sms=sms,
            full_message=whatsapp,
        )

    @staticmethod
    def get_active_links(chama_id: str) -> list:
        """Get all active invite links for a chama."""
        links = InviteLink.objects.filter(
            chama_id=chama_id,
            is_active=True,
        ).select_related("chama", "created_by")
        
        result = []
        for link in links:
            display_code = link.token[:INVITE_CODE_LENGTH].upper()
            result.append(MobileInviteService._build_link_data(link, display_code))
        
        return result

    @staticmethod
    def get_expired_links(chama_id: str) -> list:
        """Get expired invite links."""
        links = InviteLink.objects.filter(
            chama_id=chama_id,
            is_active=False,
            expires_at__lt=timezone.now(),
        ).select_related("chama", "created_by")
        
        result = []
        for link in links:
            display_code = link.token[:INVITE_CODE_LENGTH].upper()
            result.append(MobileInviteService._build_link_data(link, display_code))
        
        return result

    @staticmethod
    def get_revoked_links(chama_id: str) -> list:
        """Get revoked invite links."""
        links = InviteLink.objects.filter(
            chama_id=chama_id,
            revoked_at__isnull=False,
        ).select_related("chama", "created_by")
        
        result = []
        for link in links:
            display_code = link.token[:INVITE_CODE_LENGTH].upper()
            result.append(MobileInviteService._build_link_data(link, display_code))
        
        return result


class PendingInviteStorage:
    """Handle pending invite state for auth continuation."""
    
    STORAGE_KEY = "pending_invite_token"
    
    @staticmethod
    def save(token: str) -> None:
        """Save pending invite token for later use."""
        # This would typically use react-native AsyncStorage
        # Implemented in frontend layer
        pass
    
    @staticmethod
    def get() -> Optional[str]:
        """Get stored pending invite token."""
        pass
    
    @staticmethod
    def clear() -> None:
        """Clear pending invite token."""
        pass


__all__ = [
    "MobileInviteService",
    "PendingInviteStorage",
    "InviteLinkData",
    "ShareMessage",
    "generate_invite_code",
    "generate_invite_token",
]