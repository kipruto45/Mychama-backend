from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.automations.models import AutomationRule, JobRun, JobRunStatus, ScheduledJob
from apps.chama.models import (
    Membership,
    MembershipRequest,
    MembershipRequestStatus,
    MembershipRole,
    MemberStatus,
)
from apps.chama.services import get_effective_role
from apps.finance.models import ContributionSchedule, Expense, InstallmentSchedule
from apps.governance.models import ApprovalRequest, ApprovalStatus
from apps.meetings.models import Meeting
from apps.notifications.models import (
    BroadcastAnnouncement,
    BroadcastAnnouncementStatus,
    DeviceToken,
    Notification,
    NotificationInboxStatus,
    NotificationLog,
    NotificationPreference,
)
from apps.payments.models import PaymentIntent, PaymentIntentStatus, PaymentIntentType

ADMIN_ROLES = {
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.ADMIN,
    MembershipRole.TREASURER,
    MembershipRole.SECRETARY,
    MembershipRole.AUDITOR,
}


def _scoped_memberships(request):
    memberships = Membership.objects.select_related("chama").filter(
        user=request.user,
        is_active=True,
        is_approved=True,
        status=MemberStatus.ACTIVE,
        exited_at__isnull=True,
    )
    scoped_chama_id = request.query_params.get("chama_id") or request.headers.get("X-CHAMA-ID")
    if scoped_chama_id:
        memberships = memberships.filter(chama_id=scoped_chama_id)
    return list(memberships.order_by("-updated_at", "-joined_at"))


def _decimal_to_str(value):
    if value is None:
        return "0.00"
    if isinstance(value, Decimal):
        return format(value.quantize(Decimal("0.01")), "f")
    return str(value)


def _action(*, route=None, label=None, params=None):
    return {
        "route": route,
        "label": label,
        "params": params or {},
    }


def _queue_item(*, key, title, description, severity, badge_count=0, route=None, params=None):
    return {
        "id": key,
        "key": key,
        "title": title,
        "description": description,
        "severity": severity,
        "badge_count": badge_count,
        "action": _action(route=route, label=title, params=params),
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def automation_hub_view(request):
    memberships = _scoped_memberships(request)
    now = timezone.now()
    today = timezone.localdate()

    if not memberships:
        return Response(
            {
                "scope": {
                    "chama_id": None,
                    "chama_name": None,
                    "active_chamas": 0,
                    "is_admin_scope": False,
                },
                "summary": {
                    "my_due_contributions": 0,
                    "my_overdue_loans": 0,
                    "meetings_next_24h": 0,
                    "unread_notifications": 0,
                    "pending_membership_approvals": 0,
                    "pending_finance_approvals": 0,
                    "failed_deliveries": 0,
                    "jobs_needing_attention": 0,
                },
                "channel_status": {
                    "in_app_enabled": True,
                    "email_enabled": True,
                    "sms_enabled": False,
                    "quiet_hours_start": "21:00:00",
                    "quiet_hours_end": "07:00:00",
                    "active_devices": 0,
                },
                "job_health": {
                    "enabled_jobs": 0,
                    "recent_failures": 0,
                    "stale_jobs": 0,
                    "recent_runs": [],
                },
                "queue": [],
                "recommendations": [
                    _queue_item(
                        key="create-chama",
                        title="Create your first chama",
                        description="Automation starts working after your chama has members, meetings, and finance activity.",
                        severity="info",
                        route="CreateChama",
                    )
                ],
                "rules": [],
            }
        )

    chama_ids = [membership.chama_id for membership in memberships]
    effective_roles = {
        membership.chama_id: get_effective_role(request.user, membership.chama_id, membership)
        for membership in memberships
    }
    admin_memberships = [membership for membership in memberships if effective_roles[membership.chama_id] in ADMIN_ROLES]
    admin_chama_ids = [membership.chama_id for membership in admin_memberships]
    primary_membership = memberships[0]
    primary_admin_membership = admin_memberships[0] if admin_memberships else None
    primary_chama = primary_membership.chama
    primary_admin_chama_id = str(primary_admin_membership.chama_id) if primary_admin_membership else None

    my_due_contributions = ContributionSchedule.objects.filter(
        chama_id__in=chama_ids,
        member=request.user,
        is_active=True,
        status="pending",
        scheduled_date__lte=today,
    ).count()
    my_overdue_loans = InstallmentSchedule.objects.filter(
        loan__chama_id__in=chama_ids,
        loan__member=request.user,
        status="overdue",
    ).count()
    meetings_next_24h = Meeting.objects.filter(
        chama_id__in=chama_ids,
        date__gte=now,
        date__lte=now + timedelta(hours=24),
        cancelled_at__isnull=True,
    ).count()
    unread_notifications = Notification.objects.filter(
        recipient=request.user,
        inbox_status=NotificationInboxStatus.UNREAD,
    ).count()

    pending_membership_approvals = MembershipRequest.objects.filter(
        chama_id__in=admin_chama_ids,
        status__in=[MembershipRequestStatus.PENDING, MembershipRequestStatus.NEEDS_INFO],
    ).count()
    pending_finance_approvals = ApprovalRequest.objects.filter(
        chama_id__in=admin_chama_ids,
        status=ApprovalStatus.PENDING,
    ).count()
    pending_expenses = Expense.objects.filter(
        chama_id__in=admin_chama_ids,
        status__in=["pending", "pending_approval"],
    ).count()
    pending_withdrawals = PaymentIntent.objects.filter(
        chama_id__in=admin_chama_ids,
        intent_type=PaymentIntentType.WITHDRAWAL,
        status__in=[PaymentIntentStatus.INITIATED, PaymentIntentStatus.PENDING],
    ).count()
    failed_deliveries = NotificationLog.objects.filter(
        notification__chama_id__in=admin_chama_ids,
        status="failed",
    ).count()
    pending_broadcasts = BroadcastAnnouncement.objects.filter(
        chama_id__in=admin_chama_ids,
        status=BroadcastAnnouncementStatus.PENDING,
    ).count()

    enabled_jobs = ScheduledJob.objects.filter(is_enabled=True)
    enabled_jobs_count = enabled_jobs.count()
    recent_failures = JobRun.objects.filter(
        status=JobRunStatus.FAILED,
        started_at__gte=now - timedelta(days=7),
    ).count()
    successful_job_names = set(
        JobRun.objects.filter(
            status=JobRunStatus.SUCCESS,
            started_at__gte=now - timedelta(days=2),
        ).values_list("job__name", flat=True)
    )
    stale_jobs = enabled_jobs.exclude(name__in=successful_job_names).count()
    jobs_needing_attention = recent_failures + stale_jobs

    preference = NotificationPreference.objects.filter(
        user=request.user,
        chama=primary_chama,
    ).first()
    active_devices = DeviceToken.objects.filter(user=request.user, is_active=True).count()

    recent_runs = [
        {
            "id": str(run.id),
            "job_name": run.job.name,
            "description": run.job.description,
            "status": run.status,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "error": run.error,
        }
        for run in JobRun.objects.select_related("job").order_by("-started_at")[:8]
    ]

    queue = []
    if my_due_contributions:
        queue.append(
            _queue_item(
                key="my-contributions",
                title="Your contribution reminder is due",
                description="You have due contribution items waiting. Posting them now improves compliance and avoids penalties.",
                severity="warning",
                badge_count=my_due_contributions,
                route="MakeContribution",
                params={"chamaId": str(primary_chama.id)} if len(chama_ids) == 1 else {},
            )
        )
    if my_overdue_loans:
        queue.append(
            _queue_item(
                key="my-overdue-loans",
                title="Loan repayment follow-up needed",
                description="One or more of your loan installments are overdue and should be addressed soon.",
                severity="critical",
                badge_count=my_overdue_loans,
                route="Finance",
            )
        )
    if meetings_next_24h:
        queue.append(
            _queue_item(
                key="meeting-reminders",
                title="Meeting reminders due soon",
                description="Upcoming meetings are within the next 24 hours. This is the right time to nudge members.",
                severity="info",
                badge_count=meetings_next_24h,
                route="Meetings",
            )
        )
    if pending_membership_approvals and primary_admin_chama_id:
        queue.append(
            _queue_item(
                key="membership-approvals",
                title="Membership approvals waiting",
                description="Join requests are pending review and could block onboarding momentum.",
                severity="warning",
                badge_count=pending_membership_approvals,
                route="MembershipRequests",
                params={"chamaId": primary_admin_chama_id},
            )
        )
    if pending_expenses and primary_admin_chama_id:
        queue.append(
            _queue_item(
                key="expense-approvals",
                title="Expense requests waiting",
                description="Expense approvals and payment posting are pending.",
                severity="warning",
                badge_count=pending_expenses,
                route="Expenses",
                params={"chamaId": primary_admin_chama_id},
            )
        )
    if pending_withdrawals and primary_admin_chama_id:
        queue.append(
            _queue_item(
                key="withdrawal-approvals",
                title="Withdrawal approvals waiting",
                description="Money-out requests need approval or disbursement follow-through.",
                severity="critical",
                badge_count=pending_withdrawals,
                route="Withdrawals",
                params={"chamaId": primary_admin_chama_id},
            )
        )
    if failed_deliveries:
        queue.append(
            _queue_item(
                key="failed-deliveries",
                title="Some notifications failed delivery",
                description="Important reminders may not have reached members. Review preferences and retry channels.",
                severity="critical",
                badge_count=failed_deliveries,
                route="Notifications",
            )
        )

    recommendations = []
    if preference and not preference.sms_enabled:
        recommendations.append(
            _queue_item(
                key="enable-sms",
                title="Consider enabling SMS alerts",
                description="SMS is useful for overdue contributions and urgent meeting reminders when members miss push notifications.",
                severity="info",
                route="Settings",
            )
        )
    if jobs_needing_attention:
        recommendations.append(
            _queue_item(
                key="job-health",
                title="Automation job health needs review",
                description="Some scheduled jobs are stale or have failed recently, which can weaken reminders and escalation flows.",
                severity="warning",
                badge_count=jobs_needing_attention,
                route="Notifications",
            )
        )
    if pending_broadcasts and primary_admin_chama_id:
        recommendations.append(
            _queue_item(
                key="pending-broadcasts",
                title="Scheduled broadcasts are pending",
                description="There are announcements waiting to be sent or processed.",
                severity="info",
                badge_count=pending_broadcasts,
                route="CommunicationCenter",
                params={"chamaId": primary_admin_chama_id},
            )
        )
    if not recommendations:
        recommendations.append(
            _queue_item(
                key="steady-state",
                title="Automation is in a healthy state",
                description="No urgent reminder backlogs or delivery problems were detected in your current scope.",
                severity="success",
                route="AIChat",
            )
        )

    rules = [
        {
            "id": str(rule.id),
            "rule_type": rule.rule_type,
            "is_enabled": rule.is_enabled,
            "config": rule.config or {},
        }
        for rule in AutomationRule.objects.filter(chama=primary_chama).order_by("rule_type")[:20]
    ]

    return Response(
        {
            "scope": {
                "chama_id": str(primary_chama.id),
                "chama_name": primary_chama.name,
                "active_chamas": len(chama_ids),
                "is_admin_scope": bool(admin_memberships),
            },
            "summary": {
                "my_due_contributions": my_due_contributions,
                "my_overdue_loans": my_overdue_loans,
                "meetings_next_24h": meetings_next_24h,
                "unread_notifications": unread_notifications,
                "pending_membership_approvals": pending_membership_approvals,
                "pending_finance_approvals": pending_finance_approvals + pending_expenses + pending_withdrawals,
                "failed_deliveries": failed_deliveries,
                "jobs_needing_attention": jobs_needing_attention,
            },
            "channel_status": {
                "in_app_enabled": preference.in_app_enabled if preference else True,
                "email_enabled": preference.email_enabled if preference else True,
                "sms_enabled": preference.sms_enabled if preference else False,
                "quiet_hours_start": preference.quiet_hours_start.isoformat() if preference else "21:00:00",
                "quiet_hours_end": preference.quiet_hours_end.isoformat() if preference else "07:00:00",
                "active_devices": active_devices,
            },
            "job_health": {
                "enabled_jobs": enabled_jobs_count,
                "recent_failures": recent_failures,
                "stale_jobs": stale_jobs,
                "recent_runs": recent_runs,
            },
            "queue": queue[:8],
            "recommendations": recommendations[:5],
            "rules": rules,
        }
    )
