from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.ai.tasks import ai_issue_auto_triage_task
from apps.chama.models import Membership, MembershipRole, MemberStatus
from apps.finance.models import ContributionSchedule, ContributionScheduleStatus, InstallmentSchedule, InstallmentStatus
from apps.issues.models import Issue, IssueCategory, IssuePriority, IssueStatus
from apps.meetings.models import AttendanceStatus, Meeting
from apps.issues.services import change_issue_status

logger = logging.getLogger(__name__)


@shared_task
def issues_auto_create_system():
    from apps.automations.services import AutomationJobRunner

    def callback():
        today = timezone.localdate()
        created = 0

        missed_schedules = ContributionSchedule.objects.select_related("chama", "member").filter(
            status=ContributionScheduleStatus.MISSED,
            is_active=True,
        )
        for schedule in missed_schedules:
            _, issue_created = Issue.objects.get_or_create(
                chama=schedule.chama,
                title=f"Missed contribution: {schedule.member.get_full_name() or schedule.member.phone}",
                defaults={
                    "description": (
                        f"Contribution scheduled for {schedule.scheduled_date:%Y-%m-%d} "
                        f"was missed for {schedule.member.get_full_name() or schedule.member.phone}."
                    ),
                    "category": IssueCategory.FINANCIAL,
                    "severity": IssuePriority.HIGH,
                    "created_by": schedule.member,
                    "updated_by": schedule.member,
                },
            )
            if issue_created:
                created += 1

        overdue_installments = InstallmentSchedule.objects.select_related(
            "loan",
            "loan__chama",
            "loan__member",
        ).filter(status=InstallmentStatus.OVERDUE)
        for installment in overdue_installments:
            _, issue_created = Issue.objects.get_or_create(
                chama=installment.loan.chama,
                loan=installment.loan,
                title=f"Overdue loan: {installment.loan.member.get_full_name() or installment.loan.member.phone}",
                defaults={
                    "description": (
                        f"Loan installment due on {installment.due_date:%Y-%m-%d} is overdue."
                    ),
                    "category": IssueCategory.LOAN_DISPUTE,
                    "severity": IssuePriority.CRITICAL,
                    "created_by": installment.loan.member,
                    "updated_by": installment.loan.member,
                },
            )
            if issue_created:
                created += 1

        meetings = Meeting.objects.select_related("chama").filter(
            date__date__lt=today,
            cancelled_at__isnull=True,
        )
        for meeting in meetings:
            member_count = Membership.objects.filter(
                chama=meeting.chama,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
                exited_at__isnull=True,
            ).count()
            if member_count <= 0:
                continue
            attendance_count = meeting.attendance.exclude(
                status=AttendanceStatus.EXCUSED
            ).count()
            quorum_met = attendance_count >= ((member_count * meeting.quorum_percentage) / 100)
            if quorum_met:
                continue
            _, issue_created = Issue.objects.get_or_create(
                chama=meeting.chama,
                title=f"Quorum failure: {meeting.title}",
                defaults={
                    "description": (
                        f"Meeting '{meeting.title}' did not reach quorum on "
                        f"{timezone.localtime(meeting.date).strftime('%Y-%m-%d')}."
                    ),
                    "category": IssueCategory.OPERATIONAL,
                    "severity": IssuePriority.HIGH,
                    "created_by": meeting.created_by,
                    "updated_by": meeting.updated_by or meeting.created_by,
                },
            )
            if issue_created:
                created += 1

        return {"created": created}

    return AutomationJobRunner.run_job(
        name="issues_auto_create_system",
        schedule="15 6 * * *",
        description="Creates operational issues for missed payments, overdue loans, and quorum failures.",
        callback=callback,
    )


@shared_task
def issues_escalate_old_open():
    from apps.automations.services import AutomationJobRunner

    def callback():
        cutoff = timezone.now() - timedelta(days=7)
        escalated = 0

        queryset = Issue.objects.filter(
            status__in=[IssueStatus.OPEN, IssueStatus.PENDING_ASSIGNMENT, IssueStatus.ASSIGNED],
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
            status__in=[IssueStatus.OPEN, IssueStatus.PENDING_ASSIGNMENT],
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


@shared_task
def issues_due_reminders_and_auto_close():
    from apps.notifications.models import NotificationType
    from apps.notifications.services import NotificationService

    now = timezone.now()
    reminder_window_end = now + timedelta(hours=24)
    reminded = 0
    closed = 0

    due_soon = Issue.objects.select_related("chama", "assigned_to", "created_by").filter(
        status__in=[IssueStatus.OPEN, IssueStatus.ASSIGNED, IssueStatus.UNDER_INVESTIGATION, IssueStatus.IN_PROGRESS],
        due_at__isnull=False,
        due_at__gte=now,
        due_at__lte=reminder_window_end,
    )
    for issue in due_soon:
        recipients = [user for user in {issue.assigned_to, issue.created_by} if user]
        for recipient in recipients:
            NotificationService.send_notification(
                user=recipient,
                chama=issue.chama,
                channels=["in_app"],
                message=f"Issue {issue.issue_code or issue.id} is due by {issue.due_at:%Y-%m-%d %H:%M}.",
                subject="Issue due reminder",
                notification_type=NotificationType.SYSTEM,
                idempotency_key=f"issue-due-reminder:{issue.id}:{recipient.id}:{now.date().isoformat()}",
            )
            reminded += 1

    resolved_cutoff = now - timedelta(days=7)
    stale_resolved = Issue.objects.filter(
        status=IssueStatus.RESOLVED,
        resolved_at__isnull=False,
        resolved_at__lte=resolved_cutoff,
    )
    for issue in stale_resolved:
        issue.status = IssueStatus.CLOSED
        issue.closed_at = now
        issue.updated_at = now
        issue.save(update_fields=["status", "closed_at", "updated_at"])
        closed += 1

    return {"reminded": reminded, "auto_closed": closed, "time": now.isoformat()}
