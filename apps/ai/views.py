"""
AI Views for Digital Chama

API endpoints for:
- AI Chat Assistant
- AI Chat Streaming
- AI Suggestions
- AI Feedback
- Risk Profile
- Loan Eligibility
- Fraud Flags
- Smart Insights
"""

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from decimal import Decimal
from django.conf import settings
from django.core.cache import cache
from django.db import close_old_connections
from django.http import StreamingHttpResponse
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response

from apps.accounts.models import User
from apps.billing.gating import require_feature
from apps.billing.services import get_access_status, get_active_chama_from_request, get_entitlements, has_feature
from apps.chama.models import Chama, Membership, MembershipRole
from apps.chama.services import get_effective_role

from .ai_prompt import get_role_suggestions, build_system_prompt, get_quick_actions
from .ai_tools import ToolRegistry, ToolRouter
from .chat_engine import ChatEngine
from .fraud_engine import FraudEngine
from .insights_engine import InsightsEngine
from .models import (
    AIAnswerFeedback,
    AIConversation,
    AIConversationMode,
    AIInsight,
    AIMessage,
    AIUsageLog,
    FraudFlag,
    RiskProfile,
)
from .risk_engine import RiskEngine
from .selectors import mask_phone
from .serializers import (
    AIAnswerFeedbackCreateSerializer,
    AIAnswerFeedbackSerializer,
    AIChatRequestSerializer,
    AIChatResponseSerializer,
    AIConversationListSerializer,
    AIInsightSerializer,
    AIMessageSerializer,
    FraudFlagResolveSerializer,
    FraudFlagSerializer,
    LoanEligibilityResponseSerializer,
    RiskProfileSerializer,
)
from .services import AIGatewayService, AIClientPool, AIServiceError, AIWorkflowService


logger = logging.getLogger(__name__)

AI_CONTEXT_CACHE_TTL = 45
AI_TOOL_CACHE_TTL = 45
AI_MAX_PARALLEL_TOOLS = 3
AI_TOOL_TIMEOUT_SECONDS = 3
AI_MAX_MULTI_TOOL_MATCHES = 3


def _json_safe(payload):
    return json.loads(json.dumps(payload, default=str))


# =============================================================================
# PUBLIC AI ENDPOINTS (No Authentication Required)
# =============================================================================

# Public FAQ content for unauthenticated users
PUBLIC_FAQ_CONTENT = """
Digital Chama is a comprehensive platform for managing savings groups (chamas) digitally.

**Key Features:**
- **Chama Management**: Create chamas, invite members, assign roles (Chair, Secretary, Treasurer, Auditor)
- **Contributions & Goals**: Track monthly contributions, set savings goals, automated reminders
- **Loans & Guarantors**: Apply for loans, get guarantors, track repayment schedules
- **Wallet & Ledger**: Complete financial trail with receipts, statements, and real-time balance
- **Meeting Governance**: Schedule meetings, manage agenda, record minutes, track attendance
- **Investments**: Track group investments and returns

**Pricing & Plans:**
- **Free Plan**: Basic features for small groups (up to 10 members)
- **Pro Plan (KES 2,500/month)**: Advanced features, unlimited members, priority support
- **Enterprise Plan**: Custom solutions for large organizations

**How to Get Started:**
1. Visit our website and click "Get Started"
2. Create your account with phone number and OTP verification
3. Create a new chama or join an existing one with an invite code
4. Invite members and assign roles
5. Start tracking contributions and managing loans

**Security:**
- Bank-grade security with encrypted data
- Two-factor authentication (OTP)
- Role-based access control
- Audit logs for all transactions
"""


PUBLIC_SUGGESTIONS = [
    {"id": "1", "text": "How does it work?", "category": "howto"},
    {"id": "2", "text": "Pricing & plans", "category": "pricing"},
    {"id": "3", "text": "Is it secure?", "category": "security"},
    {"id": "4", "text": "How to create a chama", "category": "howto"},
    {"id": "5", "text": "How invites work", "category": "howto"},
    {"id": "6", "text": "Talk to support", "category": "support"},
]


def _generate_public_response(message: str) -> str:
    """Generate a response for public/unauthenticated users."""
    message_lower = message.lower()
    
    # Feature-related questions
    if any(word in message_lower for word in ["feature", "what can", "do", "offer", "capability"]):
        return PUBLIC_FAQ_CONTENT
    
    # Pricing questions
    if any(word in message_lower for word in ["price", "cost", "pricing", "plan", "fee", "charge", "subscription"]):
        return """**Pricing & Plans:**

- **Free Plan**: Basic features for small groups (up to 10 members)
- **Pro Plan (KES 2,500/month)**: Advanced features, unlimited members, priority support
- **Enterprise Plan**: Custom solutions for large organizations

Visit our pricing page for more details: /pricing

Would you like to create an account to get started?"""
    
    # Security questions
    if any(word in message_lower for word in ["security", "safe", "secure", "encrypt", "privacy", "data protection"]):
        return """**Security & Trust:**

- Bank-grade security with encrypted data
- Two-factor authentication (OTP) for all accounts
- Role-based access control (Admin, Treasurer, Secretary, Auditor, Member)
- Complete audit logs for all transactions
- Your data is stored securely in Kenya
- We never share your personal information

Digital Chama is trusted by thousands of chamas across Kenya!"""
    
    # How to create/invite questions
    if any(word in message_lower for word in ["create", "invite", "join", "start", "setup", "new chama"]):
        return """**Getting Started:**

1. **Create Account**: Visit our website and click "Get Started"
2. **Verify Phone**: You'll receive an OTP code to verify your phone number
3. **Create or Join Chama**: 
   - Create a new chama and invite members
   - Or join an existing chama with an invite code
4. **Assign Roles**: Chair, Secretary, Treasurer, Auditor
5. **Start Managing**: Track contributions, loans, and meetings

**Invite Members**: Share the invite link or code with members. They can join by:
- Clicking the invite link
- Entering the invite code at /join

Would you like to create an account?"""
    
    # Login issues
    if any(word in message_lower for word in ["login", "log in", "password", "otp", "verify", "can't access"]):
        return """**Login Help:**

1. **Phone Number**: Enter the phone number you registered with
2. **OTP Verification**: You'll receive a 6-digit code via SMS
3. **Enter OTP**: Enter the code to log in

If you're not receiving OTPs:
- Check your phone number is correct
- Ensure you have good network signal
- Contact support if issues persist

Need help? Click "Talk to support" below."""
    
    # Default response with CTA
    return f"""Thank you for your interest in Digital Chama!

{PUBLIC_FAQ_CONTENT}

**Ready to get started?**
- [Create Account](/register)
- [View Pricing](/pricing)
- [Login if you have an account](/login)

Or click one of the quick options above for more information."""


@api_view(["POST"])
@permission_classes([AllowAny])
def public_ai_chat(request):
    """
    POST /api/public-ai/chat
    
    Public AI chat for unauthenticated users.
    Provides general information about features, pricing, and how to get started.
    """
    message = str(request.data.get("message") or "").strip()
    
    if not message:
        return Response(
            {"detail": "message is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    
    response_text = _generate_public_response(message)
    
    return Response({
        "answer": response_text,
        "suggestions": PUBLIC_SUGGESTIONS,
    }, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([AllowAny])
def public_ai_suggestions(request):
    """
    GET /api/public-ai/suggestions
    
    Get public suggestions for unauthenticated users.
    """
    return Response({
        "suggestions": PUBLIC_SUGGESTIONS,
    }, status=status.HTTP_200_OK)


def _json_safe(payload):
    return json.loads(json.dumps(payload, default=str))


AI_MANAGER_ROLES = {
    MembershipRole.ADMIN,
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.SUPERADMIN,
}


def _get_active_membership(user, chama):
    if not user or not getattr(user, "is_authenticated", False) or not chama:
        return None
    return (
        Membership.objects.filter(
            user=user,
            chama=chama,
            is_active=True,
            is_approved=True,
        )
        .select_related("chama")
        .first()
    )


def _get_chama_from_request(request):
    """Resolve the chama using the same rules as billing enforcement."""
    return get_active_chama_from_request(request)


def _has_allowed_role(membership, allowed_roles: set[str]) -> bool:
    if not membership:
        return False
    effective_role = get_effective_role(
        membership.user,
        membership.chama_id,
        membership,
    )
    return bool(effective_role in allowed_roles)


def _require_ai_feature_for_chama(chama, feature_key: str, or_features=None):
    if not chama:
        return Response(
            {"error": "No active chama selected"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    access = get_access_status(chama)
    if access.get("requires_payment"):
        return Response(
            {
                "error": "payment_required",
                "message": "Your billing access does not cover this AI action.",
                "reason": access.get("reason"),
                "feature": feature_key,
                "trial_ends_at": access.get("trial_ends_at"),
                "trial_days_remaining": access.get("trial_days_remaining", 0),
                "chama_id": str(chama.id),
            },
            status=status.HTTP_402_PAYMENT_REQUIRED,
        )

    if has_feature(chama, feature_key):
        return None

    for fallback_feature in or_features or []:
        if has_feature(chama, fallback_feature):
            return None

    entitlements = get_entitlements(chama)
    return Response(
        {
            "error": "upgrade_required",
            "message": "This AI capability requires a higher subscription plan.",
            "feature": feature_key,
            "current_plan": entitlements.get("plan_code", "FREE"),
            "chama_id": str(chama.id),
        },
        status=status.HTTP_402_PAYMENT_REQUIRED,
    )


def _require_ai_manager(membership, message: str):
    if _has_allowed_role(membership, AI_MANAGER_ROLES):
        return None
    return Response({"error": message}, status=status.HTTP_403_FORBIDDEN)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@require_feature('ai_basic', or_features=['ai_advanced'])
def ai_chat(request):
    """
    POST /api/v1/ai/chat

    Gateway-backed AI chat endpoint used by API tests and web clients.
    """
    message = str(request.data.get("message") or "").strip()
    mode = str(request.data.get("mode") or "member_assistant").strip()
    conversation_id = request.data.get("conversation_id")

    if not message:
        return Response(
            {"detail": "message is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    chama = _get_chama_from_request(request)
    if not chama:
        return Response(
            {"detail": "chama_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    membership = _get_active_membership(request.user, chama)
    if not membership:
        return Response(
            {"detail": "You are not an approved active member of this chama."},
            status=status.HTTP_403_FORBIDDEN,
        )

    try:
        payload = AIGatewayService.chat(
            user=request.user,
            chama_id=str(chama.id),
            mode=mode or "member_assistant",
            message=message,
            conversation_id=conversation_id,
        )
        return Response(_json_safe(payload), status=status.HTTP_200_OK)
    except AIServiceError as exc:
        detail = str(exc)
        lowered = detail.lower()
        code = status.HTTP_403_FORBIDDEN
        if any(token in lowered for token in {"required", "invalid", "missing"}):
            code = status.HTTP_400_BAD_REQUEST
        return Response({"detail": detail}, status=code)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ai_status(request):
    return Response(
        _json_safe(
            {
            "status": "operational",
            "chat_model": getattr(settings, "AI_CHAT_MODEL", "gpt-5-mini"),
            "features": {
                "chat": True,
                "workflows": True,
                "openai_enabled": bool(getattr(settings, "OPENAI_API_KEY", "")),
            },
        }
        )
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ai_membership_risk_scoring(request):
    chama = _get_chama_from_request(request)
    if not chama:
        return Response(
            {"detail": "chama_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not _get_active_membership(request.user, chama):
        return Response(
            {"detail": "You are not an approved active member of this chama."},
            status=status.HTTP_403_FORBIDDEN,
        )
    feature_block = _require_ai_feature_for_chama(chama, "ai_advanced")
    if feature_block:
        return feature_block
    try:
        payload = AIWorkflowService.membership_risk_scoring_for_chama(
            chama_id=str(chama.id),
            actor=request.user,
        )
    except AIServiceError as exc:
        payload = {
            "chama_id": str(chama.id),
            "generated_at": timezone.now().isoformat(),
            "count": 0,
            "members": [],
            "fallback_reason": str(exc),
        }

    members = payload.get("members", [])
    high_count = sum(1 for item in members if item.get("risk_band") == "HIGH")
    confidence = 0.9 if members else 0.7
    decision = "manual_review_required" if high_count else "normal_review"
    return Response(
        _json_safe(
            {
            "decision": decision,
            "confidence": confidence,
            "result": payload,
        }
        )
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ai_loan_default_prediction(request):
    chama = _get_chama_from_request(request)
    if not chama:
        return Response(
            {"detail": "chama_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not _get_active_membership(request.user, chama):
        return Response(
            {"detail": "You are not an approved active member of this chama."},
            status=status.HTTP_403_FORBIDDEN,
        )
    feature_block = _require_ai_feature_for_chama(chama, "ai_advanced")
    if feature_block:
        return feature_block
    try:
        payload = AIWorkflowService.loan_default_prediction_for_chama(
            chama_id=str(chama.id),
            actor=request.user,
        )
    except AIServiceError as exc:
        payload = {
            "chama_id": str(chama.id),
            "generated_at": timezone.now().isoformat(),
            "count": 0,
            "predictions": [],
            "fallback_reason": str(exc),
        }

    predictions = payload.get("predictions", [])
    high_count = sum(1 for item in predictions if item.get("risk_band") == "HIGH")
    confidence = 0.9 if predictions else 0.7
    decision = "high_default_risk_detected" if high_count else "no_high_default_risk"
    return Response(
        _json_safe(
            {
            "decision": decision,
            "confidence": confidence,
            "result": payload,
        }
        )
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ai_issue_triage(request):
    issue_id = request.data.get("issue_id")
    if not issue_id:
        return Response(
            {"detail": "issue_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    from apps.issues.models import Issue

    issue = Issue.objects.filter(id=issue_id).select_related("chama").first()
    if not issue:
        return Response(
            {"detail": "Issue not found"},
            status=status.HTTP_404_NOT_FOUND,
        )
    if not _get_active_membership(request.user, issue.chama):
        return Response(
            {"detail": "You are not an approved active member of this chama."},
            status=status.HTTP_403_FORBIDDEN,
        )
    feature_block = _require_ai_feature_for_chama(issue.chama, "ai_advanced")
    if feature_block:
        return feature_block
    try:
        payload = AIWorkflowService.triage_issue(issue_id=issue_id, actor=request.user)
    except AIServiceError as exc:
        payload = {
            "issue_id": str(issue_id),
            "category": getattr(issue, "category", "general") or "general",
            "priority": getattr(issue, "priority", "medium") or "medium",
            "suggested_assignee_role": "SECRETARY",
            "draft_response": (
                "Issue received and queued for governance review."
            ),
            "fallback_reason": str(exc),
        }
    return Response(
        _json_safe({"decision": "triaged", "confidence": 0.9, **payload})
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ai_meeting_summarize(request):
    meeting_id = request.data.get("meeting_id")
    if not meeting_id:
        return Response(
            {"detail": "meeting_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    from apps.meetings.models import Meeting

    meeting = Meeting.objects.filter(id=meeting_id).select_related("chama").first()
    if not meeting:
        return Response(
            {"detail": "Meeting not found"},
            status=status.HTTP_404_NOT_FOUND,
        )
    if not _get_active_membership(request.user, meeting.chama):
        return Response(
            {"detail": "You are not an approved active member of this chama."},
            status=status.HTTP_403_FORBIDDEN,
        )
    feature_block = _require_ai_feature_for_chama(meeting.chama, "ai_advanced")
    if feature_block:
        return feature_block
    try:
        payload = AIWorkflowService.summarize_meeting(
            meeting_id=meeting_id,
            actor=request.user,
        )
    except AIServiceError as exc:
        summary = (
            ((meeting.minutes_text or "").strip() or (meeting.agenda or "").strip())
            if meeting
            else ""
        )
        payload = {
            "meeting_id": str(meeting_id),
            "summary": summary or "Meeting summary is not available yet.",
            "action_items": [],
            "unresolved_action_items": [],
            "repeated_complaint_signals": 0,
            "sentiment": "neutral",
            "fallback_reason": str(exc),
        }
    return Response(_json_safe(payload))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ai_report_explain(request):
    report_id = request.data.get("report_id")
    if not report_id:
        return Response(
            {"detail": "report_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    from apps.reports.models import ReportRun

    report = ReportRun.objects.filter(id=report_id).select_related("chama").first()
    if not report:
        return Response(
            {"detail": "Report not found"},
            status=status.HTTP_404_NOT_FOUND,
        )
    if not _get_active_membership(request.user, report.chama):
        return Response(
            {"detail": "You are not an approved active member of this chama."},
            status=status.HTTP_403_FORBIDDEN,
        )
    feature_block = _require_ai_feature_for_chama(report.chama, "ai_advanced")
    if feature_block:
        return feature_block
    try:
        payload = AIWorkflowService.explain_report(
            report_id=report_id,
            actor=request.user,
        )
    except AIServiceError as exc:
        payload = {
            "report_id": str(report_id),
            "report_type": getattr(report, "report_type", "unknown"),
            "explanation": (
                "This report captures chama performance metrics for the selected "
                "period. Review totals and flagged items for action."
            ),
            "anomalies": [],
            "highlights": {
                "status": getattr(report, "status", "unknown"),
                "generated_at": (
                    report.created_at.isoformat() if report else timezone.now().isoformat()
                ),
            },
            "fallback_reason": str(exc),
        }
    return Response(_json_safe(payload))


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def risk_profile(request, chama_id=None):
    """
    GET /api/ai/risk-profile/{chama_id}/
    
    Get risk profile for user in a chama.
    
    POST /api/ai/risk-profile/{chama_id}/
    
    Refresh risk profile calculation.
    """
    if request.method == "GET":
        if not chama_id:
            return Response(
                {"error": "chama_id required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        try:
            chama = Chama.objects.get(id=chama_id)
            Membership.objects.get(
                user=request.user,
                chama=chama,
                is_active=True,
                is_approved=True,
            )
        except (Chama.DoesNotExist, Membership.DoesNotExist):
            return Response(
                {"error": "Chama not found or not a member"},
                status=status.HTTP_404_NOT_FOUND,
            )
        feature_block = _require_ai_feature_for_chama(chama, "ai_advanced")
        if feature_block:
            return feature_block
        
        # Get or calculate risk profile
        profile = RiskEngine.calculate_risk_profile(request.user, chama)
        
        serializer = RiskProfileSerializer(profile)
        return Response(serializer.data)
    
    # POST - Refresh risk profile
    if not chama_id:
        return Response(
            {"error": "chama_id required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    
    try:
        chama = Chama.objects.get(id=chama_id)
        Membership.objects.get(
            user=request.user,
            chama=chama,
            is_active=True,
            is_approved=True,
        )
    except (Chama.DoesNotExist, Membership.DoesNotExist):
        return Response(
            {"error": "Chama not found or not a member"},
            status=status.HTTP_404_NOT_FOUND,
        )
    feature_block = _require_ai_feature_for_chama(chama, "ai_advanced")
    if feature_block:
        return feature_block
    
    # Force refresh
    profile = RiskEngine.calculate_risk_profile(
        request.user,
        chama,
        force_refresh=True,
    )
    
    serializer = RiskProfileSerializer(profile)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def loan_eligibility(request, chama_id=None):
    """
    GET /api/ai/loan-eligibility/{chama_id}/
    
    Get loan eligibility for user in a chama.
    """
    if not chama_id:
        return Response(
            {"error": "chama_id required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    
    try:
        chama = Chama.objects.get(id=chama_id)
        Membership.objects.get(
            user=request.user,
            chama=chama,
            is_active=True,
            is_approved=True,
        )
    except (Chama.DoesNotExist, Membership.DoesNotExist):
        return Response(
            {"error": "Chama not found or not a member"},
            status=status.HTTP_404_NOT_FOUND,
        )
    feature_block = _require_ai_feature_for_chama(chama, "ai_advanced")
    if feature_block:
        return feature_block
    
    # Calculate eligibility
    eligibility = RiskEngine.calculate_loan_eligibility(request.user, chama)
    
    serializer = LoanEligibilityResponseSerializer(eligibility)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def insights(request, chama_id=None):
    """
    GET /api/ai/insights/{chama_id}/
    
    Get AI insights for a chama.
    """
    if not chama_id:
        return Response(
            {"error": "chama_id required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    
    try:
        chama = Chama.objects.get(id=chama_id)
        Membership.objects.get(
            user=request.user,
            chama=chama,
            is_active=True,
            is_approved=True,
        )
    except (Chama.DoesNotExist, Membership.DoesNotExist):
        return Response(
            {"error": "Chama not found or not a member"},
            status=status.HTTP_404_NOT_FOUND,
        )
    feature_block = _require_ai_feature_for_chama(chama, "ai_advanced")
    if feature_block:
        return feature_block
    
    # Get active insights
    insights = AIInsight.objects.filter(
        chama=chama,
        is_active=True,
    )
    
    serializer = AIInsightSerializer(insights, many=True)
    
    return Response({
        "insights": serializer.data,
        "last_generated": insights.first().created_at if insights.exists() else None,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def insights_refresh(request, chama_id=None):
    """
    POST /api/ai/insights/{chama_id}/refresh/
    
    Regenerate insights for a chama.
    """
    if not chama_id:
        return Response(
            {"error": "chama_id required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    
    try:
        chama = Chama.objects.get(id=chama_id)
        membership = Membership.objects.get(
            user=request.user,
            chama=chama,
            is_active=True,
            is_approved=True,
        )
    except (Chama.DoesNotExist, Membership.DoesNotExist):
        return Response(
            {"error": "Chama not found or not a member"},
            status=status.HTTP_404_NOT_FOUND,
        )
    feature_block = _require_ai_feature_for_chama(chama, "ai_advanced")
    if feature_block:
        return feature_block
    role_block = _require_ai_manager(membership, "Only admins can refresh insights")
    if role_block:
        return role_block
    
    # Generate new insights
    InsightsEngine.generate_all_insights(chama.id)
    
    # Return updated insights
    insights = AIInsight.objects.filter(chama=chama, is_active=True)
    serializer = AIInsightSerializer(insights, many=True)
    
    return Response({
        "insights": serializer.data,
        "last_generated": timezone.now(),
    })

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def fraud_flags(request, chama_id=None):
    """
    GET /api/ai/fraud-flags/{chama_id}/
    
    Get fraud flags for a chama.
    """
    if not chama_id:
        return Response(
            {"error": "chama_id required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    
    try:
        chama = Chama.objects.get(id=chama_id)
        membership = Membership.objects.get(
            user=request.user,
            chama=chama,
            is_active=True,
            is_approved=True,
        )
    except (Chama.DoesNotExist, Membership.DoesNotExist):
        return Response(
            {"error": "Chama not found or not a member"},
            status=status.HTTP_404_NOT_FOUND,
        )
    feature_block = _require_ai_feature_for_chama(chama, "ai_advanced")
    if feature_block:
        return feature_block
    role_block = _require_ai_manager(membership, "Only admins can view fraud flags")
    if role_block:
        return role_block
    
    # Get unresolved flags
    resolved = request.query_params.get("resolved", "false").lower() == "true"
    
    flags = FraudFlag.objects.filter(
        chama=chama,
        resolved=resolved,
    )
    
    serializer = FraudFlagSerializer(flags, many=True)
    return Response({"flags": serializer.data})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def fraud_flags_resolve(request, chama_id=None, flag_id=None):
    """
    POST /api/ai/fraud-flags/{chama_id}/{flag_id}/resolve/
    
    Resolve a fraud flag.
    """
    if not chama_id or not flag_id:
        return Response(
            {"error": "chama_id and flag_id required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    
    serializer = FraudFlagResolveSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        chama = Chama.objects.get(id=chama_id)
        membership = Membership.objects.get(
            user=request.user,
            chama=chama,
            is_active=True,
            is_approved=True,
        )
    except (Chama.DoesNotExist, Membership.DoesNotExist):
        return Response(
            {"error": "Chama not found or not a member"},
            status=status.HTTP_404_NOT_FOUND,
        )
    feature_block = _require_ai_feature_for_chama(chama, "ai_advanced")
    if feature_block:
        return feature_block
    role_block = _require_ai_manager(membership, "Only admins can resolve fraud flags")
    if role_block:
        return role_block
    
    try:
        flag = FraudFlag.objects.get(id=flag_id, chama=chama)
    except FraudFlag.DoesNotExist:
        return Response(
            {"error": "Flag not found"},
            status=status.HTTP_404_NOT_FOUND,
        )
    
    # Resolve flag
    FraudEngine.resolve_flag(
        flag_id=flag.id,
        resolved_by=request.user,
        resolution_note=serializer.validated_data["resolution_note"],
    )
    
    return Response({"status": "resolved", "flag_id": flag.id})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def fraud_check(request, chama_id=None):
    """
    POST /api/ai/fraud-check/{chama_id}/
    
    Trigger fraud check for a user.
    """
    if not chama_id:
        return Response(
            {"error": "chama_id required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    
    try:
        chama = Chama.objects.get(id=chama_id)
        membership = Membership.objects.get(
            user=request.user,
            chama=chama,
            is_active=True,
            is_approved=True,
        )
    except (Chama.DoesNotExist, Membership.DoesNotExist):
        return Response(
            {"error": "Chama not found or not a member"},
            status=status.HTTP_404_NOT_FOUND,
        )
    feature_block = _require_ai_feature_for_chama(chama, "ai_advanced")
    if feature_block:
        return feature_block
    
    # Run fraud checks
    flags = FraudEngine.check_all_rules(request.user, chama)
    
    return Response({
        "checked": True,
        "new_flags": len(flags),
        "flags": FraudFlagSerializer(flags, many=True).data,
    })


# =============================================================================
# NEW ENDPOINTS FOR FLOATING AI ASSISTANT
# =============================================================================
def _log_usage(request, chama, endpoint: str, tokens_in: int = 0, tokens_out: int = 0, 
               latency_ms: int = 0, status_code: int = 200, error_message: str = ""):
    """Log AI usage for billing/monitoring."""
    try:
        AIUsageLog.objects.create(
            user=request.user,
            chama=chama,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            endpoint=endpoint,
            status_code=status_code,
            error_message=error_message,
        )
    except Exception:
        pass  # Don't fail the request if logging fails


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@require_feature('ai_basic', or_features=['ai_advanced'])
def ai_chat_stream(request):
    """
    POST /api/ai/chat/stream
    
    Streaming AI chat endpoint. Returns SSE stream for real-time responses.
    """
    message = str(request.data.get("message") or "").strip()
    conversation_id = request.data.get("conversation_id")
    
    if not message:
        return Response(
            {"detail": "message is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    
    chama = _get_chama_from_request(request)
    if not chama:
        return Response(
            {"detail": "chama_id is required or no active membership found"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    
    # Check membership
    try:
        membership = Membership.objects.get(
            user=request.user, 
            chama=chama, 
            is_active=True, 
            is_approved=True
        )
    except Membership.DoesNotExist:
        return Response(
            {"detail": "You are not an approved active member of this chama."},
            status=status.HTTP_403_FORBIDDEN,
        )
    
    # Rate limiting check
    from apps.ai.models import AIInteraction
    from datetime import timedelta
    window_start = timezone.now() - timedelta(hours=1)
    recent_count = AIInteraction.objects.filter(
        user=request.user,
        created_at__gte=window_start,
    ).count()
    
    if recent_count >= 50:  # 50 messages per hour
        return Response(
            {"detail": "Rate limit exceeded. Please try again later."},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    
    start_time = time.time()
    
    # Use the chat engine for processing
    try:
        response_text, context_data = ChatEngine.process_message(
            user=request.user,
            message=message,
            chama=chama,
        )
    except Exception as e:
        latency_ms = int((time.time() - start_time) * 1000)
        _log_usage(request, chama, "chat_stream", 0, 0, latency_ms, 500, str(e))
        return Response(
            {"detail": f"Error processing message: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    
    latency_ms = int((time.time() - start_time) * 1000)
    tokens_in = len(message.split()) * 2  # Rough estimate
    tokens_out = len(response_text.split()) * 2
    
    # Log usage
    _log_usage(request, chama, "chat_stream", tokens_in, tokens_out, latency_ms)
    
    # Create generator for streaming with two-stage response
    def generate_response():
        # Stage A: Quick acknowledgment (within 300ms)
        quick_acks = [
            "Let me check that for you...",
            "Checking your records now...",
            "Looking up that information...",
            "Fetching your data...",
        ]
        import random
        ack = random.choice(quick_acks)
        
        # Yield quick acknowledgment first
        yield f"data: {json.dumps({'stage': 'ack', 'content': ack, 'done': False})}\n\n"
        
        # Small delay to ensure acknowledgment is received
        time.sleep(0.1)
        
        # Stage B: Stream the actual response
        words = response_text.split()
        for i, word in enumerate(words):
            yield f"data: {json.dumps({'stage': 'stream', 'content': word + ' ', 'done': False})}\n\n"
            time.sleep(0.02)  # Simulate token delay
        
        # Final chunk
        yield f"data: {json.dumps({'stage': 'done', 'content': '', 'done': True})}\n\n"
    
    return StreamingHttpResponse(
        generate_response(),
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ai_chat_stop(request):
    """
    POST /api/ai/chat/stop
    
    Stop an ongoing streaming response. This endpoint marks the current
    conversation message as partial/cancelled.
    """
    conversation_id = request.data.get("conversation_id")
    
    if not conversation_id:
        return Response(
            {"error": "conversation_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    
    # Try to find and mark the last message as partial
    try:
        from .models import AIMessage
        
        message = AIMessage.objects.filter(
            conversation_id=conversation_id,
            role=AIMessageRole.ASSISTANT,
        ).order_by('-created_at').first()
        
        if message:
            # Mark as partial by appending a note
            message.content += "\n\n[Response interrupted by user]"
            message.save()
            
        return Response({
            "status": "stopped",
            "message": "Streaming response stopped",
        })
        
    except Exception as e:
        return Response(
            {"error": f"Failed to stop: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
@require_feature('ai_basic', or_features=['ai_advanced'])
def ai_suggestions(request):
    """
    GET /api/ai/suggestions
    
    Get role-based suggestions for the AI chat.
    """
    chama = _get_chama_from_request(request)
    if not chama:
        return Response(
            {"detail": "No active chama found"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    
    # Check membership
    if not Membership.objects.filter(
        user=request.user, 
        chama=chama, 
        is_active=True, 
        is_approved=True
    ).exists():
        return Response(
            {"detail": "Not a member of this chama"},
            status=status.HTTP_403_FORBIDDEN,
        )
    
    suggestions = get_role_suggestions(request.user, chama)
    
    return Response({
        "suggestions": suggestions,
        "chama_id": str(chama.id),
        "chama_name": chama.name,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ai_context(request):
    """
    GET /api/ai/context
    
    Get AI context including role, chama, and feature information.
    This endpoint provides the context needed for role-aware AI UI.
    """
    from apps.billing.services import get_access_status, get_entitlements, has_feature
    from apps.chama.services import get_effective_role
    
    chama = _get_chama_from_request(request)
    
    # If no chama, return basic context without features
    if not chama:
        return Response({
            "has_active_chama": False,
            "role": None,
            "chama_id": None,
            "chama_name": None,
            "features": {
                "ai_basic": False,
                "ai_advanced": False,
            },
            "allowed_categories": [],
            "message": "Select a chama to enable AI features",
        })
    
    # Check membership
    membership = Membership.objects.filter(
        user=request.user, 
        chama=chama, 
        is_active=True, 
        is_approved=True
    ).select_related('chama').first()
    
    if not membership:
        return Response(
            {"detail": "Not a member of this chama"},
            status=status.HTTP_403_FORBIDDEN,
        )
    
    # Get effective role
    role = get_effective_role(request.user, chama.id, membership)
    
    # Get access status
    access = get_access_status(chama)
    entitlements = get_entitlements(chama)
    
    # Define allowed AI categories by role
    ROLE_AI_CATEGORIES = {
        "MEMBER": ["wallet", "contributions", "loans", "meetings", "payments", "guidance"],
        "SECRETARY": ["wallet", "contributions", "meetings", "governance", "activity", "members"],
        "TREASURER": ["wallet", "loans", "contributions", "fines", "payments", "reconciliation", "reports"],
        "AUDITOR": ["audit", "loans", "wallet", "activity", "reports", "anomalies"],
        "CHAMA_ADMIN": ["wallet", "loans", "contributions", "audit", "fines", "activity", "reports", "billing", "governance"],
        "ADMIN": ["wallet", "loans", "contributions", "audit", "fines", "activity", "reports", "billing", "governance", "system"],
    }
    
    allowed_categories = ROLE_AI_CATEGORIES.get(role, ["wallet", "guidance"])
    
    # Get role-specific title for AI UI
    ROLE_TITLES = {
        "MEMBER": "My Assistant",
        "SECRETARY": "Secretary Assistant",
        "TREASURER": "Finance Assistant",
        "AUDITOR": "Audit Assistant",
        "CHAMA_ADMIN": "Admin Assistant",
        "ADMIN": "System Assistant",
    }
    
    return Response({
        "has_active_chama": True,
        "role": role,
        "role_title": ROLE_TITLES.get(role, "AI Assistant"),
        "chama_id": str(chama.id),
        "chama_name": chama.name,
        "features": {
            "ai_basic": has_feature(chama, "ai_basic"),
            "ai_advanced": has_feature(chama, "ai_advanced"),
        },
        "access": {
            "requires_payment": access.get("requires_payment", False),
            "trial_days_remaining": access.get("trial_days_remaining", 0),
        },
        "plan": {
            "code": entitlements.get("plan_code", "FREE"),
            "name": entitlements.get("plan_name", "Free Plan"),
        },
        "allowed_categories": allowed_categories,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ai_feedback(request):
    """
    POST /api/ai/feedback
    
    Submit feedback on AI assistant responses.
    """
    serializer = AIAnswerFeedbackCreateSerializer(data=request.data)
    
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    message_id = serializer.validated_data["message_id"]
    rating = serializer.validated_data["rating"]
    comment = serializer.validated_data.get("comment", "")
    
    # Get the message
    try:
        from .models import AIMessage
        message = AIMessage.objects.select_related("conversation__chama").get(
            id=message_id,
            conversation__user=request.user,
        )
    except AIMessage.DoesNotExist:
        return Response(
            {"detail": "Message not found"},
            status=status.HTTP_404_NOT_FOUND,
        )
    feature_block = _require_ai_feature_for_chama(
        message.conversation.chama,
        "ai_basic",
        or_features=["ai_advanced"],
    )
    if feature_block:
        return feature_block
    
    # Check if feedback already exists
    existing = AIAnswerFeedback.objects.filter(
        message=message,
        user=request.user,
    ).first()
    
    if existing:
        # Update existing feedback
        existing.rating = rating
        existing.comment = comment
        existing.save()
        return Response({
            "status": "updated",
            "feedback_id": str(existing.id),
        })
    
    # Create new feedback
    feedback = AIAnswerFeedback.objects.create(
        message=message,
        user=request.user,
        rating=rating,
        comment=comment,
    )
    
    return Response({
        "status": "created",
        "feedback_id": str(feedback.id),
    })


# =============================================================================
# TOOL EXECUTION ENDPOINT
# =============================================================================

AVAILABLE_TOOLS = {
    "get_my_wallet_summary": {
        "name": "get_my_wallet_summary",
        "description": "Get user's personal wallet summary including contributions, withdrawals, and loans",
        "category": "wallet",
    },
    "get_chama_wallet_summary": {
        "name": "get_chama_wallet_summary",
        "description": "Get chama-wide wallet summary (treasurer/admin only)",
        "category": "wallet",
        "required_roles": ["CHAMA_ADMIN", "TREASURER"],
    },
    "get_contributions_status": {
        "name": "get_contributions_status",
        "description": "Get contribution status for members",
        "category": "contributions",
    },
    "get_unpaid_members": {
        "name": "get_unpaid_members",
        "description": "List members with unpaid contributions",
        "category": "contributions",
        "required_roles": ["CHAMA_ADMIN", "TREASURER", "SECRETARY"],
    },
    "get_my_loan_status": {
        "name": "get_my_loan_status",
        "description": "Get loan status and repayment information",
        "category": "loans",
    },
    "get_loan_book": {
        "name": "get_loan_book",
        "description": "Get entire loan book (treasurer/admin only)",
        "category": "loans",
        "required_roles": ["CHAMA_ADMIN", "TREASURER"],
    },
    "get_fines_summary": {
        "name": "get_fines_summary",
        "description": "Get fines summary",
        "category": "fines",
    },
    "get_meeting_schedule": {
        "name": "get_meeting_schedule",
        "description": "Get upcoming meeting schedule",
        "category": "meetings",
    },
    "get_recent_activity_feed": {
        "name": "get_recent_activity_feed",
        "description": "Get recent activity feed",
        "category": "activity",
    },
    "get_mpesa_transaction_status": {
        "name": "get_mpesa_transaction_status",
        "description": "Check M-Pesa transaction status",
        "category": "payments",
        "required_roles": ["CHAMA_ADMIN", "TREASURER"],
    },
    "get_audit_logs": {
        "name": "get_audit_logs",
        "description": "Get audit logs (auditor/admin only)",
        "category": "audit",
        "required_roles": ["CHAMA_ADMIN", "AUDITOR"],
    },
    "generate_statement_pdf": {
        "name": "generate_statement_pdf",
        "description": "Generate a member statement for download",
        "category": "reports",
    },
}


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ai_attachment_upload(request):
    """
    POST /api/ai/attachment/upload
    
    Upload an attachment (image or file) for AI chat messages.
    Supports: images, PDFs, documents
    """
    from django.core.files.storage import default_storage
    import uuid
    
    if 'file' not in request.FILES:
        return Response(
            {"error": "No file provided"},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    uploaded_file = request.FILES['file']
    
    # Validate file type
    allowed_types = {
        'image/jpeg', 'image/png', 'image/gif', 'image/webp',
        'application/pdf',
        'application/msword',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'text/plain',
    }
    
    if uploaded_file.content_type not in allowed_types:
        return Response(
            {"error": f"File type {uploaded_file.content_type} not allowed"},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Validate file size (max 10MB)
    if uploaded_file.size > 10 * 1024 * 1024:
        return Response(
            {"error": "File size must be less than 10MB"},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Determine attachment type
    attachment_type = 'image' if uploaded_file.content_type.startswith('image/') else 'file'
    
    # Generate unique filename
    file_ext = uploaded_file.name.split('.')[-1] if '.' in uploaded_file.name else ''
    unique_filename = f"{uuid.uuid4()}.{file_ext}" if file_ext else str(uuid.uuid4())
    file_path = f"ai_attachments/{request.user.id}/{unique_filename}"
    
    # Save file
    saved_path = default_storage.save(file_path, uploaded_file)
    file_url = default_storage.url(saved_path)
    
    # Create attachment record
    from apps.ai.models import AIAttachment
    attachment = AIAttachment.objects.create(
        user=request.user,
        attachment_type=attachment_type,
        file_name=uploaded_file.name,
        file_url=file_url,
        file_size=uploaded_file.size,
        mime_type=uploaded_file.content_type,
    )
    
    return Response({
        "success": True,
        "attachment": {
            "id": str(attachment.id),
            "type": attachment.attachment_type,
            "name": attachment.file_name,
            "url": attachment.file_url,
            "size": attachment.file_size,
            "mime_type": attachment.mime_type,
        }
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ai_tool_execute(request):
    """
    POST /api/ai/tool/execute
    
    Execute a specific tool and return its result.
    This allows the frontend to directly call tools for better UX.
    """
    from .ai_tools import ToolRouter
    
    tool_name = request.data.get("tool_name")
    params = request.data.get("params", {})
    
    if not tool_name:
        return Response(
            {"error": "tool_name is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    
    if tool_name not in AVAILABLE_TOOLS:
        return Response(
            {"error": f"Unknown tool: {tool_name}. Available: {list(AVAILABLE_TOOLS.keys())}"},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )
    
    tool_info = AVAILABLE_TOOLS[tool_name]
    
    # Get chama from request
    chama = _get_chama_from_request(request)
    if not chama:
        return Response(
            {"error": "No active chama selected"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    
    membership = _get_active_membership(request.user, chama)
    if not membership:
        return Response(
            {"error": "Not a member of this chama"},
            status=status.HTTP_403_FORBIDDEN,
        )
    
    # Execute the tool
    try:
        if tool_name == "get_my_wallet_summary":
            result = ToolRouter.get_my_wallet_summary(request.user, chama)
        elif tool_name == "get_chama_wallet_summary":
            result = ToolRouter.get_chama_wallet_summary(chama, request.user)
        elif tool_name == "get_contributions_status":
            result = ToolRouter.get_contributions_status(
                chama, request.user, params.get("cycle")
            )
        elif tool_name == "get_unpaid_members":
            result = ToolRouter.get_unpaid_members(
                chama, request.user, params.get("cycle")
            )
        elif tool_name == "get_my_loan_status":
            result = ToolRouter.get_my_loan_status(request.user, chama)
        elif tool_name == "get_loan_book":
            result = ToolRouter.get_loan_book(chama, request.user)
        elif tool_name == "get_fines_summary":
            result = ToolRouter.get_fines_summary(
                chama, request.user, params.get("cycle")
            )
        elif tool_name == "get_meeting_schedule":
            result = ToolRouter.get_meeting_schedule(chama, request.user)
        elif tool_name == "get_recent_activity_feed":
            result = ToolRouter.get_recent_activity_feed(
                chama, request.user, params.get("days", 7)
            )
        elif tool_name == "get_mpesa_transaction_status":
            result = ToolRouter.get_mpesa_transaction_status(
                chama, params.get("transaction_id"), params.get("phone")
            )
        elif tool_name == "get_audit_logs":
            result = ToolRouter.get_audit_logs(
                chama, request.user, params.get("days", 30)
            )
        elif tool_name == "generate_statement_pdf":
            result = ToolRouter.generate_statement_pdf(
                chama, request.user, params.get("period_months"), params.get("member_id")
            )
        else:
            return Response(
                {"error": f"Tool not implemented: {tool_name}"},
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )
        
        return Response({
            "success": True,
            "tool": tool_name,
            "result": result,
        })
        
    except PermissionDenied as e:
        return Response(
            {
                "success": True,
                "tool": tool_name,
                "result": {
                    "available": False,
                    "permission_error": True,
                    "error": str(e),
                },
            }
        )
    except Exception as e:
        logger.exception("Error executing tool")
        return Response(
            {"error": f"Tool execution failed: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
@require_feature('ai_basic', or_features=['ai_advanced'])
def ai_conversations(request):
    """
    GET /api/ai/conversations
    
    Get list of user's AI conversations.
    """
    chama = _get_chama_from_request(request)
    if not chama:
        return Response(
            {"detail": "No active chama found"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    
    # Check membership
    if not Membership.objects.filter(
        user=request.user, 
        chama=chama, 
        is_active=True, 
        is_approved=True
    ).exists():
        return Response(
            {"detail": "Not a member of this chama"},
            status=status.HTTP_403_FORBIDDEN,
        )
    
    from .models import AIConversation
    
    conversations = AIConversation.objects.filter(
        user=request.user,
        chama=chama,
    ).prefetch_related("messages").order_by("-created_at")[:20]
    
    serializer = AIConversationListSerializer(conversations, many=True)
    
    return Response({
        "conversations": serializer.data,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ai_messages(request, conversation_id):
    """
    GET /api/ai/conversations/{conversation_id}/messages
    
    Get messages for a specific conversation.
    """
    from .models import AIConversation, AIMessage
    
    try:
        conversation = AIConversation.objects.get(
            id=conversation_id,
            user=request.user,
        )
    except AIConversation.DoesNotExist:
        return Response(
            {"detail": "Conversation not found"},
            status=status.HTTP_404_NOT_FOUND,
        )
    feature_block = _require_ai_feature_for_chama(
        conversation.chama,
        "ai_basic",
        or_features=["ai_advanced"],
    )
    if feature_block:
        return feature_block
    
    messages = AIMessage.objects.filter(
        conversation=conversation,
    ).order_by("created_at")
    
    serializer = AIMessageSerializer(messages, many=True)
    
    return Response({
        "conversation_id": str(conversation.id),
        "messages": serializer.data,
    })


# =============================================================================
# EMBEDDED MINI CHATGPT ASSISTANT (OVERRIDES FOR WIDGET UX)
# =============================================================================

EMBEDDED_AI_ROLE_TOOL_MATRIX = {
    "MEMBER": [
        "get_my_wallet_summary",
        "get_my_contribution_status",
        "get_my_loan_status",
        "get_my_fines",
        "download_my_statement",
        "general_help",
    ],
    "TREASURER": [
        "get_my_wallet_summary",
        "get_my_contribution_status",
        "get_my_loan_status",
        "get_my_fines",
        "download_my_statement",
        "general_help",
        "get_chama_wallet_totals",
        "get_unpaid_members",
        "get_loan_queue_summary",
        "get_reconciliation_mismatches",
        "generate_group_statement",
        "finance_reports_summary",
    ],
    "SECRETARY": [
        "get_my_wallet_summary",
        "get_my_contribution_status",
        "get_my_loan_status",
        "get_my_fines",
        "download_my_statement",
        "general_help",
        "member_directory_summary",
        "join_requests_summary",
        "meetings_schedule",
        "announcements_templates",
        "contribution_reminder_targets",
    ],
    "AUDITOR": [
        "get_my_wallet_summary",
        "get_my_contribution_status",
        "get_my_loan_status",
        "get_my_fines",
        "download_my_statement",
        "general_help",
        "audit_logs_readonly",
        "anomalies_summary",
        "loan_book_aging",
        "export_reports_readonly",
        "reconciliation_readonly",
    ],
    "CHAMA_ADMIN": [
        "get_my_wallet_summary",
        "get_my_contribution_status",
        "get_my_loan_status",
        "get_my_fines",
        "download_my_statement",
        "general_help",
        "get_chama_wallet_totals",
        "get_unpaid_members",
        "get_loan_queue_summary",
        "get_reconciliation_mismatches",
        "generate_group_statement",
        "finance_reports_summary",
        "member_directory_summary",
        "join_requests_summary",
        "meetings_schedule",
        "announcements_templates",
        "contribution_reminder_targets",
        "audit_logs_readonly",
        "anomalies_summary",
        "loan_book_aging",
        "export_reports_readonly",
        "reconciliation_readonly",
        "billing_status",
        "get_plan_limits",
        "roles_permissions_summary",
        "system_activity_summary",
    ],
    "ADMIN": [
        "get_my_wallet_summary",
        "get_my_contribution_status",
        "get_my_loan_status",
        "get_my_fines",
        "download_my_statement",
        "general_help",
        "get_chama_wallet_totals",
        "get_unpaid_members",
        "get_loan_queue_summary",
        "get_reconciliation_mismatches",
        "generate_group_statement",
        "finance_reports_summary",
        "member_directory_summary",
        "join_requests_summary",
        "meetings_schedule",
        "announcements_templates",
        "contribution_reminder_targets",
        "audit_logs_readonly",
        "anomalies_summary",
        "loan_book_aging",
        "export_reports_readonly",
        "reconciliation_readonly",
        "billing_status",
        "get_plan_limits",
        "roles_permissions_summary",
        "system_activity_summary",
    ],
}

EMBEDDED_AI_ROLE_TITLES = {
    "MEMBER": "Member Mode",
    "TREASURER": "Treasurer Mode",
    "SECRETARY": "Secretary Mode",
    "AUDITOR": "Auditor Mode",
    "CHAMA_ADMIN": "Admin Mode",
    "ADMIN": "Admin Mode",
}

EMBEDDED_AI_SUGGESTIONS = {
    "public": [
        "How does Digital Chama work?",
        "Show me pricing plans",
        "Is my data secure?",
        "How do I create or join a chama?",
    ],
    "MEMBER": [
        "Show my wallet summary",
        "What is my loan status?",
        "Do I have any fines due?",
        "Download my statement",
    ],
    "TREASURER": [
        "Show chama wallet totals",
        "List unpaid members this month",
        "Show the loan queue summary",
        "Are there reconciliation mismatches?",
    ],
    "SECRETARY": [
        "Show pending join requests",
        "List upcoming meetings",
        "Who needs contribution reminders?",
        "Give me an announcement template",
    ],
    "AUDITOR": [
        "Show audit logs",
        "Summarize anomalies",
        "Show loan book aging",
        "Show reconciliation summary",
    ],
    "CHAMA_ADMIN": [
        "Show billing status",
        "Show chama wallet totals",
        "Summarize join requests",
        "Show system activity summary",
    ],
    "ADMIN": [
        "Show billing status",
        "Show chama wallet totals",
        "Summarize join requests",
        "Show system activity summary",
    ],
}


class EmbeddedAIAccessError(Exception):
    def __init__(self, detail, status_code=status.HTTP_400_BAD_REQUEST, payload=None):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.payload = payload or {"detail": detail}


def _safe_ai_cache_key(*parts):
    safe_parts = [re.sub(r"[^a-zA-Z0-9:_-]+", "-", str(part)) for part in parts]
    return "ai-widget:" + ":".join(safe_parts)


def _normalize_numeric_token(value):
    text = str(value or "").replace(",", "").strip()
    if not text:
        return ""
    try:
        normalized = format(Decimal(text), "f")
        if "." in normalized:
            normalized = normalized.rstrip("0").rstrip(".")
        return normalized or "0"
    except Exception:
        return text


def _estimate_token_count(text):
    return max(1, len(str(text or "").split()) * 2)


def _split_stream_chunks(text):
    candidate = str(text or "").strip()
    if not candidate:
        return []
    words = candidate.split()
    chunks = []
    current = []
    for word in words:
        current.append(word)
        if len(current) >= 5:
            chunks.append(" ".join(current) + " ")
            current = []
    if current:
        chunks.append(" ".join(current))
    return chunks


def _build_public_mode_payload():
    return {
        "authenticated": False,
        "has_active_chama": False,
        "role": None,
        "role_title": "Public Mode",
        "assistant_label": "Digital Chama Assistant",
        "mode": "public",
        "active_chama": None,
        "features": {"ai_enabled": True, "ai_basic": True, "ai_advanced": False},
        "allowed_tools": [],
        "allowed_suggestion_chips": EMBEDDED_AI_SUGGESTIONS["public"],
        "plan": None,
    }


def _collect_payload_ids(value, collector=None):
    collector = collector or set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "id" or key.endswith("_id"):
                text = str(item or "").strip()
                if text:
                    collector.add(text)
            _collect_payload_ids(item, collector)
    elif isinstance(value, list):
        for item in value:
            _collect_payload_ids(item, collector)
    return collector


def _collect_payload_numeric_tokens(value, collector=None):
    collector = collector or set()
    if isinstance(value, dict):
        for item in value.values():
            _collect_payload_numeric_tokens(item, collector)
    elif isinstance(value, list):
        for item in value:
            _collect_payload_numeric_tokens(item, collector)
    elif isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        collector.add(_normalize_numeric_token(value))
    elif isinstance(value, str):
        for token in _extract_numeric_tokens(value):
            collector.add(_normalize_numeric_token(token))
    return collector


def _build_data_used_ref(tool_name, payload):
    return {
        "tool_name": tool_name,
        "ids": sorted(_collect_payload_ids(payload))[:20],
    }


def _validate_grounded_assistant_output(answer_text, tool_results):
    if not tool_results:
        return True

    supported_numbers = set()
    for item in tool_results:
        supported_numbers.update(_collect_payload_numeric_tokens(item.get("payload") or {}))

    answer_numbers = {
        _normalize_numeric_token(token)
        for token in _extract_numeric_tokens(answer_text)
    }

    if answer_numbers and not supported_numbers:
        return False

    return not bool(answer_numbers - supported_numbers)


def _cache_inline_payload(context, cache_namespace, builder, **cache_kwargs):
    cache_key = _safe_ai_cache_key(
        "inline-tool",
        cache_namespace,
        context["chama"].id,
        context["user"].id,
        json.dumps(cache_kwargs, sort_keys=True, default=str),
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    result = builder()
    cache.set(cache_key, result, timeout=AI_TOOL_CACHE_TTL)
    return result


def _resolve_embedded_private_context(request):
    if not request.user or not request.user.is_authenticated:
        raise EmbeddedAIAccessError(
            "Authentication required",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    chama = _get_chama_from_request(request)
    if not chama:
        raise EmbeddedAIAccessError(
            "Select a Chama first.",
            status_code=status.HTTP_409_CONFLICT,
        )

    membership = _get_active_membership(request.user, chama)
    if not membership:
        raise EmbeddedAIAccessError(
            "You are not an approved active member of this chama.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    context_cache_key = _safe_ai_cache_key("context", chama.id, request.user.id)
    cached_snapshot = cache.get(context_cache_key)
    if cached_snapshot is None:
        access = get_access_status(chama)
        ai_basic_enabled = has_feature(chama, "ai_basic")
        ai_advanced_enabled = has_feature(chama, "ai_advanced")
        effective_role = get_effective_role(request.user, chama.id, membership)
        entitlements = get_entitlements(chama)
        cached_snapshot = {
            "access": {
                "requires_payment": access.get("requires_payment", False),
                "trial_days_remaining": access.get("trial_days_remaining", 0),
                "trial_ends_at": access.get("trial_ends_at"),
                "reason": access.get("reason"),
            },
            "features": {
                "ai_enabled": bool(ai_basic_enabled or ai_advanced_enabled),
                "ai_basic": ai_basic_enabled,
                "ai_advanced": ai_advanced_enabled,
            },
            "role": effective_role,
            "role_title": EMBEDDED_AI_ROLE_TITLES.get(effective_role, "AI Mode"),
            "allowed_tools": EMBEDDED_AI_ROLE_TOOL_MATRIX.get(effective_role, ["general_help"]),
            "allowed_suggestion_chips": EMBEDDED_AI_SUGGESTIONS.get(
                effective_role,
                EMBEDDED_AI_SUGGESTIONS["MEMBER"],
            ),
            "plan": {
                "code": entitlements.get("plan_code", "FREE"),
                "name": entitlements.get("plan_name", "Free Trial"),
            },
        }
        cache.set(context_cache_key, cached_snapshot, timeout=AI_CONTEXT_CACHE_TTL)

    access = cached_snapshot["access"]
    if access.get("requires_payment"):
        raise EmbeddedAIAccessError(
            "AI access is blocked until billing is active for this chama.",
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            payload={
                "detail": "AI access is blocked until billing is active for this chama.",
                "reason": access.get("reason"),
                "trial_ends_at": access.get("trial_ends_at"),
                "trial_days_remaining": access.get("trial_days_remaining", 0),
            },
        )

    if not cached_snapshot["features"]["ai_enabled"]:
        raise EmbeddedAIAccessError(
            "Your current plan does not include AI Assistant. Please upgrade.",
            status_code=status.HTTP_403_FORBIDDEN,
            payload={
                "detail": "Your current plan does not include AI Assistant. Please upgrade.",
                "upgrade_required": True,
            },
        )

    return {
        "authenticated": True,
        "user": request.user,
        "chama": chama,
        "membership": membership,
        "role": cached_snapshot["role"],
        "role_title": cached_snapshot["role_title"],
        "assistant_label": f"Chama Assistant • {cached_snapshot['role_title']}",
        "mode": "private",
        "allowed_tools": cached_snapshot["allowed_tools"],
        "features": cached_snapshot["features"],
        "access": access,
        "plan": cached_snapshot["plan"],
        "active_chama": {
            "id": str(chama.id),
            "name": chama.name,
        },
        "allowed_suggestion_chips": cached_snapshot["allowed_suggestion_chips"],
    }


def _store_tool_audit(*, context, tool_name, args, result_summary, allowed):
    chama = context.get("chama")
    if not chama:
        return
    try:
        from .models import AIToolCallLog

        AIToolCallLog.objects.create(
            chama=chama,
            actor=context.get("user"),
            tool_name=tool_name,
            args=args or {},
            result_summary=result_summary[:500],
            allowed=allowed,
        )
    except Exception:
        logger.exception("Failed to store AI tool audit log")


def _render_general_help(context):
    role = context.get("role") or "MEMBER"
    chips = context.get("allowed_suggestion_chips") or []
    lines = [
        "I can help with verified chama data only.",
        "",
        f"- Current role: {role}",
        f"- Plan: {context.get('plan', {}).get('name', 'Unknown')}",
    ]
    if chips:
        lines.extend(
            [
                "",
                "Try one of these:",
                *[f"- {chip}" for chip in chips[:4]],
            ]
        )
    return {
        "summary": "Guidance only",
        "content": "\n".join(lines),
        "actions": [
            {"label": "Open Wallet", "href": "/app/wallet"},
            {"label": "View Contributions", "href": "/app/contributions"},
        ],
    }


def _tool_result_from_registry(tool_name, context, **kwargs):
    from .ai_tools import ToolRegistry

    chama = context["chama"]
    user = context["user"]
    cache_key = _safe_ai_cache_key(
        "tool",
        tool_name,
        chama.id,
        user.id,
        json.dumps(kwargs, sort_keys=True, default=str),
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    result = ToolRegistry.execute_tool(tool_name, user, chama, **kwargs)
    cache.set(cache_key, result, timeout=AI_TOOL_CACHE_TTL)
    return result


def _execute_embedded_tool(tool_name, context):
    from django.db.models import Count, Sum
    from apps.chama.models import Membership, MembershipRequest, MembershipRequestStatus
    from apps.finance.models import Loan, LoanStatus
    from apps.fines.models import Fine
    from apps.payments.models import PaymentIntent, PaymentIntentStatus

    if tool_name not in context["allowed_tools"]:
        _store_tool_audit(
            context=context,
            tool_name=tool_name,
            args={},
            result_summary="Blocked by role policy",
            allowed=False,
        )
        raise EmbeddedAIAccessError(
            "That request is outside your role permissions.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    chama = context["chama"]
    user = context["user"]

    if tool_name == "general_help":
        payload = _render_general_help(context)
    elif tool_name == "get_my_wallet_summary":
        payload = _tool_result_from_registry("get_my_wallet_summary", context)
    elif tool_name == "get_my_contribution_status":
        payload = _tool_result_from_registry("get_contributions_status", context)
    elif tool_name == "get_my_loan_status":
        payload = _tool_result_from_registry("get_my_loan_status", context)
    elif tool_name == "get_my_fines":
        payload = _cache_inline_payload(
            context,
            "get_my_fines",
            lambda: (
                lambda fines: {
                    "available": True,
                    "fine_count": fines.count(),
                    "outstanding_total": float(
                        fines.exclude(status="PAID").exclude(status="WAIVED").aggregate(
                            total=Sum("amount")
                        )["total"]
                        or 0
                    ),
                    "recent_fines": [
                        {
                            "id": str(fine.id),
                            "category": fine.category,
                            "amount": float(fine.amount),
                            "status": fine.status,
                            "due_date": fine.due_date.isoformat(),
                        }
                        for fine in fines.order_by("-created_at")[:5]
                    ],
                }
            )(Fine.objects.filter(chama=chama, member=user))
        )
    elif tool_name == "download_my_statement":
        payload = _tool_result_from_registry("generate_statement", context, period_months=12)
    elif tool_name == "get_chama_wallet_totals":
        payload = _tool_result_from_registry("get_chama_wallet_summary", context)
    elif tool_name == "get_unpaid_members":
        payload = _tool_result_from_registry("get_unpaid_members", context)
    elif tool_name == "get_loan_queue_summary":
        payload = _tool_result_from_registry("get_loan_book", context)
    elif tool_name in {"get_reconciliation_mismatches", "reconciliation_readonly"}:
        payload = _cache_inline_payload(
            context,
            "reconciliation",
            lambda: (
                lambda failed_or_pending: {
                    "available": True,
                    "mismatch_count": failed_or_pending.count(),
                    "items": [
                        {
                            "id": str(item.id),
                            "reference": item.metadata.get("reference") or item.idempotency_key,
                            "status": item.status,
                            "amount": float(item.amount),
                            "phone": mask_phone(item.phone),
                        }
                        for item in failed_or_pending
                    ],
                }
            )(
                PaymentIntent.objects.filter(
                    chama=chama,
                    status__in=[PaymentIntentStatus.FAILED, PaymentIntentStatus.PENDING],
                ).order_by("-created_at")[:10]
            ),
        )
    elif tool_name == "generate_group_statement":
        payload = _tool_result_from_registry("generate_statement", context, period_months=12, member_id=str(user.id))
        payload["note"] = "Group statement generation should be completed in the Reports module."
    elif tool_name == "finance_reports_summary":
        wallet = _tool_result_from_registry("get_chama_wallet_summary", context)
        loans = _tool_result_from_registry("get_loan_book", context)
        payload = {
            "available": True,
            "wallet": wallet,
            "loans": loans,
        }
    elif tool_name == "member_directory_summary":
        payload = _cache_inline_payload(
            context,
            "member_directory",
            lambda: (
                lambda members: {
                    "available": True,
                    "member_count": sum(item["count"] for item in members),
                    "by_role": {item["role"]: item["count"] for item in members},
                }
            )(
                list(
                    Membership.objects.filter(
                        chama=chama,
                        is_active=True,
                        is_approved=True,
                    )
                    .values("role")
                    .annotate(count=Count("id"))
                )
            ),
        )
    elif tool_name == "join_requests_summary":
        payload = _cache_inline_payload(
            context,
            "join_requests",
            lambda: (
                lambda requests_qs: {
                    "available": True,
                    "pending_count": len(requests_qs),
                    "requests": [
                        {
                            "id": str(req.id),
                            "user": req.user.full_name,
                            "requested_via": req.requested_via,
                            "created_at": req.created_at.isoformat(),
                        }
                        for req in requests_qs
                    ],
                }
            )(
                list(
                    MembershipRequest.objects.filter(
                        chama=chama,
                        status=MembershipRequestStatus.PENDING,
                    ).select_related("user")[:10]
                )
            ),
        )
    elif tool_name == "meetings_schedule":
        payload = _tool_result_from_registry("get_meeting_schedule", context)
    elif tool_name == "announcements_templates":
        payload = {
            "available": True,
            "templates": [
                "Contribution reminder: Please clear your due contribution before the deadline.",
                "Meeting notice: Our next chama meeting is scheduled. Kindly confirm attendance.",
            ],
        }
    elif tool_name == "contribution_reminder_targets":
        payload = _tool_result_from_registry("get_unpaid_members", context)
    elif tool_name == "audit_logs_readonly":
        payload = _tool_result_from_registry("get_audit_logs", context, days=30)
    elif tool_name == "anomalies_summary":
        payload = _cache_inline_payload(
            context,
            "anomalies",
            lambda: (
                lambda failed_payments, loan_book: {
                    "available": True,
                    "overdue_loans": int(
                        (loan_book.get("by_status") or {}).get("defaulted", 0)
                    ),
                    "failed_payments": failed_payments,
                    "anomaly_count": int(
                        (loan_book.get("by_status") or {}).get("defaulted", 0)
                    )
                    + failed_payments,
                }
            )(
                PaymentIntent.objects.filter(
                    chama=chama,
                    status=PaymentIntentStatus.FAILED,
                ).count(),
                _tool_result_from_registry("get_loan_book", context),
            ),
        )
    elif tool_name == "loan_book_aging":
        payload = _tool_result_from_registry("get_loan_book", context)
    elif tool_name == "export_reports_readonly":
        payload = {
            "available": True,
            "summary": "Report exports are available from the Reports module.",
            "actions": [{"label": "Open Reports", "href": "/reports"}],
        }
    elif tool_name == "billing_status":
        payload = _cache_inline_payload(
            context,
            "billing_status",
            lambda: {
                "available": True,
                "access": get_access_status(chama),
                "plan": get_entitlements(chama),
            },
        )
    elif tool_name == "get_plan_limits":
        payload = _tool_result_from_registry("get_pricing_limits", context)
    elif tool_name == "roles_permissions_summary":
        payload = {
            "available": True,
            "role": context["role"],
            "allowed_tools": context["allowed_tools"],
        }
    elif tool_name == "system_activity_summary":
        payload = _tool_result_from_registry("get_recent_activity_feed", context, days=7)
    else:
        payload = _render_general_help(context)

    _store_tool_audit(
        context=context,
        tool_name=tool_name,
        args={},
        result_summary=json.dumps(payload, default=str)[:500],
        allowed=True,
    )
    return payload


EMBEDDED_AI_KEYWORD_ROUTES = [
    (("chama wallet", "group wallet", "wallet totals"), "get_chama_wallet_totals"),
    (("wallet", "balance", "cash"), "get_my_wallet_summary"),
    (("contribution", "contributions", "paid this month"), "get_my_contribution_status"),
    (("loan", "repayment", "borrowed"), "get_my_loan_status"),
    (("fine", "penalty"), "get_my_fines"),
    (("statement", "download statement"), "download_my_statement"),
    (("unpaid", "late members", "reminder targets"), "get_unpaid_members"),
    (("loan queue", "pending loans", "loan book"), "get_loan_queue_summary"),
    (("reconciliation", "mismatch", "mpesa mismatch"), "get_reconciliation_mismatches"),
    (("join request", "pending join"), "join_requests_summary"),
    (("meeting", "schedule", "calendar"), "meetings_schedule"),
    (("announcement", "broadcast template"), "announcements_templates"),
    (("audit", "audit logs"), "audit_logs_readonly"),
    (("anomaly", "suspicious", "risk signal"), "anomalies_summary"),
    (("billing", "subscription", "plan"), "billing_status"),
    (("limit", "quota", "plan limits"), "get_plan_limits"),
    (("role", "permission"), "roles_permissions_summary"),
    (("activity", "recent activity"), "system_activity_summary"),
]


def _route_embedded_tools(message, context):
    lowered = str(message or "").lower()
    if not lowered.strip():
        return ["general_help"]

    matched_tools = []
    for keywords, tool_name in EMBEDDED_AI_KEYWORD_ROUTES:
        if any(keyword in lowered for keyword in keywords):
            if tool_name not in matched_tools:
                matched_tools.append(tool_name)
            if len(matched_tools) >= AI_MAX_MULTI_TOOL_MATCHES:
                break

    return matched_tools or ["general_help"]


def _route_embedded_tool(message, context):
    return _route_embedded_tools(message, context)[0]


def _execute_embedded_tool_with_data(tool_name, context):
    payload = _execute_embedded_tool(tool_name, context)
    return {
        "tool_name": tool_name,
        "payload": payload,
        "data_used": _build_data_used_ref(tool_name, payload),
    }


def _execute_embedded_tools(tool_names, context):
    ordered_tools = []
    for tool_name in tool_names:
        if tool_name not in ordered_tools:
            ordered_tools.append(tool_name)

    if len(ordered_tools) <= 1:
        return [_execute_embedded_tool_with_data(ordered_tools[0], context)]

    results = {}

    def run_tool(tool_name):
        close_old_connections()
        try:
            return _execute_embedded_tool_with_data(tool_name, context)
        except Exception as exc:
            logger.exception("Embedded AI tool execution failed for %s", tool_name)
            error_payload = {
                "available": False,
                "error": str(exc) or "Tool execution failed.",
            }
            return {
                "tool_name": tool_name,
                "payload": error_payload,
                "data_used": _build_data_used_ref(tool_name, error_payload),
            }
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=min(len(ordered_tools), AI_MAX_PARALLEL_TOOLS)) as executor:
        future_map = {
            executor.submit(run_tool, tool_name): tool_name
            for tool_name in ordered_tools
        }
        try:
            for future in as_completed(future_map, timeout=AI_TOOL_TIMEOUT_SECONDS):
                tool_name = future_map[future]
                results[tool_name] = future.result()
        except FuturesTimeoutError:
            logger.warning("AI multi-tool execution exceeded timeout; returning completed tools only.")

    return [results[tool_name] for tool_name in ordered_tools if tool_name in results]


def _dedupe_actions(action_groups):
    deduped = []
    seen = set()
    for group in action_groups:
        for action in group or []:
            key = (action.get("label"), action.get("href"))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(action)
    return deduped


def _build_combined_assistant_text(tool_results, context):
    rendered_sections = []
    action_groups = []

    for item in tool_results:
        section_text, section_actions = _build_embedded_assistant_text(
            item["tool_name"],
            item["payload"],
            context,
        )
        rendered_sections.append(section_text)
        action_groups.append(section_actions)

    answer_text = "\n\n".join(
        section.strip()
        for section in rendered_sections
        if str(section or "").strip()
    ).strip()
    actions = _dedupe_actions(action_groups)

    if not _validate_grounded_assistant_output(answer_text, tool_results):
        answer_text = (
            "I can only confirm verified data from your records right now.\n\n"
            "- I removed unsupported numeric claims from the reply.\n"
            "- Use the actions below to open the exact module and confirm the latest figures."
        )

    return answer_text, actions


def _build_embedded_assistant_text(tool_name, payload, context):
    actions = []

    if tool_name == "general_help":
        return payload["content"], payload.get("actions", [])

    if not payload or (payload.get("available") is False and payload.get("error")):
        return (
            "I can't confirm that from your records.\n\n"
            f"- Reason: {payload.get('error', 'No verified data was returned.')}",
            [],
        )

    if tool_name == "get_my_wallet_summary":
        content = "\n".join(
            [
                "Here is your verified wallet summary:",
                "",
                f"- Total contributions: KES {payload.get('total_contributions', 0):,.2f}",
                f"- Total withdrawals: KES {payload.get('total_withdrawals', 0):,.2f}",
                f"- Outstanding loans: KES {payload.get('outstanding_loans', 0):,.2f}",
                f"- Net balance: KES {payload.get('net_balance', 0):,.2f}",
            ]
        )
        actions = [{"label": "Open Wallet", "href": "/app/wallet"}]
        return content, actions

    if tool_name in {"get_my_contribution_status", "contribution_reminder_targets", "get_unpaid_members"}:
        if "unpaid_members" in payload:
            items = payload.get("unpaid_members", [])[:5]
            content = ["Here are the verified unpaid members for the active cycle:", ""]
            content.extend([f"- {item['name']}" for item in items] or ["- No unpaid members found."])
            actions = [{"label": "Open Contributions", "href": "/app/contributions"}]
            return "\n".join(content), actions
        top = payload.get("top_contributors", [])[:3]
        content = [
            "Here is the verified contribution summary:",
            "",
            f"- Total contributed: KES {payload.get('total_contributed', 0):,.2f}",
            f"- Contribution count: {payload.get('contribution_count', 0)}",
        ]
        content.extend([f"- Top contributor: {item['name']} (KES {item['amount']:,.2f})" for item in top])
        actions = [{"label": "View Contributions", "href": "/app/contributions"}]
        return "\n".join(content), actions

    if tool_name == "get_my_loan_status":
        loans = payload.get("active_loans", [])
        content = [
            "Here is your verified loan status:",
            "",
            f"- Active or pending loans: {payload.get('loan_count', 0)}",
            f"- Total outstanding: KES {payload.get('total_outstanding', 0):,.2f}",
        ]
        if loans:
            next_loan = loans[0]
            content.append(
                f"- Next repayment: {next_loan.get('next_repayment_date') or 'Not scheduled'}"
            )
        actions = [{"label": "Open Loans", "href": "/app/loans"}]
        return "\n".join(content), actions

    if tool_name == "get_my_fines":
        content = [
            "Here are your verified fines:",
            "",
            f"- Total records: {payload.get('fine_count', 0)}",
            f"- Outstanding total: KES {payload.get('outstanding_total', 0):,.2f}",
        ]
        for item in payload.get("recent_fines", [])[:3]:
            content.append(
                f"- {item['category']}: KES {item['amount']:,.2f} ({item['status']})"
            )
        actions = [{"label": "Open Penalties", "href": "/member/penalties"}]
        return "\n".join(content), actions

    if tool_name in {"download_my_statement", "generate_group_statement"}:
        content = payload.get(
            "note",
            "Your statement request has been prepared from verified records.",
        )
        actions = [{"label": "Open Statements", "href": "/reports"}]
        return content, actions

    if tool_name == "get_chama_wallet_totals":
        content = "\n".join(
            [
                "Here are the verified chama wallet totals:",
                "",
                f"- Total contributions: KES {payload.get('total_contributions', 0):,.2f}",
                f"- Total withdrawals: KES {payload.get('total_withdrawals', 0):,.2f}",
                f"- Outstanding loans: KES {payload.get('total_outstanding_loans', 0):,.2f}",
                f"- Cash at bank: KES {payload.get('cash_at_bank', 0):,.2f}",
            ]
        )
        actions = [{"label": "Open Treasury", "href": "/treasurer/dashboard"}]
        return content, actions

    if tool_name in {"get_loan_queue_summary", "loan_book_aging"}:
        content = [
            "Here is the verified loan book summary:",
            "",
            f"- Total disbursed: KES {payload.get('total_disbursed', 0):,.2f}",
            f"- Total outstanding: KES {payload.get('total_outstanding', 0):,.2f}",
        ]
        status_map = payload.get("by_status") or {}
        for loan_status, count in list(status_map.items())[:4]:
            content.append(f"- {loan_status}: {count}")
        actions = [{"label": "Open Loans", "href": "/treasurer/loans"}]
        return "\n".join(content), actions

    if tool_name in {"get_reconciliation_mismatches", "reconciliation_readonly"}:
        items = payload.get("items", [])[:5]
        content = [
            "Here is the verified reconciliation summary:",
            "",
            f"- Mismatch count: {payload.get('mismatch_count', 0)}",
        ]
        content.extend(
            [f"- {item['reference'] or 'No ref'}: {item['status']} (KES {item['amount']:,.2f})" for item in items]
            or ["- No pending or failed items found."]
        )
        actions = [{"label": "Open Payments", "href": "/app/payments"}]
        return "\n".join(content), actions

    if tool_name == "finance_reports_summary":
        wallet = payload.get("wallet", {})
        loans = payload.get("loans", {})
        content = [
            "Here is the finance summary built from verified records:",
            "",
            f"- Net chama funds: KES {wallet.get('net_chama_funds', 0):,.2f}",
            f"- Outstanding loan book: KES {loans.get('total_outstanding', 0):,.2f}",
        ]
        actions = [{"label": "Open Reports", "href": "/reports"}]
        return "\n".join(content), actions

    if tool_name == "member_directory_summary":
        content = [
            "Here is the current member directory summary:",
            "",
            f"- Active members: {payload.get('member_count', 0)}",
        ]
        for role_name, count in (payload.get("by_role") or {}).items():
            content.append(f"- {role_name}: {count}")
        actions = [{"label": "Open Members", "href": "/admin/members"}]
        return "\n".join(content), actions

    if tool_name == "join_requests_summary":
        content = [
            "Here are the pending join requests:",
            "",
            f"- Pending count: {payload.get('pending_count', 0)}",
        ]
        for item in payload.get("requests", [])[:5]:
            content.append(f"- {item['user']} via {item['requested_via']}")
        actions = [{"label": "Review Join Requests", "href": "/app/join-requests"}]
        return "\n".join(content), actions

    if tool_name == "meetings_schedule":
        upcoming = payload.get("upcoming_meetings", [])[:5]
        content = ["Here are the upcoming meetings:", ""]
        content.extend(
            [f"- {item['title']} on {item['date']}" for item in upcoming]
            or ["- No upcoming meetings found."]
        )
        actions = [{"label": "Open Meetings", "href": "/meetings"}]
        return "\n".join(content), actions

    if tool_name == "announcements_templates":
        content = ["Here are ready-to-edit announcement templates:", ""]
        content.extend([f"- {item}" for item in payload.get("templates", [])])
        actions = [{"label": "Open Broadcast Center", "href": "/app/notifications"}]
        return "\n".join(content), actions

    if tool_name == "audit_logs_readonly":
        content = [
            "Here is the audit log summary:",
            "",
            f"- Entries returned: {payload.get('log_count', 0)}",
        ]
        actions = [{"label": "Open Audit Logs", "href": "/auditor/dashboard"}]
        return "\n".join(content), actions

    if tool_name == "anomalies_summary":
        content = "\n".join(
            [
                "Here is the verified anomalies summary:",
                "",
                f"- Overdue loans: {payload.get('overdue_loans', 0)}",
                f"- Failed payments: {payload.get('failed_payments', 0)}",
                f"- Total anomaly signals: {payload.get('anomaly_count', 0)}",
            ]
        )
        actions = [{"label": "Open Auditor Tools", "href": "/auditor/dashboard"}]
        return content, actions

    if tool_name in {"export_reports_readonly", "billing_status", "get_plan_limits", "roles_permissions_summary", "system_activity_summary"}:
        content = json.dumps(payload, indent=2, default=str)
        actions = payload.get("actions", [])
        return f"Here is the verified summary:\n\n```json\n{content}\n```", actions

    return (
        "I reviewed your verified records, but I do not have a custom formatter for that response yet.",
        [],
    )


def _extract_numeric_tokens(text):
    return set(re.findall(r"\b\d[\d,]*(?:\.\d+)?\b", str(text or "")))


def _refine_embedded_reply_with_llm(*, context, tool_name, tool_payload, fallback_text):
    api_key = getattr(settings, "OPENAI_API_KEY", "")
    if not api_key:
        return fallback_text

    client = AIClientPool.get_client()
    if not client:
        return fallback_text

    try:
        response = client.chat.completions.create(
            model=getattr(settings, "AI_CHAT_MODEL", "gpt-5-mini"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are Digital Chama AI. Rewrite the grounded assistant response into a "
                        "clean, concise answer. Use only the facts already present in the provided "
                        "tool result and base answer. Do not add any balances, counts, references, "
                        "or dates that are not already present. Never reveal restricted data."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"ROLE: {context.get('role')}\n"
                        f"TOOL: {tool_name}\n"
                        f"TOOL_RESULT_JSON:\n{json.dumps(tool_payload, default=str)}\n\n"
                        f"BASE_ANSWER:\n{fallback_text}\n\n"
                        "Return a polished markdown answer with the same facts only."
                    ),
                },
            ],
            max_tokens=260,
            temperature=0.1,
        )
        refined_text = (
            response.choices[0].message.content.strip()
            if response and response.choices
            else ""
        )
    except Exception:
        logger.exception("Embedded AI LLM refinement failed; using deterministic fallback.")
        return fallback_text

    if not refined_text:
        return fallback_text

    fallback_numbers = _extract_numeric_tokens(fallback_text)
    refined_numbers = _extract_numeric_tokens(refined_text)
    if refined_numbers - fallback_numbers:
        return fallback_text

    return refined_text


def _bootstrap_private_assistant_reply(request, message, conversation_id=None):
    from .models import AIConversation, AIConversationMode, AIMessage, AIMessageRole

    context = _resolve_embedded_private_context(request)
    tool_names = _route_embedded_tools(message, context)
    blocked_tools = [
        tool_name
        for tool_name in tool_names
        if tool_name not in context["allowed_tools"]
    ]
    if blocked_tools:
        _store_tool_audit(
            context=context,
            tool_name=blocked_tools[0],
            args={"message": str(message).strip()},
            result_summary="Blocked by role policy",
            allowed=False,
        )
        raise EmbeddedAIAccessError(
            "That request is outside your role permissions.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    if conversation_id:
        conversation = AIConversation.objects.filter(
            id=conversation_id,
            user=request.user,
        ).first()
    else:
        conversation = None

    if not conversation:
        conversation = AIConversation.objects.create(
            user=request.user,
            chama=context["chama"],
            mode=AIConversationMode.PRIVATE,
            title=" ".join(str(message).split())[:160],
        )

    user_message = AIMessage.objects.create(
        conversation=conversation,
        role=AIMessageRole.USER,
        content=str(message).strip(),
    )
    assistant_message = AIMessage.objects.create(
        conversation=conversation,
        role=AIMessageRole.ASSISTANT,
        content="",
        tool_name="",
        tool_payload={"actions": []},
    )

    return {
        "context": context,
        "tool_names": tool_names,
        "conversation": conversation,
        "user_message": user_message,
        "assistant_message": assistant_message,
    }


def _build_private_assistant_answer(message, context, tool_names=None):
    tool_names = tool_names or _route_embedded_tools(message, context)
    tool_results = _execute_embedded_tools(tool_names, context)
    if not tool_results:
        fallback_payload = _render_general_help(context)
        tool_results = [
            {
                "tool_name": "general_help",
                "payload": fallback_payload,
                "data_used": _build_data_used_ref("general_help", fallback_payload),
            }
        ]
    primary_tool_name = tool_results[0]["tool_name"] if tool_results else "general_help"
    primary_payload = tool_results[0]["payload"] if tool_results else _render_general_help(context)
    answer_text, actions = _build_combined_assistant_text(tool_results, context)

    high_risk_topics = {
        "get_my_wallet_summary",
        "get_chama_wallet_totals",
        "get_my_contribution_status",
        "get_unpaid_members",
        "get_my_loan_status",
        "get_loan_queue_summary",
        "get_reconciliation_mismatches",
        "billing_status",
    }
    if len(tool_results) == 1 and primary_tool_name not in high_risk_topics:
        answer_text = _refine_embedded_reply_with_llm(
            context=context,
            tool_name=primary_tool_name,
            tool_payload=primary_payload,
            fallback_text=answer_text,
        )

    if not _validate_grounded_assistant_output(answer_text, tool_results):
        answer_text = (
            "I can only confirm verified data from your records right now.\n\n"
            "- I could not safely verify every numeric claim.\n"
            "- Please use the linked module below to confirm the latest values."
        )

    return {
        "answer_text": answer_text,
        "actions": actions,
        "tool_name": primary_tool_name,
        "tool_payload": primary_payload,
        "tool_results": tool_results,
        "data_used": [item["data_used"] for item in tool_results],
    }


def _prepare_private_assistant_reply(request, message, conversation_id=None):
    prepared = _bootstrap_private_assistant_reply(
        request,
        message,
        conversation_id=conversation_id,
    )
    answer_payload = _build_private_assistant_answer(
        message,
        prepared["context"],
        tool_names=prepared.get("tool_names"),
    )
    prepared.update(answer_payload)
    prepared["assistant_message"].tool_name = answer_payload["tool_name"]
    prepared["assistant_message"].tool_payload = {
        "actions": answer_payload["actions"],
        "data_used": answer_payload["data_used"],
    }
    prepared["assistant_message"].save(update_fields=["tool_name", "tool_payload", "updated_at"])
    return prepared


def _build_stage_a_message(context):
    if context.get("mode") == "private":
        return "I'm checking your records..."
    return "I'm preparing an answer..."


def _prepare_public_assistant_reply(message):
    answer_text = _generate_public_response(message)
    actions = [
        {"label": "View Pricing", "href": "/pricing"},
        {"label": "Create Account", "href": "/register"},
    ]
    return {
        "context": _build_public_mode_payload(),
        "answer_text": answer_text,
        "actions": actions,
    }


def _build_me_context_payload(request):
    if not request.user or not request.user.is_authenticated:
        return _build_public_mode_payload(), status.HTTP_200_OK

    try:
        return _resolve_embedded_private_context(request), status.HTTP_200_OK
    except EmbeddedAIAccessError as exc:
        if exc.status_code == status.HTTP_409_CONFLICT:
            public_payload = _build_public_mode_payload()
            public_payload.update(
                {
                    "authenticated": True,
                    "features": {"ai_enabled": False, "ai_basic": False, "ai_advanced": False},
                    "detail": exc.detail,
                }
            )
            return public_payload, status.HTTP_200_OK
        return exc.payload, exc.status_code


@api_view(["GET"])
@permission_classes([AllowAny])
def ai_me_context(request):
    payload, response_status = _build_me_context_payload(request)
    return Response(payload, status=response_status)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ai_context(request):
    payload, response_status = _build_me_context_payload(request)
    if response_status != status.HTTP_200_OK:
        return Response(payload, status=response_status)

    if payload.get("mode") == "public":
        return Response(
            {
                "user": None,
                "chama": None,
                "role": None,
                "has_active_chama": False,
                "features": payload.get("features", {}),
                "detail": payload.get("detail"),
            }
        )

    legacy_payload = {
        "user": {
            "id": str(request.user.id),
            "full_name": request.user.full_name,
            "phone": request.user.phone,
        },
        "chama": payload.get("active_chama"),
        "role": payload.get("role"),
        "has_active_chama": True,
        "features": payload.get("features", {}),
        "allowed_suggestion_chips": payload.get("allowed_suggestion_chips", []),
        "allowed_tools": payload.get("allowed_tools", []),
        "plan": payload.get("plan"),
        "assistant_label": payload.get("assistant_label"),
        "role_title": payload.get("role_title"),
    }
    return Response(legacy_payload)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ai_suggestions(request):
    payload, response_status = _build_me_context_payload(request)
    if response_status != status.HTTP_200_OK:
        return Response(payload, status=response_status)

    if payload.get("mode") == "public":
        suggestions = payload.get("allowed_suggestion_chips", PUBLIC_SUGGESTIONS)
    else:
        chama = _get_chama_from_request(request)
        suggestions = get_role_suggestions(request.user, chama) if chama else payload.get("allowed_suggestion_chips", [])

    normalized_suggestions = []
    for index, item in enumerate(suggestions):
        if isinstance(item, dict):
            normalized_suggestions.append(item)
        else:
            normalized_suggestions.append(
                {"id": str(index + 1), "text": str(item), "category": "assistant"}
            )

    return Response(
        {
            "suggestions": normalized_suggestions,
            "chama_id": (
                payload.get("active_chama", {}).get("id")
                if isinstance(payload.get("active_chama"), dict)
                else None
            ),
            "chama_name": (
                payload.get("active_chama", {}).get("name")
                if isinstance(payload.get("active_chama"), dict)
                else ""
            ),
        }
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def public_ai_chat_stream(request):
    message = str(request.data.get("message") or "").strip()
    if not message:
        return Response({"detail": "message is required"}, status=status.HTTP_400_BAD_REQUEST)

    conversation_id = request.data.get("conversation_id") or f"public-{timezone.now().timestamp()}"
    message_id = f"public-msg-{timezone.now().timestamp()}"

    def generate_response():
        stage_text = _build_stage_a_message({"mode": "public"})
        yield f"data: {json.dumps({'conversation_id': str(conversation_id), 'message_id': str(message_id), 'done': False, 'meta': True, 'stage': 'checking'})}\n\n"
        yield f"data: {json.dumps({'conversation_id': str(conversation_id), 'message_id': str(message_id), 'content': stage_text + ' ', 'done': False, 'stage': 'checking'})}\n\n"
        prepared = _prepare_public_assistant_reply(message)
        yield f"data: {json.dumps({'conversation_id': str(conversation_id), 'message_id': str(message_id), 'replace': True, 'content': '', 'actions': prepared['actions'], 'done': False, 'stage': 'answering'})}\n\n"
        for chunk in _split_stream_chunks(prepared["answer_text"]):
            yield f"data: {json.dumps({'conversation_id': str(conversation_id), 'message_id': str(message_id), 'content': chunk, 'done': False})}\n\n"
            time.sleep(0.02)
        yield f"data: {json.dumps({'conversation_id': str(conversation_id), 'message_id': str(message_id), 'content': '', 'actions': prepared['actions'], 'done': True})}\n\n"

    return StreamingHttpResponse(
        generate_response(),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ai_chat_stop(request):
    conversation_id = str(request.data.get("conversation_id") or "").strip()
    if not conversation_id:
        return Response({"status": "stopping"})

    conversation = AIConversation.objects.filter(id=conversation_id, user=request.user).first()
    if not conversation:
        return Response({"detail": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)

    cache.set(_safe_ai_cache_key("stop", conversation.id), True, timeout=300)
    return Response({"status": "stopping", "conversation_id": str(conversation.id)})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ai_chat_stream(request):
    message = str(request.data.get("message") or "").strip()
    if not message:
        return Response({"detail": "message is required"}, status=status.HTTP_400_BAD_REQUEST)
    if len(message) > 4000:
        return Response({"detail": "message is too long"}, status=status.HTTP_400_BAD_REQUEST)

    rate_key = _safe_ai_cache_key("rate", request.user.id, timezone.now().strftime("%Y%m%d%H%M"))
    current_rate = cache.get(rate_key, 0)
    if current_rate >= 10:
        return Response(
            {"detail": "Rate limit exceeded. Please wait and try again."},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    cache.set(rate_key, current_rate + 1, timeout=60)

    started_at = time.time()

    try:
        prepared = _bootstrap_private_assistant_reply(
            request,
            message,
            conversation_id=request.data.get("conversation_id"),
        )
    except EmbeddedAIAccessError as exc:
        AIUsageLog.objects.create(
            user=request.user,
            chama=_get_chama_from_request(request),
            tokens_in=_estimate_token_count(message),
            tokens_out=0,
            latency_ms=int((time.time() - started_at) * 1000),
            endpoint="ai_chat_stream",
            status_code=exc.status_code,
            error_message=exc.detail,
        )
        return Response(exc.payload, status=exc.status_code)

    conversation = prepared["conversation"]
    assistant_message = prepared["assistant_message"]
    stop_key = _safe_ai_cache_key("stop", conversation.id)
    cache.delete(stop_key)

    def generate_response():
        emitted = []
        stage_text = _build_stage_a_message(prepared["context"])
        stage_partial = False
        final_actions = []
        final_data_used = []
        final_tool_name = "general_help"

        yield f"data: {json.dumps({'conversation_id': str(conversation.id), 'message_id': str(assistant_message.id), 'done': False, 'meta': True, 'stage': 'checking'})}\n\n"
        yield f"data: {json.dumps({'conversation_id': str(conversation.id), 'message_id': str(assistant_message.id), 'content': stage_text + ' ', 'done': False, 'stage': 'checking'})}\n\n"

        if cache.get(stop_key):
            final_content = "Generation stopped before I finished checking your records."
            partial = True
            stage_partial = True
        else:
            try:
                answer_payload = _build_private_assistant_answer(
                    message,
                    prepared["context"],
                    tool_names=prepared.get("tool_names"),
                )
            except Exception:
                logger.exception("Embedded AI answer generation failed during stream.")
                fallback_payload = _render_general_help(prepared["context"])
                answer_payload = {
                    "answer_text": (
                        "I hit a timeout while checking your records.\n\n"
                        "- Please try again in a moment.\n"
                        "- Or open the relevant module below for the latest verified data."
                    ),
                    "actions": fallback_payload.get("actions", []),
                    "tool_name": "general_help",
                    "tool_payload": fallback_payload,
                    "tool_results": [
                        {
                            "tool_name": "general_help",
                            "payload": fallback_payload,
                            "data_used": _build_data_used_ref("general_help", fallback_payload),
                        }
                    ],
                    "data_used": [_build_data_used_ref("general_help", fallback_payload)],
                }
            answer_text = answer_payload["answer_text"]
            final_actions = answer_payload["actions"]
            final_data_used = answer_payload["data_used"]
            final_tool_name = answer_payload["tool_name"]

            assistant_message.tool_name = final_tool_name
            assistant_message.tool_payload = {
                "actions": final_actions,
                "data_used": final_data_used,
                "partial": False,
            }
            assistant_message.save(update_fields=["tool_name", "tool_payload", "updated_at"])

            yield f"data: {json.dumps({'conversation_id': str(conversation.id), 'message_id': str(assistant_message.id), 'replace': True, 'content': '', 'actions': final_actions, 'data_used': final_data_used, 'done': False, 'stage': 'answering'})}\n\n"

            for chunk in _split_stream_chunks(answer_text):
                if cache.get(stop_key):
                    break
                emitted.append(chunk)
                yield f"data: {json.dumps({'conversation_id': str(conversation.id), 'message_id': str(assistant_message.id), 'content': chunk, 'done': False, 'stage': 'answering'})}\n\n"
                time.sleep(0.02)

            final_content = "".join(emitted).strip() or answer_text[:240]
            partial = bool(cache.get(stop_key)) and final_content != answer_text.strip()

        assistant_message.content = final_content
        assistant_message.tool_payload = {
            "actions": final_actions,
            "partial": partial,
            "tool_name": final_tool_name,
            "data_used": final_data_used,
        }
        assistant_message.save(update_fields=["content", "tool_payload", "updated_at"])
        conversation.updated_at = timezone.now()
        conversation.save(update_fields=["updated_at"])
        cache.delete(stop_key)

        AIUsageLog.objects.create(
            user=request.user,
            chama=prepared["context"]["chama"],
            conversation=conversation,
            tokens_in=_estimate_token_count(message),
            tokens_out=_estimate_token_count(final_content),
            latency_ms=int((time.time() - started_at) * 1000),
            endpoint="ai_chat_stream",
            model_name=getattr(settings, "AI_CHAT_MODEL", "deterministic-tool-router"),
            status_code=200,
        )

        yield f"data: {json.dumps({'conversation_id': str(conversation.id), 'message_id': str(assistant_message.id), 'content': '', 'actions': final_actions, 'data_used': final_data_used, 'partial': partial or stage_partial, 'done': True})}\n\n"

    return StreamingHttpResponse(
        generate_response(),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ai_conversations(request):
    try:
        context = _resolve_embedded_private_context(request)
    except EmbeddedAIAccessError as exc:
        return Response(exc.payload, status=exc.status_code)

    conversations = AIConversation.objects.filter(
        user=request.user,
        chama=context["chama"],
    ).order_by("-updated_at")[:20]

    serializer = AIConversationListSerializer(conversations, many=True)
    return Response({"conversations": serializer.data})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ai_messages(request, conversation_id):
    conversation = AIConversation.objects.filter(
        id=conversation_id,
        user=request.user,
    ).first()
    if not conversation:
        return Response({"detail": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)

    if conversation.chama:
        feature_block = _require_ai_feature_for_chama(
            conversation.chama,
            "ai_basic",
            or_features=["ai_advanced"],
        )
        if feature_block:
            return feature_block

    messages = AIMessage.objects.filter(conversation=conversation).order_by("created_at")[-10:]  # Last 10 messages only
    return Response(
        {
            "conversation_id": str(conversation.id),
            "title": conversation.title or conversation.get_mode_display(),
            "messages": AIMessageSerializer(messages, many=True).data,
        }
    )
