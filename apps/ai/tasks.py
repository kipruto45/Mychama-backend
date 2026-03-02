from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.ai.models import AIConversation, KnowledgeDocument
from apps.ai.services import (
    AIWorkflowService,
    ai_daily_anomaly_scan,
    run_nightly_kb_reindex,
)

logger = logging.getLogger(__name__)


@shared_task
def ai_weekly_insights_report_task(chama_id: str | None = None):
    from apps.automations.services import AutomationJobRunner
    from apps.chama.models import Chama

    def callback():
        queryset = Chama.objects.filter(status="active")
        if chama_id:
            queryset = queryset.filter(id=chama_id)

        generated = 0
        for chama in queryset:
            AIWorkflowService.weekly_insights_for_chama(chama_id=chama.id, actor=None)
            generated += 1
        return {"generated": generated}

    return AutomationJobRunner.run_job(
        name="ai_weekly_insights_report",
        schedule="30 7 * * 1",
        description="Generates weekly AI insights per active chama.",
        callback=callback,
    )


@shared_task
def ai_nightly_kb_reindex_task(chama_id: str | None = None):
    from apps.automations.services import AutomationJobRunner

    return AutomationJobRunner.run_job(
        name="ai_nightly_kb_reindex",
        schedule="0 1 * * *",
        description="Reindexes AI knowledge chunks nightly.",
        callback=lambda: run_nightly_kb_reindex(chama_id=chama_id),
    )


@shared_task
def ai_anomaly_scan_task(chama_id: str | None = None):
    from apps.automations.services import AutomationJobRunner

    return AutomationJobRunner.run_job(
        name="ai_anomaly_scan",
        schedule="0 2 * * *",
        description="Runs daily AI anomaly scan across payments/ledger indicators.",
        callback=lambda: ai_daily_anomaly_scan(chama_id=chama_id),
    )


@shared_task
def ai_membership_risk_scoring_task(chama_id: str | None = None):
    from apps.automations.services import AutomationJobRunner
    from apps.chama.models import Chama

    def callback():
        queryset = Chama.objects.filter(status="active")
        if chama_id:
            queryset = queryset.filter(id=chama_id)

        generated = 0
        for chama in queryset:
            AIWorkflowService.membership_risk_scoring_for_chama(
                chama_id=chama.id,
                actor=None,
            )
            generated += 1
        return {"generated": generated}

    return AutomationJobRunner.run_job(
        name="ai_membership_risk_scoring",
        schedule="0 3 * * *",
        description="Computes weighted membership risk scores per chama.",
        callback=callback,
    )


@shared_task
def ai_loan_default_prediction_task(chama_id: str | None = None):
    from apps.automations.services import AutomationJobRunner
    from apps.chama.models import Chama

    def callback():
        queryset = Chama.objects.filter(status="active")
        if chama_id:
            queryset = queryset.filter(id=chama_id)

        generated = 0
        for chama in queryset:
            AIWorkflowService.loan_default_prediction_for_chama(
                chama_id=chama.id,
                actor=None,
            )
            generated += 1
        return {"generated": generated}

    return AutomationJobRunner.run_job(
        name="ai_loan_default_prediction",
        schedule="15 3 * * *",
        description="Predicts loan default risk bands for active loans.",
        callback=callback,
    )


@shared_task
def ai_contribution_behavior_forecast_task(chama_id: str | None = None):
    from apps.automations.services import AutomationJobRunner
    from apps.chama.models import Chama

    def callback():
        queryset = Chama.objects.filter(status="active")
        if chama_id:
            queryset = queryset.filter(id=chama_id)

        generated = 0
        for chama in queryset:
            AIWorkflowService.contribution_behavior_forecast_for_chama(
                chama_id=chama.id,
                actor=None,
            )
            generated += 1
        return {"generated": generated}

    return AutomationJobRunner.run_job(
        name="ai_contribution_behavior_forecast",
        schedule="30 3 * * *",
        description="Forecasts contribution/default/dropout behavior for active members.",
        callback=callback,
    )


@shared_task
def ai_governance_health_score_task(chama_id: str | None = None):
    from apps.automations.services import AutomationJobRunner
    from apps.chama.models import Chama

    def callback():
        queryset = Chama.objects.filter(status="active")
        if chama_id:
            queryset = queryset.filter(id=chama_id)

        generated = 0
        for chama in queryset:
            AIWorkflowService.governance_health_score_for_chama(
                chama_id=chama.id,
                actor=None,
            )
            generated += 1
        return {"generated": generated}

    return AutomationJobRunner.run_job(
        name="ai_governance_health_score",
        schedule="45 3 * * *",
        description="Generates governance, financial, participation, and transparency scores.",
        callback=callback,
    )


@shared_task
def ai_executive_summary_task(chama_id: str | None = None):
    from apps.automations.services import AutomationJobRunner
    from apps.chama.models import Chama

    def callback():
        today = timezone.localdate()
        target_month = today.month
        target_year = today.year
        queryset = Chama.objects.filter(status="active")
        if chama_id:
            queryset = queryset.filter(id=chama_id)

        generated = 0
        for chama in queryset:
            AIWorkflowService.executive_summary_for_chama(
                chama_id=chama.id,
                month=target_month,
                year=target_year,
                actor=None,
            )
            generated += 1
        return {
            "generated": generated,
            "month": target_month,
            "year": target_year,
        }

    return AutomationJobRunner.run_job(
        name="ai_executive_summary",
        schedule="0 6 1 * *",
        description="Generates monthly executive summary payloads.",
        callback=callback,
    )


@shared_task
def ai_issue_auto_triage_task(issue_id: str):
    from apps.accounts.models import User
    from apps.issues.models import Issue

    issue = Issue.objects.filter(id=issue_id).select_related("chama").first()
    if not issue:
        return {"status": "not_found", "issue_id": issue_id}

    actor = (
        User.objects.filter(
            memberships__chama=issue.chama,
            memberships__is_active=True,
            memberships__is_approved=True,
            memberships__role__in=["CHAMA_ADMIN", "SECRETARY", "TREASURER"],
        )
        .distinct()
        .first()
    )
    if not actor:
        return {
            "status": "skipped",
            "reason": "no_eligible_actor",
            "issue_id": issue_id,
        }

    try:
        payload = AIWorkflowService.triage_issue(issue_id=issue.id, actor=actor)
        return {"status": "ok", "payload": payload}
    except Exception as exc:  # noqa: BLE001
        logger.exception("AI issue triage failed for %s", issue_id)
        return {"status": "failed", "detail": str(exc), "issue_id": issue_id}


@shared_task
def ai_meeting_summarize_task(meeting_id: str):
    from apps.accounts.models import User
    from apps.meetings.models import Meeting

    meeting = Meeting.objects.filter(id=meeting_id).select_related("chama").first()
    if not meeting:
        return {"status": "not_found", "meeting_id": meeting_id}

    actor = (
        User.objects.filter(
            memberships__chama=meeting.chama,
            memberships__is_active=True,
            memberships__is_approved=True,
            memberships__role__in=["CHAMA_ADMIN", "SECRETARY", "TREASURER"],
        )
        .distinct()
        .first()
    )
    if not actor:
        return {
            "status": "skipped",
            "reason": "no_eligible_actor",
            "meeting_id": meeting_id,
        }

    try:
        payload = AIWorkflowService.summarize_meeting(
            meeting_id=meeting.id, actor=actor
        )
        return {"status": "ok", "payload": payload}
    except Exception as exc:  # noqa: BLE001
        logger.exception("AI meeting summarization failed for %s", meeting_id)
        return {"status": "failed", "detail": str(exc), "meeting_id": meeting_id}


@shared_task
def ai_reindex_document_task(document_id: str):
    document = KnowledgeDocument.objects.filter(id=document_id).first()
    if not document:
        return {"status": "not_found", "document_id": document_id}

    from apps.ai.services import KnowledgeBaseService

    chunks = KnowledgeBaseService.reindex_document(document=document, actor=None)
    return {"status": "ok", "document_id": document_id, "chunks": chunks}


@shared_task
def ai_prune_old_conversations_task(days_old: int = 90) -> dict:
    """Prune conversations older than specified days to optimize database size.
    
    This reduces memory overhead and keeps the database clean for better performance.
    Runs monthly by default.
    """
    from apps.automations.services import AutomationJobRunner

    def callback():
        cutoff = timezone.now() - timedelta(days=days_old)
        deleted_count, _ = AIConversation.objects.filter(
            created_at__lt=cutoff
        ).delete()
        logger.info(
            "Pruned %d old conversations (older than %d days)",
            deleted_count,
            days_old,
        )
        return {"deleted": deleted_count, "days_old": days_old}

    return AutomationJobRunner.run_job(
        name="ai_prune_old_conversations",
        schedule="0 2 1 * *",  # 1st of month at 2 AM
        description=f"Prunes AI conversations older than {days_old} days.",
        callback=callback,
    )
