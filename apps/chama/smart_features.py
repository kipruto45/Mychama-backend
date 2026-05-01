from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.chama.models import (
    Chama,
    Membership,
    MembershipRequest,
    MembershipRequestStatus,
    MembershipRole,
    MemberStatus,
)
from apps.chama.services import get_effective_role
from apps.finance.models import (
    Contribution,
    ContributionSchedule,
    Expense,
    FinancialSnapshot,
    InstallmentSchedule,
    Loan,
    LoanApplication,
    LoanApplicationStatus,
    LoanStatus,
    Penalty,
    PenaltyStatus,
    Repayment,
)
from apps.governance.models import (
    ApprovalRequest,
    ApprovalStatus,
    ChamaRule,
    RoleChange,
    RoleChangeStatus,
    RuleStatus,
)
from apps.issues.models import Issue, IssueStatus
from apps.meetings.models import Attendance, AttendanceStatus, Meeting, MeetingVote
from apps.notifications.models import (
    BroadcastAnnouncement,
    BroadcastAnnouncementStatus,
    Notification,
    NotificationInboxStatus,
    NotificationStatus,
)
from apps.payments.models import (
    MpesaB2CPayout,
    MpesaB2CStatus,
    PaymentIntent,
    PaymentIntentStatus,
    PaymentIntentType,
)
from core.algorithms.smart_ai_assistant import generate_ai_response
from core.algorithms.smart_scoring import (
    compute_chama_health_score,
    compute_contribution_compliance,
    compute_member_reliability_score,
    detect_risk_flags,
    generate_smart_insights,
)

ADMIN_ROLES = {
    MembershipRole.SUPERADMIN,
    MembershipRole.ADMIN,
    MembershipRole.CHAMA_ADMIN,
}
MANAGER_ROLES = ADMIN_ROLES | {
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
PENDING_PAYMENT_STATUSES = {
    PaymentIntentStatus.INITIATED,
    PaymentIntentStatus.PENDING,
}
PENDING_LOAN_APPLICATION_STATUSES = {
    LoanApplicationStatus.SUBMITTED,
    LoanApplicationStatus.IN_REVIEW,
    LoanApplicationStatus.TREASURER_APPROVED,
    LoanApplicationStatus.COMMITTEE_APPROVED,
}
PENDING_LOAN_STATUSES = {LoanStatus.REQUESTED, LoanStatus.REVIEW}
OVERDUE_LOAN_STATUSES = {
    LoanStatus.OVERDUE,
    LoanStatus.DEFAULTED,
    LoanStatus.DEFAULTED_RECOVERING,
}


def _to_decimal(value: Decimal | int | float | None) -> Decimal:
    if value is None:
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _month_bounds(reference_date, months_ago: int) -> tuple:
    base = reference_date.replace(day=1)
    year = base.year
    month = base.month - months_ago
    while month <= 0:
        month += 12
        year -= 1
    start = base.replace(year=year, month=month)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def get_active_membership_for_chama(user, chama_id) -> Membership:
    membership = (
        Membership.objects.select_related("chama", "user")
        .filter(
            user=user,
            chama_id=chama_id,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        )
        .first()
    )
    if not membership:
        raise PermissionDenied("You are not an approved active member of this chama.")
    return membership


def _latest_snapshot(chama: Chama):
    return (
        FinancialSnapshot.objects.filter(chama=chama)
        .order_by("-snapshot_date", "-created_at")
        .first()
    )


def _financial_totals(chama: Chama) -> dict:
    snapshot = _latest_snapshot(chama)
    total_savings = _to_decimal(
        getattr(snapshot, "total_contributions", None)
        or Contribution.objects.filter(chama=chama).aggregate(total=Sum("amount"))["total"]
    )
    current_balance = _to_decimal(getattr(snapshot, "total_balance", None))
    if current_balance == Decimal("0.00"):
        credit_total = _to_decimal(
            chama.ledger_entries.filter(status="success").aggregate(total=Sum("credit"))["total"]
        )
        debit_total = _to_decimal(
            chama.ledger_entries.filter(status="success").aggregate(total=Sum("debit"))["total"]
        )
        if credit_total == Decimal("0.00") and debit_total == Decimal("0.00"):
            credit_total = _to_decimal(
                chama.ledger_entries.filter(status="success", direction="credit").aggregate(total=Sum("amount"))["total"]
            )
            debit_total = _to_decimal(
                chama.ledger_entries.filter(status="success", direction="debit").aggregate(total=Sum("amount"))["total"]
            )
        current_balance = credit_total - debit_total

    total_expenses = _to_decimal(
        getattr(snapshot, "total_expenses", None)
        or Expense.objects.filter(chama=chama).aggregate(total=Sum("amount"))["total"]
    )
    total_loans = _to_decimal(
        getattr(snapshot, "total_loans", None)
        or Loan.objects.filter(chama=chama).aggregate(total=Sum("total_due"))["total"]
    )
    return {
        "snapshot": snapshot,
        "total_savings": total_savings,
        "current_balance": current_balance,
        "total_expenses": total_expenses,
        "total_loans": total_loans,
    }


def _monthly_series(chama: Chama, *, model, date_field: str, amount_field: str = "amount", months: int = 6) -> list[Decimal]:
    today = timezone.localdate()
    values: list[Decimal] = []
    for months_ago in reversed(range(months)):
        month_start, month_end = _month_bounds(today, months_ago)
        total = model.objects.filter(
            chama=chama,
            **{
                f"{date_field}__gte": month_start,
                f"{date_field}__lt": month_end,
            },
        ).aggregate(total=Sum(amount_field))["total"]
        values.append(_to_decimal(total))
    return values


def _meeting_attendance_rate(chama: Chama, active_members_count: int) -> Decimal:
    meetings = list(
        Meeting.objects.filter(
            chama=chama,
            date__lt=timezone.now(),
            cancelled_at__isnull=True,
        )
        .order_by("-date")[:6]
    )
    if not meetings or active_members_count <= 0:
        return Decimal("100.00")

    attendance_count = Attendance.objects.filter(
        meeting__in=meetings,
        status__in=ATTENDED_STATUSES,
    ).count()
    denominator = max(len(meetings) * active_members_count, 1)
    return (Decimal(attendance_count) / Decimal(denominator) * Decimal("100")).quantize(
        Decimal("0.01")
    )


def _member_growth_rate(chama: Chama, total_members: int) -> Decimal:
    if total_members <= 0:
        return Decimal("0.00")
    recent_members = Membership.objects.filter(
        chama=chama,
        status=MemberStatus.ACTIVE,
        joined_at__gte=timezone.now() - timezone.timedelta(days=30),
    ).count()
    return (Decimal(recent_members) / Decimal(total_members) * Decimal("100")).quantize(
        Decimal("0.01")
    )


def _expense_control_rate(monthly_contributions: list[Decimal], monthly_expenses: list[Decimal]) -> Decimal:
    income = monthly_contributions[-1] if monthly_contributions else Decimal("0.00")
    expenses = monthly_expenses[-1] if monthly_expenses else Decimal("0.00")
    if expenses <= Decimal("0.00"):
        return Decimal("100.00")
    if income <= Decimal("0.00"):
        return Decimal("20.00")
    ratio = min(Decimal("1.00"), income / expenses)
    return (ratio * Decimal("100")).quantize(Decimal("0.01"))


def _member_score_context(chama: Chama, memberships: list[Membership]) -> tuple[list, list]:
    member_ids = [membership.user_id for membership in memberships]
    schedules_by_member: dict = defaultdict(list)
    for schedule in ContributionSchedule.objects.filter(
        chama=chama,
        member_id__in=member_ids,
        is_active=True,
    ).select_related("contribution"):
        schedules_by_member[schedule.member_id].append(
            {
                "status": schedule.status,
                "due_date": schedule.scheduled_date,
                "paid_date": schedule.contribution.date_paid if schedule.contribution else None,
            }
        )

    attendance_by_member: dict = defaultdict(list)
    for attendance in Attendance.objects.filter(
        meeting__chama=chama,
        member_id__in=member_ids,
    ):
        attendance_by_member[attendance.member_id].append({"status": attendance.status})

    votes_by_member = {
        row["voter_id"]: row["total_votes"]
        for row in MeetingVote.objects.filter(meeting__chama=chama, voter_id__in=member_ids)
        .values("voter_id")
        .annotate(total_votes=Count("id"))
    }
    total_vote_opportunities = max(
        MeetingVote.objects.filter(meeting__chama=chama).values("meeting_id", "agenda_item_id").distinct().count(),
        1,
    )

    savings_by_member = {
        row["member_id"]: _to_decimal(row["total"])
        for row in Contribution.objects.filter(chama=chama, member_id__in=member_ids)
        .values("member_id")
        .annotate(total=Sum("amount"))
    }

    member_loans: dict = defaultdict(list)
    members_data: list[dict] = []
    for loan in Loan.objects.filter(chama=chama, member_id__in=member_ids).prefetch_related("guarantors"):
        overdue_installments = loan.installments.filter(status="overdue").count()
        member_loans[loan.member_id].append(
            {
                "status": loan.status,
                "late_payments": overdue_installments,
            }
        )

    scores = []
    for membership in memberships:
        history = schedules_by_member.get(membership.user_id, [])
        compliance = compute_contribution_compliance(
            member_id=str(membership.user_id),
            chama_id=str(chama.id),
            contribution_history=history,
            contribution_schedule={
                "grace_period_days": getattr(
                    getattr(chama, "contribution_setting", None),
                    "grace_period_days",
                    0,
                )
            },
        )
        score = compute_member_reliability_score(
            member_id=str(membership.user_id),
            chama_id=str(chama.id),
            contribution_compliance=compliance,
            loan_history=member_loans.get(membership.user_id, []),
            attendance_records=attendance_by_member.get(membership.user_id, []),
            participation_data={
                "votes_cast": votes_by_member.get(membership.user_id, 0),
                "total_votes": total_vote_opportunities,
            },
        )
        scores.append(score)
        members_data.append(
            {
                "id": str(membership.user_id),
                "name": membership.user.full_name or membership.user.phone,
                "missed_contributions": compliance.total_missed,
                "total_loans": len(member_loans.get(membership.user_id, [])),
                "outstanding_loans": sum(
                    1
                    for loan in member_loans.get(membership.user_id, [])
                    if loan["status"] in OVERDUE_LOAN_STATUSES | {LoanStatus.ACTIVE}
                ),
                "savings": savings_by_member.get(membership.user_id, Decimal("0.00")),
            }
        )
    return scores, members_data


def _risk_context(chama: Chama, members_data: list[dict]) -> list:
    monthly_balance = [
        _to_decimal(snapshot.total_balance)
        for snapshot in FinancialSnapshot.objects.filter(chama=chama).order_by("snapshot_date")[:6]
    ]
    loan_payload = []
    loans = Loan.objects.filter(chama=chama).prefetch_related("guarantors")
    for loan in loans:
        loan_payload.append(
            {
                "status": loan.status,
                "guarantors": [
                    {
                        "id": str(guarantor.guarantor_id),
                        "amount": guarantor.guaranteed_amount,
                    }
                    for guarantor in loan.guarantors.all()
                ],
            }
        )
    return detect_risk_flags(
        chama_id=str(chama.id),
        members_data=members_data,
        finance_data={"monthly_balance_trend": monthly_balance},
        loan_data=loan_payload,
    )


def _recent_transactions(chama: Chama) -> list[dict]:
    transactions: list[dict] = []
    contributions = (
        Contribution.objects.select_related("member", "contribution_type")
        .filter(chama=chama)
        .order_by("-date_paid", "-created_at")[:4]
    )
    expenses = Expense.objects.select_related("requested_by").filter(chama=chama).order_by(
        "-expense_date", "-created_at"
    )[:4]
    repayments = (
        Repayment.objects.select_related("loan__member")
        .filter(loan__chama=chama)
        .order_by("-date_paid", "-created_at")[:4]
    )

    for contribution in contributions:
        transactions.append(
            {
                "id": str(contribution.id),
                "description": getattr(contribution.contribution_type, "name", "Contribution"),
                "amount": str(contribution.amount),
                "direction": "credit",
                "posted_at": contribution.created_at.isoformat(),
                "type": "contribution",
                "counterparty": contribution.member.full_name or contribution.member.phone,
            }
        )
    for expense in expenses:
        transactions.append(
            {
                "id": str(expense.id),
                "description": expense.description or expense.category or "Expense",
                "amount": str(expense.amount),
                "direction": "debit",
                "posted_at": expense.created_at.isoformat(),
                "type": "expense",
                "counterparty": expense.requested_by.full_name if expense.requested_by else "",
            }
        )
    for repayment in repayments:
        transactions.append(
            {
                "id": str(repayment.id),
                "description": "Loan repayment",
                "amount": str(repayment.amount),
                "direction": "credit",
                "posted_at": repayment.created_at.isoformat(),
                "type": "loan_repayment",
                "counterparty": repayment.loan.member.full_name or repayment.loan.member.phone,
            }
        )
    return sorted(transactions, key=lambda item: item["posted_at"], reverse=True)[:6]


def _recent_announcements(chama: Chama) -> list[dict]:
    return [
        {
            "id": str(item.id),
            "title": item.title,
            "message": item.message,
            "sent_at": item.sent_at.isoformat() if item.sent_at else None,
            "priority": item.priority,
        }
        for item in BroadcastAnnouncement.objects.filter(
            chama=chama,
            status=BroadcastAnnouncementStatus.SENT,
        ).order_by("-sent_at", "-created_at")[:3]
    ]


def _upcoming_meetings(chama: Chama) -> list[dict]:
    return [
        {
            "id": str(meeting.id),
            "title": meeting.title,
            "date": meeting.date.isoformat(),
            "location": meeting.location,
            "location_type": meeting.location_type,
            "meeting_link": meeting.meeting_link,
            "quorum_percentage": meeting.quorum_percentage,
        }
        for meeting in Meeting.objects.filter(
            chama=chama,
            date__gte=timezone.now(),
            cancelled_at__isnull=True,
        ).order_by("date")[:5]
    ]


def build_smart_dashboard(user, chama: Chama) -> dict:
    membership = get_active_membership_for_chama(user, chama.id)
    effective_role = get_effective_role(user, chama.id, membership) or membership.role
    today = timezone.localdate()
    month_start = today.replace(day=1)

    memberships = list(
        Membership.objects.select_related("user")
        .filter(
            chama=chama,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            exited_at__isnull=True,
        )
    )
    total_members = len(memberships)
    active_members = total_members

    finance = _financial_totals(chama)
    pending_contribution_qs = ContributionSchedule.objects.filter(
        chama=chama,
        status="pending",
        is_active=True,
        scheduled_date__lte=today,
    )
    overdue_contribution_qs = pending_contribution_qs.filter(scheduled_date__lt=today)
    overdue_member_names = list(
        Membership.objects.filter(
            chama=chama,
            user_id__in=overdue_contribution_qs.values_list("member_id", flat=True).distinct(),
        )
        .select_related("user")
        .values_list("user__full_name", flat=True)
    )

    overdue_loans_qs = Loan.objects.filter(chama=chama, status__in=OVERDUE_LOAN_STATUSES)
    overdue_installment_loan_ids = list(
        InstallmentSchedule.objects.filter(
            loan__chama=chama,
            status="overdue",
        ).values_list("loan_id", flat=True).distinct()
    )
    overdue_loans = Loan.objects.filter(
        Q(id__in=overdue_installment_loan_ids) | Q(id__in=overdue_loans_qs.values_list("id", flat=True))
    ).distinct()
    overdue_loans_count = overdue_loans.count()

    monthly_contributions = _monthly_series(
        chama,
        model=Contribution,
        date_field="date_paid",
        months=6,
    )
    monthly_expenses = _monthly_series(
        chama,
        model=Expense,
        date_field="expense_date",
        months=6,
    )
    contribution_due_count = ContributionSchedule.objects.filter(
        chama=chama,
        scheduled_date__gte=month_start,
        scheduled_date__lte=today,
        is_active=True,
    ).count()
    contribution_paid_count = ContributionSchedule.objects.filter(
        chama=chama,
        scheduled_date__gte=month_start,
        scheduled_date__lte=today,
        is_active=True,
        status="paid",
    ).count()
    contribution_completion_rate = Decimal("100.00")
    if contribution_due_count:
        contribution_completion_rate = (
            Decimal(contribution_paid_count) / Decimal(contribution_due_count) * Decimal("100")
        ).quantize(Decimal("0.01"))

    attendance_rate = _meeting_attendance_rate(chama, active_members)
    expense_control_rate = _expense_control_rate(monthly_contributions, monthly_expenses)
    member_growth_rate = _member_growth_rate(chama, total_members)

    total_loans_outstanding = _to_decimal(
        Loan.objects.filter(
            chama=chama,
            status__in=[
                LoanStatus.ACTIVE,
                LoanStatus.OVERDUE,
                LoanStatus.DEFAULTED,
                LoanStatus.DEFAULTED_RECOVERING,
                LoanStatus.RESTRUCTURED,
            ],
        ).aggregate(total=Sum("total_due"))["total"]
    )

    health = compute_chama_health_score(
        chama_id=str(chama.id),
        total_members=total_members,
        active_members=active_members,
        total_savings=finance["total_savings"],
        total_loans_outstanding=total_loans_outstanding,
        overdue_loans_count=overdue_loans_count,
        contribution_completion_rate=contribution_completion_rate,
        meeting_attendance_rate=attendance_rate,
        expense_control_rate=expense_control_rate,
        member_growth_rate=member_growth_rate,
        monthly_contributions=monthly_contributions,
        monthly_expenses=monthly_expenses,
    )

    member_scores, members_data = _member_score_context(chama, memberships)
    risk_flags = _risk_context(chama, members_data)
    insights = generate_smart_insights(
        chama_id=str(chama.id),
        health_score=health,
        member_scores=member_scores,
        risk_flags=risk_flags,
        finance_summary={
            "total_savings": finance["total_savings"],
            "current_balance": finance["current_balance"],
            "overdue_loans_count": overdue_loans_count,
        },
    )

    unread_notifications = Notification.objects.filter(
        chama=chama,
        recipient=user,
        inbox_status=NotificationInboxStatus.UNREAD,
    ).count()
    unpaid_penalties_count = Penalty.objects.filter(
        chama=chama,
        status=PenaltyStatus.UNPAID,
    ).count()
    pending_approval_count = 0
    if effective_role in MANAGER_ROLES:
        pending_approval_count = (
            ApprovalRequest.objects.filter(chama=chama, status=ApprovalStatus.PENDING).count()
            + MembershipRequest.objects.filter(
                chama=chama,
                status__in=[MembershipRequestStatus.PENDING, MembershipRequestStatus.NEEDS_INFO],
            ).count()
            + Expense.objects.filter(chama=chama, status="pending").count()
            + PaymentIntent.objects.filter(
                chama=chama,
                intent_type=PaymentIntentType.WITHDRAWAL,
                status__in=PENDING_PAYMENT_STATUSES,
            ).count()
        )

    next_actions = []
    if pending_contribution_qs.filter(member=user).exists():
        next_actions.append(
            {
                "type": "contribution",
                "title": "Make your contribution",
                "description": "Clear your pending contribution to improve your reliability score.",
                "action_url": "/payments/make-contribution",
                "priority": "high",
            }
        )
    if effective_role in MANAGER_ROLES and overdue_contribution_qs.exists():
        next_actions.append(
            {
                "type": "reminders",
                "title": "Send contribution reminders",
                "description": f"{overdue_contribution_qs.count()} overdue contribution item(s) need follow-up.",
                "action_url": "/admin/contributions/overdue",
                "priority": "high",
            }
        )
    if effective_role in MANAGER_ROLES and overdue_loans_count:
        next_actions.append(
            {
                "type": "loan_followup",
                "title": "Review overdue loans",
                "description": f"{overdue_loans_count} loan(s) need recovery action or restructuring review.",
                "action_url": "/admin/loans/overdue",
                "priority": "high",
            }
        )
    if effective_role in MANAGER_ROLES and pending_approval_count:
        next_actions.append(
            {
                "type": "approvals",
                "title": "Clear pending approvals",
                "description": f"{pending_approval_count} queued approval item(s) are waiting on an admin decision.",
                "action_url": "/admin/approvals",
                "priority": "medium",
            }
        )
    if not Meeting.objects.filter(
        chama=chama,
        date__gte=timezone.now(),
        cancelled_at__isnull=True,
    ).exists():
        next_actions.append(
            {
                "type": "meeting",
                "title": "Schedule the next meeting",
                "description": "No future meeting is scheduled yet, which can hurt governance and attendance.",
                "action_url": "/meetings/create",
                "priority": "medium",
            }
        )
    if not next_actions:
        next_actions.append(
            {
                "type": "ai",
                "title": "Ask the AI assistant",
                "description": "Get a plain-language finance summary, compliance review, or admin checklist.",
                "action_url": "/ai/chat",
                "priority": "low",
            }
        )

    return {
        "membership_role": effective_role,
        "financial_summary": {
            "total_savings": str(finance["total_savings"]),
            "current_balance": str(finance["current_balance"]),
            "pending_contributions": pending_contribution_qs.count(),
            "overdue_loans": overdue_loans_count,
            "overdue_fines": unpaid_penalties_count,
        },
        "recent_transactions": _recent_transactions(chama),
        "upcoming_meetings": _upcoming_meetings(chama),
        "recent_announcements": _recent_announcements(chama),
        "unread_notifications": unread_notifications,
        "pending_approvals": pending_approval_count,
        "health_score": {
            "overall_score": health.overall_score,
            "financial_health": health.financial_health,
            "member_engagement": health.member_engagement,
            "governance_score": health.governance_score,
            "growth_score": health.growth_score,
            "risk_level": health.risk_level.value,
            "risk_flags": health.risk_flags,
            "insights": health.insights,
            "recommendations": health.recommendations,
        },
        "smart_insights": [
            {
                "type": insight.insight_type,
                "title": insight.title,
                "description": insight.description,
                "severity": insight.severity,
                "action_required": insight.action_required,
                "suggested_action": insight.suggested_action,
            }
            for insight in insights[:5]
        ],
        "next_actions": next_actions[:5],
        "analytics": {
            "monthly_contributions": [str(item) for item in monthly_contributions],
            "monthly_expenses": [str(item) for item in monthly_expenses],
            "contribution_completion_rate": str(contribution_completion_rate),
            "meeting_attendance_rate": str(attendance_rate),
            "expense_control_rate": str(expense_control_rate),
            "member_growth_rate": str(member_growth_rate),
        },
        "risk_flags": [
            {
                "type": flag.flag_type,
                "severity": flag.severity.value,
                "description": flag.description,
                "entity_type": flag.entity_type,
                "affected_entity": flag.affected_entity,
            }
            for flag in risk_flags
        ],
        "overdue_member_names": [name for name in overdue_member_names if name][:8],
    }


def build_admin_action_center(user, chama: Chama) -> dict:
    membership = get_active_membership_for_chama(user, chama.id)
    effective_role = get_effective_role(user, chama.id, membership) or membership.role
    if effective_role not in MANAGER_ROLES:
        raise PermissionDenied("Only admins, treasurers, secretaries, and auditors can access the admin action center.")

    risk_flags = _risk_context(chama, _member_score_context(chama, list(
        Membership.objects.select_related("user").filter(
            chama=chama,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            exited_at__isnull=True,
        )
    ))[1])

    pending_join_requests = MembershipRequest.objects.filter(
        chama=chama,
        status__in=[MembershipRequestStatus.PENDING, MembershipRequestStatus.NEEDS_INFO],
    ).count()
    pending_invites = chama.invites.filter(status="pending").count()
    pending_loan_requests = Loan.objects.filter(chama=chama, status__in=PENDING_LOAN_STATUSES).count() + LoanApplication.objects.filter(
        chama=chama,
        status__in=PENDING_LOAN_APPLICATION_STATUSES,
    ).count()
    pending_expense_requests = Expense.objects.filter(chama=chama, status="pending").count()
    pending_withdrawals = PaymentIntent.objects.filter(
        chama=chama,
        intent_type=PaymentIntentType.WITHDRAWAL,
        status__in=PENDING_PAYMENT_STATUSES,
    ).count()
    pending_approvals = ApprovalRequest.objects.filter(
        chama=chama,
        status=ApprovalStatus.PENDING,
    ).count()
    overdue_contributions = ContributionSchedule.objects.filter(
        chama=chama,
        status="pending",
        is_active=True,
        scheduled_date__lt=timezone.localdate(),
    ).count()
    overdue_loans = Loan.objects.filter(chama=chama, status__in=OVERDUE_LOAN_STATUSES).count()
    failed_notifications = Notification.objects.filter(
        chama=chama,
        status=NotificationStatus.FAILED,
    ).count()
    unresolved_disputes = Issue.objects.filter(
        chama=chama,
        status__in=OPEN_ISSUE_STATUSES,
    ).count()
    pending_policy_changes = ChamaRule.objects.filter(
        chama=chama,
        status=RuleStatus.PENDING_APPROVAL,
    ).count()
    pending_role_changes = RoleChange.objects.filter(
        chama=chama,
        status=RoleChangeStatus.PENDING,
    ).count()
    failed_payouts = MpesaB2CPayout.objects.filter(
        chama=chama,
        status__in=[MpesaB2CStatus.FAILED, MpesaB2CStatus.TIMEOUT],
    ).count()
    unusual_activity_flags = len(
        [flag for flag in risk_flags if flag.severity.value in {"high", "critical"}]
    )

    action_items = [
        {
            "type": "join_requests",
            "title": "Pending join requests",
            "count": pending_join_requests,
            "priority": "high" if pending_join_requests >= 3 else "medium",
            "action_url": "/admin/membership-requests",
            "description": f"{pending_join_requests} prospective member(s) are waiting for approval.",
        },
        {
            "type": "loan_requests",
            "title": "Pending loan reviews",
            "count": pending_loan_requests,
            "priority": "critical" if pending_loan_requests >= 5 else "high",
            "action_url": "/admin/loan-requests",
            "description": "Review loan eligibility, guarantor strength, and liquidity before approving more credit.",
        },
        {
            "type": "expenses",
            "title": "Expense approvals",
            "count": pending_expense_requests,
            "priority": "medium",
            "action_url": "/admin/expenses",
            "description": "Expense requests must be approved before they hit the ledger and treasury.",
        },
        {
            "type": "withdrawals",
            "title": "Pending withdrawals",
            "count": pending_withdrawals,
            "priority": "high" if pending_withdrawals else "medium",
            "action_url": "/admin/withdrawals",
            "description": "Verify approvals, beneficiary details, and liquidity before money leaves the chama.",
        },
        {
            "type": "overdue_loans",
            "title": "Overdue loans",
            "count": overdue_loans,
            "priority": "critical" if overdue_loans else "high",
            "action_url": "/admin/loans/overdue",
            "description": "Recovery, penalties, restructuring, or guarantor follow-up may be required.",
        },
        {
            "type": "overdue_contributions",
            "title": "Contribution arrears",
            "count": overdue_contributions,
            "priority": "high" if overdue_contributions >= 5 else "medium",
            "action_url": "/admin/contributions",
            "description": "Send reminders, escalate to SMS, and review member reliability before arrears worsen.",
        },
        {
            "type": "failed_notifications",
            "title": "Failed notifications",
            "count": failed_notifications,
            "priority": "medium",
            "action_url": "/admin/notifications/failed",
            "description": "Operational reminders or approvals may not be reaching members on time.",
        },
        {
            "type": "disputes",
            "title": "Unresolved disputes",
            "count": unresolved_disputes,
            "priority": "high" if unresolved_disputes else "medium",
            "action_url": "/admin/disputes",
            "description": "Open issues, escalations, or suspected fraud items need governance follow-up.",
        },
        {
            "type": "policy_changes",
            "title": "Pending policy changes",
            "count": pending_policy_changes,
            "priority": "medium",
            "action_url": "/admin/settings/policies",
            "description": "Pending governance updates should be reviewed and approved with a full audit trail.",
        },
        {
            "type": "role_changes",
            "title": "Pending role changes",
            "count": pending_role_changes,
            "priority": "medium",
            "action_url": "/admin/roles",
            "description": "Role changes affect approvals, permissions, and segregation of duties.",
        },
    ]
    for flag in risk_flags:
        if flag.severity.value not in {"high", "critical"}:
            continue
        action_items.append(
            {
                "type": "risk_flag",
                "title": flag.flag_type.replace("_", " ").title(),
                "count": 1,
                "priority": flag.severity.value,
                "action_url": "/admin/risk-flags",
                "description": flag.description,
            }
        )

    priority_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    visible_items = [item for item in action_items if item["count"] > 0]
    visible_items.sort(key=lambda item: (priority_rank.get(item["priority"], 4), -item["count"]))

    return {
        "action_items": visible_items[:12],
        "summary": {
            "total_pending": len(visible_items),
            "critical_count": sum(1 for item in visible_items if item["priority"] == "critical"),
            "high_count": sum(1 for item in visible_items if item["priority"] == "high"),
            "medium_count": sum(1 for item in visible_items if item["priority"] == "medium"),
            "low_count": sum(1 for item in visible_items if item["priority"] == "low"),
            "pending_join_requests": pending_join_requests,
            "pending_invites": pending_invites,
            "pending_loan_requests": pending_loan_requests,
            "pending_expense_requests": pending_expense_requests,
            "pending_withdrawals": pending_withdrawals,
            "pending_approvals": pending_approvals,
            "failed_notifications": failed_notifications,
            "unresolved_disputes": unresolved_disputes,
            "failed_payouts": failed_payouts,
            "unusual_activity_flags": unusual_activity_flags,
        },
    }


def answer_ai_question(user, chama: Chama, query: str) -> dict:
    membership = get_active_membership_for_chama(user, chama.id)
    effective_role = get_effective_role(user, chama.id, membership) or membership.role
    dashboard = build_smart_dashboard(user, chama)
    admin_center = None
    if effective_role in MANAGER_ROLES:
        admin_center = build_admin_action_center(user, chama)

    query_lower = (query or "").lower().strip()
    monthly_contributions = _to_decimal(dashboard["analytics"]["monthly_contributions"][-1])
    total_savings = _to_decimal(dashboard["financial_summary"]["total_savings"])
    current_balance = _to_decimal(dashboard["financial_summary"]["current_balance"])
    overdue_contribution_names = dashboard["overdue_member_names"]

    if "saved this month" in query_lower:
        response = (
            f"This chama has saved KES {monthly_contributions:,.2f} so far this month. "
            f"Total cumulative savings stand at KES {total_savings:,.2f} and the current ledger-backed balance is KES {current_balance:,.2f}."
        )
    elif "not contributed" in query_lower or "who has not contributed" in query_lower:
        overdue_count = dashboard["financial_summary"]["pending_contributions"]
        if effective_role in MANAGER_ROLES and overdue_contribution_names:
            sample = ", ".join(overdue_contribution_names[:5])
            response = f"{overdue_count} contribution item(s) are overdue. Members currently needing follow-up include {sample}."
        else:
            response = f"There are {overdue_count} overdue contribution item(s) in this chama."
    elif "overdue loan" in query_lower:
        overdue_count = dashboard["financial_summary"]["overdue_loans"]
        response = (
            f"There are {overdue_count} overdue loan(s) right now. "
            "Recovery actions should focus on reminders, penalties, restructuring eligibility, and guarantor notifications where applicable."
        )
    elif "health score" in query_lower or "how are we doing" in query_lower:
        health = dashboard["health_score"]
        response = (
            f"The chama health score is {health['overall_score']}/100 with a {health['risk_level']} risk profile. "
            f"Financial health is {health['financial_health']}/100 and governance is {health['governance_score']}/100."
        )
    elif "risk" in query_lower:
        risk_lines = [item["description"] for item in dashboard["risk_flags"][:3]]
        if admin_center and admin_center["summary"]["unusual_activity_flags"]:
            risk_lines.append(
                f"{admin_center['summary']['unusual_activity_flags']} unusual activity flag(s) are currently escalated."
            )
        response = "Top risks to review: " + " ".join(risk_lines) if risk_lines else "No high-severity risks are active right now."
    elif "summarize" in query_lower or "summary" in query_lower or "finances" in query_lower:
        response = (
            f"Current balance is KES {current_balance:,.2f}, total savings are KES {total_savings:,.2f}, "
            f"there are {dashboard['financial_summary']['overdue_loans']} overdue loan(s), "
            f"and {dashboard['financial_summary']['overdue_fines']} unpaid fine(s)."
        )
    elif "take today" in query_lower or "do today" in query_lower or "next action" in query_lower:
        action_lines = [f"{index + 1}. {item['title']}: {item['description']}" for index, item in enumerate(dashboard["next_actions"][:4])]
        response = "Recommended actions for today: " + " ".join(action_lines)
    else:
        ai_response = generate_ai_response(
            query=query,
            user_role=effective_role,
            chama_data={"name": chama.name},
            member_data={
                "total_members": Membership.objects.filter(chama=chama).count(),
                "active_members": Membership.objects.filter(chama=chama, status=MemberStatus.ACTIVE).count(),
            },
            finance_data={
                "total_savings": total_savings,
                "current_balance": current_balance,
                "monthly_contributions": monthly_contributions,
                "contribution_trend": (
                    "up"
                    if len(dashboard["analytics"]["monthly_contributions"]) >= 2
                    and _to_decimal(dashboard["analytics"]["monthly_contributions"][-1])
                    >= _to_decimal(dashboard["analytics"]["monthly_contributions"][-2])
                    else "down"
                ),
            },
            loan_data={
                "active_loans": Loan.objects.filter(chama=chama, status=LoanStatus.ACTIVE).count(),
                "overdue_loans": dashboard["financial_summary"]["overdue_loans"],
                "total_outstanding": _to_decimal(
                    Loan.objects.filter(
                        chama=chama,
                        status__in=[LoanStatus.ACTIVE, LoanStatus.OVERDUE, LoanStatus.DEFAULTED],
                    ).aggregate(total=Sum("total_due"))["total"]
                ),
            },
            meeting_data={
                "upcoming_meetings": len(dashboard["upcoming_meetings"]),
                "last_attendance_rate": dashboard["analytics"]["meeting_attendance_rate"],
            },
            health_score=dashboard["health_score"],
            insights=[
                {
                    "title": insight["title"],
                    "suggested_action": insight.get("suggested_action"),
                }
                for insight in dashboard["smart_insights"]
            ],
        )
        response = ai_response.response

    return {
        "query": query,
        "response": response,
        "confidence": "90",
        "data_sources": [
            "financial_snapshots",
            "contribution_schedules",
            "loans",
            "meetings",
            "notifications",
        ],
        "suggested_actions": [item["title"] for item in dashboard["next_actions"][:4]],
        "related_insights": [item["title"] for item in dashboard["smart_insights"][:4]],
    }
