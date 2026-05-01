# MyChama AI Chatbot - API Views
# apps/ai/chatbot_views.py

from __future__ import annotations

import logging

from rest_framework import permissions, status, views
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.ai.chatbot_orchestration import ChatbotOrchestrationService
from apps.ai.chatbot_serializers import (
    ChatbotConversationSerializer,
    ChatbotMessageSerializer,
    FeedbackSerializer,
    SendMessageSerializer,
    StartConversationSerializer,
)
from apps.ai.models import AIConversation
from apps.ai.services import AIServiceError
from core.throttles import AIActionRateThrottle, AIChatRateThrottle

logger = logging.getLogger(__name__)


def chatbot_response(*, success: bool, code: str, message: str, data=None, errors=None):
    return {
        "success": bool(success),
        "code": str(code),
        "message": str(message),
        "errors": errors or {},
        "data": data or {},
    }


class ChatbotStartConversationView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AIActionRateThrottle]

    def post(self, request):
        serializer = StartConversationSerializer(data=request.data)
        if not serializer.is_valid():
            logger.warning("Invalid chatbot start payload", extra={"errors": serializer.errors})
            return Response(
                chatbot_response(
                    success=False,
                    code="INVALID_START_PAYLOAD",
                    message="Unable to start a conversation.",
                    errors=serializer.errors,
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        orchestration = ChatbotOrchestrationService(request.user)
        conversation, suggestions = orchestration.start_conversation(
            title=serializer.validated_data.get("title", ""),
            chama_id=serializer.validated_data.get("chama_id"),
            mode=serializer.validated_data.get("mode", ""),
        )

        return Response(
            chatbot_response(
                success=True,
                code="CHAT_STARTED",
                message="Conversation started.",
                data={
                    "conversation": ChatbotConversationSerializer(conversation).data,
                    "suggestions": suggestions,
                },
            ),
            status=status.HTTP_201_CREATED,
        )


class ChatbotSendMessageView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AIChatRateThrottle]

    def post(self, request):
        serializer = SendMessageSerializer(data=request.data)
        if not serializer.is_valid():
            logger.warning("Invalid chatbot message payload", extra={"errors": serializer.errors})
            return Response(
                chatbot_response(
                    success=False,
                    code="INVALID_MESSAGE_PAYLOAD",
                    message="Unable to send your message.",
                    errors=serializer.errors,
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        orchestration = ChatbotOrchestrationService(request.user)
        logger.info(
            "Chatbot message received",
            extra={
                "conversation_id": str(serializer.validated_data.get("conversation_id")),
                "stream": bool(serializer.validated_data.get("stream", False)),
                "message_len": len(str(serializer.validated_data.get("message") or "")),
            },
        )
        try:
            payload = orchestration.send_message(
                conversation_id=str(serializer.validated_data["conversation_id"]),
                message_text=serializer.validated_data["message"],
                stream=bool(serializer.validated_data.get("stream", False)),
            )
        except (NotFound, PermissionDenied, ValidationError) as exc:
            raise exc
        except AIServiceError as exc:
            logger.warning("Chatbot AIServiceError", extra={"detail": str(exc)})
            return Response(
                chatbot_response(
                    success=False,
                    code="CHAT_MESSAGE_FAILED",
                    message=str(exc) if str(exc) else "Unable to get an answer right now.",
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Chatbot message failed")
            return Response(
                chatbot_response(
                    success=False,
                    code="CHAT_SERVER_ERROR",
                    message="Unable to get an answer right now. Please try again.",
                ),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            chatbot_response(
                success=True,
                code="CHAT_MESSAGE_OK",
                message="Answer generated.",
                data=payload,
            ),
            status=status.HTTP_200_OK,
        )


class ChatbotGetHistoryView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AIActionRateThrottle]

    def get(self, request, conversation_id):
        limit = int(request.query_params.get("limit", 20))
        offset = int(request.query_params.get("offset", 0))
        limit = max(1, min(limit, 100))
        offset = max(0, offset)

        orchestration = ChatbotOrchestrationService(request.user)
        payload = orchestration.get_conversation_history(
            conversation_id=str(conversation_id),
            limit=limit,
            offset=offset,
        )
        return Response(
            chatbot_response(
                success=True,
                code="CHAT_HISTORY_OK",
                message="Conversation history loaded.",
                data={
                    "messages": ChatbotMessageSerializer(payload["messages"], many=True).data,
                    "total": payload["total"],
                    "has_more": payload["has_more"],
                },
            ),
            status=status.HTTP_200_OK,
        )


class ChatbotClearConversationView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AIActionRateThrottle]

    def post(self, request, conversation_id):
        orchestration = ChatbotOrchestrationService(request.user)
        archived_at = orchestration.clear_conversation(conversation_id=str(conversation_id))
        return Response(
            chatbot_response(
                success=True,
                code="CHAT_CLEARED",
                message="Conversation cleared.",
                data={"archived_at": archived_at},
            ),
            status=status.HTTP_200_OK,
        )


class ChatbotSaveFeedbackView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AIActionRateThrottle]

    def post(self, request):
        serializer = FeedbackSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                chatbot_response(
                    success=False,
                    code="INVALID_FEEDBACK_PAYLOAD",
                    message="Unable to save feedback.",
                    errors=serializer.errors,
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        orchestration = ChatbotOrchestrationService(request.user)
        feedback_id = orchestration.save_feedback(
            message_id=serializer.validated_data["message_id"],
            rating=serializer.validated_data["rating"],
            comment=serializer.validated_data.get("comment", ""),
        )

        return Response(
            chatbot_response(
                success=True,
                code="FEEDBACK_SAVED",
                message="Feedback saved.",
                data={"feedback_id": str(feedback_id)},
            ),
            status=status.HTTP_201_CREATED,
        )


class ChatbotSuggestionsView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AIActionRateThrottle]

    def get(self, request):
        orchestration = ChatbotOrchestrationService(request.user)
        chama_id = request.query_params.get("chama_id") or None
        suggestions = orchestration.get_suggestions(chama_id=chama_id)

        return Response(
            chatbot_response(
                success=True,
                code="SUGGESTIONS_OK",
                message="Suggestions loaded.",
                data={"suggestions": suggestions},
            ),
            status=status.HTTP_200_OK,
        )


class ChatbotExecuteActionView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AIActionRateThrottle]

    def post(self, request):
        action = request.data.get("action") or {}
        if not isinstance(action, dict):
            raise ValidationError("action must be an object")

        action_type = str(action.get("type") or "").strip()
        href = str(action.get("href") or "").strip()
        if action_type != "navigate" or not href:
            return Response(
                chatbot_response(
                    success=False,
                    code="INVALID_ACTION",
                    message="This action is not supported.",
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Backend never executes state-changing actions without explicit confirmation flows.
        return Response(
            chatbot_response(
                success=True,
                code="ACTION_READY",
                message="Action ready.",
                data={"action": {"type": "navigate", "href": href}},
            ),
            status=status.HTTP_200_OK,
        )


class ChatbotListConversationsView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AIActionRateThrottle]

    def get(self, request):
        limit = int(request.query_params.get("limit", 20))
        offset = int(request.query_params.get("offset", 0))
        limit = max(1, min(limit, 100))
        offset = max(0, offset)

        chama_id = str(request.query_params.get("chama_id") or "").strip()
        qs = AIConversation.objects.filter(user=request.user)
        if chama_id.lower() in {"none", "null"}:
            qs = qs.filter(chama__isnull=True)
        elif chama_id:
            qs = qs.filter(chama_id=chama_id)
        qs = qs.order_by("-updated_at")
        total = qs.count()
        items = qs[offset : offset + limit]

        return Response(
            chatbot_response(
                success=True,
                code="CONVERSATIONS_OK",
                message="Conversations loaded.",
                data={
                    "conversations": ChatbotConversationSerializer(items, many=True).data,
                    "total": total,
                    "has_more": (offset + limit) < total,
                },
            ),
            status=status.HTTP_200_OK,
        )
