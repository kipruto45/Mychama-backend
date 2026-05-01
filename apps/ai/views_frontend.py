from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import get_object_or_404, render

from apps.ai.models import AIConversation, AIMessage, KnowledgeDocument
from apps.ai.services import (
    AIGatewayService,
    AIServiceError,
    AIWorkflowService,
    KnowledgeBaseService,
)
from apps.ai.utils import AISystemConfig
from apps.chama.models import Membership, MembershipRole, MemberStatus
from apps.chama.services import get_effective_role
from apps.issues.models import Issue, IssueStatus
from apps.meetings.models import Meeting


def _resolve_membership(request):
    chama_id = (
        request.POST.get("chama_id")
        or request.GET.get("chama_id")
        or request.headers.get("X-CHAMA-ID")
    )
    memberships = Membership.objects.filter(
        user=request.user,
        is_active=True,
        is_approved=True,
        status=MemberStatus.ACTIVE,
        exited_at__isnull=True,
    ).select_related("chama")
    if chama_id:
        return memberships.filter(chama_id=chama_id).first()
    return memberships.first()


def _has_role(membership, allowed_roles: set[str]) -> bool:
    if not membership:
        return False
    role = get_effective_role(membership.user, membership.chama_id, membership)
    return bool(role in allowed_roles)


def _pretty_json(value) -> str:
    return json.dumps(value, indent=2, ensure_ascii=True, default=str)


def _parse_uuid(raw_value: str | None):
    value = str(raw_value or "").strip()
    if not value:
        return None
    try:
        return UUID(value)
    except (TypeError, ValueError):
        return None


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")


@login_required
def assistant_member_view(request):
    membership = _resolve_membership(request)
    if not membership:
        return render(request, "errors/403.html", status=403)

    selected_conversation_id = (
        request.POST.get("conversation_id") or request.GET.get("conversation_id")
    )
    chat_result = None

    if request.method == "POST":
        message_text = (request.POST.get("message") or "").strip()
        if not message_text:
            messages.error(request, "Enter a message before sending.")
        else:
            try:
                chat_result = AIGatewayService.chat(
                    user=request.user,
                    chama_id=str(membership.chama_id),
                    mode="member_assistant",
                    message=message_text,
                    conversation_id=selected_conversation_id or None,
                )
                selected_conversation_id = str(chat_result["conversation_id"])
                messages.success(request, "AI response generated.")
            except AIServiceError as exc:
                messages.error(request, str(exc))
            except Exception:  # noqa: BLE001
                messages.error(request, "Unable to process your AI request right now.")

    conversations = (
        AIConversation.objects.filter(user=request.user, chama_id=membership.chama_id)
        if membership
        else AIConversation.objects.none()
    )
    selected_conversation = None
    if selected_conversation_id:
        selected_conversation = conversations.filter(id=selected_conversation_id).first()

    conversation_messages = (
        AIMessage.objects.filter(conversation=selected_conversation)
        .order_by("created_at")
        .only("role", "content", "tool_name", "created_at")
        if selected_conversation
        else AIMessage.objects.none()
    )

    return render(
        request,
        "ai/assistant_member.html",
        {
            "membership": membership,
            "conversations": conversations.order_by("-created_at")[:20],
            "selected_conversation": selected_conversation,
            "conversation_messages": conversation_messages[:120],
            "chat_result": chat_result,
            "chat_result_json": _pretty_json(chat_result) if chat_result else "",
            "openai_enabled": AISystemConfig.is_openai_enabled(),
        },
    )


@login_required
def assistant_admin_view(request):
    membership = _resolve_membership(request)
    if not _has_role(
        membership,
        {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
            MembershipRole.SECRETARY,
        },
    ):
        return render(request, "errors/403.html", status=403)

    selected_conversation_id = (
        request.POST.get("conversation_id") or request.GET.get("conversation_id")
    )
    chat_result = None

    if request.method == "POST":
        message_text = (request.POST.get("message") or "").strip()
        if not message_text:
            messages.error(request, "Enter a message before running assistant analysis.")
        else:
            try:
                chat_result = AIGatewayService.chat(
                    user=request.user,
                    chama_id=str(membership.chama_id),
                    mode="admin_assistant",
                    message=message_text,
                    conversation_id=selected_conversation_id or None,
                )
                selected_conversation_id = str(chat_result["conversation_id"])
                messages.success(request, "AI admin insight generated.")
            except AIServiceError as exc:
                messages.error(request, str(exc))
            except Exception:  # noqa: BLE001
                messages.error(request, "Unable to process this admin AI request right now.")

    conversations = AIConversation.objects.filter(chama_id=membership.chama_id)
    selected_conversation = None
    if selected_conversation_id:
        selected_conversation = conversations.filter(id=selected_conversation_id).first()

    conversation_messages = (
        AIMessage.objects.filter(conversation=selected_conversation)
        .order_by("created_at")
        .only("role", "content", "tool_name", "created_at")
        if selected_conversation
        else AIMessage.objects.none()
    )
    return render(
        request,
        "ai/assistant_admin.html",
        {
            "membership": membership,
            "conversations": conversations.order_by("-created_at")[:50],
            "selected_conversation": selected_conversation,
            "conversation_messages": conversation_messages[:150],
            "chat_result": chat_result,
            "chat_result_json": _pretty_json(chat_result) if chat_result else "",
            "openai_enabled": AISystemConfig.is_openai_enabled(),
        },
    )


@login_required
def insights_admin_view(request):
    membership = _resolve_membership(request)
    if not _has_role(
        membership,
        {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
            MembershipRole.SECRETARY,
            MembershipRole.AUDITOR,
        },
    ):
        return render(request, "errors/403.html", status=403)

    insights = AIWorkflowService.weekly_insights_for_chama(
        chama_id=membership.chama_id,
        actor=request.user,
    )
    portfolio = insights.get("portfolio") if isinstance(insights, dict) else {}
    overdue = insights.get("overdue") if isinstance(insights, dict) else {}
    suspicious = insights.get("suspicious") if isinstance(insights, dict) else {}

    overdue_items = (
        overdue.get("items", [])
        if isinstance(overdue, dict)
        else []
    )
    overdue_exposure = sum((_decimal(item.get("expected_amount")) for item in overdue_items), Decimal("0.00"))

    suspicious_flags = 0
    if isinstance(suspicious, dict):
        suspicious_flags = len(suspicious.get("duplicate_idempotency_keys", [])) + len(
            suspicious.get("odd_withdrawals", [])
        )

    repayment_rate = _decimal(
        (portfolio or {}).get("repayment_rate_percent", "0.00")
        if isinstance(portfolio, dict)
        else "0.00"
    )
    if repayment_rate >= Decimal("90.00"):
        risk_level = "LOW"
    elif repayment_rate >= Decimal("75.00"):
        risk_level = "MEDIUM"
    else:
        risk_level = "HIGH"

    return render(
        request,
        "ai/insights_admin.html",
        {
            "membership": membership,
            "insights": insights,
            "portfolio_risk_level": risk_level,
            "overdue_exposure": overdue_exposure,
            "suspicious_flags": suspicious_flags,
            "insights_portfolio_json": _pretty_json(portfolio),
            "insights_overdue_json": _pretty_json(overdue),
            "insights_suspicious_json": _pretty_json(suspicious),
        },
    )


@login_required
def issue_triage_preview_view(request):
    membership = _resolve_membership(request)
    if not _has_role(
        membership,
        {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
            MembershipRole.SECRETARY,
        },
    ):
        return render(request, "errors/403.html", status=403)

    issues = Issue.objects.filter(chama_id=membership.chama_id).exclude(
        status=IssueStatus.CLOSED
    ).order_by("-created_at")
    triage_result = None
    selected_issue_id = request.POST.get("issue_id") or request.GET.get("issue_id")

    if request.method == "POST":
        parsed_issue_id = _parse_uuid(selected_issue_id)
        if not parsed_issue_id:
            messages.error(request, "Provide a valid issue ID.")
        elif not issues.filter(id=parsed_issue_id).exists():
            messages.error(request, "Selected issue is outside your active chama scope.")
        else:
            try:
                triage_result = AIWorkflowService.triage_issue(
                    issue_id=parsed_issue_id,
                    actor=request.user,
                )
                messages.success(request, "AI triage completed.")
            except AIServiceError as exc:
                messages.error(request, str(exc))
            except Exception:  # noqa: BLE001
                messages.error(request, "Unable to complete triage right now.")

    return render(
        request,
        "ai/issue_triage_preview.html",
        {
            "membership": membership,
            "issues": issues[:100],
            "selected_issue_id": selected_issue_id or "",
            "triage_result": triage_result,
            "triage_result_json": _pretty_json(triage_result) if triage_result else "",
        },
    )


@login_required
def meeting_summary_preview_view(request):
    membership = _resolve_membership(request)
    if not membership:
        return render(request, "errors/403.html", status=403)

    meetings = Meeting.objects.filter(chama_id=membership.chama_id).order_by("-date")
    summary_result = None
    selected_meeting_id = request.POST.get("meeting_id") or request.GET.get("meeting_id")

    if request.method == "POST":
        parsed_meeting_id = _parse_uuid(selected_meeting_id)
        if not parsed_meeting_id:
            messages.error(request, "Provide a valid meeting ID.")
        elif not meetings.filter(id=parsed_meeting_id).exists():
            messages.error(
                request,
                "Selected meeting is outside your active chama scope.",
            )
        else:
            try:
                summary_result = AIWorkflowService.summarize_meeting(
                    meeting_id=parsed_meeting_id,
                    actor=request.user,
                )
                messages.success(request, "Meeting summary generated.")
            except AIServiceError as exc:
                messages.error(request, str(exc))
            except Exception:  # noqa: BLE001
                messages.error(request, "Unable to summarize this meeting right now.")

    return render(
        request,
        "ai/meeting_summary_preview.html",
        {
            "membership": membership,
            "meetings": meetings[:100],
            "selected_meeting_id": selected_meeting_id or "",
            "summary_result": summary_result,
            "summary_result_json": _pretty_json(summary_result) if summary_result else "",
        },
    )


@login_required
def kb_documents_view(request):
    membership = _resolve_membership(request)
    if not _has_role(
        membership,
        {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
            MembershipRole.SECRETARY,
        },
    ):
        return render(request, "errors/403.html", status=403)

    if request.method == "POST":
        reindex_document_id = _parse_uuid(request.POST.get("reindex_document_id"))
        if reindex_document_id:
            document = get_object_or_404(
                KnowledgeDocument, id=reindex_document_id, chama_id=membership.chama_id
            )
            chunks = KnowledgeBaseService.reindex_document(
                document=document,
                actor=request.user,
            )
            messages.success(
                request,
                f"Reindexed '{document.title}' successfully ({chunks} chunks).",
            )
        else:
            title = (request.POST.get("title") or "").strip()
            source_type = (request.POST.get("source_type") or "").strip() or "other"
            text_content = (request.POST.get("text_content") or "").strip()
            file_obj = request.FILES.get("file")

            if not title:
                messages.error(request, "Document title is required.")
            elif not text_content and not file_obj:
                messages.error(request, "Provide text content or upload a file.")
            else:
                document = KnowledgeDocument.objects.create(
                    chama_id=membership.chama_id,
                    title=title,
                    source_type=source_type,
                    text_content=text_content,
                    file=file_obj,
                    created_by=request.user,
                    updated_by=request.user,
                )
                chunks = KnowledgeBaseService.reindex_document(
                    document=document,
                    actor=request.user,
                )
                messages.success(
                    request,
                    f"Uploaded '{document.title}' and indexed {chunks} chunks.",
                )

    documents = (
        KnowledgeDocument.objects.filter(chama_id=membership.chama_id)
        if membership
        else KnowledgeDocument.objects.none()
    )
    documents = documents.annotate(chunk_count=Count("chunks")).order_by("-created_at")
    return render(
        request,
        "ai/kb_documents.html",
        {"membership": membership, "documents": documents[:100]},
    )
