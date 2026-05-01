# MyChama AI Chatbot - Orchestration Service
# apps/ai/chatbot_orchestration.py

from __future__ import annotations

import logging
from typing import Any

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied

from apps.ai.ai_prompt import get_role_suggestions
from apps.ai.models import AIAnswerFeedback, AIConversation, AIConversationMode, AIMessage
from apps.ai.selectors import require_membership
from apps.ai.services import AIGatewayService
from apps.billing.services import get_access_status, get_entitlements, has_feature
from apps.chama.models import Chama, MembershipRole
from apps.chama.services import get_effective_role
from core.audit import create_audit_log

logger = logging.getLogger(__name__)


ADMIN_MEMBERSHIP_ROLES = {
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.TREASURER,
    MembershipRole.SECRETARY,
}


def _ensure_ai_enabled_for_chama(chama: Chama):
    access = get_access_status(chama)
    if access.get("requires_payment"):
        raise PermissionDenied("AI access is blocked until billing is active for this chama.")

    if has_feature(chama, "ai_basic") or has_feature(chama, "ai_advanced"):
        return

    entitlements = get_entitlements(chama)
    raise PermissionDenied(
        f"AI Assistant requires an upgraded plan (current: {entitlements.get('plan_code', 'FREE')})."
    )


class ChatbotOrchestrationService:
    """
    Production wrapper around the existing AI gateway assistant.

    The gateway is the source of truth for:
    - tool selection (OpenAI tool-calling when available)
    - deterministic tool-first fallback
    - conversation persistence (AIConversation + AIMessage)
    - audit/tool logs
    """

    def __init__(self, user):
        self.user = user

    @transaction.atomic
    def start_conversation(self, *, title: str = "", chama_id=None, mode: str = "") -> tuple[AIConversation, list[str]]:
        chama = None
        effective_role = None
        if chama_id:
            chama = Chama.objects.filter(id=chama_id).first()
            if not chama:
                raise NotFound("Chama not found.")
            membership = require_membership(self.user, str(chama.id))
            _ensure_ai_enabled_for_chama(chama)
            effective_role = get_effective_role(self.user, str(chama.id), membership)

        resolved_mode = mode.strip() or AIConversationMode.MEMBER_ASSISTANT
        if chama and effective_role in {role.value for role in ADMIN_MEMBERSHIP_ROLES}:
            resolved_mode = AIConversationMode.ADMIN_ASSISTANT

        conversation = AIConversation.objects.create(
            user=self.user,
            chama=chama,
            mode=resolved_mode,
            title=title.strip()[:160] if title else "",
            created_by=self.user,
            updated_by=self.user,
        )

        suggestions = []
        if chama:
            suggestions = [item["text"] for item in (get_role_suggestions(self.user, chama) or [])][:4]

        create_audit_log(
            actor=self.user,
            chama_id=str(chama.id) if chama else None,
            action="ai_chat_conversation_started",
            entity_type="AIConversation",
            entity_id=conversation.id,
            metadata={"mode": resolved_mode},
        )

        return conversation, suggestions

    def get_suggestions(self, *, chama_id=None) -> list[str]:
        if not chama_id:
            return []
        chama = Chama.objects.filter(id=chama_id).first()
        if not chama:
            raise NotFound("Chama not found.")
        membership = require_membership(self.user, str(chama.id))
        _ensure_ai_enabled_for_chama(chama)
        return [item["text"] for item in (get_role_suggestions(self.user, chama) or [])][:6]

    def send_message(self, *, conversation_id, message_text: str, stream: bool = False) -> dict[str, Any]:
        if stream:
            raise PermissionDenied("Streaming is not supported on this endpoint. Use /api/v1/ai/chat/stream/.")

        conversation = AIConversation.objects.filter(id=conversation_id, user=self.user).select_related("chama").first()
        if not conversation:
            raise NotFound("Conversation not found.")

        if conversation.chama_id:
            require_membership(self.user, str(conversation.chama_id))
            _ensure_ai_enabled_for_chama(conversation.chama)
            payload = AIGatewayService.chat(
                user=self.user,
                chama_id=str(conversation.chama_id),
                mode=str(conversation.mode or AIConversationMode.MEMBER_ASSISTANT),
                message=message_text,
                conversation_id=str(conversation.id),
            )
        else:
            payload = AIGatewayService.chat_global(
                user=self.user,
                mode=str(conversation.mode or AIConversationMode.MEMBER_ASSISTANT),
                message=message_text,
                conversation_id=str(conversation.id),
            )
        return {
            "conversation_id": str(payload.get("conversation_id") or conversation.id),
            "message_id": str(payload.get("message_id") or ""),
            "response": payload.get("answer") or "",
            "actions": payload.get("actions") or [],
            "follow_up_suggestions": payload.get("follow_up_suggestions") or [],
            "citations": payload.get("citations") or [],
        }

    def get_conversation_history(self, *, conversation_id, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        conversation = AIConversation.objects.filter(id=conversation_id, user=self.user).first()
        if not conversation:
            raise NotFound("Conversation not found.")

        qs = AIMessage.objects.filter(conversation=conversation).order_by("-created_at")
        total = qs.count()
        items = list(qs[offset : offset + limit])
        items.reverse()

        return {
            "messages": items,
            "total": total,
            "has_more": (offset + limit) < total,
        }

    @transaction.atomic
    def clear_conversation(self, *, conversation_id) -> str:
        conversation = AIConversation.objects.filter(id=conversation_id, user=self.user).first()
        if not conversation:
            raise NotFound("Conversation not found.")

        AIMessage.objects.filter(conversation=conversation).delete()
        conversation.updated_at = timezone.now()
        conversation.save(update_fields=["updated_at"])
        return conversation.updated_at.isoformat()

    @transaction.atomic
    def save_feedback(self, *, message_id, rating: str, comment: str = ""):
        message = AIMessage.objects.select_related("conversation__chama").filter(id=message_id).first()
        if not message or message.conversation.user_id != self.user.id:
            raise NotFound("Message not found.")

        feedback, _created = AIAnswerFeedback.objects.update_or_create(
            message=message,
            user=self.user,
            defaults={"rating": rating, "comment": comment or ""},
        )
        create_audit_log(
            actor=self.user,
            chama_id=str(message.conversation.chama_id) if message.conversation.chama_id else None,
            action="ai_chat_feedback",
            entity_type="AIMessage",
            entity_id=message.id,
            metadata={"rating": rating},
        )
        return feedback.id
