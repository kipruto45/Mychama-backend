from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from statistics import mean, pstdev
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.ai.models import (
    AIActionLog,
    AIConversation,
    AIMessage,
    AIMessageRole,
    AIToolCallLog,
    KnowledgeChunk,
    KnowledgeDocument,
)
from apps.ai.prompts import (
    build_context_prompt,
    get_model_for_task,
    get_token_limit,
)
from apps.ai.chatbot_prompts import (
    detect_greeting,
    get_greeting_response,
    should_skip_tools_for_message,
)
from apps.ai.rag_engine import get_ai_context_for_decision
from apps.ai.selectors import (
    draft_notification,
    find_suspicious_transactions,
    get_chama_portfolio_summary,
    get_issue,
    get_meeting_minutes,
    get_member_contribution_summary,
    get_member_loan_summary,
    get_member_next_installment,
    get_payment_status,
    list_failed_or_pending_payouts,
    list_my_payments,
    list_open_issues,
    list_overdue_installments,
    list_upcoming_meetings,
    recent_chama_summary,
    require_admin_membership,
    require_member_scope,
    require_membership,
    search_kb,
    send_notification_with_confirm,
)
from apps.ai.validators import (
    AIRetryHandler,
    create_safe_fallback_response,
)
from apps.chama.services import get_effective_role
from apps.finance.models import Contribution, InstallmentStatus, Loan, LoanStatus
from apps.issues.models import Issue
from apps.meetings.models import Meeting
from apps.payments.models import PaymentIntent, PaymentIntentType
from apps.reports.models import ReportRun
from core.audit import create_audit_log

logger = logging.getLogger(__name__)


try:
    from openai import OpenAI
except Exception:  # noqa: BLE001
    OpenAI = None


class AIServiceError(Exception):
    pass


class AIClientPool:
    """Singleton client pool for OpenAI to avoid creating new clients per request."""
    _client = None
    
    @classmethod
    def get_client(cls):
        if cls._client is None:
            api_key = getattr(settings, "OPENAI_API_KEY", "")
            if OpenAI and api_key:
                cls._client = OpenAI(api_key=api_key)
        return cls._client


@dataclass
class ToolExecution:
    tool_name: str
    args: dict[str, Any]
    result: Any


class AIModerationService:
    @staticmethod
    def moderate_text(text: str) -> dict[str, Any]:
        candidate = str(text or "").strip()
        if not candidate:
            return {"allowed": False, "reason": "Message cannot be empty."}

        # Check cache first (1 hour TTL)
        cache_key = f"ai_moderation:{hashlib.md5(candidate.encode()).hexdigest()}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        blocked_patterns = ["how to hack", "bypass otp", "steal", "fraud instructions"]
        lower = candidate.lower()
        if any(pattern in lower for pattern in blocked_patterns):
            result = {
                "allowed": False,
                "reason": "Your request violates assistant safety policy.",
            }
            cache.set(cache_key, result, timeout=3600)
            return result

        api_key = getattr(settings, "OPENAI_API_KEY", "")
        moderation_model = getattr(
            settings,
            "AI_MODERATION_MODEL",
            "omni-moderation-latest",
        )
        if not (OpenAI and api_key):
            result = {"allowed": True, "reason": "local-policy"}
            cache.set(cache_key, result, timeout=3600)
            return result

        try:
            client = AIClientPool.get_client()
            result = client.moderations.create(model=moderation_model, input=candidate)
            output = result.results[0]
            if output.flagged:
                response = {
                    "allowed": False,
                    "reason": "Message flagged by moderation checks.",
                }
                cache.set(cache_key, response, timeout=3600)
                return response
        except Exception:  # noqa: BLE001
            logger.exception("AI moderation request failed; allowing local fallback.")

        result = {"allowed": True, "reason": "ok"}
        cache.set(cache_key, result, timeout=3600)
        return result


class AIEmbeddingService:
    @staticmethod
    def _fallback_embedding(text: str, dimensions: int = 1536) -> list[float]:
        # Stable deterministic embedding fallback for environments without API keys.
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        values = [int(digest[i : i + 2], 16) / 255.0 for i in range(0, len(digest), 2)]
        vector = [0.0] * dimensions
        for index, value in enumerate(values):
            vector[index % dimensions] = value
        return vector

    @staticmethod
    def embed_text(text: str) -> list[float]:
        # Check cache first (24 hour TTL)
        cache_key = f"embedding:{hashlib.md5(text.encode()).hexdigest()}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        api_key = getattr(settings, "OPENAI_API_KEY", "")
        model = getattr(settings, "AI_EMBEDDING_MODEL", "text-embedding-3-small")
        if not (OpenAI and api_key):
            result = AIEmbeddingService._fallback_embedding(text)
            cache.set(cache_key, result, timeout=86400)
            return result

        try:
            client = AIClientPool.get_client()
            response = client.embeddings.create(model=model, input=text)
            result = list(response.data[0].embedding)
            cache.set(cache_key, result, timeout=86400)
            return result
        except Exception:  # noqa: BLE001
            logger.exception("Embedding call failed; using deterministic fallback.")
            result = AIEmbeddingService._fallback_embedding(text)
            cache.set(cache_key, result, timeout=86400)
            return result

    @staticmethod
    def embed_batch(texts: list[str]) -> list[list[float]]:
        """Batch embed multiple texts efficiently for 50% faster processing."""
        api_key = getattr(settings, "OPENAI_API_KEY", "")
        model = getattr(settings, "AI_EMBEDDING_MODEL", "text-embedding-3-small")
        
        # Check cache for each text
        results = [None] * len(texts)
        uncached_indices = []
        
        for i, text in enumerate(texts):
            cache_key = f"embedding:{hashlib.md5(text.encode()).hexdigest()}"
            cached = cache.get(cache_key)
            if cached:
                results[i] = cached
            else:
                uncached_indices.append(i)
        
        # If all cached, return early
        if not uncached_indices:
            return results
        
        # Batch embed only uncached texts
        uncached_texts = [texts[i] for i in uncached_indices]
        
        if not (OpenAI and api_key):
            for idx in uncached_indices:
                results[idx] = AIEmbeddingService._fallback_embedding(texts[idx])
            return results
        
        try:
            client = AIClientPool.get_client()
            response = client.embeddings.create(model=model, input=uncached_texts)
            embeddings = {item.index: list(item.embedding) for item in response.data}
            
            # Map back and cache
            for local_idx, global_idx in enumerate(uncached_indices):
                embedding = embeddings[local_idx]
                results[global_idx] = embedding
                cache_key = f"embedding:{hashlib.md5(texts[global_idx].encode()).hexdigest()}"
                cache.set(cache_key, embedding, timeout=86400)
            
            return results
        except Exception:  # noqa: BLE001
            logger.exception("Batch embedding failed; using fallback.")
            for idx in uncached_indices:
                results[idx] = AIEmbeddingService._fallback_embedding(texts[idx])
            return results


class KnowledgeBaseService:
    chunk_size = 1000

    @staticmethod
    def _chunk_text(text: str) -> list[str]:
        cleaned = "\n".join(
            part.strip() for part in str(text or "").splitlines() if part.strip()
        )
        if not cleaned:
            return []
        return [
            cleaned[i : i + KnowledgeBaseService.chunk_size]
            for i in range(0, len(cleaned), KnowledgeBaseService.chunk_size)
        ]

    @staticmethod
    def _read_document_text(document: KnowledgeDocument) -> str:
        body = document.text_content or ""
        if document.file:
            try:
                file_bytes = document.file.read()
                if isinstance(file_bytes, bytes):
                    body = (
                        f"{body}\n{file_bytes.decode('utf-8', errors='ignore')}".strip()
                    )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed reading knowledge document file: %s", document.id
                )
        return body.strip()

    @staticmethod
    @transaction.atomic
    def reindex_document(*, document: KnowledgeDocument, actor=None) -> int:
        text = KnowledgeBaseService._read_document_text(document)
        chunks = KnowledgeBaseService._chunk_text(text)

        document.chunks.all().delete()
        
        # Use batch embeddings for 50% faster reindex
        if chunks:
            vectors = AIEmbeddingService.embed_batch(chunks)
        else:
            vectors = []
        
        created = 0
        for position, (chunk_text, vector) in enumerate(zip(chunks, vectors, strict=False)):
            KnowledgeChunk.objects.create(
                document=document,
                chunk_text=chunk_text,
                embedding_vector=vector,
                metadata={"position": position},
                created_by=actor,
                updated_by=actor,
            )
            created += 1

        create_audit_log(
            actor=actor,
            chama_id=document.chama_id,
            action="ai_kb_reindexed",
            entity_type="KnowledgeDocument",
            entity_id=document.id,
            metadata={"chunks_created": created},
        )
        return created

    @staticmethod
    def search(*, chama_id, query: str, top_k: int = 5):
        """Search knowledge base with embedding caching and optional vector optimization."""
        query_vector = AIEmbeddingService.embed_text(query)
        return search_kb(chama_id=chama_id, query_embedding=query_vector, top_k=top_k)


class AIGatewayService:
    member_tools = {
        "get_my_wallet_summary",
        "get_my_kyc_status",
        "get_member_contribution_summary",
        "get_member_loan_summary",
        "get_member_next_installment",
        "get_payment_status",
        "list_my_payments",
        "list_upcoming_meetings",
        "search_kb",
        "draft_notification",
    }
    admin_tools = {
        "get_chama_portfolio_summary",
        "list_overdue_installments",
        "list_failed_or_pending_payouts",
        "list_open_issues",
        "get_issue",
        "get_meeting_minutes",
        "find_suspicious_transactions",
        "recent_chama_summary",
    }

    @staticmethod
    def _is_admin_membership_role(role: str) -> bool:
        return role in {"CHAMA_ADMIN", "TREASURER", "SECRETARY"}

    @staticmethod
    def _tool_schema() -> dict[str, dict[str, Any]]:
        return {
            "get_my_wallet_summary": {
                "description": "Get my wallet and activity summary for this chama.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            "get_my_kyc_status": {
                "description": "Get my latest KYC status for this chama (or create_chama draft).",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            "get_member_contribution_summary": {
                "description": "Get contribution summary for current member in a date range.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "from_date": {"type": "string"},
                        "to_date": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            "get_member_loan_summary": {
                "description": "Get loan summary for current member.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            "get_member_next_installment": {
                "description": "Get next due installment for current member.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "loan_id": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            "get_chama_portfolio_summary": {
                "description": "Get high-level chama finance portfolio summary.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            "list_overdue_installments": {
                "description": "List overdue installments in this chama.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            "get_payment_status": {
                "description": "Get payment intent status by intent_id.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "intent_id": {"type": "string"},
                    },
                    "required": ["intent_id"],
                    "additionalProperties": False,
                },
            },
            "list_my_payments": {
                "description": "List my payment intents in this chama.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            "list_failed_or_pending_payouts": {
                "description": "List failed/pending payouts in this chama.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            "get_issue": {
                "description": "Get issue detail by issue_id.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "issue_id": {"type": "string"},
                    },
                    "required": ["issue_id"],
                    "additionalProperties": False,
                },
            },
            "list_open_issues": {
                "description": "List currently open chama issues.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            "get_meeting_minutes": {
                "description": "Get meeting minutes by meeting_id.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "meeting_id": {"type": "string"},
                    },
                    "required": ["meeting_id"],
                    "additionalProperties": False,
                },
            },
            "list_upcoming_meetings": {
                "description": "List upcoming meetings for this chama.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            "search_kb": {
                "description": "Search chama knowledge base using semantic retrieval.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "top_k": {"type": "integer"},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
            "draft_notification": {
                "description": "Draft a notification payload without sending.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {"type": "string"},
                        "channels": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["message"],
                    "additionalProperties": False,
                },
            },
            "send_notification": {
                "description": "Send notification after explicit confirmation.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string"},
                        "message": {"type": "string"},
                        "channels": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "confirm": {"type": "boolean"},
                    },
                    "required": ["user_id", "message", "confirm"],
                    "additionalProperties": False,
                },
            },
            "find_suspicious_transactions": {
                "description": "Find suspicious finance/payment patterns.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            "recent_chama_summary": {
                "description": "Get recent chama summary for assistant fallback.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        }

    @staticmethod
    def _responses_tools_for_user(*, membership_role: str) -> list[dict[str, Any]]:
        allowed_tools = set(AIGatewayService.member_tools)
        if AIGatewayService._is_admin_membership_role(membership_role):
            allowed_tools |= AIGatewayService.admin_tools

        schema = AIGatewayService._tool_schema()
        tools = []
        for tool_name in sorted(allowed_tools):
            tool_meta = schema.get(tool_name)
            if not tool_meta:
                continue
            tools.append(
                {
                    "type": "function",
                    "name": tool_name,
                    "description": tool_meta["description"],
                    "parameters": tool_meta["parameters"],
                }
            )
        return tools

    @staticmethod
    def _extract_response_text(response) -> str:
        output_text = getattr(response, "output_text", None)
        if output_text:
            return output_text.strip()

        parts: list[str] = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", "") != "message":
                continue
            for content in getattr(item, "content", []) or []:
                text_value = getattr(content, "text", None)
                if text_value:
                    parts.append(text_value)
        return "\n".join(part.strip() for part in parts if str(part).strip()).strip()

    @staticmethod
    def _extract_function_calls(response) -> list[tuple[str, dict[str, Any], str]]:
        calls: list[tuple[str, dict[str, Any], str]] = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", "") != "function_call":
                continue
            name = getattr(item, "name", "")
            call_id = getattr(item, "call_id", "") or getattr(item, "id", "")
            raw_args = getattr(item, "arguments", "") or "{}"
            args: dict[str, Any]
            try:
                parsed = json.loads(raw_args)
                args = parsed if isinstance(parsed, dict) else {}
            except Exception:  # noqa: BLE001
                args = {}
            if not name:
                continue
            calls.append((name, args, call_id))
        return calls

    @staticmethod
    def _chat_with_openai_responses(
        *, user, chama_id, membership_role: str, message: str
    ):
        if not (OpenAI and getattr(settings, "OPENAI_API_KEY", "")):
            return "", []

        model = getattr(settings, "AI_CHAT_MODEL", "gpt-5-mini")
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        tools = AIGatewayService._responses_tools_for_user(
            membership_role=membership_role
        )

        response = client.responses.create(
            model=model,
            input=[
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "You are a chama fintech assistant. "
                                "Use only provided tools for factual statements. "
                                "Never fabricate balances or references."
                            ),
                        }
                    ],
                },
                {"role": "user", "content": [{"type": "text", "text": message}]},
            ],
            tools=tools,
        )

        runs: list[ToolExecution] = []
        for _ in range(4):
            function_calls = AIGatewayService._extract_function_calls(response)
            if not function_calls:
                break

            tool_outputs = []
            for tool_name, args, call_id in function_calls:
                result = AIGatewayService._execute_tool(
                    user=user,
                    chama_id=chama_id,
                    tool_name=tool_name,
                    args=args,
                )
                runs.append(
                    ToolExecution(tool_name=tool_name, args=args, result=result)
                )
                if call_id:
                    tool_outputs.append(
                        {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": json.dumps(result, default=str),
                        }
                    )

            if not tool_outputs:
                break

            response = client.responses.create(
                model=model,
                previous_response_id=response.id,
                input=tool_outputs,
                tools=tools,
            )

        answer = AIGatewayService._extract_response_text(response)
        return answer, runs

    @staticmethod
    def _resolve_conversation(*, user, chama_id, mode: str, conversation_id=None):
        if conversation_id:
            conversation = get_object_or_404(
                AIConversation,
                id=conversation_id,
                chama_id=chama_id,
                user=user,
            )
            return conversation

        return AIConversation.objects.create(
            chama_id=chama_id,
            user=user,
            mode=mode,
            created_by=user,
            updated_by=user,
        )

    @staticmethod
    def _detect_tools(*, message: str, mode: str):
        text = message.lower()
        tools: list[tuple[str, dict[str, Any]]] = []

        if "kyc" in text or ("verify" in text and "identity" in text):
            tools.append(("get_my_kyc_status", {}))

        # Wallet / balance questions: prefer wallet unless explicitly about loans.
        if "wallet" in text or ("balance" in text and "loan" not in text and "owe" not in text):
            tools.append(("get_my_wallet_summary", {}))

        if "contribution" in text:
            start_month = timezone.localdate().replace(day=1)
            tools.append(
                (
                    "get_member_contribution_summary",
                    {
                        "from_date": start_month.isoformat(),
                        "to_date": timezone.localdate().isoformat(),
                    },
                )
            )

        if "next" in text and ("installment" in text or "due" in text):
            tools.append(("get_member_next_installment", {}))

        if "loan" in text or "owe" in text or ("balance" in text and "wallet" not in text):
            tools.append(("get_member_loan_summary", {}))

        if "payment" in text and "status" in text:
            match = re.search(
                r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
                text,
            )
            if match:
                tools.append(("get_payment_status", {"intent_id": match.group(1)}))
            else:
                tools.append(("list_my_payments", {}))

        if "meeting" in text:
            meeting_match = re.search(
                r"meeting\s+([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
                text,
            )
            if meeting_match and ("minutes" in text or "summary" in text):
                tools.append(
                    (
                        "get_meeting_minutes",
                        {"meeting_id": meeting_match.group(1)},
                    )
                )
            else:
                tools.append(("list_upcoming_meetings", {}))

        if "issue" in text:
            issue_match = re.search(
                r"issue\s+([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
                text,
            )
            if issue_match:
                tools.append(("get_issue", {"issue_id": issue_match.group(1)}))
            else:
                tools.append(("list_open_issues", {}))

        if "overdue" in text or "defaulter" in text:
            tools.append(("list_overdue_installments", {}))

        if "portfolio" in text or "summarize this month" in text:
            tools.append(("get_chama_portfolio_summary", {}))

        if "anomal" in text or "fraud" in text:
            tools.append(("find_suspicious_transactions", {}))

        if "policy" in text or "constitution" in text or "minutes" in text:
            tools.append(("search_kb", {"query": message, "top_k": 5}))

        if not tools:
            tools.append(("recent_chama_summary", {}))

        # Preserve order while de-duplicating.
        deduped = []
        seen = set()
        for name, args in tools:
            key = (name, json.dumps(args, sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            deduped.append((name, args))

        return deduped

    @staticmethod
    def _log_tool_call(*, chama_id, actor, name: str, args: dict, result: Any):
        return AIGatewayService._log_tool_call_v2(
            chama_id=chama_id,
            actor=actor,
            name=name,
            args=args,
            result=result,
            allowed=True,
        )

    @staticmethod
    def _log_tool_call_v2(*, chama_id, actor, name: str, args: dict, result: Any, allowed: bool):
        result_summary = str(result)
        if len(result_summary) > 1200:
            result_summary = result_summary[:1200] + "..."
        AIToolCallLog.objects.create(
            chama_id=chama_id,
            actor=actor,
            tool_name=name,
            args=args,
            result_summary=result_summary,
            allowed=bool(allowed),
            created_by=actor,
            updated_by=actor,
        )

    @staticmethod
    def _execute_tool(*, user, chama_id, tool_name: str, args: dict):
        membership = require_membership(user, chama_id)
        effective_role = get_effective_role(user, chama_id, membership)

        if tool_name in AIGatewayService.admin_tools:
            require_admin_membership(user, chama_id)

        if tool_name == "get_my_wallet_summary":
            from apps.ai.ai_tools import ToolRouter
            from apps.chama.models import Chama

            chama = get_object_or_404(Chama, id=chama_id)
            return ToolRouter.get_my_wallet_summary(user, chama)

        if tool_name == "get_my_kyc_status":
            from apps.accounts.models import MemberKYC

            record = (
                MemberKYC.objects.filter(user=user, chama_id=chama_id)
                .order_by("-updated_at", "-created_at")
                .first()
            ) or (
                MemberKYC.objects.filter(user=user, chama__isnull=True)
                .order_by("-updated_at", "-created_at")
                .first()
            )
            if not record:
                return {"status": "not_started"}
            return {
                "kyc_id": str(record.id),
                "status": record.status,
                "onboarding_path": record.onboarding_path,
                "retry_allowed": bool(getattr(record, "retry_allowed", True)),
                "review_note": getattr(record, "review_note", "") or "",
            }

        if tool_name == "get_member_contribution_summary":
            return get_member_contribution_summary(
                chama_id=chama_id,
                member_id=user.id,
                from_date=args.get("from_date"),
                to_date=args.get("to_date"),
            )

        if tool_name == "get_member_loan_summary":
            return get_member_loan_summary(chama_id=chama_id, member_id=user.id)

        if tool_name == "get_member_next_installment":
            return get_member_next_installment(
                chama_id=chama_id,
                member_id=user.id,
                loan_id=args.get("loan_id"),
            )

        if tool_name == "get_chama_portfolio_summary":
            return get_chama_portfolio_summary(chama_id=chama_id)

        if tool_name == "list_overdue_installments":
            return list_overdue_installments(chama_id=chama_id)

        if tool_name == "get_payment_status":
            payload = get_payment_status(intent_id=args["intent_id"])
            intent = get_object_or_404(PaymentIntent, id=args["intent_id"])
            if str(intent.chama_id) != str(chama_id):
                raise AIServiceError("Payment is outside your chama scope.")
            if str(intent.created_by_id) != str(user.id) and effective_role not in {
                "CHAMA_ADMIN",
                "TREASURER",
                "SECRETARY",
            }:
                raise AIServiceError("Not allowed to access this payment intent.")
            return payload

        if tool_name == "list_my_payments":
            return list_my_payments(chama_id=chama_id, member_id=user.id)

        if tool_name == "list_failed_or_pending_payouts":
            return list_failed_or_pending_payouts(chama_id=chama_id)

        if tool_name == "list_open_issues":
            return list_open_issues(chama_id=chama_id)

        if tool_name == "get_issue":
            payload = get_issue(issue_id=args["issue_id"])
            if str(payload["chama_id"]) != str(chama_id):
                raise AIServiceError("Issue is outside your chama scope.")
            if effective_role not in {"CHAMA_ADMIN", "TREASURER", "SECRETARY"}:
                if str(payload.get("reported_user_id") or "") != str(user.id):
                    raise AIServiceError("Not allowed to access this issue details.")
            return payload

        if tool_name == "list_upcoming_meetings":
            return list_upcoming_meetings(chama_id=chama_id)

        if tool_name == "get_meeting_minutes":
            payload = get_meeting_minutes(meeting_id=args["meeting_id"])
            if str(payload["chama_id"]) != str(chama_id):
                raise AIServiceError("Meeting is outside your chama scope.")
            return payload

        if tool_name == "search_kb":
            return {
                "query": args.get("query", ""),
                "results": KnowledgeBaseService.search(
                    chama_id=chama_id,
                    query=args.get("query", ""),
                    top_k=int(args.get("top_k", 5)),
                ),
            }

        if tool_name == "draft_notification":
            require_admin_membership(user, chama_id)
            return draft_notification(
                message=args.get("message", ""),
                channels=args.get("channels", ["email"]),
            )

        if tool_name == "send_notification":
            require_admin_membership(user, chama_id)
            target_user_id = args.get("user_id")
            from apps.accounts.models import User

            target = get_object_or_404(User, id=target_user_id)
            require_member_scope(user, chama_id, target.id)
            from apps.chama.models import Chama

            chama = get_object_or_404(Chama, id=chama_id)
            return send_notification_with_confirm(
                user=target,
                chama=chama,
                message=args.get("message", ""),
                channels=args.get("channels", ["email"]),
                confirm=bool(args.get("confirm", False)),
            )

        if tool_name == "find_suspicious_transactions":
            return find_suspicious_transactions(chama_id=chama_id)

        if tool_name == "recent_chama_summary":
            return recent_chama_summary(chama_id=chama_id)

        raise AIServiceError(f"Unsupported tool: {tool_name}")

    @staticmethod
    def _build_answer(
        tool_runs: list[ToolExecution],
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        """
        Deterministic fallback answer builder.

        Returns:
            (answer_text, citations, actions, follow_up_suggestions)
        """
        lines: list[str] = []
        citations: list[dict[str, Any]] = []
        actions: list[dict[str, Any]] = []
        follow_ups: list[str] = []

        def _add_action(label: str, href: str):
            if not href:
                return
            key = (label.strip().lower(), href.strip().lower())
            if any((a.get("label", "").strip().lower(), a.get("href", "").strip().lower()) == key for a in actions):
                return
            actions.append({"label": label, "href": href})

        def _money(value: Any) -> str:
            from decimal import Decimal, InvalidOperation

            try:
                as_decimal = Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError):
                return f"KES {value}"
            quantized = as_decimal.quantize(Decimal("0.01"))
            return f"KES {quantized:,.2f}"

        permission_denied = False

        for run in tool_runs:
            result = run.result
            if isinstance(result, dict) and result.get("permission_error"):
                permission_denied = True
                continue

            if isinstance(result, dict):
                if run.tool_name == "get_my_wallet_summary":
                    currency = result.get("currency") or "KES"
                    lines.append(f"Your wallet summary ({currency}):")
                    if "net_balance" in result:
                        lines.append(f"- Net balance: {_money(result.get('net_balance'))}")
                    if "total_contributions" in result:
                        lines.append(f"- Contributions: {_money(result.get('total_contributions'))}")
                    if "total_withdrawals" in result:
                        lines.append(f"- Withdrawals: {_money(result.get('total_withdrawals'))}")
                    if "outstanding_loans" in result:
                        lines.append(f"- Outstanding loans: {_money(result.get('outstanding_loans'))}")
                    _add_action("Open Payments", "/app/payments")
                    _add_action("Open Finance", "/app/finance")
                    follow_ups.extend(
                        [
                            "Why can’t I withdraw?",
                            "Show my recent payment activity.",
                        ]
                    )

                elif run.tool_name == "get_my_kyc_status":
                    status_value = result.get("status") or "unknown"
                    lines.append(f"Your KYC status: {status_value}.")
                    if result.get("review_note"):
                        lines.append(str(result.get("review_note")))
                    _add_action("Open KYC", "/app/kyc")
                    follow_ups.extend(
                        [
                            "What do I need to complete KYC?",
                            "Why was my KYC rejected?",
                        ]
                    )

                elif run.tool_name == "get_member_contribution_summary":
                    total = result.get("total_contributed")
                    count = result.get("count", 0)
                    lines.append(f"You’ve made {count} contribution(s) totaling {_money(total)} in this period.")
                    records = result.get("records") or []
                    if records:
                        latest = records[0]
                        lines.append(
                            f"Latest: {_money(latest.get('amount'))} on {latest.get('date_paid')} (receipt {latest.get('receipt_code') or '—'})."
                        )
                        citations.append({"type": "contribution", "id": latest.get("id")})
                    _add_action("Open Contributions", "/app/contributions")
                    follow_ups.extend(
                        [
                            "Show my last 5 contributions.",
                            "What do I need to pay next?",
                        ]
                    )

                elif run.tool_name == "get_member_loan_summary":
                    loans = result.get("loans") or []
                    if not loans:
                        lines.append("You don’t have any loans recorded in this chama.")
                    else:
                        outstanding_total = 0
                        for loan in loans:
                            try:
                                outstanding_total += float(loan.get("outstanding") or 0)
                            except Exception:  # noqa: BLE001
                                pass
                        lines.append(f"You have {len(loans)} loan(s) on record. Estimated outstanding: {_money(outstanding_total)}.")
                        for loan in loans[:2]:
                            lines.append(
                                f"- Loan {str(loan.get('id') or '')[:8]}…: status {loan.get('status')}, outstanding {_money(loan.get('outstanding'))}."
                            )
                            citations.append({"type": "loan", "id": loan.get("id")})
                    _add_action("Open Loans", "/app/loans")
                    follow_ups.extend(
                        [
                            "When is my next loan installment due?",
                            "Show my loan repayment options.",
                        ]
                    )

                elif run.tool_name == "get_member_next_installment":
                    nxt = result.get("next_installment")
                    if not nxt:
                        lines.append("No due or overdue loan installments found.")
                    else:
                        lines.append(
                            f"Next installment: {_money(nxt.get('expected_amount'))} due {nxt.get('due_date')} (status: {nxt.get('status')})."
                        )
                        citations.append({"type": "installment", "id": nxt.get("installment_id")})
                    _add_action("Open Loans", "/app/loans")

                elif run.tool_name == "list_upcoming_meetings":
                    items = result.get("items") or []
                    if not items:
                        lines.append("There are no upcoming meetings scheduled right now.")
                    else:
                        lines.append(f"Upcoming meetings ({min(len(items), 3)} shown):")
                        for item in items[:3]:
                            lines.append(f"- {item.get('title')}: {item.get('date')}")
                            citations.append({"type": "meeting", "id": item.get("meeting_id")})
                    _add_action("Open Meetings", "/app/meetings")
                    follow_ups.append("What should I prepare for the next meeting?")

                elif run.tool_name == "get_chama_portfolio_summary":
                    lines.append("Chama loan portfolio summary:")
                    lines.append(f"- Total loans out: {_money(result.get('total_loans_out'))}")
                    lines.append(f"- Outstanding: {_money(result.get('outstanding'))}")
                    lines.append(f"- Overdue loans: {result.get('overdue_count', 0)}")
                    lines.append(f"- Repayment rate: {result.get('repayment_rate_percent', '0')}%")
                    _add_action("Open Finance", "/app/finance")
                    follow_ups.extend(
                        [
                            "List overdue loans.",
                            "Which members are highest risk right now?",
                        ]
                    )

                elif run.tool_name == "list_overdue_installments":
                    items = result.get("items") or []
                    count = result.get("count", 0)
                    if not items:
                        lines.append("No overdue installments found.")
                    else:
                        lines.append(f"Overdue installments: {count} (showing up to 3)")
                        for item in items[:3]:
                            lines.append(
                                f"- {item.get('member_name')} owes {_money(item.get('expected_amount'))} due {item.get('due_date')}."
                            )
                            citations.append({"type": "installment", "id": item.get("installment_id")})
                    _add_action("Open Loans", "/app/loans")

                elif run.tool_name == "list_failed_or_pending_payouts":
                    items = result.get("items") or []
                    count = result.get("count", 0)
                    if not items:
                        lines.append("No failed or pending payouts found.")
                    else:
                        lines.append(f"Failed/pending payouts: {count} (showing up to 3)")
                        for item in items[:3]:
                            lines.append(
                                f"- {_money(item.get('amount'))} to {item.get('phone')} is {item.get('status')}."
                            )
                            citations.append({"type": "payout", "id": item.get("payout_id")})
                    _add_action("Open Payments", "/app/payments")

                elif run.tool_name == "list_open_issues":
                    items = result.get("items") or []
                    count = result.get("count", 0)
                    if not items:
                        lines.append("No open issues right now.")
                    else:
                        lines.append(f"Open issues: {count} (showing up to 3)")
                        for item in items[:3]:
                            lines.append(f"- [{item.get('priority')}] {item.get('title')}")
                            citations.append({"type": "issue", "id": item.get("id")})
                    _add_action("Open Issues", "/app/issues")

                elif run.tool_name == "get_issue":
                    lines.append(f"Issue: {result.get('title')}")
                    lines.append(f"Status: {result.get('status')} • Priority: {result.get('priority')}")
                    if result.get("description"):
                        lines.append(str(result.get("description")))
                    citations.append({"type": "issue", "id": result.get("id")})
                    _add_action("Open Issues", "/app/issues")

                elif run.tool_name == "get_meeting_minutes":
                    lines.append(f"Meeting: {result.get('title')} ({result.get('date')})")
                    if result.get("minutes_text"):
                        excerpt = str(result.get("minutes_text") or "").strip()
                        if excerpt:
                            lines.append(excerpt[:360] + ("…" if len(excerpt) > 360 else ""))
                    citations.append({"type": "meeting", "id": result.get("meeting_id")})
                    _add_action("Open Meetings", "/app/meetings")

                elif run.tool_name == "get_payment_status":
                    lines.append(f"Payment status: {result.get('status')} for {_money(result.get('amount'))}.")
                    lines.append(f"Created at: {result.get('created_at')}")
                    citations.append({"type": "payment_intent", "id": result.get("intent_id")})
                    _add_action("Open Payments", "/app/payments")

                elif run.tool_name == "list_my_payments":
                    items = result.get("items") or []
                    if not items:
                        lines.append("I can’t find any recent payments you initiated in this chama.")
                    else:
                        lines.append("Your recent payments (up to 3):")
                        for item in items[:3]:
                            lines.append(
                                f"- {_money(item.get('amount'))} • {item.get('status')} • {item.get('created_at')}"
                            )
                            citations.append({"type": "payment_intent", "id": item.get("intent_id")})
                    _add_action("Open Payments", "/app/payments")

                elif run.tool_name == "search_kb":
                    results = result.get("results") or []
                    if not results:
                        lines.append("I couldn’t find relevant policy/knowledge base content for that query.")
                    else:
                        lines.append("Relevant knowledge base matches (top 3):")
                        for hit in results[:3]:
                            lines.append(f"- {hit.get('document_title')}: {str(hit.get('text') or '')[:180]}…")
                            citations.append({"type": "kb_chunk", "id": hit.get("chunk_id")})

                else:
                    # Generic fallback
                    if "count" in result and not lines:
                        lines.append(f"Found {result.get('count')} matching item(s).")
                    if "items" in result and isinstance(result.get("items"), list) and not lines:
                        lines.append("I pulled the latest matching records for you.")

        if permission_denied:
            lines.insert(0, "I can’t access part of that request with your current role permissions.")

        if not lines:
            lines = [
                "I couldn’t find matching records for that request.",
                "Try rephrasing, or open the relevant module for the latest details.",
            ]
            _add_action("Open Dashboard", "/app")

        # De-dupe follow ups while preserving order.
        deduped_follow_ups: list[str] = []
        seen = set()
        for item in follow_ups:
            key = item.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped_follow_ups.append(item)

        return "\n".join(lines).strip(), citations, actions[:4], deduped_follow_ups[:4]

    @staticmethod
    @transaction.atomic
    def chat(*, user, chama_id, mode: str, message: str, conversation_id=None):
        moderation = AIModerationService.moderate_text(message)
        if not moderation["allowed"]:
            raise AIServiceError(moderation["reason"])

        membership = require_membership(user, chama_id)
        effective_role = get_effective_role(user, chama_id, membership)
        conversation = AIGatewayService._resolve_conversation(
            user=user,
            chama_id=chama_id,
            mode=mode,
            conversation_id=conversation_id,
        )

        AIMessage.objects.create(
            conversation=conversation,
            role=AIMessageRole.USER,
            content=message,
            created_by=user,
            updated_by=user,
        )

        # Check for greetings and small talk
        greeting_type = detect_greeting(message)
        if greeting_type:
            greeting_response = get_greeting_response(greeting_type, user.first_name or user.username)
            assistant_message = AIMessage.objects.create(
                conversation=conversation,
                role=AIMessageRole.ASSISTANT,
                content=greeting_response,
                tool_payload={
                    "citations": [],
                    "actions": [],
                    "follow_up_suggestions": [],
                },
                created_by=user,
                updated_by=user,
            )
            create_audit_log(
                actor=user,
                chama_id=chama_id,
                action="ai_greeting_response",
                entity_type="AIMessage",
                entity_id=assistant_message.id,
                metadata={"greeting_type": greeting_type},
            )
            return {
                "conversation_id": str(conversation.id),
                "message_id": str(assistant_message.id),
                "answer": greeting_response,
                "actions": [],
                "follow_up_suggestions": [],
                "citations": [],
            }

        runs: list[ToolExecution] = []
        answer = ""
        used_responses_api = False
        if OpenAI and getattr(settings, "OPENAI_API_KEY", ""):
            try:
                answer, runs = AIGatewayService._chat_with_openai_responses(
                    user=user,
                    chama_id=chama_id,
                    membership_role=effective_role or membership.role,
                    message=message,
                )
                used_responses_api = True
            except Exception:  # noqa: BLE001
                logger.exception(
                    "OpenAI responses tool-calling failed; falling back to deterministic planner."
                )
                runs = []
                answer = ""

        if not runs:
            tool_plan = AIGatewayService._detect_tools(message=message, mode=mode)
            for name, args in tool_plan:
                try:
                    result = AIGatewayService._execute_tool(
                        user=user,
                        chama_id=chama_id,
                        tool_name=name,
                        args=args,
                    )
                    allowed = True
                except PermissionDenied as exc:
                    result = {"permission_error": True, "detail": str(exc)}
                    allowed = False
                except AIServiceError as exc:
                    result = {"error": True, "detail": str(exc)}
                    allowed = True
                except Exception as exc:  # noqa: BLE001
                    logger.exception("AI tool execution failed")
                    result = {"error": True, "detail": "Tool execution failed."}
                    allowed = True

                runs.append(ToolExecution(tool_name=name, args=args, result={**result, "_allowed": allowed}))

        sanitized_runs: list[ToolExecution] = []
        for run in runs:
            result = run.result
            allowed = True
            if isinstance(result, dict) and "_allowed" in result:
                allowed = bool(result.get("_allowed"))
                result = {k: v for k, v in result.items() if k != "_allowed"}

            AIGatewayService._log_tool_call_v2(
                chama_id=chama_id,
                actor=user,
                name=run.tool_name,
                args=run.args,
                result=result,
                allowed=allowed,
            )
            AIMessage.objects.create(
                conversation=conversation,
                role=AIMessageRole.TOOL,
                content=f"Tool {run.tool_name} executed",
                tool_name=run.tool_name,
                tool_payload={"args": run.args, "result": result, "allowed": allowed},
                created_by=user,
                updated_by=user,
            )
            sanitized_runs.append(ToolExecution(tool_name=run.tool_name, args=run.args, result=result))

        synthesized_answer, citations, actions, follow_up_suggestions = AIGatewayService._build_answer(sanitized_runs)
        if not answer:
            answer = synthesized_answer

        assistant_message = AIMessage.objects.create(
            conversation=conversation,
            role=AIMessageRole.ASSISTANT,
            content=answer,
            tool_payload={
                "citations": citations,
                "actions": actions,
                "follow_up_suggestions": follow_up_suggestions,
            },
            created_by=user,
            updated_by=user,
        )

        AIActionLog.objects.create(
            chama_id=chama_id,
            actor=user,
            action_type="chat_response",
            references={
                "conversation_id": str(conversation.id),
                "message_id": str(assistant_message.id),
            },
            model_name=getattr(settings, "AI_CHAT_MODEL", "tool-only"),
            created_by=user,
            updated_by=user,
        )
        create_audit_log(
            actor=user,
            chama_id=chama_id,
            action="ai_chat_response",
            entity_type="AIConversation",
            entity_id=conversation.id,
            metadata={
                "mode": mode,
                "tool_count": len(sanitized_runs),
                "citations": citations,
                "used_responses_api": used_responses_api,
            },
        )

        return {
            "conversation_id": conversation.id,
            "message_id": assistant_message.id,
            "answer": answer,
            "citations": citations,
            "actions": actions,
            "follow_up_suggestions": follow_up_suggestions,
            "tool_usage": [
                {
                    "tool_name": run.tool_name,
                    "args": run.args,
                }
                for run in sanitized_runs
            ],
        }

    @staticmethod
    @transaction.atomic
    def chat_global(*, user, mode: str, message: str, conversation_id=None):
        """
        Authenticated assistant without a chama context.

        This is intentionally limited: it must never expose chama-scoped data
        without an explicit chama context + membership enforcement.
        """
        moderation = AIModerationService.moderate_text(message)
        if not moderation["allowed"]:
            raise AIServiceError(moderation["reason"])

        conversation = AIGatewayService._resolve_conversation(
            user=user,
            chama_id=None,
            mode=mode or "member_assistant",
            conversation_id=conversation_id,
        )

        AIMessage.objects.create(
            conversation=conversation,
            role=AIMessageRole.USER,
            content=message,
            created_by=user,
            updated_by=user,
        )

        runs: list[ToolExecution] = []
        lowered = str(message or "").lower()
        if "kyc" in lowered or ("verify" in lowered and "identity" in lowered):
            try:
                from apps.accounts.models import MemberKYC

                record = (
                    MemberKYC.objects.filter(user=user, chama__isnull=True)
                    .order_by("-updated_at", "-created_at")
                    .first()
                ) or (
                    MemberKYC.objects.filter(user=user)
                    .order_by("-updated_at", "-created_at")
                    .first()
                )
                if not record:
                    result = {"status": "not_started"}
                else:
                    result = {
                        "kyc_id": str(record.id),
                        "status": record.status,
                        "onboarding_path": record.onboarding_path,
                        "retry_allowed": bool(getattr(record, "retry_allowed", True)),
                        "review_note": getattr(record, "review_note", "") or "",
                    }
                runs.append(ToolExecution(tool_name="get_my_kyc_status", args={}, result=result))
            except Exception:  # noqa: BLE001
                logger.exception("Global assistant tool execution failed")
                runs.append(
                    ToolExecution(
                        tool_name="get_my_kyc_status",
                        args={},
                        result={"error": True, "detail": "Unable to load KYC status right now."},
                    )
                )

        synthesized_answer, citations, actions, follow_up_suggestions = AIGatewayService._build_answer(runs)
        answer = synthesized_answer or (
            "I can help with onboarding, KYC guidance, and navigation tips. "
            "If you’re asking about wallet/loans/contributions, please select a chama first."
        )

        assistant_message = AIMessage.objects.create(
            conversation=conversation,
            role=AIMessageRole.ASSISTANT,
            content=answer,
            tool_payload={
                "citations": citations,
                "actions": actions,
                "follow_up_suggestions": follow_up_suggestions,
            },
            created_by=user,
            updated_by=user,
        )

        create_audit_log(
            actor=user,
            chama_id=None,
            action="ai_chat_response_global",
            entity_type="AIConversation",
            entity_id=conversation.id,
            metadata={
                "mode": mode,
                "tool_count": len(runs),
            },
        )

        return {
            "conversation_id": conversation.id,
            "message_id": assistant_message.id,
            "answer": answer,
            "citations": citations,
            "actions": actions,
            "follow_up_suggestions": follow_up_suggestions,
            "tool_usage": [
                {
                    "tool_name": run.tool_name,
                    "args": run.args,
                }
                for run in runs
            ],
        }


class AIWorkflowService:
    @staticmethod
    def triage_issue(*, issue_id, actor):
        issue = get_object_or_404(Issue, id=issue_id)
        membership = require_membership(actor, issue.chama_id)
        effective_role = get_effective_role(actor, issue.chama_id, membership)
        if effective_role not in {"CHAMA_ADMIN", "SECRETARY", "TREASURER"}:
            raise AIServiceError("Only admin/secretary/treasurer can run issue triage.")

        text = f"{issue.title}\n{issue.description}".lower()
        category = issue.category
        if any(token in text for token in ["loan", "repay", "installment"]):
            category = "loan"
        elif any(token in text for token in ["payment", "contribution", "ledger"]):
            category = "finance"
        elif any(token in text for token in ["meeting", "minutes", "attendance"]):
            category = "meeting"

        priority = issue.priority
        if any(token in text for token in ["urgent", "fraud", "immediately", "stolen"]):
            priority = "urgent"
        elif any(token in text for token in ["delay", "late", "overdue"]):
            priority = "high"

        suggested_role = "SECRETARY"
        if category in {"finance", "loan"}:
            suggested_role = "TREASURER"

        draft_response = (
            "Thank you for reporting this issue. "
            "The matter has been logged for review and will be handled according to chama policy."
        )

        payload = {
            "issue_id": str(issue.id),
            "category": category,
            "priority": priority,
            "suggested_assignee_role": suggested_role,
            "draft_response": draft_response,
        }

        AIActionLog.objects.create(
            chama=issue.chama,
            actor=actor,
            action_type="issue_triage",
            references={"issue_id": str(issue.id)},
            model_name=getattr(settings, "AI_CHAT_MODEL", "tool-only"),
            created_by=actor,
            updated_by=actor,
        )
        create_audit_log(
            actor=actor,
            chama_id=issue.chama_id,
            action="ai_issue_triage",
            entity_type="Issue",
            entity_id=issue.id,
            metadata=payload,
        )
        return payload

    @staticmethod
    def summarize_meeting(*, meeting_id, actor):
        meeting = get_object_or_404(Meeting, id=meeting_id)
        require_membership(actor, meeting.chama_id)

        source = (meeting.minutes_text or "").strip() or (meeting.agenda or "").strip()
        if not source:
            raise AIServiceError("Meeting has no minutes or agenda to summarize.")

        lines = [line.strip("-• \t") for line in source.splitlines() if line.strip()]
        summary = " ".join(lines[:4])
        action_items = []
        for line in lines:
            lowered = line.lower()
            if any(
                marker in lowered
                for marker in ["action", "follow up", "assign", "deadline"]
            ):
                action_items.append({"item": line, "owner": None, "due_date": None})

        if not action_items:
            action_items = [
                {"item": item, "owner": None, "due_date": None} for item in lines[:3]
            ]

        complaint_markers = ["complaint", "delay", "problem", "issue", "dispute"]
        repeated_complaints = [
            line for line in lines if any(token in line.lower() for token in complaint_markers)
        ]
        unresolved_items = [
            {
                "resolution_id": str(item.id),
                "text": item.text,
                "due_date": item.due_date.isoformat() if item.due_date else None,
            }
            for item in meeting.resolutions.filter(status="open").order_by("due_date")[:20]
        ]

        positive_terms = {"completed", "resolved", "approved", "success", "on track"}
        negative_terms = {"overdue", "late", "delay", "failed", "blocked"}
        positive_hits = sum(
            1 for line in lines if any(term in line.lower() for term in positive_terms)
        )
        negative_hits = sum(
            1 for line in lines if any(term in line.lower() for term in negative_terms)
        )
        if negative_hits > positive_hits:
            sentiment = "negative"
        elif positive_hits > negative_hits:
            sentiment = "positive"
        else:
            sentiment = "neutral"

        payload = {
            "meeting_id": str(meeting.id),
            "summary": summary,
            "action_items": action_items,
            "unresolved_action_items": unresolved_items,
            "repeated_complaint_signals": len(repeated_complaints),
            "sentiment": sentiment,
        }

        AIActionLog.objects.create(
            chama_id=meeting.chama_id,
            actor=actor,
            action_type="meeting_summary",
            references={"meeting_id": str(meeting.id)},
            model_name=getattr(settings, "AI_CHAT_MODEL", "tool-only"),
            created_by=actor,
            updated_by=actor,
        )
        create_audit_log(
            actor=actor,
            chama_id=meeting.chama_id,
            action="ai_meeting_summary",
            entity_type="Meeting",
            entity_id=meeting.id,
            metadata={"action_items": len(action_items)},
        )
        return payload

    @staticmethod
    def explain_report(*, report_id, actor):
        report = get_object_or_404(ReportRun, id=report_id)
        membership = require_membership(actor, report.chama_id)
        effective_role = get_effective_role(actor, report.chama_id, membership)

        if str(report.generated_by_id or "") not in {
            str(actor.id),
            "",
        } and effective_role not in {
            "CHAMA_ADMIN",
            "TREASURER",
            "AUDITOR",
            "SECRETARY",
        }:
            raise AIServiceError("Not allowed to explain this report.")

        if report.status != "success":
            raise AIServiceError("Report is not ready for explanation.")

        result = report.result or {}
        anomalies = []
        if isinstance(result, dict):
            defaulters_count = int(result.get("defaulters_count") or 0)
            if defaulters_count > 0:
                anomalies.append(f"Detected {defaulters_count} defaulter records.")
            cashflow = result.get("cashflow") or {}
            if (
                cashflow
                and cashflow.get("net")
                and str(cashflow.get("net", "")).startswith("-")
            ):
                anomalies.append("Net cashflow is negative in this period.")

        explanation = (
            "This report summarizes chama performance for the selected period. "
            "Check the totals and cashflow sections, then review anomalies for action."
        )

        payload = {
            "report_id": str(report.id),
            "report_type": report.report_type,
            "explanation": explanation,
            "anomalies": anomalies,
            "highlights": {
                "status": report.status,
                "generated_at": report.created_at.isoformat(),
            },
        }

        AIActionLog.objects.create(
            chama_id=report.chama_id,
            actor=actor,
            action_type="report_explain",
            references={"report_id": str(report.id)},
            model_name=getattr(settings, "AI_CHAT_MODEL", "tool-only"),
            created_by=actor,
            updated_by=actor,
        )
        create_audit_log(
            actor=actor,
            chama_id=report.chama_id,
            action="ai_report_explain",
            entity_type="ReportRun",
            entity_id=report.id,
            metadata={"anomalies": anomalies},
        )
        return payload

    @staticmethod
    def weekly_insights_for_chama(*, chama_id, actor=None):
        portfolio = get_chama_portfolio_summary(chama_id=chama_id)
        overdue = list_overdue_installments(chama_id=chama_id)
        suspicious = find_suspicious_transactions(chama_id=chama_id)

        payload = {
            "chama_id": str(chama_id),
            "generated_at": timezone.now().isoformat(),
            "portfolio": portfolio,
            "overdue": overdue,
            "suspicious": suspicious,
        }

        AIActionLog.objects.create(
            chama_id=chama_id,
            actor=actor,
            action_type="weekly_insights",
            references={},
            model_name=getattr(settings, "AI_CHAT_MODEL", "tool-only"),
            created_by=actor,
            updated_by=actor,
        )
        return payload

    @staticmethod
    def _require_governance_actor(*, actor, chama_id):
        if actor is None:
            return
        membership = require_membership(actor, chama_id)
        effective_role = get_effective_role(actor, chama_id, membership)
        if effective_role not in {
            "CHAMA_ADMIN",
            "TREASURER",
            "SECRETARY",
            "AUDITOR",
        }:
            raise AIServiceError("Only governance roles can run this AI workflow.")

    @staticmethod
    def membership_risk_scoring_for_chama(*, chama_id, actor=None):
        from apps.chama.models import Membership, MemberStatus
        from apps.security.models import DeviceSession

        AIWorkflowService._require_governance_actor(actor=actor, chama_id=chama_id)

        memberships = list(
            Membership.objects.select_related("user").filter(
                chama_id=chama_id,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
                exited_at__isnull=True,
            )
        )
        user_ids = [item.user_id for item in memberships]
        if not memberships:
            payload = {"chama_id": str(chama_id), "members": [], "count": 0}
            return payload

        name_counter = Counter(
            (item.user.full_name or "").strip().lower() for item in memberships
        )
        membership_counts = {
            str(row["user_id"]): int(row["total"])
            for row in Membership.objects.filter(user_id__in=user_ids)
            .values("user_id")
            .annotate(total=Count("id"))
        }

        recent_sessions = (
            DeviceSession.objects.filter(
                user_id__in=user_ids,
                is_revoked=False,
                last_seen__gte=timezone.now() - timedelta(days=90),
            )
            .values("user_id", "ip_address", "user_agent")
            .annotate(events=Count("id"))
        )
        shared_fingerprints = Counter()
        per_user_fingerprints: dict[str, set[tuple[str, str]]] = {}
        for row in recent_sessions:
            fp = (str(row["ip_address"] or ""), str(row["user_agent"] or ""))
            shared_fingerprints[fp] += 1
            key = str(row["user_id"])
            per_user_fingerprints.setdefault(key, set()).add(fp)

        high_shared_users = set()
        for user_id, fingerprints in per_user_fingerprints.items():
            if any(shared_fingerprints[fp] >= 3 for fp in fingerprints):
                high_shared_users.add(user_id)

        rows = []
        today = timezone.localdate()
        for membership in memberships:
            normalized_name = (membership.user.full_name or "").strip().lower()
            account_age_days = max(
                (today - membership.user.date_joined.date()).days,
                0,
            )
            verified_phone = bool(
                re.match(r"^\+254(?:7|1)\d{8}$", str(membership.user.phone or ""))
            )
            cross_join_count = membership_counts.get(str(membership.user_id), 1)
            similar_name_count = name_counter.get(normalized_name, 1)

            score = Decimal("5.0")
            if account_age_days < 30:
                score += Decimal("20")
            elif account_age_days < 90:
                score += Decimal("10")
            if cross_join_count > 2:
                score += Decimal("15")
            if similar_name_count > 1:
                score += Decimal("15")
            if not verified_phone:
                score += Decimal("20")
            if str(membership.user_id) in high_shared_users:
                score += Decimal("25")

            score = max(Decimal("0"), min(score, Decimal("100")))
            if score >= Decimal("70"):
                band = "HIGH"
                recommendation = "Manual KYC review and secretary approval required."
            elif score >= Decimal("40"):
                band = "MEDIUM"
                recommendation = "Request additional profile verification."
            else:
                band = "LOW"
                recommendation = "Proceed with normal governance workflow."

            rows.append(
                {
                    "member_id": str(membership.user_id),
                    "member_name": membership.user.full_name,
                    "risk_score": str(score.quantize(Decimal("0.01"))),
                    "risk_band": band,
                    "factors": {
                        "account_age_days": account_age_days,
                        "cross_chama_memberships": cross_join_count,
                        "similar_name_count": similar_name_count,
                        "verified_phone": verified_phone,
                        "shared_device_pattern": str(membership.user_id)
                        in high_shared_users,
                    },
                    "recommendation": recommendation,
                }
            )

        rows.sort(key=lambda item: Decimal(item["risk_score"]), reverse=True)
        payload = {
            "chama_id": str(chama_id),
            "generated_at": timezone.now().isoformat(),
            "count": len(rows),
            "members": rows[:500],
        }
        AIActionLog.objects.create(
            chama_id=chama_id,
            actor=actor,
            action_type="membership_risk_scoring",
            references={},
            model_name=getattr(settings, "AI_CHAT_MODEL", "tool-only"),
            created_by=actor,
            updated_by=actor,
        )
        return payload

    @staticmethod
    def loan_default_prediction_for_chama(*, chama_id, actor=None):
        AIWorkflowService._require_governance_actor(actor=actor, chama_id=chama_id)

        loans = Loan.objects.select_related("member").filter(
            chama_id=chama_id,
            status__in=[
                LoanStatus.APPROVED,
                LoanStatus.DISBURSING,
                LoanStatus.DISBURSED,
                LoanStatus.ACTIVE,
            ],
        )
        member_ids = list(loans.values_list("member_id", flat=True).distinct())
        six_months_ago = timezone.localdate() - timedelta(days=180)

        contribution_stats = {
            str(row["member_id"]): {
                "total": Decimal(str(row["total"] or "0.00")),
                "count": int(row["count"] or 0),
            }
            for row in Contribution.objects.filter(
                chama_id=chama_id,
                member_id__in=member_ids,
                date_paid__gte=six_months_ago,
            )
            .values("member_id")
            .annotate(
                total=Coalesce(
                    Sum("amount"),
                    Value(Decimal("0.00"), output_field=DecimalField()),
                ),
                count=Count("id"),
            )
        }

        predictions = []
        for loan in loans:
            overdue_qs = loan.installments.filter(status=InstallmentStatus.OVERDUE)
            overdue_count = overdue_qs.count()
            earliest_overdue = overdue_qs.order_by("due_date").first()
            days_past_due = (
                max((timezone.localdate() - earliest_overdue.due_date).days, 0)
                if earliest_overdue
                else 0
            )
            contributor = contribution_stats.get(str(loan.member_id), {"total": Decimal("0"), "count": 0})
            avg_monthly_contribution = contributor["total"] / Decimal("6")
            if avg_monthly_contribution <= Decimal("0"):
                size_ratio = Decimal("99")
            else:
                size_ratio = loan.principal / avg_monthly_contribution

            consistency = Decimal(min(contributor["count"], 6)) / Decimal("6")
            probability = Decimal("0.10")
            probability += Decimal("0.35") if overdue_count > 0 else Decimal("0.00")
            probability += min(Decimal(days_past_due) / Decimal("365"), Decimal("0.25"))
            if consistency < Decimal("0.50"):
                probability += Decimal("0.20")
            if size_ratio > Decimal("3"):
                probability += Decimal("0.15")
            if size_ratio > Decimal("5"):
                probability += Decimal("0.10")
            probability = max(Decimal("0.01"), min(probability, Decimal("0.99")))

            if probability >= Decimal("0.70"):
                risk_band = "HIGH"
                suggestion = "Reduce amount or require guarantor before approval."
            elif probability >= Decimal("0.40"):
                risk_band = "MEDIUM"
                suggestion = "Approve conditionally with closer monitoring."
            else:
                risk_band = "LOW"
                suggestion = "Approve under standard monitoring."

            predictions.append(
                {
                    "loan_id": str(loan.id),
                    "member_id": str(loan.member_id),
                    "member_name": loan.member.full_name,
                    "default_probability_percent": str(
                        (probability * Decimal("100")).quantize(Decimal("0.01"))
                    ),
                    "risk_band": risk_band,
                    "features": {
                        "days_past_due": days_past_due,
                        "overdue_installments": overdue_count,
                        "contribution_consistency": str(
                            consistency.quantize(Decimal("0.01"))
                        ),
                        "loan_to_contribution_ratio": str(
                            size_ratio.quantize(Decimal("0.01"))
                        ),
                    },
                    "suggested_action": suggestion,
                }
            )

        predictions.sort(
            key=lambda row: Decimal(row["default_probability_percent"]),
            reverse=True,
        )
        payload = {
            "chama_id": str(chama_id),
            "generated_at": timezone.now().isoformat(),
            "count": len(predictions),
            "predictions": predictions[:500],
        }
        AIActionLog.objects.create(
            chama_id=chama_id,
            actor=actor,
            action_type="loan_default_prediction",
            references={},
            model_name=getattr(settings, "AI_CHAT_MODEL", "tool-only"),
            created_by=actor,
            updated_by=actor,
        )
        return payload

    @staticmethod
    def contribution_behavior_forecast_for_chama(*, chama_id, actor=None):
        from apps.chama.models import Membership, MemberStatus
        from apps.meetings.models import Attendance, AttendanceStatus

        AIWorkflowService._require_governance_actor(actor=actor, chama_id=chama_id)

        memberships = Membership.objects.select_related("user").filter(
            chama_id=chama_id,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        )
        member_ids = list(memberships.values_list("user_id", flat=True))
        now_date = timezone.localdate()
        ninety_days = now_date - timedelta(days=90)
        one_eighty_days = now_date - timedelta(days=180)

        contribution_counts = {
            str(row["member_id"]): int(row["total"] or 0)
            for row in Contribution.objects.filter(
                chama_id=chama_id,
                member_id__in=member_ids,
                date_paid__gte=one_eighty_days,
            )
            .values("member_id")
            .annotate(total=Count("id"))
        }
        recent_contributions = {
            str(row["member_id"]): int(row["total"] or 0)
            for row in Contribution.objects.filter(
                chama_id=chama_id,
                member_id__in=member_ids,
                date_paid__gte=ninety_days,
            )
            .values("member_id")
            .annotate(total=Count("id"))
        }
        recent_attendance = {
            str(row["member_id"]): int(row["total"] or 0)
            for row in Attendance.objects.filter(
                meeting__chama_id=chama_id,
                meeting__date__date__gte=one_eighty_days,
                member_id__in=member_ids,
                status__in=[AttendanceStatus.PRESENT, AttendanceStatus.LATE],
            )
            .values("member_id")
            .annotate(total=Count("id"))
        }
        active_loan_members = set(
            Loan.objects.filter(
                chama_id=chama_id,
                status__in=[
                    LoanStatus.APPROVED,
                    LoanStatus.DISBURSING,
                    LoanStatus.DISBURSED,
                    LoanStatus.ACTIVE,
                ],
            ).values_list("member_id", flat=True)
        )

        forecasts = []
        for membership in memberships:
            member_key = str(membership.user_id)
            long_window_count = contribution_counts.get(member_key, 0)
            recent_count = recent_contributions.get(member_key, 0)
            attendance_count = recent_attendance.get(member_key, 0)

            default_prob = Decimal("0.15")
            if recent_count == 0:
                default_prob += Decimal("0.35")
            if attendance_count == 0:
                default_prob += Decimal("0.20")
            if member_key in active_loan_members:
                default_prob += Decimal("0.10")
            default_prob = min(default_prob, Decimal("0.95"))

            dropout_prob = Decimal("0.10")
            if recent_count == 0 and long_window_count <= 1:
                dropout_prob += Decimal("0.55")
            elif recent_count == 0:
                dropout_prob += Decimal("0.35")
            if attendance_count == 0:
                dropout_prob += Decimal("0.15")
            dropout_prob = min(dropout_prob, Decimal("0.95"))

            loan_request_prob = Decimal("0.20")
            if recent_count >= 3 and member_key not in active_loan_members:
                loan_request_prob += Decimal("0.40")
            if attendance_count >= 2:
                loan_request_prob += Decimal("0.15")
            loan_request_prob = min(loan_request_prob, Decimal("0.95"))

            forecasts.append(
                {
                    "member_id": member_key,
                    "member_name": membership.user.full_name,
                    "likely_default_next_month_percent": str(
                        (default_prob * Decimal("100")).quantize(Decimal("0.01"))
                    ),
                    "likely_dropout_percent": str(
                        (dropout_prob * Decimal("100")).quantize(Decimal("0.01"))
                    ),
                    "likely_loan_request_percent": str(
                        (loan_request_prob * Decimal("100")).quantize(Decimal("0.01"))
                    ),
                    "preventive_actions": [
                        "Send contribution reminder",
                        "Invite to next meeting",
                        "Offer repayment support if active loan exists",
                    ],
                }
            )

        forecasts.sort(
            key=lambda row: Decimal(row["likely_default_next_month_percent"]),
            reverse=True,
        )
        payload = {
            "chama_id": str(chama_id),
            "generated_at": timezone.now().isoformat(),
            "count": len(forecasts),
            "forecasts": forecasts[:500],
        }
        AIActionLog.objects.create(
            chama_id=chama_id,
            actor=actor,
            action_type="contribution_behavior_forecast",
            references={},
            model_name=getattr(settings, "AI_CHAT_MODEL", "tool-only"),
            created_by=actor,
            updated_by=actor,
        )
        return payload

    @staticmethod
    def governance_health_score_for_chama(*, chama_id, actor=None):
        from apps.chama.models import Membership, MemberStatus
        from apps.meetings.models import Attendance, AttendanceStatus, Meeting

        AIWorkflowService._require_governance_actor(actor=actor, chama_id=chama_id)

        today = timezone.localdate()
        last_30 = today - timedelta(days=30)
        last_90 = today - timedelta(days=90)

        active_members = Membership.objects.filter(
            chama_id=chama_id,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        )
        total_members = active_members.count()
        contributors_30 = (
            Contribution.objects.filter(
                chama_id=chama_id,
                date_paid__gte=last_30,
                member_id__in=active_members.values("user_id"),
            )
            .values("member_id")
            .distinct()
            .count()
        )
        contribution_consistency = (
            (Decimal(contributors_30) / Decimal(total_members) * Decimal("100"))
            if total_members
            else Decimal("0.00")
        )

        meetings_90 = Meeting.objects.filter(
            chama_id=chama_id,
            date__date__gte=last_90,
        ).count()
        attendance_hits = Attendance.objects.filter(
            meeting__chama_id=chama_id,
            meeting__date__date__gte=last_90,
            status__in=[AttendanceStatus.PRESENT, AttendanceStatus.LATE],
        ).count()
        attendance_rate = Decimal("0.00")
        denominator = Decimal(max(total_members * max(meetings_90, 1), 1))
        if total_members and meetings_90:
            attendance_rate = (Decimal(attendance_hits) / denominator) * Decimal("100")

        portfolio = get_chama_portfolio_summary(chama_id=chama_id)
        repayment_rate = Decimal(str(portfolio.get("repayment_rate_percent", "0.00")))

        issues_qs = Issue.objects.filter(chama_id=chama_id, created_at__date__gte=last_90)
        open_issues = issues_qs.filter(
            status__in=["open", "in_review", "assigned", "reopened", "escalated"]
        ).count()
        resolved = issues_qs.filter(status__in=["resolved", "closed"])
        avg_resolution_days = Decimal("0.00")
        if resolved.exists():
            deltas = [
                max((item.updated_at.date() - item.created_at.date()).days, 0)
                for item in resolved
            ]
            avg_resolution_days = Decimal(str(mean(deltas))).quantize(Decimal("0.01"))

        minutes_coverage = Decimal("0.00")
        if meetings_90:
            with_minutes = Meeting.objects.filter(
                chama_id=chama_id,
                date__date__gte=last_90,
            ).exclude(Q(minutes_text="") & Q(minutes_file="")).count()
            minutes_coverage = (
                Decimal(with_minutes) / Decimal(max(meetings_90, 1)) * Decimal("100")
            )

        financial_health = min(max(repayment_rate, Decimal("0.00")), Decimal("100.00"))
        participation = (
            contribution_consistency * Decimal("0.60")
            + attendance_rate * Decimal("0.40")
        )
        transparency = (
            minutes_coverage * Decimal("0.60")
            + max(Decimal("0.00"), Decimal("100.00") - Decimal(open_issues * 5))
            * Decimal("0.40")
        )
        governance_score = (
            financial_health * Decimal("0.40")
            + participation * Decimal("0.35")
            + transparency * Decimal("0.25")
        ).quantize(Decimal("0.01"))

        payload = {
            "chama_id": str(chama_id),
            "generated_at": timezone.now().isoformat(),
            "scores": {
                "governance_score": str(governance_score),
                "financial_health_score": str(financial_health.quantize(Decimal("0.01"))),
                "participation_score": str(participation.quantize(Decimal("0.01"))),
                "transparency_score": str(transparency.quantize(Decimal("0.01"))),
            },
            "metrics": {
                "active_members": total_members,
                "contributors_last_30_days": contributors_30,
                "attendance_rate_percent": str(attendance_rate.quantize(Decimal("0.01"))),
                "repayment_rate_percent": str(repayment_rate.quantize(Decimal("0.01"))),
                "open_issues": open_issues,
                "avg_resolution_days": str(avg_resolution_days),
            },
        }
        AIActionLog.objects.create(
            chama_id=chama_id,
            actor=actor,
            action_type="governance_health_score",
            references={},
            model_name=getattr(settings, "AI_CHAT_MODEL", "tool-only"),
            created_by=actor,
            updated_by=actor,
        )
        return payload

    @staticmethod
    def generate_controlled_response(
        *,
        chama_id,
        template_type: str,
        context: dict | None = None,
        actor=None,
    ):
        AIWorkflowService._require_governance_actor(actor=actor, chama_id=chama_id)
        context = context or {}
        reason = str(context.get("reason") or "policy constraints")
        policy = str(context.get("policy") or "chama policy")
        next_step = str(context.get("next_step") or "contact your chama secretary")

        templates = {
            "rejected_loan": (
                "Your loan request was not approved at this stage due to {reason}. "
                "Decision was made under {policy}. Next step: {next_step}."
            ),
            "delayed_approval": (
                "Your request is still under review because {reason}. "
                "Review follows {policy}. Next step: {next_step}."
            ),
            "warning_notice": (
                "This is an official warning regarding {reason}. "
                "It is issued under {policy}. Next step: {next_step}."
            ),
            "penalty_explanation": (
                "A penalty was applied due to {reason}. "
                "Calculation follows {policy}. Next step: {next_step}."
            ),
        }
        template = templates.get(template_type)
        if not template:
            raise AIServiceError("Unsupported controlled response template type.")

        message = template.format(reason=reason, policy=policy, next_step=next_step)
        payload = {
            "template_type": template_type,
            "message": message,
            "context": {
                "reason": reason,
                "policy": policy,
                "next_step": next_step,
            },
        }
        AIActionLog.objects.create(
            chama_id=chama_id,
            actor=actor,
            action_type="controlled_response_generator",
            references={"template_type": template_type},
            model_name=getattr(settings, "AI_CHAT_MODEL", "tool-only"),
            created_by=actor,
            updated_by=actor,
        )
        return payload

    @staticmethod
    def executive_summary_for_chama(
        *,
        chama_id,
        month: int | None = None,
        year: int | None = None,
        actor=None,
    ):
        AIWorkflowService._require_governance_actor(actor=actor, chama_id=chama_id)
        current = timezone.localdate()
        target_month = int(month or current.month)
        target_year = int(year or current.year)
        period_start = current.replace(year=target_year, month=target_month, day=1)
        if target_month == 12:
            period_end = period_start.replace(year=target_year + 1, month=1, day=1) - timedelta(days=1)
        else:
            period_end = period_start.replace(month=target_month + 1, day=1) - timedelta(days=1)

        portfolio = get_chama_portfolio_summary(chama_id=chama_id)
        governance = AIWorkflowService.governance_health_score_for_chama(
            chama_id=chama_id,
            actor=actor,
        )
        suspicious = find_suspicious_transactions(chama_id=chama_id)

        contributions_total = Contribution.objects.filter(
            chama_id=chama_id,
            date_paid__gte=period_start,
            date_paid__lte=period_end,
        ).aggregate(
            total=Coalesce(
                Sum("amount"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        loans_issued = Loan.objects.filter(
            chama_id=chama_id,
            requested_at__date__gte=period_start,
            requested_at__date__lte=period_end,
        )
        active_loans = loans_issued.filter(
            status__in=[LoanStatus.APPROVED, LoanStatus.DISBURSING, LoanStatus.DISBURSED, LoanStatus.ACTIVE]
        ).count()

        recommendations = []
        if Decimal(str(portfolio.get("repayment_rate_percent", "0.00"))) < Decimal("70.00"):
            recommendations.append("Strengthen repayment follow-up and restructure high-risk loans.")
        if int(portfolio.get("overdue_count", 0)) > 0:
            recommendations.append("Trigger early delinquency outreach for overdue members.")
        if len(suspicious.get("odd_withdrawals", [])) > 0:
            recommendations.append("Review high-value withdrawals in audit committee.")
        if not recommendations:
            recommendations.append("Maintain current controls and continue weekly monitoring.")

        payload = {
            "chama_id": str(chama_id),
            "period": {"month": target_month, "year": target_year},
            "summary": (
                "Monthly executive summary generated from internal records. "
                "Review growth, risk, revenue, and engagement indicators."
            ),
            "growth": {
                "new_loans_requested": loans_issued.count(),
                "active_loans": active_loans,
            },
            "revenue": {
                "contributions_total": str(Decimal(str(contributions_total or "0.00")).quantize(Decimal("0.01"))),
                "repayment_rate_percent": str(portfolio.get("repayment_rate_percent", "0.00")),
            },
            "risk": {
                "overdue_count": int(portfolio.get("overdue_count", 0)),
                "defaulters_count": int(portfolio.get("defaulters_count", 0)),
                "suspicious_groups": len(suspicious.get("odd_withdrawals", [])),
            },
            "engagement": governance.get("metrics", {}),
            "recommendations": recommendations,
            "generated_at": timezone.now().isoformat(),
        }
        AIActionLog.objects.create(
            chama_id=chama_id,
            actor=actor,
            action_type="executive_summary",
            references={"month": target_month, "year": target_year},
            model_name=getattr(settings, "AI_CHAT_MODEL", "tool-only"),
            created_by=actor,
            updated_by=actor,
        )
        return payload


def run_nightly_kb_reindex(*, chama_id=None):
    queryset = KnowledgeDocument.objects.all()
    if chama_id:
        queryset = queryset.filter(chama_id=chama_id)

    total_chunks = 0
    docs = 0
    for document in queryset.select_related("chama"):
        docs += 1
        total_chunks += KnowledgeBaseService.reindex_document(
            document=document, actor=None
        )

    return {
        "documents": docs,
        "chunks": total_chunks,
        "completed_at": timezone.now().isoformat(),
    }


def ai_daily_anomaly_scan(*, chama_id=None):
    from apps.chama.models import Chama

    chamas = Chama.objects.filter(status="active")
    if chama_id:
        chamas = chamas.filter(id=chama_id)

    findings = []
    for chama in chamas:
        summary = find_suspicious_transactions(chama_id=chama.id)
        withdrawal_qs = PaymentIntent.objects.filter(
            chama_id=chama.id,
            intent_type__in=[
                PaymentIntentType.WITHDRAWAL,
                PaymentIntentType.LOAN_DISBURSEMENT,
            ],
        ).order_by("-created_at")
        historical_amounts = [
            Decimal(str(row.amount)) for row in withdrawal_qs[:200] if row.amount is not None
        ]
        recent_amounts = [
            Decimal(str(row.amount))
            for row in withdrawal_qs.filter(
                created_at__gte=timezone.now() - timedelta(hours=24)
            )[:50]
            if row.amount is not None
        ]

        zscore_flags = []
        if len(historical_amounts) >= 10 and len(recent_amounts) > 0:
            historical_float = [float(item) for item in historical_amounts]
            mu = mean(historical_float)
            sigma = pstdev(historical_float) if len(historical_float) > 1 else 0.0
            if sigma > 0:
                for amount in recent_amounts:
                    z_score = (float(amount) - mu) / sigma
                    if z_score >= 3:
                        zscore_flags.append(
                            {
                                "amount": str(amount),
                                "z_score": round(z_score, 2),
                            }
                        )

        loans_today = Loan.objects.filter(
            chama_id=chama.id,
            requested_at__date=timezone.localdate(),
        ).count()
        avg_loans_daily = (
            Loan.objects.filter(
                chama_id=chama.id,
                requested_at__date__gte=timezone.localdate() - timedelta(days=30),
            ).count()
            / 30
        )
        loan_spike = loans_today > max(int(avg_loans_daily * 2), 3)

        if (
            summary["duplicate_idempotency_keys"]
            or summary["odd_withdrawals"]
            or zscore_flags
            or loan_spike
        ):
            findings.append(
                {
                    "chama_id": str(chama.id),
                    **summary,
                    "zscore_outlier_withdrawals": zscore_flags,
                    "loan_spike_detected": loan_spike,
                    "loans_today": loans_today,
                    "avg_loans_daily_30d": round(avg_loans_daily, 2),
                }
            )

    return {
        "generated_at": timezone.now().isoformat(),
        "findings": findings,
        "count": len(findings),
    }


def ai_notify_loan_approved(*, loan, actor=None):
    try:
        from apps.notifications.services import NotificationService

        NotificationService.send_notification(
            user=loan.member,
            chama=loan.chama,
            message=(
                "Your loan is approved and will be disbursed to your M-Pesa number after final processing."
            ),
            channels=["sms", "email"],
            subject="Loan approved",
            notification_type="loan_update",
            actor=actor,
            idempotency_key=f"ai-loan-approved:{loan.id}",
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed sending AI loan-approved notification for %s", loan.id)


class AIDecisionEngine:
    """
    Enterprise-grade AI Decision Engine with:
    ✅ Rule-first + AI hybrid logic
    ✅ Strict validation & safety checks
    ✅ RAG-powered context retrieval
    ✅ Model routing for speed/accuracy
    ✅ Anti-hallucination measures
    ✅ Human-in-the-loop for high-risk
    """
    
    @staticmethod
    def make_decision(
        chama_id: str,
        context_type: str,
        request_data: dict[str, Any],
        risk_signals: dict[str, Any] = None,
        actor=None
    ) -> dict[str, Any]:
        """
        Make an AI-powered decision with full safety and accuracy guarantees.
        
        Process:
        1. Retrieve RAG context (rules, policies, history)
        2. Apply hard business rules first
        3. Route to appropriate AI model
        4. Validate output strictly
        5. Return safe, compliant decision
        
        Args:
            chama_id: Chama identifier
            context_type: Type of decision (membership_review, loan_eligibility, etc.)
            request_data: Request-specific data
            risk_signals: Fraud/security signals
            actor: User making the request
        
        Returns:
            Validated decision with all required fields
        """
        try:
            # Step 1: Get RAG context
            rag_context = get_ai_context_for_decision(
                chama_id=chama_id,
                context_type=context_type,
                request_data=request_data,
                risk_signals=risk_signals or {}
            )
            
            # Step 2: Check if hard rules decide outcome
            rule_decision = AIDecisionEngine._apply_hard_rules(
                context_type, request_data, rag_context.system_rules
            )
            
            if rule_decision:
                logger.info(f"Rule-based decision for {context_type}: {rule_decision['decision']}")
                return rule_decision
            
            # Step 3: Prepare AI prompt with context
            prompt = build_context_prompt(
                context_type=context_type,
                system_rules=rag_context.system_rules,
                policy_limits=rag_context.policy_limits
            )
            
            # Add request-specific data
            input_data = {
                "context_type": context_type,
                "system_rules": rag_context.system_rules,
                "request_data": request_data,
                "risk_signals": risk_signals or {},
                "policy_limits": rag_context.policy_limits,
            }
            
            # Step 4: Route to appropriate model
            model = get_model_for_task(context_type)
            token_limit = get_token_limit(context_type)
            
            # Step 5: Make AI call with retry logic
            def ai_call(**kwargs):
                return AIDecisionEngine._call_ai_model(
                    model=model,
                    prompt=prompt,
                    input_data=input_data,
                    token_limit=token_limit
                )
            
            validated_response = AIRetryHandler.retry_on_validation_error(
                ai_call_func=ai_call,
                context_type=context_type,
                system_rules=rag_context.system_rules,
                max_retries=1
            )
            
            # Step 6: Final validation and audit
            AIDecisionEngine._audit_decision(
                chama_id, context_type, validated_response, actor
            )
            
            return validated_response
            
        except Exception as e:
            logger.error(f"AI decision failed for {context_type}: {e}")
            return create_safe_fallback_response(context_type, str(e))
    
    @staticmethod
    def _apply_hard_rules(
        context_type: str, 
        request_data: dict[str, Any], 
        system_rules: dict[str, Any]
    ) -> dict[str, Any] | None:
        """
        Apply hard business rules that AI cannot override.
        
        Returns decision dict if rules fully decide, None if AI needed.
        """
        # Membership rules
        if context_type == "membership_review":
            phone_verified = request_data.get("phone_verified", True)
            if not phone_verified:
                return {
                    "decision": "REJECT_RECOMMENDED",
                    "confidence": 1.0,
                    "risk_score": 80,
                    "reasons": ["Phone number not verified"],
                    "risk_flags": ["UNVERIFIED_PHONE"],
                    "questions_to_ask": ["Please verify your phone number"],
                    "message_to_member": "Please verify your phone number to complete registration.",
                    "next_steps_for_admin": ["Wait for phone verification"],
                    "audit_summary": "Rejected due to unverified phone (hard rule)"
                }
        
        # Withdrawal rules
        elif context_type == "withdrawal_review":
            amount = request_data.get("amount", 0)
            max_daily = system_rules.get("max_daily_amount_percent", 50)
            available_balance = request_data.get("available_balance", 0)
            max_amount = (available_balance * max_daily) / 100
            
            if amount > max_amount:
                return {
                    "decision": "BLOCK",
                    "confidence": 1.0,
                    "risk_score": 90,
                    "reasons": [f"Amount exceeds daily limit of {max_daily}% of balance"],
                    "risk_flags": ["AMOUNT_EXCEEDS_LIMIT"],
                    "questions_to_ask": ["Consider smaller withdrawal amounts"],
                    "message_to_member": f"Withdrawal amount exceeds your daily limit of {max_daily}% of available balance.",
                    "next_steps_for_admin": ["Review withdrawal policy if needed"],
                    "audit_summary": "Blocked due to amount limit violation (hard rule)"
                }
        
        # Loan rules
        elif context_type == "loan_eligibility":
            membership_months = request_data.get("membership_months", 0)
            min_months = system_rules.get("min_contribution_months", 6)
            
            if membership_months < min_months:
                return {
                    "decision": "REJECT",
                    "confidence": 1.0,
                    "risk_score": 70,
                    "reasons": [f"Member has only {membership_months} months membership, minimum {min_months} required"],
                    "risk_flags": ["INSUFFICIENT_HISTORY"],
                    "questions_to_ask": ["Continue contributing to build membership history"],
                    "message_to_member": f"You need at least {min_months} months of membership to apply for a loan.",
                    "next_steps_for_admin": ["Member needs more contribution history"],
                    "audit_summary": "Rejected due to insufficient membership duration (hard rule)"
                }
        
        return None  # AI decision needed
    
    @staticmethod
    def _call_ai_model(
        model: str,
        prompt: str,
        input_data: dict[str, Any],
        token_limit: int
    ) -> str:
        """
        Make actual AI model call with proper formatting.
        
        Returns raw JSON string from AI.
        """
        if not OpenAI or not getattr(settings, "OPENAI_API_KEY", ""):
            # Fallback for deterministic responses
            return AIDecisionEngine._deterministic_fallback(input_data)
        
        try:
            client = AIClientPool.get_client()
            
            # Format input as JSON for AI
            input_json = json.dumps(input_data, indent=2)
            full_prompt = f"{prompt}\n\nINPUT DATA:\n{input_json}\n\nReturn only valid JSON:"
            
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": full_prompt}],
                max_tokens=token_limit,
                temperature=0.1,  # Low temperature for consistency
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            logger.exception("AI model call failed")
            raise e
    
    @staticmethod
    def _deterministic_fallback(input_data: dict[str, Any]) -> str:
        """
        Deterministic fallback when OpenAI is unavailable.
        
        Provides safe, rule-based responses.
        """
        context_type = input_data.get("context_type", "")
        
        if context_type == "membership_review":
            return json.dumps({
                "decision": "NEEDS_INFO",
                "confidence": 0.5,
                "risk_score": 30,
                "reasons": ["Using fallback decision engine"],
                "risk_flags": [],
                "questions_to_ask": ["Please provide more information"],
                "message_to_member": "Your application is being reviewed. We'll contact you soon.",
                "next_steps_for_admin": ["Manual review required"],
                "audit_summary": "Fallback decision - manual review recommended"
            })
        
        elif context_type == "withdrawal_review":
            return json.dumps({
                "decision": "REQUIRE_SECOND_APPROVAL",
                "confidence": 0.6,
                "risk_score": 40,
                "reasons": ["Standard security check"],
                "risk_flags": [],
                "questions_to_ask": [],
                "message_to_member": "Your withdrawal request requires additional approval for security.",
                "next_steps_for_admin": ["Verify transaction details"],
                "audit_summary": "Fallback - requires secondary approval"
            })
        
        # Generic fallback
        return json.dumps({
            "decision": "NEEDS_INFO",
            "confidence": 0.0,
            "risk_score": 50,
            "reasons": ["AI service temporarily unavailable"],
            "risk_flags": ["AI_FAILURE"],
            "questions_to_ask": ["Please try again later"],
            "message_to_member": "Service temporarily unavailable. Please try again.",
            "next_steps_for_admin": ["Check AI service status"],
            "audit_summary": "AI unavailable - fallback response"
        })
    
    @staticmethod
    def _audit_decision(
        chama_id: str,
        context_type: str,
        decision_data: dict[str, Any],
        actor
    ) -> None:
        """
        Audit AI decision for compliance and monitoring.
        
        Logs decision for review and system improvement.
        """
        try:
            create_audit_log(
                actor=actor,
                chama_id=chama_id,
                action=f"ai_decision_{context_type}",
                entity_type="AIDecision",
                entity_id=f"{context_type}_{timezone.now().timestamp()}",
                metadata={
                    "decision": decision_data.get("decision"),
                    "confidence": decision_data.get("confidence"),
                    "risk_score": decision_data.get("risk_score"),
                    "context_type": context_type,
                }
            )
        except Exception as e:
            logger.warning(f"Failed to audit AI decision: {e}")
    
    @staticmethod
    def requires_human_review(decision_data: dict[str, Any]) -> bool:
        """
        Determine if decision requires human review.
        
        High-risk or uncertain decisions should be reviewed.
        """
        risk_score = decision_data.get("risk_score", 0)
        confidence = decision_data.get("confidence", 1.0)
        decision = decision_data.get("decision", "")
        
        # High risk always needs review
        if risk_score > 60:
            return True
        
        # Low confidence needs review
        if confidence < 0.7:
            return True
        
        # Certain decisions always need review
        high_risk_decisions = [
            "BLOCK", "ESCALATE_REVIEW", "REQUIRE_SECOND_APPROVAL"
        ]
        if decision in high_risk_decisions:
            return True
        
        return False
