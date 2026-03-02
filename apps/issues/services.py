from __future__ import annotations

from datetime import timedelta

from django.db import models, transaction
from django.db.models import Count
from django.utils import timezone

from apps.chama.services import (
    lift_membership_suspension_in_chama,
    suspend_membership_in_chama,
)
from apps.issues.models import (
    AppealStatus,
    Issue,
    IssueActivityAction,
    IssueActivityLog,
    IssueMediationNote,
    IssueAppeal,
    IssueStatus,
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
        IssueStatus.IN_REVIEW,
        IssueStatus.ASSIGNED,
        IssueStatus.RESOLVED,
        IssueStatus.CLOSED,
        IssueStatus.REJECTED,
        IssueStatus.ESCALATED,
    },
    IssueStatus.IN_REVIEW: {
        IssueStatus.ASSIGNED,
        IssueStatus.RESOLVED,
        IssueStatus.CLOSED,
        IssueStatus.REJECTED,
        IssueStatus.ESCALATED,
    },
    IssueStatus.ASSIGNED: {
        IssueStatus.IN_REVIEW,
        IssueStatus.RESOLVED,
        IssueStatus.CLOSED,
        IssueStatus.REJECTED,
        IssueStatus.ESCALATED,
    },
    IssueStatus.RESOLVED: {
        IssueStatus.CLOSED,
        IssueStatus.REOPENED,
    },
    IssueStatus.CLOSED: {IssueStatus.REOPENED},
    IssueStatus.REOPENED: {
        IssueStatus.IN_REVIEW,
        IssueStatus.ASSIGNED,
        IssueStatus.RESOLVED,
        IssueStatus.CLOSED,
        IssueStatus.REJECTED,
        IssueStatus.ESCALATED,
    },
    IssueStatus.REJECTED: {
        IssueStatus.REOPENED,
        IssueStatus.CLOSED,
    },
    IssueStatus.ESCALATED: {
        IssueStatus.IN_REVIEW,
        IssueStatus.ASSIGNED,
        IssueStatus.RESOLVED,
        IssueStatus.CLOSED,
        IssueStatus.REJECTED,
    },
}


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


@transaction.atomic
def assign_issue(issue: Issue, assignee, actor, note: str = "") -> Issue:
    issue.assigned_to = assignee
    issue.updated_by = actor

    if issue.status in {
        IssueStatus.OPEN,
        IssueStatus.IN_REVIEW,
        IssueStatus.REOPENED,
    }:
        issue.status = IssueStatus.ASSIGNED

    issue.save(update_fields=["assigned_to", "status", "updated_by", "updated_at"])
    log_issue_activity(
        issue,
        actor,
        IssueActivityAction.ASSIGNED,
        {
            "assigned_to_id": str(assignee.id),
            "assigned_to_phone": assignee.phone,
            "note": note,
        },
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

    if not force and new_status not in ALLOWED_STATUS_TRANSITIONS.get(
        old_status, set()
    ):
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

    if issue.status not in {IssueStatus.CLOSED, IssueStatus.RESOLVED}:
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
        IssueStatus.REJECTED,
        IssueStatus.ESCALATED,
    }:
        raise IssueServiceError("Appeals can only be filed on resolved/closed issues.")

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
        IssueActivityAction.APPEALED,
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
        IssueStatus.REJECTED,
    }:
        appeal.issue.status = IssueStatus.REOPENED
        appeal.issue.updated_by = actor
        appeal.issue.save(update_fields=["status", "updated_by", "updated_at"])

    log_issue_activity(
        appeal.issue,
        actor,
        IssueActivityAction.APPEAL_REVIEWED,
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
        # Final escalation: convert to long-term (indefinite) suspension.
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


def build_issue_stats(chama_id):
    queryset = Issue.objects.filter(chama_id=chama_id)

    status_counts = {
        row["status"]: row["total"]
        for row in queryset.values("status").annotate(total=Count("id"))
    }
    category_counts = {
        row["category"]: row["total"]
        for row in queryset.values("category").annotate(total=Count("id"))
    }
    priority_counts = {
        row["priority"]: row["total"]
        for row in queryset.values("priority").annotate(total=Count("id"))
    }

    resolution_hours = []
    resolved_queryset = queryset.filter(
        models.Q(resolved_at__isnull=False) | models.Q(closed_at__isnull=False)
    ).values("created_at", "resolved_at", "closed_at")
    for row in resolved_queryset:
        end_at = row["resolved_at"] or row["closed_at"]
        if not end_at:
            continue
        delta_hours = (end_at - row["created_at"]).total_seconds() / 3600
        resolution_hours.append(max(delta_hours, 0))

    avg_resolution_hours = (
        round(sum(resolution_hours) / len(resolution_hours), 2)
        if resolution_hours
        else 0.0
    )

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
        "priority_counts": priority_counts,
        "avg_resolution_hours": avg_resolution_hours,
        "active_suspensions": defaulters,
    }
