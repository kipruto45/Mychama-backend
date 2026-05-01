"""
Unified App API Views - Integration layer for mobile app clients.
"""

from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from apps.billing.gating import require_billing_access, require_feature
from apps.chama.models import (
    Chama,
    Invite,
    InviteStatus,
    Membership,
    MembershipRequest,
    MembershipRequestStatus,
    MembershipRole,
    MemberStatus,
)
from apps.chama.permissions import get_membership
from apps.chama.services import get_effective_role
from apps.finance.models import (
    Contribution,
    ContributionSchedule,
    Expense,
    FinancialSnapshot,
    InstallmentSchedule,
    LedgerEntry,
    Loan,
    Penalty,
    Repayment,
    Wallet,
)
from apps.fines.models import Fine, FineStatus
from apps.governance.models import (
    ApprovalRequest,
    ApprovalStatus,
    ChamaRule,
    Motion,
    MotionVote,
    RoleChange,
    RoleChangeStatus,
    RuleAcknowledgment,
    RuleStatus,
)
from apps.issues.models import Issue, IssueStatus
from apps.meetings.models import Attendance, AttendanceStatus, Meeting, MinutesStatus
from apps.notifications.models import (
    Notification,
    NotificationInboxStatus,
    NotificationStatus,
    NotificationType,
)
from apps.payments.models import (
    MpesaB2CPayout,
    MpesaB2CStatus,
    MpesaSTKTransaction,
    PaymentDispute,
    PaymentDisputeStatus,
    PaymentIntent,
    PaymentIntentStatus,
    PaymentIntentType,
    PaymentReconciliationRun,
    ReconciliationRunStatus,
)
from core.algorithms.membership import (
    calculate_loan_eligibility,
    compute_effective_role,
)
from core.algorithms.smart_scoring import (
    compute_contribution_compliance,
    compute_member_reliability_score,
)
from core.models import ActivityLog, AuditLog

# Wallet API constants
CURRENCY = 'KES'
MIN_DEPOSIT = 10  # KES
MAX_DEPOSIT = 150000  # KES
MIN_WITHDRAWAL = 100  # KES
MAX_WITHDRAWAL = 50000  # KES
DAILY_WITHDRAWAL_LIMIT = 150000  # KES
WITHDRAWAL_COOLDOWN_MINUTES = 5


def _active_memberships_for_user(user):
    return (
        Membership.objects.select_related("chama")
        .filter(
            user=user,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        )
        .order_by("-updated_at", "-joined_at")
    )


def _scoped_memberships(request):
    memberships = _active_memberships_for_user(request.user)
    scoped_chama_id = request.query_params.get("chama_id") or request.headers.get("X-CHAMA-ID")
    if scoped_chama_id:
        memberships = memberships.filter(chama_id=scoped_chama_id)
    return memberships


def _paginate_queryset(request, queryset, *, default_page_size=20, max_page_size=100):
    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except (TypeError, ValueError):
        raise ValidationError({"page": "page must be a positive integer."})

    try:
        page_size = min(
            max_page_size,
            max(1, int(request.query_params.get("page_size", default_page_size))),
        )
    except (TypeError, ValueError):
        raise ValidationError(
            {"page_size": "page_size must be a positive integer."}
        )

    total_count = queryset.count()
    offset = (page - 1) * page_size
    items = queryset[offset : offset + page_size]
    return page, page_size, total_count, items


def _parse_iso_date_param(value, field_name):
    if not value:
        return None
    try:
        parsed = timezone.datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ValidationError({field_name: f"{field_name} must be a valid ISO datetime."}) from exc
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _require_admin_or_auditor(request, chama_id):
    membership = get_membership(request.user, chama_id)
    if not membership:
        raise PermissionDenied("You are not an approved active member of this chama.")
    role = get_effective_role(request.user, chama_id, membership)
    if role not in {MembershipRole.CHAMA_ADMIN, MembershipRole.ADMIN, MembershipRole.AUDITOR}:
        raise PermissionDenied("Only chama admin or auditor can access this data.")
    return membership


def _serialize_audit_like(log):
    return {
        "id": str(log.id),
        "actor_id": str(log.actor_id) if log.actor_id else "",
        "actor_name": getattr(log.actor, "full_name", "") if getattr(log, "actor", None) else "System",
        "action": log.action,
        "entity_type": log.entity_type,
        "entity_id": str(log.entity_id) if log.entity_id else "",
        "metadata": log.metadata or {},
        "trace_id": log.trace_id,
        "created_at": log.created_at.isoformat(),
    }


ADMIN_SCOPE_ROLES = {
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.ADMIN,
    MembershipRole.TREASURER,
    MembershipRole.SECRETARY,
    MembershipRole.AUDITOR,
}
ATTENDED_STATUSES = {AttendanceStatus.PRESENT, AttendanceStatus.LATE}
OPEN_ISSUE_STATUSES = {
    IssueStatus.OPEN,
    IssueStatus.PENDING_ASSIGNMENT,
    IssueStatus.ASSIGNED,
    IssueStatus.CLARIFICATION_REQUESTED,
    IssueStatus.UNDER_INVESTIGATION,
    IssueStatus.IN_PROGRESS,
    IssueStatus.RESOLUTION_PROPOSED,
    IssueStatus.AWAITING_CHAIRPERSON_APPROVAL,
    IssueStatus.REOPENED,
    IssueStatus.ESCALATED,
    IssueStatus.IN_VOTE,
}
OPEN_PAYMENT_DISPUTE_STATUSES = {
    PaymentDisputeStatus.OPEN,
    PaymentDisputeStatus.IN_REVIEW,
}
PENDING_PAYMENT_STATUSES = {
    PaymentIntentStatus.INITIATED,
    PaymentIntentStatus.PENDING,
}
UNPAID_FINE_STATUSES = {
    FineStatus.PENDING,
    FineStatus.DUE,
    FineStatus.OVERDUE,
    FineStatus.DISPUTED,
}
ACTIVE_LOAN_STATUSES = {
    "requested",
    "review",
    "approved",
    "disbursing",
    "disbursed",
    "active",
    "due_soon",
    "overdue",
    "restructured",
    "defaulted",
    "defaulted_recovering",
}
OVERDUE_LOAN_STATUSES = {"overdue", "defaulted", "defaulted_recovering"}
COMPLETED_LOAN_STATUSES = {
    "paid",
    "closed",
    "cleared",
    "recovered_from_offset",
    "recovered_from_guarantor",
}
ROLE_WORKSPACE_META = {
    "member": {
        "title": "Member Workspace",
        "summary": "Track personal savings, dues, reliability, and participation.",
        "reports": ["FinanceReportDetail", "AIChat"],
    },
    "treasurer": {
        "title": "Treasurer Workspace",
        "summary": "Monitor liquidity, approvals, reconciliation, and contribution collection.",
        "reports": ["Finance", "ApprovalsCenter"],
    },
    "chairperson": {
        "title": "Chairperson Workspace",
        "summary": "Guide approvals, policy decisions, and chama health priorities.",
        "reports": ["Governance", "ApprovalsCenter"],
    },
    "secretary": {
        "title": "Secretary Workspace",
        "summary": "Keep meetings, minutes, announcements, and governance follow-through on track.",
        "reports": ["Meetings", "Governance"],
    },
    "auditor": {
        "title": "Auditor Workspace",
        "summary": "Review disputes, reconciliations, and audit-sensitive activity.",
        "reports": ["ApprovalsCenter", "SupportIssues"],
    },
    "platform_super_admin": {
        "title": "Platform Super Admin",
        "summary": "Watch operational health across managed chamas and escalations.",
        "reports": ["Dashboard", "ApprovalsCenter"],
    },
}


def _decimal_to_str(value):
    if value is None:
        return "0.00"
    if isinstance(value, Decimal):
        return format(value.quantize(Decimal("0.01")), "f")
    return str(value)


def _clamp_score(value, minimum=0, maximum=100):
    try:
        numeric = int(round(float(value)))
    except (TypeError, ValueError):
        numeric = minimum
    return max(minimum, min(maximum, numeric))


def _build_route(route=None, *, label=None, params=None):
    return {
        "label": label,
        "route": route,
        "params": params or {},
    }


def _serialize_insight(*, key, title, message, severity, metric=None, action_label=None, action_route=None, action_params=None):
    return {
        "id": key,
        "key": key,
        "title": title,
        "message": message,
        "severity": severity,
        "metric": metric,
        "action": _build_route(action_route, label=action_label, params=action_params),
    }


def _serialize_action(*, key, title, description, priority, route=None, params=None, badge_count=0):
    return {
        "id": key,
        "key": key,
        "title": title,
        "description": description,
        "priority": priority,
        "badge_count": badge_count,
        "action": _build_route(route, label=title, params=params),
    }


def _serialize_approval_queue_item(
    *,
    key,
    item_type,
    title,
    description,
    status,
    chama,
    route=None,
    params=None,
    amount=None,
    currency=None,
    created_at=None,
    requested_by=None,
    severity="medium",
):
    return {
        "id": key,
        "item_type": item_type,
        "title": title,
        "description": description,
        "status": status,
        "severity": severity,
        "amount": _decimal_to_str(amount) if amount is not None else None,
        "currency": currency,
        "created_at": created_at.isoformat() if created_at else None,
        "requested_by": requested_by,
        "chama_id": str(chama.id),
        "chama_name": chama.name,
        "action": _build_route(route, label=title, params=params),
    }


def _count_anomaly_items(payload):
    if isinstance(payload, dict):
        return sum(_count_anomaly_items(value) for value in payload.values())
    if isinstance(payload, list | tuple | set):
        return len(payload)
    if isinstance(payload, bool):
        return int(payload)
    if isinstance(payload, int | float | Decimal):
        return max(0, int(payload))
    return 0


def _workspace_key_for_membership(membership, effective_role):
    if membership.role == MembershipRole.SUPERADMIN:
        return "platform_super_admin"
    if membership.role in {MembershipRole.ADMIN, MembershipRole.CHAMA_ADMIN} or effective_role == MembershipRole.CHAMA_ADMIN:
        return "chairperson"
    if effective_role == MembershipRole.TREASURER:
        return "treasurer"
    if effective_role == MembershipRole.SECRETARY:
        return "secretary"
    if effective_role == MembershipRole.AUDITOR:
        return "auditor"
    return "member"


def _build_member_chama_profile(membership):
    user = membership.user
    chama = membership.chama
    effective_role = get_effective_role(user, chama.id, membership) or membership.role

    contributions_qs = Contribution.objects.filter(chama=chama, member=user)
    contribution_total = contributions_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    contribution_count = contributions_qs.count()

    month_start = timezone.localdate().replace(day=1)
    monthly_contribution_total = (
        contributions_qs.filter(date_paid__gte=month_start).aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )

    schedules = list(
        ContributionSchedule.objects.select_related("contribution")
        .filter(chama=chama, member=user, is_active=True)
        .order_by("-scheduled_date")[:24]
    )
    schedule_history = []
    for schedule in reversed(schedules):
        schedule_history.append(
            {
                "status": schedule.status if schedule.status in {"paid", "missed", "pending"} else "pending",
                "due_date": schedule.scheduled_date,
                "paid_date": schedule.contribution.date_paid if schedule.contribution_id else None,
            }
        )
    if not schedule_history:
        contribution_dates = list(
            contributions_qs.order_by("-date_paid").values_list("date_paid", flat=True)[:12]
        )
        schedule_history = [
            {"status": "paid", "due_date": paid_date, "paid_date": paid_date}
            for paid_date in reversed(contribution_dates)
        ]

    contribution_compliance = compute_contribution_compliance(
        member_id=str(user.id),
        chama_id=str(chama.id),
        contribution_history=schedule_history,
        contribution_schedule={
            "grace_period_days": getattr(getattr(chama, "contribution_setting", None), "grace_period_days", 0)
        },
    )

    pending_due_schedules = [
        item
        for item in schedules
        if item.status == "pending" and item.scheduled_date <= timezone.localdate()
    ]
    next_due_schedule = next(
        (
            item
            for item in sorted(schedules, key=lambda record: record.scheduled_date)
            if item.status == "pending"
        ),
        None,
    )

    loans = list(Loan.objects.filter(chama=chama, member=user).order_by("-requested_at", "-created_at"))
    active_loans = [loan for loan in loans if loan.status in ACTIVE_LOAN_STATUSES]
    overdue_loans = [loan for loan in loans if loan.status in OVERDUE_LOAN_STATUSES]
    outstanding_total = sum(
        (
            (loan.outstanding_principal or Decimal("0.00"))
            + (loan.outstanding_interest or Decimal("0.00"))
            + (loan.outstanding_penalty or Decimal("0.00"))
        )
        for loan in active_loans
    )
    loans_taken_total = sum((loan.principal or Decimal("0.00")) for loan in loans)
    repayments_total = (
        Repayment.objects.filter(loan__in=loans).aggregate(total=Sum("amount"))["total"]
        if loans
        else Decimal("0.00")
    ) or Decimal("0.00")

    fines = list(Fine.objects.filter(chama=chama, member=user).order_by("-created_at"))
    fines_total = sum((fine.amount or Decimal("0.00")) for fine in fines)
    unpaid_fines_total = sum(
        Decimal(str(fine.outstanding_amount))
        for fine in fines
        if fine.status in UNPAID_FINE_STATUSES
    )
    disputed_fines_count = sum(1 for fine in fines if fine.status == FineStatus.DISPUTED)

    penalties_qs = Penalty.objects.filter(chama=chama, member=user)
    penalties_total = penalties_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    unpaid_penalties_total = (
        penalties_qs.filter(status="unpaid").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    )

    attendance_qs = Attendance.objects.filter(meeting__chama=chama, member=user)
    attendance_total = attendance_qs.count()
    attendance_attended = attendance_qs.filter(status__in=ATTENDED_STATUSES).count()
    attendance_rate = round((attendance_attended / attendance_total) * 100, 1) if attendance_total else 0.0

    total_votes = Motion.objects.filter(chama=chama).count()
    votes_cast = MotionVote.objects.filter(motion__chama=chama, user=user).count()
    voting_rate = round((votes_cast / total_votes) * 100, 1) if total_votes else 0.0

    loan_history = [
        {
            "status": loan.status,
            "late_payments": InstallmentSchedule.objects.filter(
                loan=loan,
                status="overdue",
            ).count(),
        }
        for loan in loans
    ]
    attendance_history = [
        {
            "status": "attended" if item.status in ATTENDED_STATUSES else "missed",
        }
        for item in attendance_qs.order_by("-created_at")[:12]
    ]
    reliability = compute_member_reliability_score(
        member_id=str(user.id),
        chama_id=str(chama.id),
        contribution_compliance=contribution_compliance,
        loan_history=loan_history,
        attendance_records=attendance_history,
        participation_data={
            "votes_cast": votes_cast,
            "total_votes": total_votes,
        },
    )

    pending_payments_total = (
        PaymentIntent.objects.filter(chama=chama, status__in=PENDING_PAYMENT_STATUSES)
        .filter(Q(user=user) | Q(created_by=user))
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )

    active_disputes_count = PaymentDispute.objects.filter(
        chama=chama,
        opened_by=user,
        status__in=OPEN_PAYMENT_DISPUTE_STATUSES,
    ).count()
    active_issues_count = Issue.objects.filter(
        chama=chama,
        created_by=user,
        status__in=OPEN_ISSUE_STATUSES,
    ).count()
    pending_acknowledgments = RuleAcknowledgment.objects.filter(
        rule__chama=chama,
        member=user,
        status="pending",
    ).count()

    return {
        "chama": {
            "id": str(chama.id),
            "name": chama.name,
            "currency": chama.currency,
        },
        "role": membership.role,
        "effective_role": effective_role,
        "joined_at": membership.joined_at.isoformat(),
        "contributions": {
            "total": _decimal_to_str(contribution_total),
            "count": contribution_count,
            "this_month": _decimal_to_str(monthly_contribution_total),
            "pending_count": len(pending_due_schedules),
            "missed_count": contribution_compliance.total_missed,
            "next_due_amount": _decimal_to_str(next_due_schedule.amount) if next_due_schedule else "0.00",
            "next_due_date": next_due_schedule.scheduled_date.isoformat() if next_due_schedule else None,
            "compliance_rate": float(contribution_compliance.compliance_rate),
            "on_time_rate": float(contribution_compliance.on_time_rate),
        },
        "fines": {
            "issued_total": _decimal_to_str(fines_total + penalties_total),
            "unpaid_total": _decimal_to_str(unpaid_fines_total + unpaid_penalties_total),
            "disputed_count": disputed_fines_count,
        },
        "loans": {
            "taken_total": _decimal_to_str(loans_taken_total),
            "repaid_total": _decimal_to_str(repayments_total),
            "outstanding_total": _decimal_to_str(outstanding_total),
            "active_count": len(active_loans),
            "completed_count": sum(1 for loan in loans if loan.status in COMPLETED_LOAN_STATUSES),
            "overdue_count": len(overdue_loans),
        },
        "attendance": {
            "attended": attendance_attended,
            "total": attendance_total,
            "rate": attendance_rate,
        },
        "voting": {
            "votes_cast": votes_cast,
            "total_votes": total_votes,
            "rate": voting_rate,
        },
        "obligations": {
            "loan_exposure": _decimal_to_str(outstanding_total),
            "payable_fines": _decimal_to_str(unpaid_fines_total + unpaid_penalties_total),
            "pending_payments": _decimal_to_str(pending_payments_total),
            "expected_next_due_amount": _decimal_to_str(next_due_schedule.amount) if next_due_schedule else "0.00",
        },
        "reliability": {
            "score": reliability.overall_score,
            "risk_level": reliability.risk_level.value,
            "flags": reliability.risk_flags,
            "recommendations": reliability.recommendations,
        },
        "activity": {
            "open_disputes": active_disputes_count,
            "open_issues": active_issues_count,
            "pending_policy_acknowledgments": pending_acknowledgments,
        },
    }


def _build_role_workspace(membership):
    profile = _build_member_chama_profile(membership)
    workspace_key = _workspace_key_for_membership(membership, profile["effective_role"])
    meta = ROLE_WORKSPACE_META[workspace_key]
    chama_id = membership.chama_id

    pending_approvals = (
        MembershipRequest.objects.filter(
            chama_id=chama_id,
            status__in=[MembershipRequestStatus.PENDING, MembershipRequestStatus.NEEDS_INFO],
        ).count()
        + ApprovalRequest.objects.filter(chama_id=chama_id, status=ApprovalStatus.PENDING).count()
        + Expense.objects.filter(chama_id=chama_id, status__in=["pending", "pending_approval"]).count()
        + Loan.objects.filter(chama_id=chama_id, status__in=["requested", "pending", "review"]).count()
        + PaymentIntent.objects.filter(
            chama_id=chama_id,
            intent_type=PaymentIntentType.WITHDRAWAL,
            status__in=PENDING_PAYMENT_STATUSES,
        ).count()
    )
    open_disputes = (
        PaymentDispute.objects.filter(chama_id=chama_id, status__in=OPEN_PAYMENT_DISPUTE_STATUSES).count()
        + Issue.objects.filter(chama_id=chama_id, status__in=OPEN_ISSUE_STATUSES).count()
    )
    reconciliation_alerts = 0
    recent_reconciliation = (
        PaymentReconciliationRun.objects.filter(chama_id=chama_id)
        .order_by("-run_at", "-created_at")
        .first()
    )
    if recent_reconciliation:
        reconciliation_alerts = _count_anomaly_items(recent_reconciliation.anomalies)
        if recent_reconciliation.status in {ReconciliationRunStatus.PARTIAL, ReconciliationRunStatus.FAILED}:
            reconciliation_alerts = max(reconciliation_alerts, 1)

    alerts = []
    if profile["contributions"]["pending_count"] > 0:
        alerts.append(
            {
                "id": f"{workspace_key}:due:{chama_id}",
                "severity": "warning",
                "title": "Contribution follow-up needed",
                "message": f"{profile['contributions']['pending_count']} contribution item(s) are due for {membership.chama.name}.",
            }
        )
    if profile["loans"]["overdue_count"] > 0:
        alerts.append(
            {
                "id": f"{workspace_key}:loan:{chama_id}",
                "severity": "critical",
                "title": "Overdue loan exposure",
                "message": f"{profile['loans']['overdue_count']} loan(s) need recovery attention in {membership.chama.name}.",
            }
        )
    if pending_approvals > 0 and workspace_key in {"treasurer", "chairperson", "secretary", "auditor", "platform_super_admin"}:
        alerts.append(
            {
                "id": f"{workspace_key}:approvals:{chama_id}",
                "severity": "info",
                "title": "Pending approvals queue",
                "message": f"{pending_approvals} operational decision(s) are waiting in the approvals center.",
            }
        )
    if reconciliation_alerts > 0 and workspace_key in {"treasurer", "auditor", "platform_super_admin"}:
        alerts.append(
            {
                "id": f"{workspace_key}:reconciliation:{chama_id}",
                "severity": "warning",
                "title": "Reconciliation items require review",
                "message": f"{reconciliation_alerts} reconciliation anomaly signal(s) were found for {membership.chama.name}.",
            }
        )

    reports = []
    for route_name in meta["reports"]:
        params = {"chamaId": str(chama_id)} if route_name not in {"Dashboard", "AIChat"} else {}
        if route_name == "FinanceReportDetail":
            params = {
                "reportType": "member-statement",
                "chamaId": str(chama_id),
                "memberId": str(membership.user_id),
            }
        reports.append(_build_route(route_name, label=route_name.replace("_", " "), params=params))

    recommended_actions = [
        _serialize_action(
            key=f"{workspace_key}:finance:{chama_id}",
            title="Open finance workspace",
            description="Review live balances, payment activity, and member obligations.",
            priority="high" if workspace_key in {"treasurer", "chairperson"} else "medium",
            route="Finance",
            params={},
        ),
        _serialize_action(
            key=f"{workspace_key}:approvals:{chama_id}",
            title="Review pending actions",
            description="Clear approvals, disputes, and follow-up tasks from the shared queue.",
            priority="high",
            route="ApprovalsCenter",
            params={"chamaId": str(chama_id)},
            badge_count=pending_approvals + open_disputes,
        ),
        _serialize_action(
            key=f"{workspace_key}:governance:{chama_id}",
            title="Open governance center",
            description="Review policies, meetings, and searchable decisions for this chama.",
            priority="medium",
            route="Governance",
            params={"chamaId": str(chama_id)},
            badge_count=profile["activity"]["pending_policy_acknowledgments"],
        ),
    ]

    return {
        "workspace_key": workspace_key,
        "workspace_label": meta["title"],
        "workspace_summary": meta["summary"],
        "role": membership.role,
        "effective_role": profile["effective_role"],
        "chama_id": str(chama_id),
        "chama_name": membership.chama.name,
        "currency": membership.chama.currency,
        "metrics": {
            "pending_contributions": profile["contributions"]["pending_count"],
            "pending_approvals": pending_approvals,
            "overdue_loans": profile["loans"]["overdue_count"],
            "open_disputes": open_disputes,
            "policy_acknowledgments_due": profile["activity"]["pending_policy_acknowledgments"],
            "reliability_score": profile["reliability"]["score"],
            "reconciliation_alerts": reconciliation_alerts,
        },
        "alerts": alerts[:3],
        "recommended_actions": recommended_actions,
        "reports": reports,
    }


def _build_attendance_metrics(chama_ids, active_member_counts):
    recent_meetings = list(
        Meeting.objects.filter(
            chama_id__in=chama_ids,
            date__lt=timezone.now(),
            cancelled_at__isnull=True,
        )
        .order_by("-date")[:6]
    )
    if not recent_meetings:
        return {
            "recent_average": None,
            "previous_average": None,
            "trend_delta": 0,
            "score": 70,
        }

    attendance_counts = {
        row["meeting_id"]: row["present_count"]
        for row in Attendance.objects.filter(
            meeting_id__in=[meeting.id for meeting in recent_meetings],
            status__in=[AttendanceStatus.PRESENT, AttendanceStatus.LATE],
        )
        .values("meeting_id")
        .annotate(present_count=Count("id"))
    }

    rates = []
    for meeting in recent_meetings:
        denominator = max(active_member_counts.get(meeting.chama_id, 0), 1)
        present_count = attendance_counts.get(meeting.id, 0)
        rates.append((present_count / denominator) * 100)

    midpoint = min(3, len(rates))
    recent_average = sum(rates[:midpoint]) / midpoint if midpoint else 0
    previous_slice = rates[midpoint : midpoint * 2]
    previous_average = (
        sum(previous_slice) / len(previous_slice)
        if previous_slice
        else recent_average
    )
    trend_delta = recent_average - previous_average
    return {
        "recent_average": round(recent_average, 1),
        "previous_average": round(previous_average, 1),
        "trend_delta": round(trend_delta, 1),
        "score": _clamp_score(recent_average),
    }


def _build_dashboard_intelligence(*, user, memberships, chama_ids, total_savings, chama_balance, monthly_contributions, monthly_expenses):
    today = timezone.localdate()
    month_start = today.replace(day=1)
    effective_roles = {}
    active_member_counts = {}
    admin_scoped_memberships = []
    for membership in memberships:
        effective_role = get_effective_role(user, membership.chama_id, membership)
        effective_roles[membership.chama_id] = effective_role
        active_member_counts[membership.chama_id] = membership.chama.memberships.filter(
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        ).count()
        if effective_role in ADMIN_SCOPE_ROLES:
            admin_scoped_memberships.append(membership)

    pending_contribution_qs = ContributionSchedule.objects.filter(
        chama_id__in=chama_ids,
        status="pending",
        is_active=True,
        scheduled_date__lte=today,
    )
    overdue_contribution_qs = pending_contribution_qs.filter(scheduled_date__lt=today)
    user_pending_contributions = pending_contribution_qs.filter(member=user).count()
    overdue_contributions = overdue_contribution_qs.count()

    current_month_schedules = ContributionSchedule.objects.filter(
        chama_id__in=chama_ids,
        scheduled_date__gte=month_start,
        scheduled_date__lte=today,
        is_active=True,
    )
    current_month_due_count = current_month_schedules.count()
    current_month_paid_count = current_month_schedules.filter(status="paid").count()
    contribution_completion_rate = (
        (current_month_paid_count / current_month_due_count) * 100
        if current_month_due_count
        else 100
    )

    overdue_installments = InstallmentSchedule.objects.filter(
        loan__chama_id__in=chama_ids,
        status="overdue",
    )
    overdue_loans_count = overdue_installments.values("loan_id").distinct().count()
    overdue_user_loans_count = overdue_installments.filter(loan__member=user).values("loan_id").distinct().count()

    unpaid_penalties_qs = Penalty.objects.filter(
        chama_id__in=chama_ids,
        status__in=["unpaid", "pending"],
    )
    unpaid_penalties_count = unpaid_penalties_qs.count()
    unpaid_penalties_total = unpaid_penalties_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    user_unpaid_penalties = unpaid_penalties_qs.filter(member=user).count()

    pending_expense_qs = Expense.objects.filter(
        chama_id__in=chama_ids,
        status__in=["pending", "pending_approval"],
    )
    pending_expenses_count = pending_expense_qs.count()
    pending_expenses_total = pending_expense_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

    pending_withdrawal_qs = PaymentIntent.objects.filter(
        chama_id__in=chama_ids,
        intent_type=PaymentIntentType.WITHDRAWAL,
        status__in=[PaymentIntentStatus.INITIATED, PaymentIntentStatus.PENDING],
    )
    pending_withdrawals_count = pending_withdrawal_qs.count()
    pending_withdrawals_total = pending_withdrawal_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

    pending_disbursement_qs = PaymentIntent.objects.filter(
        chama_id__in=chama_ids,
        intent_type=PaymentIntentType.LOAN_DISBURSEMENT,
        status__in=[PaymentIntentStatus.INITIATED, PaymentIntentStatus.PENDING],
    )
    pending_disbursements_count = pending_disbursement_qs.count()

    requested_loans_qs = Loan.objects.filter(chama_id__in=chama_ids, status__in=["requested", "pending", "review"])
    requested_loans_count = requested_loans_qs.count()
    requested_loans_total = requested_loans_qs.aggregate(total=Sum("principal"))["total"] or Decimal("0.00")

    pending_approvals_qs = ApprovalRequest.objects.filter(
        chama_id__in=chama_ids,
        status=ApprovalStatus.PENDING,
    )
    pending_approvals_count = pending_approvals_qs.count()

    pending_join_requests_count = MembershipRequest.objects.filter(
        chama_id__in=chama_ids,
        status__in=[MembershipRequestStatus.PENDING, MembershipRequestStatus.NEEDS_INFO],
    ).count()

    pending_minutes_count = Meeting.objects.filter(
        chama_id__in=chama_ids,
        minutes_status=MinutesStatus.PENDING_APPROVAL,
    ).count()

    open_issues_count = Issue.objects.filter(
        chama_id__in=chama_ids,
        status__in=[
            IssueStatus.OPEN,
            IssueStatus.PENDING_ASSIGNMENT,
            IssueStatus.ASSIGNED,
            IssueStatus.CLARIFICATION_REQUESTED,
            IssueStatus.UNDER_INVESTIGATION,
            IssueStatus.IN_PROGRESS,
            IssueStatus.RESOLUTION_PROPOSED,
            IssueStatus.AWAITING_CHAIRPERSON_APPROVAL,
            IssueStatus.REOPENED,
            IssueStatus.ESCALATED,
            IssueStatus.IN_VOTE,
        ],
    ).count()

    failed_payouts_count = MpesaB2CPayout.objects.filter(
        chama_id__in=chama_ids,
        status__in=[MpesaB2CStatus.FAILED, MpesaB2CStatus.TIMEOUT],
    ).count()

    failed_notifications_count = Notification.objects.filter(
        chama_id__in=chama_ids,
        recipient=user,
        status="failed",
    ).count()

    attendance_metrics = _build_attendance_metrics(chama_ids, active_member_counts)

    obligations = monthly_expenses + pending_withdrawals_total + requested_loans_total
    if obligations <= Decimal("0.00"):
        liquidity_health = 100
    else:
        liquidity_ratio = float(chama_balance / obligations) if obligations else 1.0
        liquidity_health = _clamp_score(min(liquidity_ratio / 1.5, 1) * 100)

    member_reliability = _clamp_score(
        96
        - (user_pending_contributions * 12)
        - (user_unpaid_penalties * 8)
        - (overdue_user_loans_count * 18)
    )
    loan_eligibility_score = _clamp_score(
        member_reliability
        - (overdue_user_loans_count * 12)
        - (user_unpaid_penalties * 6)
        + (8 if user_pending_contributions == 0 else 0)
    )
    default_risk_score = _clamp_score((overdue_loans_count * 20) + (failed_payouts_count * 12) + (open_issues_count * 6))
    approval_backlog_score = _clamp_score(
        100
        - (pending_approvals_count * 10)
        - (pending_join_requests_count * 6)
        - (pending_expenses_count * 5)
        - (pending_withdrawals_count * 5)
    )
    chama_health_score = _clamp_score(
        (contribution_completion_rate * 0.35)
        + (liquidity_health * 0.25)
        + (attendance_metrics["score"] * 0.15)
        + (max(0, 100 - (overdue_loans_count * 15)) * 0.15)
        + (approval_backlog_score * 0.10)
    )

    scope_label = memberships[0].chama.name if len(memberships) == 1 else "Your chamas"
    summary_headline = f"{scope_label} collected KES {_decimal_to_str(monthly_contributions)} this month"
    summary_body = (
        f"{overdue_contributions} overdue contribution item(s), "
        f"{overdue_loans_count} overdue loan(s), and "
        f"{pending_approvals_count + pending_expenses_count + pending_withdrawals_count} finance approval item(s) need follow-up."
    )

    first_admin_membership = admin_scoped_memberships[0] if admin_scoped_memberships else None
    admin_chama_id = str(first_admin_membership.chama_id) if first_admin_membership else None

    smart_insights = []
    if overdue_contributions:
        smart_insights.append(
            _serialize_insight(
                key="overdue-contributions",
                title=f"{overdue_contributions} overdue contributions need follow-up",
                message="Members have missed expected contribution dates. Review compliance and send reminders before arrears grow.",
                severity="warning",
                metric=str(overdue_contributions),
                action_label="Review compliance",
                action_route="ContributionCompliance",
                action_params={"chamaId": admin_chama_id} if admin_chama_id else {},
            )
        )
    if requested_loans_total > chama_balance and requested_loans_count:
        smart_insights.append(
            _serialize_insight(
                key="liquidity-warning",
                title="Requested loans exceed safe liquidity",
                message="Pending loan requests are higher than current available balance. Approve carefully to avoid cashflow strain.",
                severity="critical",
                metric=f"KES {_decimal_to_str(requested_loans_total)}",
                action_label="Review withdrawals",
                action_route="Withdrawals",
                action_params={"chamaId": admin_chama_id} if admin_chama_id else {},
            )
        )
    if overdue_loans_count:
        smart_insights.append(
            _serialize_insight(
                key="overdue-loans",
                title=f"{overdue_loans_count} loans are overdue",
                message="Loan recovery action is needed. Prioritize follow-up before defaults deepen.",
                severity="warning",
                metric=str(overdue_loans_count),
                action_label="Open finance",
                action_route="Finance",
                action_params={},
            )
        )
    if attendance_metrics["trend_delta"] <= -10:
        smart_insights.append(
            _serialize_insight(
                key="attendance-drop",
                title="Meeting attendance has dropped",
                message=f"Average attendance fell by {abs(attendance_metrics['trend_delta'])}% across recent meetings.",
                severity="info",
                metric=f"{attendance_metrics['recent_average']}%",
                action_label="View meetings",
                action_route="Meetings",
                action_params={},
            )
        )
    if pending_expenses_count:
        smart_insights.append(
            _serialize_insight(
                key="expense-backlog",
                title=f"Expense requests worth KES {_decimal_to_str(pending_expenses_total)} need review",
                message="Expense workflow has pending approvals. Clearing the backlog keeps the ledger and treasury current.",
                severity="info",
                metric=str(pending_expenses_count),
                action_label="Review expenses",
                action_route="Expenses",
                action_params={"chamaId": admin_chama_id} if admin_chama_id else {},
            )
        )
    if not smart_insights:
        smart_insights.append(
            _serialize_insight(
                key="healthy-state",
                title="This chama is in a stable operating state",
                message="Contributions, liquidity, and approvals are within healthy ranges right now.",
                severity="success",
                metric=f"{chama_health_score}/100",
                action_label="Ask AI assistant",
                action_route="AIChat",
                action_params={},
            )
        )

    next_actions = []
    if user_pending_contributions:
        next_actions.append(
            _serialize_action(
                key="make-contribution",
                title="Make contribution",
                description="Your contribution is due. Posting it now improves your compliance score and reliability.",
                priority="high",
                route="MakeContribution",
                params={"chamaId": str(memberships[0].chama_id)} if len(memberships) == 1 else {},
                badge_count=user_pending_contributions,
            )
        )
    if pending_join_requests_count and admin_chama_id:
        next_actions.append(
            _serialize_action(
                key="review-join-requests",
                title="Review join requests",
                description="New members are waiting for approval. Clear the queue to avoid onboarding delays.",
                priority="high",
                route="MembershipRequests",
                params={"chamaId": admin_chama_id},
                badge_count=pending_join_requests_count,
            )
        )
    if pending_expenses_count and admin_chama_id:
        next_actions.append(
            _serialize_action(
                key="approve-expenses",
                title="Approve expenses",
                description="Expense requests are waiting for treasurer/admin review.",
                priority="medium",
                route="Expenses",
                params={"chamaId": admin_chama_id},
                badge_count=pending_expenses_count,
            )
        )
    if pending_withdrawals_count and admin_chama_id:
        next_actions.append(
            _serialize_action(
                key="review-withdrawals",
                title="Review withdrawals",
                description="Withdrawal and disbursement requests need approval before money leaves the treasury.",
                priority="medium",
                route="Withdrawals",
                params={"chamaId": admin_chama_id},
                badge_count=pending_withdrawals_count,
            )
        )
    upcoming_meeting_exists = Meeting.objects.filter(
        chama_id__in=chama_ids,
        date__gte=timezone.now(),
        cancelled_at__isnull=True,
    ).exists()
    if admin_chama_id and not upcoming_meeting_exists:
        next_actions.append(
            _serialize_action(
                key="schedule-meeting",
                title="Schedule meeting",
                description="No upcoming meeting is scheduled. Add one so attendance and governance stay active.",
                priority="medium",
                route="CreateMeeting",
                params={"chamaId": admin_chama_id},
            )
        )
    if not next_actions:
        next_actions.append(
            _serialize_action(
                key="open-ai",
                title="Ask AI assistant",
                description="Get a plain-language finance summary or recommended admin actions.",
                priority="low",
                route="AIChat",
                params={},
            )
        )

    admin_action_items = []
    if pending_join_requests_count and admin_chama_id:
        admin_action_items.append(
            _serialize_action(
                key="admin-join-requests",
                title="Pending join requests",
                description="Membership approvals are waiting.",
                priority="high",
                route="MembershipRequests",
                params={"chamaId": admin_chama_id},
                badge_count=pending_join_requests_count,
            )
        )
    if pending_expenses_count and admin_chama_id:
        admin_action_items.append(
            _serialize_action(
                key="admin-expenses",
                title="Pending expenses",
                description="Expense requests require review or payment posting.",
                priority="high",
                route="Expenses",
                params={"chamaId": admin_chama_id},
                badge_count=pending_expenses_count,
            )
        )
    if pending_withdrawals_count and admin_chama_id:
        admin_action_items.append(
            _serialize_action(
                key="admin-withdrawals",
                title="Pending withdrawals",
                description="Treasury money-out requests are awaiting approval or disbursement.",
                priority="high",
                route="Withdrawals",
                params={"chamaId": admin_chama_id},
                badge_count=pending_withdrawals_count,
            )
        )
    if overdue_contributions and admin_chama_id:
        admin_action_items.append(
            _serialize_action(
                key="admin-compliance",
                title="Overdue contributions",
                description="Compliance arrears need reminders, fines, or follow-up.",
                priority="medium",
                route="ContributionCompliance",
                params={"chamaId": admin_chama_id},
                badge_count=overdue_contributions,
            )
        )

    return {
        "summary": {
            "headline": summary_headline,
            "plain_language": summary_body,
            "period_label": today.strftime("%B %Y"),
        },
        "scores": {
            "member_reliability": member_reliability,
            "loan_eligibility": loan_eligibility_score,
            "chama_health": chama_health_score,
            "liquidity_health": liquidity_health,
            "default_risk": default_risk_score,
            "attendance_score": attendance_metrics["score"],
        },
        "analytics": {
            "monthly_contributions": _decimal_to_str(monthly_contributions),
            "monthly_expenses": _decimal_to_str(monthly_expenses),
            "pending_withdrawals_amount": _decimal_to_str(pending_withdrawals_total),
            "pending_expenses_amount": _decimal_to_str(pending_expenses_total),
            "pending_loan_requests_amount": _decimal_to_str(requested_loans_total),
            "contribution_completion_rate": round(contribution_completion_rate, 1),
            "attendance_recent_average": attendance_metrics["recent_average"],
            "attendance_previous_average": attendance_metrics["previous_average"],
            "attendance_trend_delta": attendance_metrics["trend_delta"],
            "failed_payouts": failed_payouts_count,
            "failed_notifications": failed_notifications_count,
            "pending_disbursements": pending_disbursements_count,
            "unpaid_penalties_total": _decimal_to_str(unpaid_penalties_total),
            "unpaid_penalties_count": unpaid_penalties_count,
        },
        "smart_insights": smart_insights[:5],
        "next_actions": next_actions[:5],
        "admin_action_center": {
            "is_visible": bool(admin_scoped_memberships),
            "pending_approvals": pending_approvals_count,
            "pending_join_requests": pending_join_requests_count,
            "pending_expenses": pending_expenses_count,
            "pending_withdrawals": pending_withdrawals_count,
            "overdue_contributions": overdue_contributions,
            "overdue_loans": overdue_loans_count,
            "pending_minutes_approval": pending_minutes_count,
            "open_issues": open_issues_count,
            "failed_payouts": failed_payouts_count,
            "items": admin_action_items[:4],
        },
        "member_overview": {
            "active_chamas": len(chama_ids),
            "pending_contributions": user_pending_contributions,
            "loan_eligibility": {
                "score": loan_eligibility_score,
                "recommended_state": "eligible" if loan_eligibility_score >= 60 else "review",
            },
        },
        "compliance": {
            "member_reliability_score": member_reliability,
            "pending_contributions": user_pending_contributions,
            "unpaid_penalties": user_unpaid_penalties,
            "overdue_loans": overdue_user_loans_count,
            "contribution_completion_rate": round(contribution_completion_rate, 1),
        },
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@require_billing_access()
def dashboard_summary(request):
    user = request.user
    memberships = list(_scoped_memberships(request))
    chama_ids = [membership.chama_id for membership in memberships]
    if not chama_ids:
        return Response(
            {
                "user": {
                    "id": str(user.id),
                    "name": user.get_full_name() or user.phone,
                    "phone": user.phone,
                },
                "totals": {
                    "total_savings": "0.00",
                    "chama_balance": "0.00",
                    "total_loans": "0.00",
                    "total_expenses": "0.00",
                    "pending_contributions": 0,
                    "unread_notifications": 0,
                },
                "chamas": [],
                "recent_transactions": [],
                "upcoming_meetings": [],
                "announcements": [],
                "trends": {
                    "contributions": [],
                    "expenses": [],
                },
                "loan_stats": {
                    "active_count": 0,
                    "requested_count": 0,
                },
                "smart_summary": {
                    "headline": "You are ready to create or join a chama",
                    "plain_language": "Once you join a chama, your savings, meetings, risks, and admin actions will appear here.",
                    "period_label": timezone.localdate().strftime("%B %Y"),
                },
                "scores": {
                    "member_reliability": 0,
                    "loan_eligibility": 0,
                    "chama_health": 0,
                    "liquidity_health": 0,
                    "default_risk": 0,
                    "attendance_score": 0,
                },
                "analytics": {
                    "monthly_contributions": "0.00",
                    "monthly_expenses": "0.00",
                    "pending_withdrawals_amount": "0.00",
                    "pending_expenses_amount": "0.00",
                    "pending_loan_requests_amount": "0.00",
                    "contribution_completion_rate": 0,
                    "attendance_recent_average": None,
                    "attendance_previous_average": None,
                    "attendance_trend_delta": 0,
                    "failed_payouts": 0,
                    "failed_notifications": 0,
                    "pending_disbursements": 0,
                    "unpaid_penalties_total": "0.00",
                    "unpaid_penalties_count": 0,
                },
                "smart_insights": [
                    _serialize_insight(
                        key="onboarding",
                        title="Create or join your first chama",
                        message="The smart dashboard starts working after you join a chama and real finance activity begins flowing in.",
                        severity="info",
                        action_label="Create chama",
                        action_route="CreateChama",
                    )
                ],
                "next_actions": [
                    _serialize_action(
                        key="create-chama",
                        title="Create a chama",
                        description="Start a new savings group and configure policies with smart defaults.",
                        priority="high",
                        route="CreateChama",
                    )
                ],
                "admin_action_center": {
                    "is_visible": False,
                    "pending_approvals": 0,
                    "pending_join_requests": 0,
                    "pending_expenses": 0,
                    "pending_withdrawals": 0,
                    "overdue_contributions": 0,
                    "overdue_loans": 0,
                    "pending_minutes_approval": 0,
                    "open_issues": 0,
                    "failed_payouts": 0,
                    "items": [],
                },
                "compliance": {
                    "member_reliability_score": 0,
                    "pending_contributions": 0,
                    "unpaid_penalties": 0,
                    "overdue_loans": 0,
                    "contribution_completion_rate": 0,
                },
            }
        )

    latest_snapshots = {}
    snapshot_qs = FinancialSnapshot.objects.filter(chama_id__in=chama_ids).order_by(
        "chama_id", "-snapshot_date", "-created_at"
    )
    for snapshot in snapshot_qs:
        latest_snapshots.setdefault(str(snapshot.chama_id), snapshot)

    total_savings = Decimal("0.00")
    chama_balance = Decimal("0.00")
    total_loans = Decimal("0.00")
    total_expenses = Decimal("0.00")
    chama_rows = []
    for membership in memberships:
        snapshot = latest_snapshots.get(str(membership.chama_id))
        if snapshot:
            total_savings += snapshot.total_contributions
            chama_balance += snapshot.total_balance
            total_loans += snapshot.total_loans
            total_expenses += snapshot.total_expenses
        chama_rows.append(
            {
                "id": str(membership.chama.id),
                "name": membership.chama.name,
                "role": membership.role,
                "effective_role": get_effective_role(user, membership.chama_id, membership),
                "currency": membership.chama.currency,
                "member_count": membership.chama.memberships.filter(
                    is_active=True,
                    is_approved=True,
                    status=MemberStatus.ACTIVE,
                    exited_at__isnull=True,
                ).count(),
            }
        )

    month_start = timezone.localdate().replace(day=1)
    monthly_contributions = (
        Contribution.objects.filter(
            chama_id__in=chama_ids,
            date_paid__gte=month_start,
        ).aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )
    monthly_expenses = (
        Expense.objects.filter(
            chama_id__in=chama_ids,
            expense_date__gte=month_start,
        ).aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )

    pending_schedules = ContributionSchedule.objects.filter(
        chama_id__in=chama_ids,
        member=user,
        status="pending",
        scheduled_date__lte=timezone.localdate(),
        is_active=True,
    )
    pending_contributions = pending_schedules.count()
    unread_notifications = Notification.objects.filter(
        recipient=user,
        inbox_status=NotificationInboxStatus.UNREAD,
    ).count()

    contributions = list(
        Contribution.objects.select_related("chama", "member", "contribution_type")
        .filter(chama_id__in=chama_ids)
        .order_by("-date_paid", "-created_at")[:5]
    )
    expenses = list(
        Expense.objects.select_related("chama")
        .filter(chama_id__in=chama_ids)
        .order_by("-expense_date", "-created_at")[:5]
    )
    recent_transactions = sorted(
        [
            {
                "id": str(item.id),
                "kind": "contribution",
                "amount": str(item.amount),
                "currency": item.chama.currency,
                "description": getattr(item.contribution_type, "name", "Contribution"),
                "counterparty": getattr(item.member, "full_name", "") or getattr(item.member, "phone", ""),
                "chama_id": str(item.chama_id),
                "chama_name": item.chama.name,
                "created_at": item.created_at.isoformat(),
            }
            for item in contributions
        ]
        + [
            {
                "id": str(item.id),
                "kind": "expense",
                "amount": str(item.amount),
                "currency": item.chama.currency,
                "description": item.description or item.category,
                "counterparty": item.vendor_name,
                "chama_id": str(item.chama_id),
                "chama_name": item.chama.name,
                "created_at": item.created_at.isoformat(),
            }
            for item in expenses
        ],
        key=lambda row: row["created_at"],
        reverse=True,
    )[:5]

    upcoming_meetings = [
        {
            "id": str(meeting.id),
            "title": meeting.title,
            "date": meeting.date.isoformat(),
            "location": meeting.location,
            "location_type": meeting.location_type,
            "meeting_link": meeting.meeting_link,
            "chama_id": str(meeting.chama_id),
            "chama_name": meeting.chama.name,
            "quorum_percentage": meeting.quorum_percentage,
            "minutes_status": meeting.minutes_status,
        }
        for meeting in Meeting.objects.select_related("chama")
        .filter(chama_id__in=chama_ids, date__gte=timezone.now())
        .order_by("date")[:5]
    ]

    announcements = [
        {
            "id": str(item.id),
            "title": item.subject,
            "message": item.message,
            "chama_id": str(item.chama_id) if item.chama_id else "",
            "chama_name": item.chama.name if item.chama else "",
            "created_at": item.created_at.isoformat(),
            "sent_at": item.sent_at.isoformat() if item.sent_at else None,
        }
        for item in Notification.objects.select_related("chama")
        .filter(
            recipient=user,
            chama_id__in=chama_ids,
            type=NotificationType.GENERAL_ANNOUNCEMENT,
            status__in=[
                NotificationStatus.PENDING,
                NotificationStatus.PROCESSING,
                NotificationStatus.SENT,
            ],
        )
        .filter(Q(scheduled_at__isnull=True) | Q(scheduled_at__lte=timezone.now()))
        .order_by("-sent_at", "-created_at")[:3]
    ]

    trend_snapshots = list(
        FinancialSnapshot.objects.filter(chama_id__in=chama_ids)
        .order_by("-snapshot_date", "-created_at")[:180]
    )
    contribution_trend = [
        {
            "date": snapshot.snapshot_date.isoformat(),
            "amount": str(snapshot.total_contributions),
            "chama_id": str(snapshot.chama_id),
        }
        for snapshot in trend_snapshots[:12]
    ]
    expense_trend = [
        {
            "date": snapshot.snapshot_date.isoformat(),
            "amount": str(snapshot.total_expenses),
            "chama_id": str(snapshot.chama_id),
        }
        for snapshot in trend_snapshots[:12]
    ]

    loan_stats = Loan.objects.filter(chama_id__in=chama_ids).aggregate(
        active_count=Count("id", filter=Q(status__in=["approved", "disbursed", "active"])),
        requested_count=Count("id", filter=Q(status__in=["requested", "pending", "review"])),
    )
    activity_preview = [
        _serialize_audit_like(item)
        for item in ActivityLog.objects.select_related("actor")
        .filter(Q(actor=user) | Q(chama_id__in=chama_ids))
        .order_by("-created_at")[:5]
    ]
    intelligence = _build_dashboard_intelligence(
        user=user,
        memberships=memberships,
        chama_ids=chama_ids,
        total_savings=total_savings,
        chama_balance=chama_balance,
        monthly_contributions=monthly_contributions,
        monthly_expenses=monthly_expenses,
    )

    return Response(
        {
            "user": {
                "id": str(user.id),
                "name": user.get_full_name() or user.phone,
                "phone": user.phone,
            },
            "totals": {
                "total_savings": str(total_savings),
                "chama_balance": str(chama_balance),
                "total_loans": str(total_loans),
                "total_expenses": str(total_expenses),
                "pending_contributions": pending_contributions,
                "unread_notifications": unread_notifications,
            },
            "member_overview": intelligence["member_overview"],
            "chamas": chama_rows[:5],
            "recent_transactions": recent_transactions,
            "upcoming_meetings": upcoming_meetings,
            "announcements": announcements,
            "activity_preview": activity_preview,
            "trends": {
                "contributions": contribution_trend,
                "expenses": expense_trend,
            },
            "loan_stats": loan_stats,
            "smart_summary": intelligence["summary"],
            "scores": intelligence["scores"],
            "analytics": intelligence["analytics"],
            "smart_insights": intelligence["smart_insights"],
            "next_actions": intelligence["next_actions"],
            "admin_action_center": intelligence["admin_action_center"],
            "compliance": intelligence["compliance"],
            "role_workspaces": [_build_role_workspace(membership) for membership in memberships[:5]],
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
@require_billing_access()
def approvals_center(request):
    user = request.user
    memberships = list(_scoped_memberships(request))
    if not memberships:
        return Response(
            {
                "scope": {
                    "active_chamas": 0,
                    "is_admin_scope": False,
                    "primary_chama_id": None,
                    "primary_chama_name": None,
                },
                "summary": {
                    "pending_total": 0,
                    "join_requests": 0,
                    "invites": 0,
                    "loan_requests": 0,
                    "expense_requests": 0,
                    "withdrawal_requests": 0,
                    "approval_requests": 0,
                    "policy_changes": 0,
                    "role_changes": 0,
                    "disputes": 0,
                    "reconciliation_items": 0,
                },
                "sections": [],
                "recent_items": [],
            }
        )

    admin_memberships = [
        membership
        for membership in memberships
        if get_effective_role(user, membership.chama_id, membership) in ADMIN_SCOPE_ROLES
    ]
    admin_chama_ids = [membership.chama_id for membership in admin_memberships]
    if not admin_chama_ids:
        return Response(
            {
                "scope": {
                    "active_chamas": len(memberships),
                    "is_admin_scope": False,
                    "primary_chama_id": str(memberships[0].chama_id),
                    "primary_chama_name": memberships[0].chama.name,
                },
                "summary": {
                    "pending_total": 0,
                    "join_requests": 0,
                    "invites": 0,
                    "loan_requests": 0,
                    "expense_requests": 0,
                    "withdrawal_requests": 0,
                    "approval_requests": 0,
                    "policy_changes": 0,
                    "role_changes": 0,
                    "disputes": 0,
                    "reconciliation_items": 0,
                },
                "sections": [],
                "recent_items": [],
            }
        )

    primary_admin_membership = admin_memberships[0]
    default_chama_id = str(primary_admin_membership.chama_id)

    join_requests_qs = MembershipRequest.objects.select_related("chama", "user").filter(
        chama_id__in=admin_chama_ids,
        status__in=[MembershipRequestStatus.PENDING, MembershipRequestStatus.NEEDS_INFO],
    )
    join_requests_count = join_requests_qs.count()
    join_requests = list(join_requests_qs.order_by("-created_at")[:20])

    invites_qs = Invite.objects.select_related("chama", "invited_by", "invitee_user").filter(
        chama_id__in=admin_chama_ids,
        status=InviteStatus.PENDING,
    )
    invites_count = invites_qs.count()
    invites = list(invites_qs.order_by("-created_at")[:20])

    governance_approvals_qs = ApprovalRequest.objects.select_related("chama", "requested_by").filter(
        chama_id__in=admin_chama_ids,
        status=ApprovalStatus.PENDING,
    )
    governance_approvals_count = governance_approvals_qs.count()
    governance_approvals = list(governance_approvals_qs.order_by("-created_at")[:20])

    expense_requests_qs = Expense.objects.select_related("chama", "requested_by").filter(
        chama_id__in=admin_chama_ids,
        status__in=["pending", "pending_approval"],
    )
    expense_requests_count = expense_requests_qs.count()
    expense_requests = list(expense_requests_qs.order_by("-created_at")[:20])

    withdrawal_requests_qs = PaymentIntent.objects.select_related("chama", "created_by").filter(
        chama_id__in=admin_chama_ids,
        intent_type=PaymentIntentType.WITHDRAWAL,
        status__in=[PaymentIntentStatus.INITIATED, PaymentIntentStatus.PENDING],
    )
    withdrawal_requests_count = withdrawal_requests_qs.count()
    withdrawal_requests = list(withdrawal_requests_qs.order_by("-created_at")[:20])

    loan_requests_qs = Loan.objects.select_related("chama", "member").filter(
        chama_id__in=admin_chama_ids,
        status__in=["requested", "pending", "review"],
    )
    loan_requests_count = loan_requests_qs.count()
    loan_requests = list(loan_requests_qs.order_by("-requested_at", "-created_at")[:20])

    pending_rules_qs = ChamaRule.objects.select_related("chama", "created_by").filter(
        chama_id__in=admin_chama_ids,
        status=RuleStatus.PENDING_APPROVAL,
    )
    pending_rules_count = pending_rules_qs.count()
    pending_rules = list(pending_rules_qs.order_by("-created_at")[:20])

    pending_role_changes_qs = RoleChange.objects.select_related("chama", "member").filter(
        chama_id__in=admin_chama_ids,
        status=RoleChangeStatus.PENDING,
    )
    pending_role_changes_count = pending_role_changes_qs.count()
    pending_role_changes = list(pending_role_changes_qs.order_by("-created_at")[:20])

    payment_disputes_qs = PaymentDispute.objects.select_related("chama", "opened_by").filter(
        chama_id__in=admin_chama_ids,
        status__in=OPEN_PAYMENT_DISPUTE_STATUSES,
    )
    payment_disputes_count = payment_disputes_qs.count()
    payment_disputes = list(payment_disputes_qs.order_by("-created_at")[:20])

    issues_qs = Issue.objects.select_related("chama", "created_by", "assigned_to").filter(
        chama_id__in=admin_chama_ids,
        status__in=OPEN_ISSUE_STATUSES,
    )
    issues_count = issues_qs.count()
    issues = list(issues_qs.order_by("-created_at")[:20])

    reconciliation_runs_qs = PaymentReconciliationRun.objects.select_related("chama").filter(
        chama_id__in=admin_chama_ids,
    ).order_by("-run_at", "-created_at")
    reconciliation_runs = list(reconciliation_runs_qs[:20])
    reconciliation_count = sum(
        1
        for run in reconciliation_runs
        if run.status in {ReconciliationRunStatus.PARTIAL, ReconciliationRunStatus.FAILED}
        or _count_anomaly_items(run.anomalies) > 0
    )

    sections = []
    recent_items = []

    if join_requests:
        items = [
            _serialize_approval_queue_item(
                key=f"join:{item.id}",
                item_type="join_request",
                title=f"{item.user.full_name or item.user.phone} wants to join",
                description=item.request_note or "Review member admission and onboarding readiness.",
                status=item.status,
                chama=item.chama,
                route="MembershipRequests",
                params={"chamaId": str(item.chama_id)},
                created_at=item.created_at,
                requested_by=item.user.full_name or item.user.phone,
                severity="medium",
            )
            for item in join_requests
        ]
        sections.append(
            {
                "key": "join_requests",
                "title": "Join Requests",
                "count": join_requests_count,
                "route": _build_route("MembershipRequests", label="Open join requests", params={"chamaId": default_chama_id}),
                "items": items[:5],
            }
        )
        recent_items.extend(items[:4])

    if invites:
        items = [
            _serialize_approval_queue_item(
                key=f"invite:{item.id}",
                item_type="invite",
                title=f"Invite pending for {item.invitee_user.full_name if item.invitee_user else item.identifier}",
                description="Track whether the invite should be resent, revoked, or followed up.",
                status=item.status,
                chama=item.chama,
                route="InviteMember",
                params={"chamaId": str(item.chama_id)},
                created_at=item.created_at,
                requested_by=item.invited_by.full_name or item.invited_by.phone,
                severity="low",
            )
            for item in invites
        ]
        sections.append(
            {
                "key": "invites",
                "title": "Pending Invites",
                "count": invites_count,
                "route": _build_route("InviteMember", label="Manage invites", params={"chamaId": default_chama_id}),
                "items": items[:5],
            }
        )
        recent_items.extend(items[:2])

    if governance_approvals:
        items = [
            _serialize_approval_queue_item(
                key=f"approval:{item.id}",
                item_type="approval_request",
                title=item.title,
                description=item.description or item.reference_display,
                status=item.status,
                chama=item.chama,
                route="AutomationCenter",
                params={"chamaId": str(item.chama_id)},
                amount=item.amount,
                currency=item.currency,
                created_at=item.created_at,
                requested_by=item.requested_by.full_name if item.requested_by else "System",
                severity="high" if str(item.approval_type).lower() in {"withdrawal", "loan", "fine_waiver"} else "medium",
            )
            for item in governance_approvals
        ]
        sections.append(
            {
                "key": "governance_approvals",
                "title": "Governance Approvals",
                "count": governance_approvals_count,
                "route": _build_route("AutomationCenter", label="Open automation center", params={"chamaId": default_chama_id}),
                "items": items[:5],
            }
        )
        recent_items.extend(items[:4])

    if loan_requests:
        items = [
            _serialize_approval_queue_item(
                key=f"loan:{item.id}",
                item_type="loan_request",
                title=f"Loan request from {item.member.full_name}",
                description=item.purpose or "Review eligibility, guarantors, and liquidity before approval.",
                status=item.status,
                chama=item.chama,
                route="Finance",
                params={},
                amount=item.principal,
                currency=item.chama.currency,
                created_at=item.requested_at,
                requested_by=item.member.full_name,
                severity="high",
            )
            for item in loan_requests
        ]
        sections.append(
            {
                "key": "loan_requests",
                "title": "Loan Requests",
                "count": loan_requests_count,
                "route": _build_route("Finance", label="Open finance", params={}),
                "items": items[:5],
            }
        )
        recent_items.extend(items[:4])

    if expense_requests:
        items = [
            _serialize_approval_queue_item(
                key=f"expense:{item.id}",
                item_type="expense_request",
                title=item.description or item.category or "Expense request",
                description=f"Category: {item.category or 'general'}",
                status=item.status,
                chama=item.chama,
                route="Expenses",
                params={"chamaId": str(item.chama_id)},
                amount=item.amount,
                currency=item.chama.currency,
                created_at=item.created_at,
                requested_by=item.requested_by.full_name if item.requested_by else "Unknown",
                severity="medium",
            )
            for item in expense_requests
        ]
        sections.append(
            {
                "key": "expense_requests",
                "title": "Expense Requests",
                "count": expense_requests_count,
                "route": _build_route("Expenses", label="Open expenses", params={"chamaId": default_chama_id}),
                "items": items[:5],
            }
        )
        recent_items.extend(items[:4])

    if withdrawal_requests:
        items = [
            _serialize_approval_queue_item(
                key=f"withdrawal:{item.id}",
                item_type="withdrawal_request",
                title=f"Withdrawal request for {item.phone}",
                description="Verify beneficiary, proof, and approval chain before funds move out.",
                status=item.status,
                chama=item.chama,
                route="Withdrawals",
                params={"chamaId": str(item.chama_id)},
                amount=item.amount,
                currency=item.currency,
                created_at=item.created_at,
                requested_by=item.created_by.full_name if getattr(item, "created_by", None) else (item.user.full_name if item.user_id else "Unknown"),
                severity="high",
            )
            for item in withdrawal_requests
        ]
        sections.append(
            {
                "key": "withdrawal_requests",
                "title": "Withdrawal Requests",
                "count": withdrawal_requests_count,
                "route": _build_route("Withdrawals", label="Open withdrawals", params={"chamaId": default_chama_id}),
                "items": items[:5],
            }
        )
        recent_items.extend(items[:4])

    if pending_rules:
        items = [
            _serialize_approval_queue_item(
                key=f"rule:{item.id}",
                item_type="policy_change",
                title=f"Policy update: {item.title}",
                description=f"{item.get_category_display()} policy is awaiting approval.",
                status=item.status,
                chama=item.chama,
                route="ChamaSettings",
                params={"chamaId": str(item.chama_id)},
                created_at=item.created_at,
                requested_by=item.created_by.full_name if item.created_by else "Unknown",
                severity="medium",
            )
            for item in pending_rules
        ]
        sections.append(
            {
                "key": "policy_changes",
                "title": "Policy Changes",
                "count": pending_rules_count,
                "route": _build_route("ChamaSettings", label="Open chama settings", params={"chamaId": default_chama_id}),
                "items": items[:5],
            }
        )
        recent_items.extend(items[:3])

    if pending_role_changes:
        items = [
            _serialize_approval_queue_item(
                key=f"role:{item.id}",
                item_type="role_change",
                title=f"Role change for {item.member.full_name}",
                description=f"{item.old_role or 'member'} to {item.new_role}",
                status=item.status,
                chama=item.chama,
                route="RoleDelegations",
                params={"chamaId": str(item.chama_id)},
                created_at=item.created_at,
                requested_by=item.member.full_name,
                severity="medium",
            )
            for item in pending_role_changes
        ]
        sections.append(
            {
                "key": "role_changes",
                "title": "Role Changes",
                "count": pending_role_changes_count,
                "route": _build_route("RoleDelegations", label="Open role workflow", params={"chamaId": default_chama_id}),
                "items": items[:5],
            }
        )
        recent_items.extend(items[:3])

    dispute_items = [
        _serialize_approval_queue_item(
            key=f"payment-dispute:{item.id}",
            item_type="payment_dispute",
            title=f"Payment dispute from {item.opened_by.full_name or item.opened_by.phone}",
            description=item.reason[:140] if item.reason else "Review disputed payment and supporting evidence.",
            status=item.status,
            chama=item.chama,
            route="SupportIssues",
            params={"chamaId": str(item.chama_id)},
            created_at=item.created_at,
            requested_by=item.opened_by.full_name or item.opened_by.phone,
            severity="high",
        )
        for item in payment_disputes
    ] + [
        _serialize_approval_queue_item(
            key=f"issue:{item.id}",
            item_type="issue_dispute",
            title=item.title,
            description=item.description[:140] if item.description else "Review issue details and next action.",
            status=item.status,
            chama=item.chama,
            route="SupportIssues",
            params={"chamaId": str(item.chama_id)},
            created_at=item.created_at,
            requested_by=(
                item.created_by.full_name
                if getattr(item, "created_by", None)
                else "Member report"
            ),
            severity="high" if getattr(item, "priority", "") in {"high", "urgent"} else "medium",
        )
        for item in issues
    ]
    if dispute_items:
        sections.append(
            {
                "key": "disputes",
                "title": "Disputes & Issues",
                "count": payment_disputes_count + issues_count,
                "route": _build_route("SupportIssues", label="Open disputes", params={"chamaId": default_chama_id}),
                "items": dispute_items[:5],
            }
        )
        recent_items.extend(dispute_items[:4])

    reconciliation_items = [
        _serialize_approval_queue_item(
            key=f"reconciliation:{item.id}",
            item_type="reconciliation_review",
            title=f"Reconciliation run for {item.chama.name if item.chama_id else 'global queue'}",
            description=(
                f"{_count_anomaly_items(item.anomalies)} anomaly signal(s) detected."
                if _count_anomaly_items(item.anomalies) > 0
                else "Review partial or failed reconciliation workflow."
            ),
            status=item.status,
            chama=item.chama or primary_admin_membership.chama,
            route="AutomationCenter",
            params={"chamaId": str(item.chama_id)} if item.chama_id else {"chamaId": default_chama_id},
            created_at=item.run_at,
            requested_by="System",
            severity="high" if item.status == ReconciliationRunStatus.FAILED else "medium",
        )
        for item in reconciliation_runs
        if item.status in {ReconciliationRunStatus.PARTIAL, ReconciliationRunStatus.FAILED}
        or _count_anomaly_items(item.anomalies) > 0
    ]
    if reconciliation_items:
        sections.append(
            {
                "key": "reconciliation",
                "title": "Reconciliation Queue",
                "count": reconciliation_count,
                "route": _build_route("AutomationCenter", label="Open automation center", params={"chamaId": default_chama_id}),
                "items": reconciliation_items[:5],
            }
        )
        recent_items.extend(reconciliation_items[:3])

    pending_total = sum(section["count"] for section in sections)

    return Response(
        {
            "scope": {
                "active_chamas": len(admin_chama_ids),
                "is_admin_scope": True,
                "primary_chama_id": default_chama_id,
                "primary_chama_name": primary_admin_membership.chama.name,
            },
            "summary": {
                "pending_total": pending_total,
                "join_requests": join_requests_count,
                "invites": invites_count,
                "loan_requests": loan_requests_count,
                "expense_requests": expense_requests_count,
                "withdrawal_requests": withdrawal_requests_count,
                "approval_requests": governance_approvals_count,
                "policy_changes": pending_rules_count,
                "role_changes": pending_role_changes_count,
                "disputes": payment_disputes_count + issues_count,
                "reconciliation_items": reconciliation_count,
            },
            "sections": sections,
            "recent_items": sorted(
                recent_items,
                key=lambda item: item.get("created_at") or "",
                reverse=True,
            )[:10],
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def activity_history(request):
    memberships = _scoped_memberships(request)
    chama_ids = list(memberships.values_list("chama_id", flat=True))
    queryset = ActivityLog.objects.select_related("actor")
    if chama_ids:
        queryset = queryset.filter(Q(actor=request.user) | Q(chama_id__in=chama_ids))
    else:
        queryset = queryset.filter(actor=request.user)

    action = str(request.query_params.get("action", "")).strip()
    entity_type = str(request.query_params.get("entity_type", "")).strip()
    search = str(request.query_params.get("search", "")).strip()
    created_from = _parse_iso_date_param(
        request.query_params.get("created_from"),
        "created_from",
    )
    created_to = _parse_iso_date_param(
        request.query_params.get("created_to"),
        "created_to",
    )
    if action:
        queryset = queryset.filter(action=action)
    if entity_type:
        queryset = queryset.filter(entity_type=entity_type)
    if search:
        queryset = queryset.filter(
            Q(action__icontains=search)
            | Q(entity_type__icontains=search)
            | Q(trace_id__icontains=search)
        )
    if created_from:
        queryset = queryset.filter(created_at__gte=created_from)
    if created_to:
        queryset = queryset.filter(created_at__lte=created_to)

    page, page_size, total_count, items = _paginate_queryset(request, queryset.order_by("-created_at"))
    return Response(
        {
            "results": [_serialize_audit_like(item) for item in items],
            "count": total_count,
            "page": page,
            "page_size": page_size,
            "has_next": (page * page_size) < total_count,
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def audit_logs(request):
    chama_id = request.query_params.get("chama_id")
    if not chama_id:
        raise ValidationError({"chama_id": "chama_id is required."})

    _require_admin_or_auditor(request, chama_id)
    queryset = AuditLog.objects.select_related("actor").filter(chama_id=chama_id)

    action = str(request.query_params.get("action", "")).strip()
    entity_type = str(request.query_params.get("entity_type", "")).strip()
    search = str(request.query_params.get("search", "")).strip()
    created_from = _parse_iso_date_param(
        request.query_params.get("created_from"),
        "created_from",
    )
    created_to = _parse_iso_date_param(
        request.query_params.get("created_to"),
        "created_to",
    )
    if action:
        queryset = queryset.filter(action=action)
    if entity_type:
        queryset = queryset.filter(entity_type=entity_type)
    if search:
        queryset = queryset.filter(
            Q(action__icontains=search)
            | Q(entity_type__icontains=search)
            | Q(trace_id__icontains=search)
        )
    if created_from:
        queryset = queryset.filter(created_at__gte=created_from)
    if created_to:
        queryset = queryset.filter(created_at__lte=created_to)

    page, page_size, total_count, items = _paginate_queryset(request, queryset.order_by("-created_at"))
    return Response(
        {
            "results": [_serialize_audit_like(item) for item in items],
            "count": total_count,
            "page": page,
            "page_size": page_size,
            "has_next": (page * page_size) < total_count,
        }
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@require_billing_access()
def chama_detail(request, chama_id):
    """
    Get detailed information about a specific chama.
    Integrates: chama, finance, payments, loans, meetings
    """
    try:
        user = request.user
        
        # Verify membership
        membership = Membership.objects.filter(
            user=user,
            chama_id=chama_id,
            status='ACTIVE'
        ).first()
        
        if not membership:
            return Response(
                {'error': 'Not a member of this chama'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        chama = membership.chama
        
        # Get members
        members = Membership.objects.filter(
            chama=chama,
            status='ACTIVE'
        ).select_related('user')
        
        # Get wallet
        wallet = Wallet.objects.filter(
            chama=chama
        ).first()
        
        # Get recent transactions
        recent_transactions = LedgerEntry.objects.filter(
            chama=chama
        ).order_by('-created_at')[:10]
        
        # Get active loans
        active_loans = Loan.objects.filter(
            chama=chama,
            status__in=['ACTIVE', 'DISBURSED']
        ).select_related('member')
        
        # Get contribution totals
        contribution_totals = Contribution.objects.filter(
            chama=chama
        ).aggregate(
            total=Sum('amount'),
            count=Count('id')
        )
        
        return Response({
            'chama': {
                'id': str(chama.id),
                'name': chama.name,
                'description': chama.description,
                'created_at': chama.created_at.isoformat(),
            },
            'membership': {
                'role': membership.role,
                'effective_role': compute_effective_role(membership, None)[0],
                'joined_at': membership.joined_at.isoformat(),
            },
            'wallet': {
                'available': float(wallet.available_balance) if wallet else 0,
                'locked': float(wallet.locked_balance) if wallet else 0,
                'total': float(wallet.total_balance()) if wallet else 0,
            } if wallet else None,
            'members': {
                'count': members.count(),
                'list': [
                    {
                        'id': str(m.user.id),
                        'name': m.user.get_full_name() or m.user.phone,
                        'role': m.role,
                    }
                    for m in members
                ]
            },
            'transactions': {
                'recent': [
                    {
                        'id': str(t.id),
                        'type': t.entry_type,
                        'amount': float(t.amount),
                        'direction': t.direction,
                        'date': t.created_at.isoformat(),
                    }
                    for t in recent_transactions
                ]
            },
            'loans': {
                'active_count': active_loans.count(),
                'total_outstanding': float(
                    sum(l.balance_remaining for l in active_loans)
                ),
                'list': [
                    {
                        'id': str(l.id),
                        'member': l.member.get_full_name() or l.member.phone,
                        'principal': float(l.principal),
                        'balance': float(l.balance_remaining),
                        'status': l.status,
                    }
                    for l in active_loans[:5]
                ]
            },
            'contributions': {
                'total': float(contribution_totals['total'] or 0),
                'count': contribution_totals['count'] or 0,
            }
        })
        
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@require_feature('full_finance_management')
def loan_detail(request, loan_id):
    """
    Get detailed loan information including repayment schedule.
    """
    try:
        user = request.user
        
        loan = Loan.objects.filter(
            id=loan_id,
            member=user
        ).first()
        
        if not loan:
            return Response(
                {'error': 'Loan not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Calculate eligibility for                status=status.HTTP new loans
        membership = Membership.objects.filter(
            user=user,
            chama=loan.chama,
            status='ACTIVE'
        ).first()
        
        eligibility = None
        if membership:
            eligibility = calculate_loan_eligibility(membership)
        
        return Response({
            'loan': {
                'id': str(loan.id),
                'chama': {
                    'id': str(loan.chama.id),
                    'name': loan.chama.name,
                },
                'principal': float(loan.principal),
                'interest_rate': float(loan.interest_rate),
                'balance_remaining': float(loan.balance_remaining),
                'status': loan.status,
                'disbursed_at': loan.disbursed_at.isoformat() if loan.disbursed_at else None,
                'due_date': loan.due_date.isoformat() if loan.due_date else None,
                'repayment_amount': float(loan.repayment_amount),
            },
            'eligibility': eligibility,
        })
        
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@require_feature('mpesa_stk')
def payment_history(request):
    """
    Get payment history for the user across all chamas.
    Combines STK transactions, B2C payouts, and ledger entries.
    """
    try:
        user = request.user
        
        # Get membership chama IDs
        memberships = Membership.objects.filter(
            user=user,
            status='ACTIVE'
        )
        chama_ids = [m.chama_id for m in memberships]
        
        # Get STK transactions
        stk_transactions = MpesaSTKTransaction.objects.filter(
            chama_id__in=chama_ids
        ).order_by('-created_at')[:20]
        
        # Get B2C payouts
        b2c_payouts = MpesaB2CPayout.objects.filter(
            chama_id__in=chama_ids
        ).order_by('-created_at')[:20]
        
        # Get payment intents
        payment_intents = PaymentIntent.objects.filter(
            chama_id__in=chama_ids
        ).order_by('-created_at')[:20]
        
        return Response({
            'stk_transactions': [
                {
                    'id': str(t.id),
                    'amount': float(t.amount),
                    'phone': t.phone,
                    'status': t.status,
                    'receipt': t.mpesa_receipt_number,
                    'date': t.created_at.isoformat(),
                }
                for t in stk_transactions
            ],
            'b2c_payouts': [
                {
                    'id': str(p.id),
                    'amount': float(p.amount),
                    'phone': p.phone_number,
                    'status': p.status,
                    'date': p.created_at.isoformat(),
                }
                for p in b2c_payouts
            ],
            'payment_intents': [
                {
                    'id': str(i.id),
                    'amount': float(i.amount),
                    'type': i.intent_type,
                    'status': i.status,
                    'date': i.created_at.isoformat(),
                }
                for i in payment_intents
            ],
        })
        
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@require_billing_access()
def member_profile(request):
    """
    Get comprehensive member financial profile, role context, and chama participation.
    """
    try:
        user = request.user
        memberships = list(_active_memberships_for_user(user))
        scoped_memberships = list(_scoped_memberships(request))
        memberships_for_profile = scoped_memberships or memberships

        wallet = Wallet.objects.filter(
            owner_type='USER',
            owner_id=user.id,
        ).first()

        chama_profiles = [_build_member_chama_profile(membership) for membership in memberships_for_profile]

        total_contributions = sum(Decimal(profile["contributions"]["total"]) for profile in chama_profiles)
        total_fines = sum(Decimal(profile["fines"]["issued_total"]) for profile in chama_profiles)
        total_loans_taken = sum(Decimal(profile["loans"]["taken_total"]) for profile in chama_profiles)
        total_loans_repaid = sum(Decimal(profile["loans"]["repaid_total"]) for profile in chama_profiles)
        total_outstanding = sum(Decimal(profile["loans"]["outstanding_total"]) for profile in chama_profiles)
        total_pending_payments = sum(Decimal(profile["obligations"]["pending_payments"]) for profile in chama_profiles)
        total_payable_fines = sum(Decimal(profile["obligations"]["payable_fines"]) for profile in chama_profiles)
        total_votes_cast = sum(profile["voting"]["votes_cast"] for profile in chama_profiles)
        total_vote_opportunities = sum(profile["voting"]["total_votes"] for profile in chama_profiles)
        total_attended = sum(profile["attendance"]["attended"] for profile in chama_profiles)
        total_meetings = sum(profile["attendance"]["total"] for profile in chama_profiles)
        reliability_average = (
            round(sum(profile["reliability"]["score"] for profile in chama_profiles) / len(chama_profiles))
            if chama_profiles
            else 0
        )

        return Response({
            'user': {
                'id': str(user.id),
                'name': user.get_full_name(),
                'phone': user.phone,
                'email': user.email,
                'date_joined': user.date_joined.isoformat(),
            },
            'wallet': {
                'available': _decimal_to_str(wallet.available_balance) if wallet else "0.00",
                'locked': _decimal_to_str(wallet.locked_balance) if wallet else "0.00",
                'total': _decimal_to_str(wallet.total_balance) if wallet else "0.00",
                'currency': wallet.currency if wallet else CURRENCY,
            },
            'memberships': {
                'total': len(memberships),
                'active': len(memberships),
                'scoped': len(memberships_for_profile),
                'list': [
                    {
                        'chama': {
                            'id': str(m.chama.id),
                            'name': m.chama.name,
                            'currency': m.chama.currency,
                        },
                        'role': m.role,
                        'effective_role': get_effective_role(user, m.chama_id, m) or m.role,
                        'status': m.status,
                        'joined_at': m.joined_at.isoformat(),
                    }
                    for m in memberships_for_profile
                ]
            },
            'portfolio': {
                'total_contributions': _decimal_to_str(total_contributions),
                'total_fines': _decimal_to_str(total_fines),
                'total_loans_taken': _decimal_to_str(total_loans_taken),
                'total_loans_repaid': _decimal_to_str(total_loans_repaid),
                'outstanding_balances': _decimal_to_str(total_outstanding),
                'pending_payments': _decimal_to_str(total_pending_payments),
                'payable_fines': _decimal_to_str(total_payable_fines),
                'attendance_rate': round((total_attended / total_meetings) * 100, 1) if total_meetings else 0.0,
                'voting_participation_rate': round((total_votes_cast / total_vote_opportunities) * 100, 1) if total_vote_opportunities else 0.0,
                'reliability_score': reliability_average,
            },
            'financial_profile': {
                'by_chama': chama_profiles,
            },
            'role_workspaces': [_build_role_workspace(membership) for membership in memberships_for_profile],
        })

    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@require_billing_access()
def role_workspaces(request):
    memberships = list(_scoped_memberships(request))
    workspaces = [_build_role_workspace(membership) for membership in memberships]
    return Response(
        {
            "summary": {
                "workspace_count": len(workspaces),
                "admin_workspaces": sum(
                    1
                    for workspace in workspaces
                    if workspace["workspace_key"] in {"treasurer", "chairperson", "secretary", "auditor", "platform_super_admin"}
                ),
                "member_workspaces": sum(1 for workspace in workspaces if workspace["workspace_key"] == "member"),
            },
            "workspaces": workspaces,
        }
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@require_billing_access()
def policy_center(request):
    memberships = list(_scoped_memberships(request))
    if not memberships:
        return Response(
            {
                "summary": {
                    "chamas": 0,
                    "active_policies": 0,
                    "pending_approval": 0,
                    "acknowledgments_due": 0,
                },
                "chamas": [],
            }
        )

    chama_entries = []
    active_policies_total = 0
    pending_approval_total = 0
    acknowledgments_due_total = 0

    for membership in memberships:
        chama = membership.chama
        effective_role = get_effective_role(request.user, chama.id, membership) or membership.role
        can_manage = effective_role in ADMIN_SCOPE_ROLES
        rules = list(
            ChamaRule.objects.filter(chama=chama)
            .select_related("approved_by", "created_by", "previous_version")
            .order_by("-created_at")[:12]
        )
        active_rules = [rule for rule in rules if rule.status == RuleStatus.ACTIVE]
        pending_rules = [rule for rule in rules if rule.status == RuleStatus.PENDING_APPROVAL]
        acknowledgments_due = RuleAcknowledgment.objects.filter(
            rule__chama=chama,
            member=request.user,
            status="pending",
        ).count()

        active_policies_total += len(active_rules)
        pending_approval_total += len(pending_rules)
        acknowledgments_due_total += acknowledgments_due

        categories = []
        for category in sorted({rule.category for rule in rules}):
            category_rules = [rule for rule in rules if rule.category == category]
            latest_rule = category_rules[0] if category_rules else None
            categories.append(
                {
                    "category": category,
                    "active_count": sum(1 for rule in category_rules if rule.status == RuleStatus.ACTIVE),
                    "pending_count": sum(1 for rule in category_rules if rule.status == RuleStatus.PENDING_APPROVAL),
                    "latest_title": latest_rule.title if latest_rule else "",
                    "latest_version": latest_rule.version if latest_rule else 0,
                }
            )

        chama_entries.append(
            {
                "chama": {
                    "id": str(chama.id),
                    "name": chama.name,
                    "currency": chama.currency,
                },
                "role": membership.role,
                "effective_role": effective_role,
                "can_manage": can_manage,
                "summary": {
                    "active_policies": len(active_rules),
                    "pending_approval": len(pending_rules),
                    "acknowledgments_due": acknowledgments_due,
                    "latest_update_at": rules[0].updated_at.isoformat() if rules else None,
                },
                "categories": categories,
                "recent_policies": [
                    {
                        "id": str(rule.id),
                        "title": rule.title,
                        "category": rule.category,
                        "status": rule.status,
                        "version": rule.version,
                        "requires_acknowledgment": rule.requires_acknowledgment,
                        "acknowledgment_rate": round(rule.get_acknowledgment_rate(), 1),
                        "effective_date": rule.effective_date.isoformat() if rule.effective_date else None,
                        "updated_at": rule.updated_at.isoformat(),
                    }
                    for rule in rules[:5]
                ],
            }
        )

    return Response(
        {
            "summary": {
                "chamas": len(chama_entries),
                "active_policies": active_policies_total,
                "pending_approval": pending_approval_total,
                "acknowledgments_due": acknowledgments_due_total,
            },
            "chamas": chama_entries,
        }
    )


# ========================================================================
# WALLET API ENDPOINTS (per requirements)
# ========================================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
@require_feature('contributions_basic')
def wallet_info(request):
    """
    GET /api/app/wallet/
    Get user's wallet details with balances.
    """
    try:
        user = request.user
        
        # Get user's wallet
        wallet = Wallet.objects.filter(
            owner_type='USER',
            owner_id=user.id
        ).first()
        
        # Get recent ledger entries
        recent_ledger = LedgerEntry.objects.filter(
            wallet__owner_type='USER',
            wallet__owner_id=user.id
        ).order_by('-created_at')[:10]
        
        # Calculate today's withdrawal total
        today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_withdrawals = LedgerEntry.objects.filter(
            wallet__owner_type='USER',
            wallet__owner_id=user.id,
            entry_type='WITHDRAWAL',
            direction='debit',
            status='success',
            created_at__gte=today_start
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        return Response({
            'wallet': {
                'id': str(wallet.id) if wallet else None,
                'available_balance': float(wallet.available_balance) if wallet else 0,
                'locked_balance': float(wallet.locked_balance) if wallet else 0,
                'total_balance': float(wallet.total_balance()) if wallet else 0,
                'currency': CURRENCY,
            },
            'limits': {
                'min_deposit': MIN_DEPOSIT,
                'max_deposit': MAX_DEPOSIT,
                'min_withdrawal': MIN_WITHDRAWAL,
                'max_withdrawal': MAX_WITHDRAWAL,
                'daily_withdrawal_limit': DAILY_WITHDRAWAL_LIMIT,
                'today_withdrawn': float(today_withdrawals),
                'remaining_daily': float(DAILY_WITHDRAWAL_LIMIT - today_withdrawals),
            },
            'recent_transactions': [
                {
                    'id': str(t.id),
                    'type': t.entry_type,
                    'direction': t.direction,
                    'amount': float(t.amount),
                    'status': t.status,
                    'date': t.created_at.isoformat(),
                }
                for t in recent_ledger
            ]
        })
        
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@require_feature('contributions_basic')
def wallet_transactions(request):
    """
    GET /api/app/wallet/transactions/
    Get ledger transactions with filters (type, status, date range).
    """
    try:
        user = request.user
        
        # Get filter params
        entry_type = request.query_params.get('type')
        tx_status = request.query_params.get('status')
        date_from = request.query_params.get('from')
        date_to = request.query_params.get('to')
        search = request.query_params.get('search')
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 20))
        
        # Base query
        transactions = LedgerEntry.objects.filter(
            wallet__owner_type='USER',
            wallet__owner_id=user.id
        ).order_by('-created_at')
        
        # Apply filters
        if entry_type:
            transactions = transactions.filter(entry_type=entry_type)
        
        if tx_status:
            transactions = transactions.filter(status=tx_status)
        
        if date_from:
            transactions = transactions.filter(created_at__gte=date_from)
        
        if date_to:
            transactions = transactions.filter(created_at__lte=date_to)
        
        if search:
            transactions = transactions.filter(
                Q(reference__icontains=search) |
                Q(provider_ref__icontains=search)
            )
        
        # Paginate
        start = (page - 1) * page_size
        end = start + page_size
        total = transactions.count()
        transactions_page = transactions[start:end]
        
        return Response({
            'transactions': [
                {
                    'id': str(t.id),
                    'reference': t.reference,
                    'type': t.entry_type,
                    'direction': t.direction,
                    'amount': float(t.amount),
                    'status': t.status,
                    'provider': t.provider,
                    'provider_ref': t.provider_ref,
                    'meta': t.meta,
                    'date': t.created_at.isoformat(),
                }
                for t in transactions_page
            ],
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total': total,
                'pages': (total + page_size - 1) // page_size
            }
        })
        
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@require_feature('contributions_basic')
def wallet_validate(request):
    """
    GET /api/app/wallet/validate/
    Validate a wallet operation (deposit/withdrawal amount).
    """
    try:
        user = request.user
        operation = request.query_params.get('operation')
        amount = request.query_params.get('amount')
        
        if not operation or not amount:
            return Response(
                {'error': 'operation and amount are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            amount = float(amount)
        except ValueError:
            return Response(
                {'error': 'Invalid amount'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get wallet
        wallet = Wallet.objects.filter(
            owner_type='USER',
            owner_id=user.id
        ).first()
        
        if not wallet:
            return Response(
                {'error': 'Wallet not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        errors = []
        warnings = []
        valid = True
        
        if operation == 'deposit':
            if amount < MIN_DEPOSIT:
                errors.append(f'Minimum deposit is {MIN_DEPOSIT} KES')
                valid = False
            if amount > MAX_DEPOSIT:
                errors.append(f'Maximum deposit is {MAX_DEPOSIT} KES')
                valid = False
                
        elif operation == 'withdraw':
            today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
            today_withdrawals = LedgerEntry.objects.filter(
                wallet__owner_type='USER',
                wallet__owner_id=user.id,
                entry_type='WITHDRAWAL',
                direction='debit',
                status='success',
                created_at__gte=today_start
            ).aggregate(total=Sum('amount'))['total'] or 0
            
            daily_remaining = DAILY_WITHDRAWAL_LIMIT - float(today_withdrawals)
            
            if amount < MIN_WITHDRAWAL:
                errors.append(f'Minimum withdrawal is {MIN_WITHDRAWAL} KES')
                valid = False
            if amount > MAX_WITHDRAWAL:
                errors.append(f'Maximum withdrawal per transaction is {MAX_WITHDRAWAL} KES')
                valid = False
            if amount > daily_remaining:
                errors.append(f'Daily limit exceeded. Remaining: {daily_remaining} KES')
                valid = False
            if float(wallet.available_balance) < amount:
                errors.append('Insufficient balance')
                valid = False
                
            if amount > 10000:
                warnings.append('Large transaction: OTP confirmation required')
                
        else:
            return Response(
                {'error': 'Invalid operation. Use deposit or withdraw'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        return Response({
            'valid': valid,
            'operation': operation,
            'amount': amount,
            'errors': errors,
            'warnings': warnings,
            'currency': CURRENCY,
        })
        
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ========================================================================
# PUBLIC SECURITY INFO ENDPOINT
# ========================================================================

@api_view(['GET'])
@permission_classes([AllowAny])
def public_security_info(request):
    """
    Public security information endpoint.
    Provides general security stats and features for the public Security page.
    No authentication required.
    """

    from apps.accounts.models import User
    
    try:
        # Get platform statistics
        total_users = User.objects.filter(is_active=True).count()
        total_chamas = Chama.objects.filter(is_active=True).count()
        
        # Security features
        security_features = {
            'mfa_enabled': True,
            'encryption': '256-bit AES',
            'compliance': ['Kenya DPA', 'PCI-DSS Level 1'],
            'audit_logging': True,
            'real_time_monitoring': True,
            'fraud_detection': True,
        }
        
        # Security best practices
        best_practices = [
            {
                'title': 'Multi-Factor Authentication',
                'description': 'Every login requires OTP verification via SMS and email',
                'icon': 'shield',
            },
            {
                'title': 'Encrypted Transactions',
                'description': 'All financial data is encrypted with 256-bit AES encryption',
                'icon': 'lock',
            },
            {
                'title': 'Real-time Monitoring',
                'description': '24/7 fraud detection and suspicious activity alerts',
                'icon': 'eye',
            },
            {
                'title': 'Audit Logging',
                'description': 'Complete audit trail of all system activities',
                'icon': 'clipboard',
            },
            {
                'title': 'Role-Based Access',
                'description': 'Granular permissions ensure data access is properly controlled',
                'icon': 'users',
            },
            {
                'title': 'M-Pesa Integration',
                'description': 'Secure STK Push with real-time transaction reconciliation',
                'icon': 'phone',
            },
        ]
        
        # Compliance info
        compliance = {
            'name': 'Kenya Data Protection Act',
            'description': 'We are fully compliant with Kenya\'s data protection regulations',
            'badge': 'KDPA Compliant',
        }
        
        # Contact info for security issues
        security_contact = {
            'email': 'security@chama.co.ke',
            'response_time': '24 hours',
            'encrypted': True,
        }
        
        return Response({
            'platform_stats': {
                'total_users': total_users,
                'total_chamas': total_chamas,
            },
            'security_features': security_features,
            'best_practices': best_practices,
            'compliance': compliance,
            'security_contact': security_contact,
        })
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
