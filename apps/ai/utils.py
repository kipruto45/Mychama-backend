"""
AI system integration utilities and helpers.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings
from django.utils import timezone

from apps.ai.models import AIActionLog, AIConversation, AIConversationMode, AIMessage
from core.audit import create_audit_log

logger = logging.getLogger(__name__)


@dataclass
class AIChatContext:
    """Context for AI chat operations."""
    user_id: str
    chama_id: str
    mode: str
    message: str
    conversation_id: str | None = None
    
    def validate(self):
        """Validate chat context."""
        if not self.user_id:
            raise ValueError("user_id is required")
        if not self.chama_id:
            raise ValueError("chama_id is required")
        if self.mode not in dict(AIConversationMode.choices):
            raise ValueError(f"Invalid mode '{self.mode}'")
        if not self.message:
            raise ValueError("message is required")


class AISystemConfig:
    """Configuration for AI system."""
    
    @staticmethod
    def is_openai_enabled() -> bool:
        """Check if OpenAI is enabled."""
        return bool(getattr(settings, "OPENAI_API_KEY", ""))
    
    @staticmethod
    def get_chat_model() -> str:
        """Get chat model name."""
        return getattr(settings, "AI_CHAT_MODEL", "gpt-5-mini")
    
    @staticmethod
    def get_embedding_model() -> str:
        """Get embedding model name."""
        return getattr(settings, "AI_EMBEDDING_MODEL", "text-embedding-3-small")
    
    @staticmethod
    def get_moderation_model() -> str:
        """Get moderation model name."""
        return getattr(settings, "AI_MODERATION_MODEL", "omni-moderation-latest")
    
    @staticmethod
    def get_otp_expiry_minutes() -> int:
        """Get OTP expiry time."""
        return int(getattr(settings, "OTP_EXPIRY_MINUTES", 5))


def log_ai_action(
    *,
    chama_id,
    actor,
    action_type: str,
    tool_name: str | None = None,
    references: dict | None = None,
    metadata: dict | None = None,
):
    """Log AI action for audit trail."""
    model_name = AISystemConfig.get_chat_model()
    
    AIActionLog.objects.create(
        chama_id=chama_id,
        actor=actor,
        action_type=action_type,
        references=references or {},
        model_name=model_name,
        created_by=actor,
        updated_by=actor,
    )
    
    create_audit_log(
        actor=actor,
        chama_id=chama_id,
        action=f"ai_{action_type}",
        metadata=metadata or {"tool": tool_name} if tool_name else {},
    )


def log_conversation(
    *,
    conversation: AIConversation,
    message: str,
    role: str,
    tool_name: str | None = None,
    tool_payload: dict | None = None,
    actor=None,
) -> AIMessage:
    """Log a message in conversation."""
    return AIMessage.objects.create(
        conversation=conversation,
        role=role,
        content=message,
        tool_name=tool_name or "",
        tool_payload=tool_payload or {},
        created_by=actor or conversation.user,
        updated_by=actor or conversation.user,
    )


def format_tool_result(result: dict | list | str, max_lines: int = 5) -> str:
    """Format tool result for display."""
    import json
    
    result_str = str(result)
    if isinstance(result, (dict, list)):
        result_str = json.dumps(result, indent=2, default=str)
    
    lines = result_str.split('\n')
    if len(lines) > max_lines:
        lines = lines[:max_lines] + ['...']
    
    return '\n'.join(lines)
