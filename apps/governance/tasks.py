from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from apps.automations.domain_services import (
    apply_membership_role_change,
    notify_role_change,
)
from apps.chama.models import Membership, MembershipRole, MemberStatus
from apps.governance.models import (
    Motion,
    MotionResult,
    MotionStatus,
    MotionVote,
    MotionVoteChoice,
    RoleChange,
    RoleChangeStatus,
)
from apps.notifications.models import NotificationPriority, NotificationType
from apps.notifications.services import NotificationService
from core.algorithms.governance import quorum_required
from core.audit import create_audit_log

logger = logging.getLogger(__name__)


def _active_memberships_for_motion(motion: Motion):
    queryset = Membership.objects.select_related("user").filter(
        chama=motion.chama,
        is_active=True,
        is_approved=True,
        status=MemberStatus.ACTIVE,
        exited_at__isnull=True,
    )
    if motion.eligible_roles:
        queryset = queryset.filter(role__in=motion.eligible_roles)
    return queryset


@shared_task
def open_due_voting():
    now = timezone.now()
    opened = 0

    motions = Motion.objects.select_related("chama", "created_by").filter(
        status=MotionStatus.OPEN,
        start_time__lte=now,
        end_time__gt=now,
    )
    for motion in motions:
        for membership in _active_memberships_for_motion(motion):
            NotificationService.send_notification(
                user=membership.user,
                chama=motion.chama,
                channels=["in_app", "push"],
                subject="Voting is now open",
                message=f"Voting is now open for '{motion.title}'. Cast your vote before the deadline.",
                notification_type=NotificationType.MEETING_NOTIFICATION,
                priority=NotificationPriority.HIGH,
                idempotency_key=f"motion-open:{motion.id}:{membership.user_id}",
                context_data={
                    "motion_id": str(motion.id),
                    "chama_id": str(motion.chama_id),
                },
            )
            opened += 1

    return {"notifications_sent": opened, "motions": motions.count()}


@shared_task
def send_voting_reminders():
    now = timezone.now()
    reminder_window_end = now + timedelta(hours=24)
    reminders = 0

    motions = Motion.objects.select_related("chama").filter(
        status=MotionStatus.OPEN,
        end_time__gt=now,
        end_time__lte=reminder_window_end,
    )
    for motion in motions:
        voted_user_ids = set(
            MotionVote.objects.filter(motion=motion).values_list("user_id", flat=True)
        )
        for membership in _active_memberships_for_motion(motion).exclude(
            user_id__in=voted_user_ids
        ):
            NotificationService.send_notification(
                user=membership.user,
                chama=motion.chama,
                channels=["in_app", "push"],
                subject="Vote reminder",
                message=(
                    f"'{motion.title}' closes in less than 24 hours. "
                    "Submit your vote before the deadline."
                ),
                notification_type=NotificationType.MEETING_NOTIFICATION,
                priority=NotificationPriority.HIGH,
                idempotency_key=f"motion-reminder:{motion.id}:{membership.user_id}",
                context_data={
                    "motion_id": str(motion.id),
                    "chama_id": str(motion.chama_id),
                },
            )
            reminders += 1

    return {"notifications_sent": reminders, "motions": motions.count()}


@shared_task
def close_expired_voting():
    now = timezone.now()
    closed = 0

    motions = Motion.objects.select_related("chama").filter(
        status=MotionStatus.OPEN,
        end_time__lte=now,
    )
    for motion in motions:
        eligible_count = _active_memberships_for_motion(motion).count()
        votes = MotionVote.objects.filter(motion=motion)
        total_votes = votes.count()
        yes_votes = votes.filter(vote=MotionVoteChoice.YES).count()
        no_votes = votes.filter(vote=MotionVoteChoice.NO).count()
        abstain_votes = votes.filter(vote=MotionVoteChoice.ABSTAIN).count()
        quorum_met = total_votes >= quorum_required(
            total_members=eligible_count,
            quorum_percentage=motion.quorum_percent,
        )
        passed = quorum_met and yes_votes > no_votes

        MotionResult.objects.update_or_create(
            motion=motion,
            defaults={
                "total_votes": total_votes,
                "yes_votes": yes_votes,
                "no_votes": no_votes,
                "abstain_votes": abstain_votes,
                "eligible_voters": eligible_count,
                "quorum_met": quorum_met,
                "passed": passed,
                "calculated_at": now,
                "created_by": motion.created_by,
                "updated_by": motion.created_by,
            },
        )
        motion.status = MotionStatus.CLOSED
        motion.closed_at = now
        motion.closed_by = motion.created_by
        motion.save(update_fields=["status", "closed_at", "closed_by", "updated_at"])
        create_audit_log(
            actor=motion.created_by,
            chama_id=motion.chama_id,
            action="motion_closed_by_scheduler",
            entity_type="Motion",
            entity_id=motion.id,
            metadata={
                "quorum_met": quorum_met,
                "passed": passed,
                "total_votes": total_votes,
            },
        )
        closed += 1

    return {"motions_closed": closed}


@shared_task
def apply_due_role_changes():
    today = timezone.localdate()
    activated = 0
    expired = 0
    activation_failures = 0
    expiry_failures = 0

    due_changes = RoleChange.objects.select_related("chama", "member").filter(
        status=RoleChangeStatus.APPROVED,
        effective_date__lte=today,
    )
    for role_change in due_changes:
        actor = role_change.approved_by or role_change.created_by
        if not actor:
            activation_failures += 1
            continue
        try:
            with transaction.atomic():
                membership, previous_role, outgoing_memberships = apply_membership_role_change(
                    chama=role_change.chama,
                    member_user=role_change.member,
                    new_role=role_change.new_role,
                    actor=actor,
                )
                if not role_change.old_role:
                    role_change.old_role = previous_role
                role_change.status = RoleChangeStatus.EFFECTIVE
                role_change.save(update_fields=["status", "old_role", "updated_at"])
                notify_role_change(
                    chama=membership.chama,
                    membership=membership,
                    old_role=previous_role,
                    new_role=role_change.new_role,
                    outgoing_memberships=outgoing_memberships,
                    actor=actor,
                    reason=role_change.reason,
                )
            activated += 1
        except Exception:  # noqa: BLE001
            logger.exception("Failed applying due role change %s", role_change.id)
            activation_failures += 1

    expiring_changes = RoleChange.objects.select_related("chama", "member").filter(
        status=RoleChangeStatus.EFFECTIVE,
        is_acting=True,
        expiry_date__lt=today,
    )
    for role_change in expiring_changes:
        actor = role_change.approved_by or role_change.created_by
        if not actor or not role_change.old_role:
            expiry_failures += 1
            continue
        try:
            with transaction.atomic():
                membership, previous_role, outgoing_memberships = apply_membership_role_change(
                    chama=role_change.chama,
                    member_user=role_change.member,
                    new_role=role_change.old_role,
                    actor=actor,
                )
                role_change.status = RoleChangeStatus.EXPIRED
                role_change.save(update_fields=["status", "updated_at"])
                notify_role_change(
                    chama=membership.chama,
                    membership=membership,
                    old_role=previous_role,
                    new_role=role_change.old_role,
                    outgoing_memberships=outgoing_memberships,
                    actor=actor,
                    reason="Acting role expired automatically.",
                )
            expired += 1
        except Exception:  # noqa: BLE001
            logger.exception("Failed expiring role change %s", role_change.id)
            expiry_failures += 1

    return {
        "activated": activated,
        "expired": expired,
        "activation_failures": activation_failures,
        "expiry_failures": expiry_failures,
    }
