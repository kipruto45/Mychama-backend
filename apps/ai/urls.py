"""
AI URLs for Digital Chama
"""

from django.urls import path, re_path

from . import views
from apps.ai.chatbot_views import (
    ChatbotStartConversationView,
    ChatbotSendMessageView,
    ChatbotGetHistoryView,
    ChatbotClearConversationView,
    ChatbotSaveFeedbackView,
    ChatbotSuggestionsView,
    ChatbotExecuteActionView,
    ChatbotListConversationsView,
)

app_name = "ai"

urlpatterns = [
    # PUBLIC AI ENDPOINTS (No Authentication Required)
    # These provide general info about features, pricing, and how to get started
    re_path(r"^public/chat/?$", views.public_ai_chat, name="public-ai-chat"),
    path("public/chat/", views.public_ai_chat, name="public-chat"),
    re_path(r"^public/chat/stream/?$", views.public_ai_chat_stream, name="public-ai-chat-stream"),
    path("public/chat/stream/", views.public_ai_chat_stream, name="public-chat-stream"),
    re_path(r"^public/suggestions/?$", views.public_ai_suggestions, name="public-ai-suggestions"),
    path("public/suggestions/", views.public_ai_suggestions, name="public-suggestions"),

    # AI Context endpoint for role-aware UI
    re_path(r"^me/context/?$", views.ai_me_context, name="ai-me-context"),
    path("me/context/", views.ai_me_context, name="me-context"),
    re_path(r"^context/?$", views.ai_context, name="ai-context"),
    path("context/", views.ai_context, name="context"),
    
    # AI gateway endpoints (slash and no-slash for test/client compatibility)
    re_path(r"^chat/?$", views.ai_chat, name="ai-chat"),
    path("chat/", views.ai_chat, name="chat"),
    
    # Streaming chat endpoint
    re_path(r"^chat/stream/?$", views.ai_chat_stream, name="ai-chat-stream"),
    path("chat/stream/", views.ai_chat_stream, name="chat-stream"),
    re_path(r"^chat/stop/?$", views.ai_chat_stop, name="ai-chat-stop"),
    path("chat/stop/", views.ai_chat_stop, name="chat-stop"),
    
    # Stop streaming endpoint
    re_path(r"^chat/stop/?$", views.ai_chat_stop, name="ai-chat-stop"),
    path("chat/stop/", views.ai_chat_stop, name="chat-stop"),
    
    # AI Context endpoint for role-aware UI
    re_path(r"^context/?$", views.ai_context, name="ai-context"),
    path("context/", views.ai_context, name="context"),
    
    # Suggestions endpoint
    re_path(r"^suggestions/?$", views.ai_suggestions, name="ai-suggestions"),
    path("suggestions/", views.ai_suggestions, name="suggestions"),
    
    # Feedback endpoint
    re_path(r"^feedback/?$", views.ai_feedback, name="ai-feedback"),
    path("feedback/", views.ai_feedback, name="feedback"),
    
    # Tool execution endpoint for AI Assistant
    re_path(r"^tool/execute/?$", views.ai_tool_execute, name="ai-tool-execute"),
    path("tool/execute/", views.ai_tool_execute, name="tool-execute"),
    
    # Attachment upload endpoint
    re_path(r"^attachment/upload/?$", views.ai_attachment_upload, name="ai-attachment-upload"),
    path("attachment/upload/", views.ai_attachment_upload, name="attachment-upload"),
    
    # Conversations
    re_path(r"^conversations/?$", views.ai_conversations, name="ai-conversations"),
    path("conversations/", views.ai_conversations, name="conversations"),
    re_path(
        r"^conversations/(?P<conversation_id>[0-9a-f-]+)/?$",
        views.ai_messages,
        name="ai-conversation-detail",
    ),
    re_path(
        r"^conversations/(?P<conversation_id>[0-9a-f-]+)/messages/?$",
        views.ai_messages,
        name="ai-messages",
    ),
    
    re_path(r"^status/?$", views.ai_status, name="ai-status"),
    re_path(
        r"^membership-risk-scoring/?$",
        views.ai_membership_risk_scoring,
        name="ai-membership-risk-scoring",
    ),
    re_path(
        r"^loan-default-prediction/?$",
        views.ai_loan_default_prediction,
        name="ai-loan-default-prediction",
    ),
    re_path(
        r"^issue-triage/?$",
        views.ai_issue_triage,
        name="ai-issue-triage",
    ),
    re_path(
        r"^meeting-summarize/?$",
        views.ai_meeting_summarize,
        name="ai-meeting-summarize",
    ),
    re_path(
        r"^report-explain/?$",
        views.ai_report_explain,
        name="ai-report-explain",
    ),

    # Risk Profile
    path(
        "risk-profile/<uuid:chama_id>/",
        views.risk_profile,
        name="risk-profile",
    ),
    
    # Loan Eligibility
    path(
        "loan-eligibility/<uuid:chama_id>/",
        views.loan_eligibility,
        name="loan-eligibility",
    ),
    
    # Insights
    path(
        "insights/<uuid:chama_id>/",
        views.insights,
        name="insights",
    ),
    path(
        "insights/<uuid:chama_id>/refresh/",
        views.insights_refresh,
        name="insights-refresh",
    ),
    
    # Fraud Flags
    path(
        "fraud-flags/<uuid:chama_id>/",
        views.fraud_flags,
        name="fraud-flags",
    ),
    path(
        "fraud-flags/<uuid:chama_id>/<uuid:flag_id>/resolve/",
        views.fraud_flags_resolve,
        name="fraud-flags-resolve",
    ),
    path(
        "fraud-check/<uuid:chama_id>/",
        views.fraud_check,
        name="fraud-check",
    ),
    
    # Chatbot endpoints (new orchestration-based system)
    # Spec-compatible aliases
    path("chat/start/", ChatbotStartConversationView.as_view(), name="ai-chat-start"),
    path("chat/message/", ChatbotSendMessageView.as_view(), name="ai-chat-message"),
    path("chat/<uuid:conversation_id>/history/", ChatbotGetHistoryView.as_view(), name="ai-chat-history"),
    path("chat/<uuid:conversation_id>/clear/", ChatbotClearConversationView.as_view(), name="ai-chat-clear"),
    path("chat/feedback/", ChatbotSaveFeedbackView.as_view(), name="ai-chat-feedback"),
    path("chat/suggestions/", ChatbotSuggestionsView.as_view(), name="ai-chat-suggestions"),
    path("chat/action/execute/", ChatbotExecuteActionView.as_view(), name="ai-chat-action-execute"),

    path('chatbot/start/', ChatbotStartConversationView.as_view(), name='chatbot-start'),
    path('chatbot/message/', ChatbotSendMessageView.as_view(), name='chatbot-message'),
    path('chatbot/<uuid:conversation_id>/history/', ChatbotGetHistoryView.as_view(), name='chatbot-history'),
    path('chatbot/<uuid:conversation_id>/clear/', ChatbotClearConversationView.as_view(), name='chatbot-clear'),
    path('chatbot/feedback/', ChatbotSaveFeedbackView.as_view(), name='chatbot-feedback'),
    path('chatbot/suggestions/', ChatbotSuggestionsView.as_view(), name='chatbot-suggestions'),
    path('chatbot/action/execute/', ChatbotExecuteActionView.as_view(), name='chatbot-execute-action'),
    path('chatbot/conversations/', ChatbotListConversationsView.as_view(), name='chatbot-list'),
]
