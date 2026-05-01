from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db import models, transaction
from django.db.models import Count
from django.utils import timezone

from apps.chama.models import MembershipRole
from apps.chama.services import (
    lift_membership_suspension_in_chama,
    suspend_membership_in_chama,
)
from apps.issues.models import (
    AppealStatus,
    Issue,
    IssueActivityAction,
    IssueActivityLog,
    IssueAppeal,
    IssueAssignmentHistory,
    IssueAutoTriggerLog,
    IssueCategory,
    IssueComment,
    IssueCommentType,
    IssueCommentVisibility,
    IssueEscalationType,
    IssueEvidence,
    IssueEvidenceType,
    IssueMediationNote,
    IssuePriority,
    IssueRating,
    IssueReopenDecision,
    IssueReopenRequest,
    IssueResolution,
    IssueResolutionStatus,
    IssueResolutionType,
    IssueScope,
    IssueSourceType,
    IssueStatus,
    IssueStatusHistory,
    Suspension,
    Warning,
)
from apps.notifications.models import NotificationPriority, NotificationType
from apps.notifications.services import NotificationService
from core.audit import create_audit_log


class IssueServiceError(Exception):
    pass


ALLOWED_STATUS_TRANSITIONS = {
    IssueStatus.OPEN: {
        IssueStatus.PENDING_ASSIGNMENT,
        IssueStatus.ASSIGNED,
        IssueStatus.UNDER_INVESTIGATION,
        IssueStatus.IN_PROGRESS,
        IssueStatus.RESOLVED,
        IssueStatus.DISMISSED,
        IssueStatus.CLOSED,
        IssueStatus.ESCALATED,
    },
    IssueStatus.PENDING_ASSIGNMENT: {
        IssueStatus.ASSIGNED,
        IssueStatus.CLARIFICATION_REQUESTED,
        IssueStatus.ESCALATED,
    },
    IssueStatus.ASSIGNED: {
        IssueStatus.CLARIFICATION_REQUESTED,
        IssueStatus.UNDER_INVESTIGATION,
        IssueStatus.IN_PROGRESS,
        IssueStatus.ESCALATED,
    },
    IssueStatus.CLARIFICATION_REQUESTED: {
        IssueStatus.IN_PROGRESS,
        IssueStatus.ASSIGNED,
        IssueStatus.ESCALATED,
    },
    IssueStatus.UNDER_INVESTIGATION: {
        IssueStatus.IN_PROGRESS,
        IssueStatus.RESOLUTION_PROPOSED,
        IssueStatus.ESCALATED,
    },
    IssueStatus.IN_PROGRESS: {
        IssueStatus.UNDER_INVESTIGATION,
        IssueStatus.RESOLUTION_PROPOSED,
        IssueStatus.ESCALATED,
    },
    IssueStatus.RESOLUTION_PROPOSED: {
        IssueStatus.AWAITING_CHAIRPERSON_APPROVAL,
        IssueStatus.IN_PROGRESS,
        IssueStatus.ESCALATED,
    },
    IssueStatus.AWAITING_CHAIRPERSON_APPROVAL: {
        IssueStatus.RESOLVED,
        IssueStatus.DISMISSED,
        IssueStatus.IN_PROGRESS,
        IssueStatus.ESCALATED,
    },
    IssueStatus.RESOLVED: {
        IssueStatus.CLOSED,
        IssueStatus.REOPENED,
    },
    IssueStatus.DISMISSED: {
        IssueStatus.CLOSED,
        IssueStatus.REOPENED,
    },
    IssueStatus.ESCALATED: {
        IssueStatus.IN_VOTE,
        IssueStatus.ASSIGNED,
        IssueStatus.UNDER_INVESTIGATION,
        IssueStatus.IN_PROGRESS,
        IssueStatus.RESOLVED,
        IssueStatus.DISMISSED,
    },
    IssueStatus.IN_VOTE: {
        IssueStatus.RESOLVED,
        IssueStatus.DISMISSED,
        IssueStatus.ESCALATED,
    },
    IssueStatus.REOPENED: {
        IssueStatus.PENDING_ASSIGNMENT,
        IssueStatus.ASSIGNED,
        IssueStatus.UNDER_INVESTIGATION,
        IssueStatus.IN_PROGRESS,
        IssueStatus.ESCALATED,
    },
    IssueStatus.CLOSED: {
        IssueStatus.REOPENED,
    },
}


def validate_status_transition(current_status: str, new_status: str) -> bool:
    if current_status == new_status:
        return True
    allowed = ALLOWED_STATUS_TRANSITIONS.get(current_status, set())
    return new_status in allowed


def generate_issue_code(chama_id) -> str:
    year = timezone.now().year
    prefix = "ISS"
    count = Issue.objects.filter(chama_id=chama_id).count() + 1
    return f"{prefix}-{year}-{count:05d}"


def log_issue_activity(issue: Issue, actor, action: str, meta: dict | None = None):
    entry = IssueActivityLog.objects.create(
        issue=issue,
        actor=actor,
        action=action,
        meta=meta or {},
    )
    create_audit_log(
        actor=actor,
        chama_id=issue.chama_id,
        action=f"issue_{action}",
        entity_type="Issue",
        entity_id=issue.id,
        metadata=meta or {},
    )
    return entry


def create_issue_status_history(issue, from_status: str, to_status: str, actor, reason: str = "", metadata: dict = None):
    return IssueStatusHistory.objects.create(
        issue=issue,
        from_status=from_status,
        to_status=to_status,
        changed_by=actor,
        reason=reason,
        metadata=metadata or {},
    )


@transaction.atomic
def create_issue(
    chama,
    title: str,
    description: str,
    category: str,
    severity: str,
    raised_by,
    source_type: str = IssueSourceType.MEMBER,
    issue_scope: str = IssueScope.PERSONAL,
    reported_user=None,
    loan=None,
    report_type: str = "",
    is_anonymous: bool = False,
    due_at=None,
) -> Issue:
    issue = Issue(
        chama=chama,
        title=title,
        description=description,
        category=category,
        severity=severity,
        source_type=source_type,
        issue_scope=issue_scope,
        reported_user=reported_user,
        loan=loan,
        report_type=report_type,
        is_anonymous=is_anonymous,
        due_at=due_at,
        status=IssueStatus.OPEN,
        created_by=raised_by,
        updated_by=raised_by,
    )
    issue.generate_issue_code()
    issue.save()

    log_issue_activity(
        issue,
        raised_by,
        IssueActivityAction.CREATED,
        {
            "category": issue.category,
            "severity": issue.severity,
            "source_type": issue.source_type,
            "issue_scope": issue.issue_scope,
            "reported_user_id": str(issue.reported_user_id) if issue.reported_user_id else None,
            "loan_id": str(issue.loan_id) if issue.loan_id else None,
        },
    )

    create_issue_status_history(
        issue, "", IssueStatus.OPEN, raised_by, "Issue created"
    )

    return issue


@transaction.atomic
def assign_issue(
    issue: Issue,
    assignee,
    actor,
    assigned_role: str = "",
    note: str = "",
) -> Issue:
    old_assignee = issue.assigned_to
    
    issue.assigned_to = assignee
    issue.assigned_role = assigned_role
    issue.updated_by = actor

    if issue.status == IssueStatus.OPEN:
        issue.status = IssueStatus.ASSIGNED
    elif issue.status == IssueStatus.PENDING_ASSIGNMENT:
        issue.status = IssueStatus.ASSIGNED

    issue.save(update_fields=["assigned_to", "assigned_role", "status", "updated_by", "updated_at"])

    IssueAssignmentHistory.objects.create(
        issue=issue,
        assigned_from=old_assignee,
        assigned_to=assignee,
        assigned_role=assigned_role,
        assigned_by=actor,
        note=note,
    )

    action = IssueActivityAction.REASSIGNED if old_assignee else IssueActivityAction.ASSIGNED
    log_issue_activity(
        issue,
        actor,
        action,
        {
            "assigned_to_id": str(assignee.id),
            "assigned_to_phone": assignee.phone,
            "assigned_role": assigned_role,
            "note": note,
        },
    )

    NotificationService.send_notification(
        user=assignee,
        chama=issue.chama,
        channels=["in_app", "push"],
        message=f"You have been assigned issue '{issue.title}'.",
        subject="Issue assigned",
        notification_type=NotificationType.ISSUE_UPDATE,
        priority=NotificationPriority.HIGH,
        idempotency_key=f"issue-assigned:{issue.id}:{assignee.id}:{issue.updated_at.isoformat()}",
        context_data={"chama_id": str(issue.chama_id)},
        actor=actor,
    )

    if issue.created_by_id and issue.created_by_id != actor.id:
        NotificationService.send_notification(
            user=issue.created_by,
            chama=issue.chama,
            channels=["in_app"],
            message=f"Your issue '{issue.title}' has been assigned to a handler.",
            subject="Issue assigned",
            notification_type=NotificationType.ISSUE_UPDATE,
            priority=NotificationPriority.NORMAL,
            idempotency_key=f"issue-assigned-notify:{issue.id}:{issue.updated_at.isoformat()}",
            context_data={"chama_id": str(issue.chama_id)},
            actor=actor,
        )

    return issue


@transaction.atomic
def change_issue_status(
    issue: Issue,
    new_status: str,
    actor,
    *,
    note: str = "",
    force: bool = False,
) -> Issue:
    old_status = issue.status

    if old_status == new_status:
        return issue

    if not force and not validate_status_transition(old_status, new_status):
        raise IssueServiceError(
            f"Cannot transition issue status from {old_status} to {new_status}."
        )

    issue.status = new_status
    issue.updated_by = actor

    if new_status == IssueStatus.RESOLVED and not issue.resolved_at:
        issue.resolved_at = timezone.now()
    if new_status == IssueStatus.CLOSED and not issue.closed_at:
        issue.closed_at = timezone.now()

    issue.save(
        update_fields=[
            "status",
            "resolved_at",
            "closed_at",
            "updated_by",
            "updated_at",
        ]
    )

    create_issue_status_history(
        issue, old_status, new_status, actor, note
    )

    log_issue_activity(
        issue,
        actor,
        IssueActivityAction.STATUS_CHANGED,
        {
            "from": old_status,
            "to": new_status,
            "note": note,
        },
    )

    if new_status == IssueStatus.CLOSED:
        log_issue_activity(
            issue,
            actor,
            IssueActivityAction.CLOSED,
            {"note": note},
        )
    if new_status == IssueStatus.REOPENED:
        log_issue_activity(
            issue,
            actor,
            IssueActivityAction.REOPENED,
            {"note": note},
        )

    if issue.created_by_id and new_status in {IssueStatus.RESOLVED, IssueStatus.CLOSED, IssueStatus.DISMISSED}:
        status_verb = "resolved" if new_status == IssueStatus.RESOLVED else ("dismissed" if new_status == IssueStatus.DISMISSED else "closed")
        NotificationService.send_notification(
            user=issue.created_by,
            chama=issue.chama,
            channels=["in_app", "push", "sms"],
            message=(
                f"Your issue '{issue.title}' was {status_verb}. "
                f"{note or 'Open the issue for the latest outcome.'}"
            ),
            subject="Issue updated",
            notification_type=NotificationType.ISSUE_UPDATE,
            priority=NotificationPriority.HIGH,
            idempotency_key=f"issue-status:{issue.id}:{new_status}:{issue.updated_at.isoformat()}",
            context_data={"chama_id": str(issue.chama_id)},
            actor=actor,
        )

    return issue


@transaction.atomic
def request_clarification(issue: Issue, actor, message: str) -> IssueComment:
    old_status = issue.status
    issue.status = IssueStatus.CLARIFICATION_REQUESTED
    issue.updated_by = actor
    issue.save(update_fields=["status", "updated_by", "updated_at"])

    create_issue_status_history(
        issue, old_status, IssueStatus.CLARIFICATION_REQUESTED, actor, message
    )

    comment = IssueComment.objects.create(
        issue=issue,
        author=actor,
        body=message,
        comment_type=IssueCommentType.CLARIFICATION,
        visibility=IssueCommentVisibility.INTERNAL_ONLY,
        is_clarification_response=False,
        created_by=actor,
        updated_by=actor,
    )

    log_issue_activity(
        issue,
        actor,
        IssueActivityAction.CLARIFICATION_REQUESTED,
        {"comment_id": str(comment.id), "message": message},
    )

    if issue.created_by_id and issue.created_by_id != actor.id:
        NotificationService.send_notification(
            user=issue.created_by,
            chama=issue.chama,
            channels=["in_app", "push", "sms"],
            message=f"Clarification requested on issue '{issue.title}'. Please respond.",
            subject="Clarification requested",
            notification_type=NotificationType.ISSUE_UPDATE,
            priority=NotificationPriority.HIGH,
            idempotency_key=f"issue-clarification:{issue.id}:{comment.id}",
            context_data={"chama_id": str(issue.chama_id)},
            actor=actor,
        )

    return comment


@transaction.atomic
def respond_to_clarification(issue: Issue, actor, message: str) -> IssueComment:
    old_status = issue.status
    
    if issue.status == IssueStatus.CLARIFICATION_REQUESTED:
        issue.status = IssueStatus.IN_PROGRESS
        issue.updated_by = actor
        issue.save(update_fields=["status", "updated_by", "updated_at"])

        create_issue_status_history(
            issue, IssueStatus.CLARIFICATION_REQUESTED, IssueStatus.IN_PROGRESS, actor, "Clarification provided"
        )

    comment = IssueComment.objects.create(
        issue=issue,
        author=actor,
        body=message,
        comment_type=IssueCommentType.CLARIFICATION,
        visibility=IssueCommentVisibility.MEMBER_VISIBLE,
        is_clarification_response=True,
        created_by=actor,
        updated_by=actor,
    )

    log_issue_activity(
        issue,
        actor,
        IssueActivityAction.CLARIFICATION_PROVIDED,
        {"comment_id": str(comment.id), "message": message},
    )

    if issue.assigned_to_id and issue.assigned_to_id != actor.id:
        NotificationService.send_notification(
            user=issue.assigned_to,
            chama=issue.chama,
            channels=["in_app", "push"],
            message=f"Clarification provided on issue '{issue.title}'.",
            subject="Clarification response",
            notification_type=NotificationType.ISSUE_UPDATE,
            priority=NotificationPriority.NORMAL,
            idempotency_key=f"issue-clarification-response:{issue.id}:{comment.id}",
            context_data={"chama_id": str(issue.chama_id)},
            actor=actor,
        )

    return comment


@transaction.atomic
def start_investigation(issue: Issue, actor, note: str = "") -> Issue:
    old_status = issue.status
    
    if issue.status not in {IssueStatus.ASSIGNED, IssueStatus.CLARIFICATION_REQUESTED}:
        raise IssueServiceError(f"Cannot start investigation from status {issue.status}.")

    issue.status = IssueStatus.UNDER_INVESTIGATION
    issue.updated_by = actor
    issue.save(update_fields=["status", "updated_by", "updated_at"])

    create_issue_status_history(
        issue, old_status, IssueStatus.UNDER_INVESTIGATION, actor, note or "Investigation started"
    )

    log_issue_activity(
        issue,
        actor,
        IssueActivityAction.INVESTIGATION_STARTED,
        {"note": note},
    )

    return issue


@transaction.atomic
def update_investigation(issue: Issue, actor, note: str) -> IssueComment:
    if issue.status not in {IssueStatus.UNDER_INVESTIGATION, IssueStatus.IN_PROGRESS}:
        raise IssueServiceError("Issue must be under investigation or in progress.")

    comment = IssueComment.objects.create(
        issue=issue,
        author=actor,
        body=note,
        comment_type=IssueCommentType.PUBLIC_UPDATE,
        visibility=IssueCommentVisibility.INTERNAL_ONLY,
        created_by=actor,
        updated_by=actor,
    )

    log_issue_activity(
        issue,
        actor,
        IssueActivityAction.INVESTIGATION_UPDATE,
        {"comment_id": str(comment.id), "note": note},
    )

    if issue.created_by_id and issue.created_by_id != actor.id:
        NotificationService.send_notification(
            user=issue.created_by,
            chama=issue.chama,
            channels=["in_app"],
            message=f"Update on issue '{issue.title}': {note[:100]}...",
            subject="Issue update",
            notification_type=NotificationType.ISSUE_UPDATE,
            priority=NotificationPriority.NORMAL,
            idempotency_key=f"issue-investigation-update:{issue.id}:{comment.id}",
            context_data={"chama_id": str(issue.chama_id)},
            actor=actor,
        )

    return comment


@transaction.atomic
def propose_resolution(
    issue: Issue,
    actor,
    resolution_type: str,
    summary: str,
    detailed_action_taken: str = "",
    financial_adjustment_amount: Decimal = None,
) -> IssueResolution:
    if issue.status not in {
        IssueStatus.OPEN,
        IssueStatus.ASSIGNED,
        IssueStatus.UNDER_INVESTIGATION,
        IssueStatus.IN_PROGRESS,
    }:
        raise IssueServiceError(
            "Issue must be open, assigned, under investigation, or in progress to propose resolution."
        )

    old_status = issue.status

    resolution = IssueResolution.objects.create(
        issue=issue,
        proposed_by=actor,
        resolution_type=resolution_type,
        summary=summary,
        detailed_action_taken=detailed_action_taken,
        financial_adjustment_amount=financial_adjustment_amount,
        status=IssueResolutionStatus.PROPOSED,
        created_by=actor,
        updated_by=actor,
    )

    issue.status = IssueStatus.RESOLUTION_PROPOSED
    issue.updated_by = actor
    issue.save(update_fields=["status", "updated_by", "updated_at"])

    create_issue_status_history(
        issue, old_status, IssueStatus.RESOLUTION_PROPOSED, actor, f"Resolution proposed: {resolution_type}"
    )

    log_issue_activity(
        issue,
        actor,
        IssueActivityAction.RESOLUTION_PROPOSED,
        {
            "resolution_id": str(resolution.id),
            "resolution_type": resolution_type,
            "summary": summary,
            "financial_adjustment": str(financial_adjustment_amount) if financial_adjustment_amount else None,
        },
    )

    return resolution


@transaction.atomic
def approve_resolution(
    resolution: IssueResolution,
    actor,
    issue: Issue = None,
) -> IssueResolution:
    if issue is None:
        issue = resolution.issue

    if resolution.status != IssueResolutionStatus.PROPOSED:
        raise IssueServiceError("Can only approve proposed resolutions.")

    resolution.status = IssueResolutionStatus.APPROVED
    resolution.approved_by = actor
    resolution.approved_at = timezone.now()
    resolution.updated_by = actor
    resolution.save(
        update_fields=["status", "approved_by", "approved_at", "updated_by", "updated_at"]
    )

    old_status = issue.status
    issue.status = IssueStatus.AWAITING_CHAIRPERSON_APPROVAL
    issue.chairperson_approved = False
    issue.updated_by = actor
    issue.save(update_fields=["status", "chairperson_approved", "updated_by", "updated_at"])

    create_issue_status_history(
        issue, old_status, IssueStatus.AWAITING_CHAIRPERSON_APPROVAL, actor, "Resolution approved by handler"
    )

    log_issue_activity(
        issue,
        actor,
        IssueActivityAction.RESOLUTION_APPROVED,
        {"resolution_id": str(resolution.id)},
    )

    return resolution


@transaction.atomic
def reject_resolution(
    resolution: IssueResolution,
    actor,
    reason: str,
    issue: Issue = None,
) -> IssueResolution:
    if issue is None:
        issue = resolution.issue

    if resolution.status != IssueResolutionStatus.PROPOSED:
        raise IssueServiceError("Can only reject proposed resolutions.")

    resolution.status = IssueResolutionStatus.REJECTED
    resolution.rejected_by = actor
    resolution.rejected_at = timezone.now()
    resolution.updated_by = actor
    resolution.save(
        update_fields=["status", "rejected_by", "rejected_at", "updated_by", "updated_at"]
    )

    issue.status = IssueStatus.IN_PROGRESS
    issue.updated_by = actor
    issue.save(update_fields=["status", "updated_by", "updated_at"])

    create_issue_status_history(
        issue, IssueStatus.RESOLUTION_PROPOSED, IssueStatus.IN_PROGRESS, actor, f"Resolution rejected: {reason}"
    )

    log_issue_activity(
        issue,
        actor,
        IssueActivityAction.RESOLUTION_REJECTED,
        {"resolution_id": str(resolution.id), "reason": reason},
    )

    return resolution


@transaction.atomic
def chairperson_approve_resolution(
    issue: Issue,
    actor,
) -> Issue:
    if issue.status != IssueStatus.AWAITING_CHAIRPERSON_APPROVAL:
        raise IssueServiceError("Issue must be awaiting chairperson approval.")

    issue.status = IssueStatus.RESOLVED
    issue.chairperson_approved = True
    issue.chairperson_approved_at = timezone.now()
    issue.chairperson_approved_by = actor
    issue.resolved_at = timezone.now()
    issue.updated_by = actor
    issue.save(
        update_fields=[
            "status",
            "chairperson_approved",
            "chairperson_approved_at",
            "chairperson_approved_by",
            "resolved_at",
            "updated_by",
            "updated_at",
        ]
    )

    latest_resolution = issue.resolutions.filter(
        status=IssueResolutionStatus.APPROVED
    ).first()
    if latest_resolution:
        latest_resolution.status = IssueResolutionStatus.EXECUTED
        latest_resolution.updated_by = actor
        latest_resolution.save(update_fields=["status", "updated_by", "updated_at"])
        
        log_issue_activity(
            issue,
            actor,
            IssueActivityAction.RESOLUTION_EXECUTED,
            {"resolution_id": str(latest_resolution.id)},
        )

    create_issue_status_history(
        issue, IssueStatus.AWAITING_CHAIRPERSON_APPROVAL, IssueStatus.RESOLVED, actor, "Chairperson approved resolution"
    )

    log_issue_activity(
        issue,
        actor,
        IssueActivityAction.STATUS_CHANGED,
        {"from": IssueStatus.AWAITING_CHAIRPERSON_APPROVAL, "to": IssueStatus.RESOLVED},
    )

    if issue.created_by_id:
        NotificationService.send_notification(
            user=issue.created_by,
            chama=issue.chama,
            channels=["in_app", "push", "sms"],
            message=f"Your issue '{issue.title}' has been resolved.",
            subject="Issue resolved",
            notification_type=NotificationType.ISSUE_UPDATE,
            priority=NotificationPriority.HIGH,
            idempotency_key=f"issue-resolved:{issue.id}:{issue.updated_at.isoformat()}",
            context_data={"chama_id": str(issue.chama_id)},
            actor=actor,
        )

    return issue


@transaction.atomic
def chairperson_reject_resolution(
    issue: Issue,
    actor,
    reason: str,
) -> Issue:
    if issue.status != IssueStatus.AWAITING_CHAIRPERSON_APPROVAL:
        raise IssueServiceError("Issue must be awaiting chairperson approval.")

    issue.status = IssueStatus.IN_PROGRESS
    issue.chairperson_approved = False
    issue.updated_by = actor
    issue.save(update_fields=["status", "chairperson_approved", "updated_by", "updated_at"])

    latest_resolution = issue.resolutions.filter(
        status=IssueResolutionStatus.APPROVED
    ).first()
    if latest_resolution:
        latest_resolution.status = IssueResolutionStatus.REJECTED
        latest_resolution.rejected_by = actor
        latest_resolution.rejected_at = timezone.now()
        latest_resolution.updated_by = actor
        latest_resolution.save(
            update_fields=["status", "rejected_by", "rejected_at", "updated_by", "updated_at"]
        )

    create_issue_status_history(
        issue, IssueStatus.AWAITING_CHAIRPERSON_APPROVAL, IssueStatus.IN_PROGRESS, actor, f"Chairperson rejected: {reason}"
    )

    log_issue_activity(
        issue,
        actor,
        IssueActivityAction.RESOLUTION_REJECTED,
        {"reason": reason},
    )

    if issue.assigned_to_id:
        NotificationService.send_notification(
            user=issue.assigned_to,
            chama=issue.chama,
            channels=["in_app", "push"],
            message=f"Resolution for '{issue.title}' was rejected. Reason: {reason}",
            subject="Resolution rejected",
            notification_type=NotificationType.ISSUE_UPDATE,
            priority=NotificationPriority.HIGH,
            idempotency_key=f"issue-resolution-rejected:{issue.id}:{issue.updated_at.isoformat()}",
            context_data={"chama_id": str(issue.chama_id)},
            actor=actor,
        )

    return issue


@transaction.atomic
def dismiss_issue(issue: Issue, actor, reason: str) -> Issue:
    old_status = issue.status
    
    if issue.status not in {
        IssueStatus.OPEN,
        IssueStatus.ASSIGNED,
        IssueStatus.UNDER_INVESTIGATION,
        IssueStatus.IN_PROGRESS,
        IssueStatus.ESCALATED,
    }:
        raise IssueServiceError(f"Cannot dismiss issue from status {issue.status}.")

    issue.status = IssueStatus.DISMISSED
    issue.updated_by = actor
    issue.save(update_fields=["status", "updated_by", "updated_at"])

    create_issue_status_history(
        issue, old_status, IssueStatus.DISMISSED, actor, reason
    )

    log_issue_activity(
        issue,
        actor,
        IssueActivityAction.DISMISSED,
        {"reason": reason},
    )

    if issue.created_by_id and issue.created_by_id != actor.id:
        NotificationService.send_notification(
            user=issue.created_by,
            chama=issue.chama,
            channels=["in_app", "push", "sms"],
            message=f"Your issue '{issue.title}' has been dismissed. Reason: {reason}",
            subject="Issue dismissed",
            notification_type=NotificationType.ISSUE_UPDATE,
            priority=NotificationPriority.NORMAL,
            idempotency_key=f"issue-dismissed:{issue.id}:{issue.updated_at.isoformat()}",
            context_data={"chama_id": str(issue.chama_id)},
            actor=actor,
        )

    return issue


@transaction.atomic
def escalate_issue(
    issue: Issue,
    actor,
    escalation_type: str,
    reason: str = "",
) -> Issue:
    old_status = issue.status
    
    if issue.status in {IssueStatus.CLOSED, IssueStatus.RESOLVED}:
        raise IssueServiceError("Cannot escalate closed or resolved issues.")

    issue.status = IssueStatus.ESCALATED
    issue.escalation_type = escalation_type
    issue.escalation_reason = reason
    issue.updated_by = actor
    issue.save(
        update_fields=["status", "escalation_type", "escalation_reason", "updated_by", "updated_at"]
    )

    create_issue_status_history(
        issue, old_status, IssueStatus.ESCALATED, actor, f"Escalated to {escalation_type}: {reason}"
    )

    log_issue_activity(
        issue,
        actor,
        IssueActivityAction.ESCALATED,
        {"escalation_type": escalation_type, "reason": reason},
    )

    if escalation_type == IssueEscalationType.FULL_GROUP_VOTE:
        issue.status = IssueStatus.IN_VOTE
        issue.save(update_fields=["status", "updated_at", "updated_by"])
        
        create_issue_status_history(
            issue, IssueStatus.ESCALATED, IssueStatus.IN_VOTE, actor, "Group vote started"
        )
        
        log_issue_activity(
            issue,
            actor,
            IssueActivityAction.VOTE_STARTED,
            {"escalation_type": escalation_type},
        )

    NotificationService.send_notification(
        user=issue.created_by,
        chama=issue.chama,
        channels=["in_app"],
        message=f"Issue '{issue.title}' has been escalated to {escalation_type}.",
        subject="Issue escalated",
        notification_type=NotificationType.ISSUE_UPDATE,
        priority=NotificationPriority.HIGH,
        idempotency_key=f"issue-escalated:{issue.id}:{issue.updated_at.isoformat()}",
        context_data={"chama_id": str(issue.chama_id)},
        actor=actor,
    )

    return issue


@transaction.atomic
def reopen_issue(
    issue: Issue,
    actor,
    reason: str,
) -> Issue:
    old_status = issue.status
    
    if issue.status not in {IssueStatus.RESOLVED, IssueStatus.CLOSED, IssueStatus.DISMISSED}:
        raise IssueServiceError("Only resolved, closed, or dismissed issues can be reopened.")

    is_creator = issue.created_by_id == actor.id
    
    if not is_creator and not actor.is_superuser:
        raise IssueServiceError("Only the issue creator or admin can reopen an issue.")

    if is_creator and issue.status != IssueStatus.CLOSED:
        reopen_request = IssueReopenRequest.objects.create(
            issue=issue,
            requested_by=actor,
            reason=reason,
            created_by=actor,
            updated_by=actor,
        )
        
        log_issue_activity(
            issue,
            actor,
            IssueActivityAction.REOPENED,
            {"request_id": str(reopen_request.id), "reason": reason},
        )
        
        raise IssueServiceError("Reopen request submitted. Pending approval.")

    issue.status = IssueStatus.REOPENED
    issue.reopened_count += 1
    issue.resolved_at = None
    issue.closed_at = None
    issue.updated_by = actor
    issue.save(
        update_fields=[
            "status",
            "reopened_count",
            "resolved_at",
            "closed_at",
            "updated_by",
            "updated_at",
        ]
    )

    create_issue_status_history(
        issue, old_status, IssueStatus.REOPENED, actor, reason
    )

    log_issue_activity(
        issue,
        actor,
        IssueActivityAction.REOPENED,
        {"reason": reason, "reopened_count": issue.reopened_count},
    )

    if issue.assigned_to_id:
        NotificationService.send_notification(
            user=issue.assigned_to,
            chama=issue.chama,
            channels=["in_app", "push"],
            message=f"Issue '{issue.title}' has been reopened. Please review.",
            subject="Issue reopened",
            notification_type=NotificationType.ISSUE_UPDATE,
            priority=NotificationPriority.HIGH,
            idempotency_key=f"issue-reopened:{issue.id}:{issue.updated_at.isoformat()}",
            context_data={"chama_id": str(issue.chama_id)},
            actor=actor,
        )

    return issue


@transaction.atomic
def rate_issue(issue: Issue, actor, score: int, feedback: str = "") -> IssueRating:
    if issue.status not in {IssueStatus.RESOLVED, IssueStatus.CLOSED}:
        raise IssueServiceError("Only resolved or closed issues can be rated.")

    if not (1 <= score <= 5):
        raise IssueServiceError("Rating must be between 1 and 5.")

    rating, created = IssueRating.objects.update_or_create(
        issue=issue,
        rated_by=actor,
        defaults={
            "score": score,
            "feedback": feedback,
            "created_by": actor,
            "updated_by": actor,
        }
    )

    log_issue_activity(
        issue,
        actor,
        IssueActivityAction.RATED,
        {"score": score, "feedback": feedback},
    )

    return rating


@transaction.atomic
def add_comment(
    issue: Issue,
    actor,
    body: str,
    comment_type: str = IssueCommentType.PUBLIC_UPDATE,
    visibility: str = IssueCommentVisibility.MEMBER_VISIBLE,
) -> IssueComment:
    comment = IssueComment.objects.create(
        issue=issue,
        author=actor,
        body=body,
        comment_type=comment_type,
        visibility=visibility,
        created_by=actor,
        updated_by=actor,
    )

    log_issue_activity(
        issue,
        actor,
        IssueActivityAction.COMMENT_ADDED,
        {
            "comment_id": str(comment.id),
            "comment_type": comment_type,
            "visibility": visibility,
        },
    )

    notify_users = []
    if issue.created_by_id and issue.created_by_id != actor.id:
        notify_users.append(issue.created_by)
    if issue.assigned_to_id and issue.assigned_to_id != actor.id:
        notify_users.append(issue.assigned_to)

    for user in notify_users:
        if visibility == IssueCommentVisibility.INTERNAL_ONLY and not user.is_superuser:
            continue
        NotificationService.send_notification(
            user=user,
            chama=issue.chama,
            channels=["in_app"],
            message=f"New comment on issue '{issue.title}'.",
            subject="New comment",
            notification_type=NotificationType.ISSUE_UPDATE,
            priority=NotificationPriority.NORMAL,
            idempotency_key=f"issue-comment:{issue.id}:{comment.id}",
            context_data={"chama_id": str(issue.chama_id)},
            actor=actor,
        )

    return comment


@transaction.atomic
def add_evidence(
    issue: Issue,
    actor,
    file,
    evidence_type: str = IssueEvidenceType.OTHER,
    caption: str = "",
) -> IssueEvidence:
    evidence = IssueEvidence.objects.create(
        issue=issue,
        uploaded_by=actor,
        file=file,
        evidence_type=evidence_type,
        caption=caption,
        created_by=actor,
        updated_by=actor,
    )

    log_issue_activity(
        issue,
        actor,
        IssueActivityAction.EVIDENCE_ADDED,
        {
            "evidence_id": str(evidence.id),
            "evidence_type": evidence_type,
            "caption": caption,
        },
    )

    return evidence


@transaction.atomic
def create_system_issue(
    chama,
    trigger_type: str,
    title: str,
    description: str,
    category: str,
    severity: str,
    linked_object_type: str = "",
    linked_object_id: str = "",
    metadata: dict = None,
) -> Issue:
    issue = create_issue(
        chama=chama,
        title=title,
        description=description,
        category=category,
        severity=severity,
        raised_by=None,
        source_type=IssueSourceType.SYSTEM,
        issue_scope=IssueScope.GROUP if category in {IssueCategory.FINANCIAL, IssueCategory.GOVERNANCE} else IssueScope.OPERATIONAL,
    )

    IssueAutoTriggerLog.objects.create(
        trigger_type=trigger_type,
        linked_object_type=linked_object_type,
        linked_object_id=linked_object_id,
        metadata=metadata or {},
        generated_issue=issue,
    )

    return issue


@transaction.atomic
def issue_warning(
    issue: Issue,
    *,
    actor,
    reason: str,
    severity: str,
    message_to_user: str = "",
    channels: list[str] | None = None,
) -> Warning:
    if not issue.reported_user:
        raise IssueServiceError("This issue has no reported user.")

    message = (
        message_to_user
        or f"You have received a warning in {issue.chama.name}. Reason: {reason}"
    )
    warning = Warning.objects.create(
        chama=issue.chama,
        user=issue.reported_user,
        issue=issue,
        reason=reason,
        severity=severity,
        message_to_user=message,
        issued_by=actor,
        created_by=actor,
        updated_by=actor,
    )

    log_issue_activity(
        issue,
        actor,
        IssueActivityAction.WARNED,
        {
            "warning_id": str(warning.id),
            "severity": severity,
            "reason": reason,
        },
    )

    NotificationService.send_notification(
        user=issue.reported_user,
        message=message,
        channels=channels or ["sms", "email"],
        chama=issue.chama,
        subject=f"Warning Notice - {issue.chama.name}",
        notification_type=NotificationType.SYSTEM,
        priority=NotificationPriority.HIGH,
        actor=actor,
    )

    return warning


@transaction.atomic
def suspend_reported_user(
    issue: Issue,
    *,
    actor,
    reason: str,
    starts_at=None,
    ends_at=None,
    message_to_user: str = "",
    channels: list[str] | None = None,
) -> Suspension:
    if not issue.reported_user:
        raise IssueServiceError("This issue has no reported user.")

    active_exists = Suspension.objects.filter(
        chama=issue.chama,
        user=issue.reported_user,
        is_active=True,
    ).exists()
    if active_exists:
        raise IssueServiceError("User already has an active suspension in this chama.")

    suspension = Suspension.objects.create(
        chama=issue.chama,
        user=issue.reported_user,
        issue=issue,
        reason=reason,
        starts_at=starts_at or timezone.now(),
        ends_at=ends_at,
        suspended_by=actor,
        is_active=True,
        created_by=actor,
        updated_by=actor,
    )

    suspend_membership_in_chama(issue.chama_id, issue.reported_user_id, actor=actor)

    if issue.status not in {IssueStatus.CLOSED, IssueStatus.RESOLVED, IssueStatus.DISMISSED}:
        issue.status = IssueStatus.ESCALATED
        issue.updated_by = actor
        issue.save(update_fields=["status", "updated_by", "updated_at"])

    log_issue_activity(
        issue,
        actor,
        IssueActivityAction.SUSPENDED,
        {
            "suspension_id": str(suspension.id),
            "reason": reason,
            "starts_at": suspension.starts_at.isoformat(),
            "ends_at": suspension.ends_at.isoformat() if suspension.ends_at else None,
        },
    )

    message = (
        message_to_user
        or f"Your membership in {issue.chama.name} has been suspended. Reason: {reason}"
    )
    NotificationService.send_notification(
        user=issue.reported_user,
        message=message,
        channels=channels or ["sms", "email"],
        chama=issue.chama,
        subject=f"Suspension Notice - {issue.chama.name}",
        notification_type=NotificationType.SYSTEM,
        priority=NotificationPriority.HIGH,
        actor=actor,
    )

    return suspension


@transaction.atomic
def lift_user_suspension(
    issue: Issue,
    *,
    actor,
    lift_reason: str = "",
    channels: list[str] | None = None,
) -> Suspension:
    if not issue.reported_user:
        raise IssueServiceError("This issue has no reported user.")

    suspension = (
        Suspension.objects.filter(
            chama=issue.chama,
            user=issue.reported_user,
            is_active=True,
        )
        .order_by("-starts_at")
        .first()
    )
    if not suspension:
        raise IssueServiceError("No active suspension found for reported user.")

    suspension.is_active = False
    suspension.lifted_at = timezone.now()
    suspension.lifted_by = actor
    suspension.lift_reason = lift_reason
    suspension.updated_by = actor
    suspension.save(
        update_fields=[
            "is_active",
            "lifted_at",
            "lifted_by",
            "lift_reason",
            "updated_by",
            "updated_at",
        ]
    )

    lift_membership_suspension_in_chama(
        issue.chama_id,
        issue.reported_user_id,
        actor=actor,
    )

    log_issue_activity(
        issue,
        actor,
        IssueActivityAction.SUSPENSION_LIFTED,
        {
            "suspension_id": str(suspension.id),
            "lift_reason": lift_reason,
        },
    )

    message = f"Your suspension in {issue.chama.name} has been lifted."
    if lift_reason:
        message = f"{message} Reason: {lift_reason}"

    NotificationService.send_notification(
        user=issue.reported_user,
        message=message,
        channels=channels or ["sms", "email"],
        chama=issue.chama,
        subject=f"Suspension Lifted - {issue.chama.name}",
        notification_type=NotificationType.SYSTEM,
        priority=NotificationPriority.NORMAL,
        actor=actor,
    )

    return suspension


@transaction.atomic
def create_issue_appeal(issue: Issue, *, actor, message: str) -> IssueAppeal:
    if issue.status not in {
        IssueStatus.RESOLVED,
        IssueStatus.CLOSED,
        IssueStatus.DISMISSED,
        IssueStatus.ESCALATED,
    }:
        raise IssueServiceError("Appeals can only be filed on resolved/closed/dismissed issues.")

    appeal = IssueAppeal.objects.create(
        issue=issue,
        appellant=actor,
        message=message,
        status=AppealStatus.OPEN,
        created_by=actor,
        updated_by=actor,
    )
    log_issue_activity(
        issue,
        actor,
        "appealed",
        {"appeal_id": str(appeal.id)},
    )
    return appeal


@transaction.atomic
def review_issue_appeal(
    appeal: IssueAppeal,
    *,
    actor,
    status: str,
    review_note: str = "",
) -> IssueAppeal:
    if status not in {
        AppealStatus.IN_REVIEW,
        AppealStatus.ACCEPTED,
        AppealStatus.REJECTED,
    }:
        raise IssueServiceError("Invalid appeal review status.")

    appeal.status = status
    appeal.review_note = review_note
    if status in {AppealStatus.ACCEPTED, AppealStatus.REJECTED}:
        appeal.reviewed_by = actor
        appeal.reviewed_at = timezone.now()
    appeal.updated_by = actor
    appeal.save(
        update_fields=[
            "status",
            "review_note",
            "reviewed_by",
            "reviewed_at",
            "updated_by",
            "updated_at",
        ]
    )

    if status == AppealStatus.ACCEPTED and appeal.issue.status in {
        IssueStatus.CLOSED,
        IssueStatus.RESOLVED,
        IssueStatus.DISMISSED,
    }:
        appeal.issue.status = IssueStatus.REOPENED
        appeal.issue.updated_by = actor
        appeal.issue.save(update_fields=["status", "updated_by", "updated_at"])

    log_issue_activity(
        appeal.issue,
        actor,
        "appeal_reviewed",
        {"appeal_id": str(appeal.id), "status": status},
    )
    return appeal


@transaction.atomic
def create_mediation_note(
    issue: Issue,
    *,
    actor,
    note: str,
    is_private: bool = True,
) -> IssueMediationNote:
    mediation_note = IssueMediationNote.objects.create(
        issue=issue,
        author=actor,
        note=note,
        is_private=is_private,
        created_by=actor,
        updated_by=actor,
    )
    log_issue_activity(
        issue,
        actor,
        IssueActivityAction.MEDIATION_NOTE_ADDED,
        {
            "mediation_note_id": str(mediation_note.id),
            "is_private": bool(is_private),
        },
    )
    return mediation_note


@transaction.atomic
def escalate_issue_ladder(
    issue: Issue,
    *,
    actor,
    reason: str = "",
    channels: list[str] | None = None,
) -> dict:
    if not issue.reported_user:
        raise IssueServiceError("Issue must have a reported user for escalation.")

    base_reason = reason.strip() or f"Escalation from issue {issue.id}"
    current_suspension = (
        Suspension.objects.filter(
            chama=issue.chama,
            user=issue.reported_user,
            is_active=True,
        )
        .order_by("-starts_at")
        .first()
    )

    if current_suspension:
        current_suspension.ends_at = None
        current_suspension.reason = (
            f"{current_suspension.reason}\nEscalated to long-term suspension: {base_reason}"
        ).strip()
        current_suspension.updated_by = actor
        current_suspension.save(update_fields=["ends_at", "reason", "updated_by", "updated_at"])
        log_issue_activity(
            issue,
            actor,
            IssueActivityAction.SUSPENDED,
            {
                "suspension_id": str(current_suspension.id),
                "escalation_level": "permanent",
                "reason": base_reason,
            },
        )
        return {
            "step": "permanent_suspension",
            "suspension_id": str(current_suspension.id),
        }

    warning_count = Warning.objects.filter(
        chama=issue.chama,
        user=issue.reported_user,
        status="active",
    ).count()
    if warning_count == 0:
        warning = issue_warning(
            issue,
            actor=actor,
            reason=base_reason,
            severity="high",
            channels=channels,
        )
        return {"step": "warning", "warning_id": str(warning.id)}

    suspension = suspend_reported_user(
        issue,
        actor=actor,
        reason=base_reason,
        starts_at=timezone.now(),
        ends_at=timezone.now() + timedelta(days=30),
        channels=channels,
    )
    return {"step": "temporary_suspension", "suspension_id": str(suspension.id)}


def build_issue_stats(chama_id, queryset=None):
    queryset = queryset if queryset is not None else Issue.objects.filter(chama_id=chama_id)

    status_counts = {
        row["status"]: row["total"]
        for row in queryset.values("status").annotate(total=Count("id"))
    }
    category_counts = {
        row["category"]: row["total"]
        for row in queryset.values("category").annotate(total=Count("id"))
    }
    severity_counts = {
        row["severity"]: row["total"]
        for row in queryset.values("severity").annotate(total=Count("id"))
    }

    resolution_hours = []
    resolved_queryset = queryset.filter(
        models.Q(resolved_at__isnull=False)
    ).values("created_at", "resolved_at")
    for row in resolved_queryset:
        if not row["resolved_at"]:
            continue
        delta_hours = (row["resolved_at"] - row["created_at"]).total_seconds() / 3600
        resolution_hours.append(max(delta_hours, 0))

    avg_resolution_hours = (
        round(sum(resolution_hours) / len(resolution_hours), 2)
        if resolution_hours
        else 0.0
    )

    reopen_count = queryset.aggregate(total=Count("reopened_count"))["total"] or 0
    escalated_count = queryset.filter(status=IssueStatus.ESCALATED).count()

    ratings = IssueRating.objects.filter(issue__chama_id=chama_id)
    rating_avg = ratings.aggregate(avg=models.Avg("score"))["avg"] or 0
    rating_count = ratings.count()

    defaulters = list(
        Suspension.objects.filter(
            chama_id=chama_id,
            is_active=True,
        )
        .select_related("user")
        .values(
            "user_id",
            "user__full_name",
            "user__phone",
            "starts_at",
            "ends_at",
        )
    )

    return {
        "chama_id": str(chama_id),
        "total_issues": queryset.count(),
        "status_counts": status_counts,
        "category_counts": category_counts,
        "severity_counts": severity_counts,
        "avg_resolution_hours": avg_resolution_hours,
        "reopen_count": reopen_count,
        "escalated_count": escalated_count,
        "rating_average": round(rating_avg, 2),
        "rating_count": rating_count,
        "active_suspensions": defaulters,
    }


def get_allowed_actions(issue: Issue, user, membership=None) -> list[str]:
    if not membership and not user.is_superuser:
        return []

    role = getattr(membership, "role", "")
    is_creator = issue.created_by_id == user.id
    is_assignee = issue.assigned_to_id == user.id if issue.assigned_to else False

    actions = []

    if issue.status == IssueStatus.OPEN:
        if role in {MembershipRole.CHAMA_ADMIN, MembershipRole.SECRETARY, MembershipRole.ADMIN} or user.is_superuser:
            actions.extend(["assign", "dismiss", "escalate"])
        if role == MembershipRole.TREASURER:
            actions.append("assign")

    if issue.status in {IssueStatus.ASSIGNED, IssueStatus.PENDING_ASSIGNMENT}:
        if is_assignee or role in {MembershipRole.CHAMA_ADMIN, MembershipRole.SECRETARY, MembershipRole.ADMIN} or user.is_superuser:
            actions.extend(["request_clarification", "start_investigation", "update_investigation"])

    if issue.status == IssueStatus.CLARIFICATION_REQUESTED:
        if is_creator:
            actions.append("respond_clarification")
        if is_assignee or role in {MembershipRole.CHAMA_ADMIN, MembershipRole.SECRETARY} or user.is_superuser:
            actions.extend(["update_investigation"])

    if issue.status in {IssueStatus.UNDER_INVESTIGATION, IssueStatus.IN_PROGRESS}:
        if is_assignee or role in {MembershipRole.CHAMA_ADMIN, MembershipRole.SECRETARY, MembershipRole.TREASURER} or user.is_superuser:
            actions.extend(["update_investigation", "propose_resolution"])

    if issue.status == IssueStatus.RESOLUTION_PROPOSED:
        if role in {MembershipRole.CHAMA_ADMIN, MembershipRole.SECRETARY} or user.is_superuser:
            actions.append("approve_resolution")
        if role in {MembershipRole.CHAMA_ADMIN, MembershipRole.SECRETARY} or user.is_superuser:
            actions.append("reject_resolution")

    if issue.status == IssueStatus.AWAITING_CHAIRPERSON_APPROVAL:
        if role == MembershipRole.CHAMA_ADMIN or user.is_superuser:
            actions.extend(["chairperson_approve", "chairperson_reject"])

    if issue.status in {IssueStatus.RESOLVED, IssueStatus.CLOSED, IssueStatus.DISMISSED}:
        if is_creator:
            actions.append("reopen")
        if role in {MembershipRole.CHAMA_ADMIN, MembershipRole.SECRETARY} or user.is_superuser:
            actions.extend(["close", "reopen"])

    if role in {MembershipRole.CHAMA_ADMIN, MembershipRole.SECRETARY, MembershipRole.TREASURER} or user.is_superuser:
        actions.extend(["add_comment", "add_evidence"])

    if is_creator or role in {MembershipRole.CHAMA_ADMIN, MembershipRole.SECRETARY} or user.is_superuser:
        actions.append("comment")

    if is_creator and issue.status in {IssueStatus.RESOLVED, IssueStatus.CLOSED}:
        actions.append("rate")

    return list(set(actions))
