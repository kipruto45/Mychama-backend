"""
AI Serializers for Digital Chama
"""

from rest_framework import serializers

from .models import (
    AIConversation,
    AIInsight,
    AIMessage,
    AIMessageRole,
    AIUsageLog,
    AIAnswerFeedback,
    FraudFlag,
    LoanEligibility,
    RiskProfile,
    AIInteraction,
)


class AIInteractionSerializer(serializers.ModelSerializer):
    """Serializer for AI chat interactions."""
    
    class Meta:
        model = AIInteraction
        fields = [
            "id",
            "user",
            "chama",
            "question",
            "response",
            "context_data",
            "helpful",
            "created_at",
        ]
        read_only_fields = ["id", "user", "response", "context_data", "created_at"]


class AIChatRequestSerializer(serializers.Serializer):
    """Serializer for chat request."""
    
    message = serializers.CharField(max_length=1000)
    chama_id = serializers.UUIDField(required=False)


class AIChatResponseSerializer(serializers.Serializer):
    """Serializer for chat response."""
    
    response = serializers.CharField()
    context_data = serializers.JSONField()


class RiskProfileSerializer(serializers.ModelSerializer):
    """Serializer for risk profile."""
    
    class Meta:
        model = RiskProfile
        fields = [
            "id",
            "user",
            "chama",
            "risk_score",
            "risk_level",
            "contribution_consistency_score",
            "payment_history_score",
            "debt_ratio",
            "withdrawal_frequency_score",
            "loan_multiplier",
            "last_calculated",
        ]
        read_only_fields = fields


class LoanEligibilitySerializer(serializers.ModelSerializer):
    """Serializer for loan eligibility."""
    
    class Meta:
        model = LoanEligibility
        fields = [
            "id",
            "user",
            "chama",
            "max_loan_amount",
            "suggested_amount",
            "eligible",
            "ineligibility_reason",
            "risk_factors",
            "suggested_term_months",
            "interest_rate",
            "created_at",
        ]
        read_only_fields = fields


class LoanEligibilityResponseSerializer(serializers.Serializer):
    """Serializer for loan eligibility API response."""
    
    max_loan_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    suggested_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    eligible = serializers.BooleanField()
    ineligibility_reason = serializers.CharField()
    risk_factors = serializers.ListField(child=serializers.CharField())
    suggested_term_months = serializers.IntegerField()
    interest_rate = serializers.DecimalField(max_digits=5, decimal_places=2)
    risk_score = serializers.IntegerField()
    risk_level = serializers.CharField()


class FraudFlagSerializer(serializers.ModelSerializer):
    """Serializer for fraud flags."""
    
    class Meta:
        model = FraudFlag
        fields = [
            "id",
            "user",
            "chama",
            "fraud_type",
            "severity",
            "description",
            "evidence",
            "resolved",
            "resolved_by",
            "resolution_note",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "user",
            "chama",
            "fraud_type",
            "severity",
            "description",
            "evidence",
            "resolved_by",
            "created_at",
        ]


class FraudFlagResolveSerializer(serializers.Serializer):
    """Serializer for resolving fraud flag."""
    
    resolution_note = serializers.CharField(max_length=500)


class AIInsightSerializer(serializers.ModelSerializer):
    """Serializer for AI insights."""
    
    class Meta:
        model = AIInsight
        fields = [
            "id",
            "chama",
            "insight_type",
            "title",
            "description",
            "chart_data",
            "recommendations",
            "is_active",
            "created_at",
        ]
        read_only_fields = fields


class AIInsightsListSerializer(serializers.Serializer):
    """Serializer for list of insights."""
    
    insights = AIInsightSerializer(many=True)
    last_generated = serializers.DateTimeField()


class AIChatStreamRequestSerializer(serializers.Serializer):
    """Serializer for streaming chat request."""
    
    message = serializers.CharField(max_length=4000)
    conversation_id = serializers.UUIDField(required=False, allow_null=True)
    chama_id = serializers.UUIDField(required=False)
    regenerate = serializers.BooleanField(required=False, default=False)


class AIChatStreamResponseSerializer(serializers.Serializer):
    """Serializer for streaming chat response."""
    
    conversation_id = serializers.UUIDField()
    message_id = serializers.UUIDField()
    content = serializers.CharField()
    done = serializers.BooleanField()


class AISuggestionSerializer(serializers.Serializer):
    """Serializer for AI suggestions."""
    
    id = serializers.CharField()
    text = serializers.CharField()
    category = serializers.CharField()


class AIConversationListSerializer(serializers.ModelSerializer):
    """Serializer for listing AI conversations."""

    title = serializers.SerializerMethodField()

    class Meta:
        model = AIConversation
        fields = ["id", "mode", "title", "created_at", "updated_at"]

    def get_title(self, obj):
        if obj.title:
            return obj.title
        first_user_message = (
            obj.messages.filter(role=AIMessageRole.USER)
            .order_by("created_at")
            .values_list("content", flat=True)
            .first()
        )
        if first_user_message:
            normalized = " ".join(str(first_user_message).split())
            return normalized[:80]
        return obj.get_mode_display()


class AIMessageSerializer(serializers.ModelSerializer):
    """Serializer for AI messages."""
    
    class Meta:
        model = AIMessage
        fields = ["id", "role", "content", "tool_name", "tool_payload", "created_at"]


class AIUsageLogSerializer(serializers.ModelSerializer):
    """Serializer for AI usage logs."""
    
    class Meta:
        model = AIUsageLog
        fields = [
            "id", "user", "chama", "tokens_in", "tokens_out",
            "latency_ms", "model_name", "endpoint", "status_code",
            "error_message", "created_at"
        ]
        read_only_fields = fields


class AIAnswerFeedbackSerializer(serializers.ModelSerializer):
    """Serializer for AI answer feedback."""
    
    class Meta:
        model = AIAnswerFeedback
        fields = ["id", "message", "user", "rating", "comment", "created_at"]
        read_only_fields = ["id", "user", "created_at"]


class AIAnswerFeedbackCreateSerializer(serializers.Serializer):
    """Serializer for creating feedback."""
    
    message_id = serializers.UUIDField()
    rating = serializers.ChoiceField(choices=AIAnswerFeedback.RATING_CHOICES)
    comment = serializers.CharField(required=False, allow_blank=True, max_length=1000)
