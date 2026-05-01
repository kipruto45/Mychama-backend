"""
Mobile Invitation API Views

REST endpoints for mobile invitation system:
- Create invites
- Validate invites
- Accept invites
- Manage invite lifecycle
- Generate share messages
"""

import logging

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.chama.models import Chama, InviteLink, MembershipRole
from apps.chama.permissions import Permission, PermissionChecker
from apps.invites.mobile_invite_service import MobileInviteService

logger = logging.getLogger(__name__)


def can_manage_chama_invites(user, chama_id: str) -> bool:
    if not user or not user.is_authenticated:
        return False
    return PermissionChecker.has_permission(user, Permission.CAN_INVITE_MEMBERS, chama_id)


class CreateInviteLinkView(APIView):
    """Create a new invite link."""
    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, chama_id: str):
        chama = get_object_or_404(Chama, pk=chama_id)

        # Get permissions
        if not can_manage_chama_invites(request.user, chama_id):
            return Response(
                {"error": "You don't have permission to create invites"},
                status=status.HTTP_403_FORBIDDEN
            )

        # Extract parameters
        role = request.data.get("role", "member")
        max_uses = int(request.data.get("max_uses", 1))
        expires_in_days = int(request.data.get("expires_in_days", 7))
        note = request.data.get("note", "")
        
        # Validate role
        if role not in [r.value for r in MembershipRole]:
            role = "member"

        # Create invite link
        link_data = MobileInviteService.create_invite_link(
            chama=chama,
            created_by=request.user,
            role=role,
            max_uses=max_uses,
            expires_in_days=expires_in_days,
            note=note,
        )

        # Generate share message
        share_message = MobileInviteService.get_share_message(link_data, note)

        logger.info(f"Invite link created for {chama.name} by {request.user.id}")

        return Response({
            "invite": {
                "token": link_data.token,
                "code": link_data.code,
                "deep_link": link_data.deep_link,
                "role": link_data.role,
                "expires_at": link_data.expires_at,
                "max_uses": link_data.max_uses,
                "current_uses": link_data.current_uses,
            },
            "share_message": {
                "whatsapp": share_message.whatsapp,
                "sms": share_message.sms,
            }
        }, status=status.HTTP_201_CREATED)


class ValidateInviteView(APIView):
    """Validate an invite token or code."""
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def get(self, request, token: str):
        valid, message, link_data = MobileInviteService.validate_invite(token)
        
        if not valid:
            return Response(
                {"valid": False, "error": message},
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response({
            "valid": True,
            "invite": {
                "chama_id": link_data.chama_id,
                "chama_name": link_data.chama_name,
                "inviter_name": link_data.inviter_name,
                "role": link_data.role,
                "expires_at": link_data.expires_at,
                "max_uses": link_data.max_uses,
                "remaining_uses": (link_data.max_uses - link_data.current_uses) if link_data.max_uses else None,
            }
        })


class AcceptInviteView(APIView):
    """Accept an invite and join a chama."""
    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        token = request.data.get("token", "").strip()
        
        if not token:
            return Response(
                {"error": "Invite token is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        success, message = MobileInviteService.accept_invite(
            token=token,
            user=request.user,
        )

        if not success:
            return Response(
                {"error": message},
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response({
            "success": True,
            "message": message
        }, status=status.HTTP_200_OK)


class RevokeInviteView(APIView):
    """Revoke an invite link."""
    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, token: str):
        # Check permission
        link = InviteLink.objects.filter(token=token).first()
        if not link:
            return Response(
                {"error": "Invite not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        if not can_manage_chama_invites(request.user, str(link.chama_id)):
            return Response(
                {"error": "Permission denied"},
                status=status.HTTP_403_FORBIDDEN
            )

        reason = request.data.get("reason", "Revoked by admin")
        success, message = MobileInviteService.revoke_invite_link(
            token=token,
            revoked_by=request.user,
            reason=reason,
        )

        if not success:
            return Response(
                {"error": message},
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response({
            "success": True,
            "message": message
        })


class RegenerateInviteView(APIView):
    """Regenerate an invite link with new token."""
    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, token: str):
        # Check permission
        link = InviteLink.objects.filter(token=token).first()
        if not link:
            return Response(
                {"error": "Invite not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        if not can_manage_chama_invites(request.user, str(link.chama_id)):
            return Response(
                {"error": "Permission denied"},
                status=status.HTTP_403_FORBIDDEN
            )

        success, message, new_data = MobileInviteService.regenerate_invite_link(
            token=token,
            regenerated_by=request.user,
        )

        if not success:
            return Response(
                {"error": message},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Generate new share message
        share_message = MobileInviteService.get_share_message(new_data)

        return Response({
            "invite": {
                "token": new_data.token,
                "code": new_data.code,
                "deep_link": new_data.deep_link,
                "role": new_data.role,
                "expires_at": new_data.expires_at,
                "max_uses": new_data.max_uses,
                "current_uses": new_data.current_uses,
            },
            "share_message": {
                "whatsapp": share_message.whatsapp,
                "sms": share_message.sms,
            }
        })


class ListInviteLinksView(APIView):
    """List all invite links for a chama."""
    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, chama_id: str):
        # Check permission
        if not can_manage_chama_invites(request.user, chama_id):
            return Response(
                {"error": "Permission denied"},
                status=status.HTTP_403_FORBIDDEN
            )

        active = MobileInviteService.get_active_links(chama_id)
        expired = MobileInviteService.get_expired_links(chama_id)
        revoked = MobileInviteService.get_revoked_links(chama_id)

        def serialize_link(link):
            return {
                "token": link.token,
                "code": link.code,
                "deep_link": link.deep_link,
                "role": link.role,
                "expires_at": link.expires_at,
                "max_uses": link.max_uses,
                "current_uses": link.current_uses,
                "is_active": link.is_active,
            }

        return Response({
            "active": [serialize_link(l) for l in active],
            "expired": [serialize_link(l) for l in expired],
            "revoked": [serialize_link(l) for l in revoked],
        })


class ShareMessageView(APIView):
    """Get share message for an invite."""
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def get(self, request, token: str):
        link_data = MobileInviteService.resolve_invite_token(token)
        
        if not link_data:
            return Response(
                {"error": "Invite not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        note = request.query_params.get("note", "")
        share_message = MobileInviteService.get_share_message(link_data, note)

        return Response({
            "whatsapp": share_message.whatsapp,
            "sms": share_message.sms,
            "deep_link": link_data.deep_link,
            "short_url": link_data.short_url,
        })


__all__ = [
    "CreateInviteLinkView",
    "ValidateInviteView",
    "AcceptInviteView",
    "RevokeInviteView",
    "RegenerateInviteView",
    "ListInviteLinksView",
    "ShareMessageView",
]
