from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.ai.tasks import ai_issue_auto_triage_task
from apps.chama.models import MemberStatus, Membership, MembershipRole
from apps.issues.models import Issue, IssueStatus
from apps.issues.services import change_issue_status

logger = logging.getLogger(__name__)


@shared_task
def issues_escalate_old_open():
    from apps.automations.services import AutomationJobRunner

    def callback():
        cutoff = timezone.now() - timedelta(days=7)
        escalated = 0

        queryset = Issue.objects.filter(
            status__in=[IssueStatus.OPEN, IssueStatus.IN_REVIEW, IssueStatus.ASSIGNED],
            created_at__lt=cutoff,
        ).select_related("chama")

        for issue in queryset:
            actor_membership = (
                Membership.objects.select_related("user")
                .filter(
                    chama=issue.chama,
                    is_active=True,
                    is_approved=True,
                    status=MemberStatus.ACTIVE,
                    role__in=[MembershipRole.CHAMA_ADMIN, MembershipRole.SECRETARY],
                )
                .first()
            )
            if not actor_membership:
                continue
            try:
                change_issue_status(
                    issue,
                    IssueStatus.ESCALATED,
                    actor=actor_membership.user,
                    note="Automated escalation for aging open issue.",
                    force=True,
                )
                escalated += 1
            except Exception:  # noqa: BLE001
                logger.exception("Failed escalating issue %s", issue.id)

        return {"escalated": escalated}

    return AutomationJobRunner.run_job(
        name="issues_escalate_old_open",
        schedule="30 6 * * *",
        description="Escalates aging unresolved issues.",
        callback=callback,
    )


@shared_task
def issues_auto_triage_ai(issue_id: str | None = None):
    from apps.automations.services import AutomationJobRunner

    def callback():
        if issue_id:
            return ai_issue_auto_triage_task(str(issue_id))

        triaged = 0
        candidates = Issue.objects.filter(
            status__in=[IssueStatus.OPEN, IssueStatus.IN_REVIEW],
            assigned_to__isnull=True,
        ).order_by("created_at")[:100]
        for issue in candidates:
            result = ai_issue_auto_triage_task(str(issue.id))
            if result.get("status") == "ok":
                triaged += 1

        return {"triaged": triaged, "candidate_count": candidates.count()}

    return AutomationJobRunner.run_job(
        name="issues_auto_triage_ai",
        schedule="0 1 * * *",
        description="AI triage sweep for unresolved issues.",
        callback=callback,
    )
