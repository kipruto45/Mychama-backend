# MyChama AI Chatbot - API Serializers
# apps/ai/chatbot_serializers.py

from rest_framework import serializers

from apps.ai.models import AIConversation, AIMessage


class ChatbotMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIMessage
        fields = ["id", "role", "content", "tool_name", "tool_payload", "created_at"]


class ChatbotConversationSerializer(serializers.ModelSerializer):
    message_count = serializers.SerializerMethodField()
    last_message_preview = serializers.SerializerMethodField()

    class Meta:
        model = AIConversation
        fields = [
            "id",
            "title",
            "mode",
            "created_at",
            "updated_at",
            "message_count",
            "last_message_preview",
        ]

    def get_message_count(self, obj) -> int:
        return obj.messages.count()

    def get_last_message_preview(self, obj) -> str:
        last_msg = obj.messages.order_by("-created_at").first()
        if not last_msg:
            return ""
        preview = (last_msg.content or "").strip()
        preview = preview.replace("\n", " ")
        return (preview[:80] + "…") if len(preview) > 80 else preview


class StartConversationSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=160, required=False, allow_blank=True)
    chama_id = serializers.UUIDField(required=False, allow_null=True)
    mode = serializers.CharField(max_length=40, required=False, allow_blank=True)


class SendMessageSerializer(serializers.Serializer):
    conversation_id = serializers.UUIDField()
    message = serializers.CharField(max_length=4000)
    stream = serializers.BooleanField(default=False, required=False)


class FeedbackSerializer(serializers.Serializer):
    message_id = serializers.UUIDField()
    rating = serializers.ChoiceField(choices=[("thumbs_up", "Helpful"), ("thumbs_down", "Not Helpful")])
    comment = serializers.CharField(max_length=1000, required=False, allow_blank=True)


class SuggestionsResponseSerializer(serializers.Serializer):
    suggestions = serializers.ListField(child=serializers.CharField())

