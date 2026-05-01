from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView

from apps.accounts.models import UserPreference
from apps.automations.models import NotificationLog as AutomationNotificationLog
from apps.chama.models import (
    Membership,
    MembershipRequest,
    MembershipRequestStatus,
    MembershipRole,
    MemberStatus,
)
from apps.chama.services import get_effective_role
from apps.finance.models import (
    Contribution,
    InstallmentStatus,
    LedgerDirection,
    LedgerEntry,
    Loan,
    LoanStatus,
    ManualAdjustment,
    Repayment,
)
from apps.issues.models import Issue, IssuePriority, IssueStatus
from apps.meetings.models import Meeting, MinutesStatus
from apps.notifications.models import (
    Notification,
    NotificationInboxStatus,
    NotificationStatus,
)
from apps.payments.models import (
    PaymentIntent,
    PaymentIntentStatus,
    PaymentIntentType,
    PaymentReconciliationRun,
)
from apps.reports.models import ReportRun, ReportRunStatus

ACTIVE_LOAN_STATUSES = {
    LoanStatus.APPROVED,
    LoanStatus.DISBURSING,
    LoanStatus.DISBURSED,
    LoanStatus.ACTIVE,
}

DASHBOARD_BY_ROLE = {
    MembershipRole.SUPERADMIN: "dashboards:chama_admin_dashboard",
    MembershipRole.CHAMA_ADMIN: "dashboards:chama_admin_dashboard",
    MembershipRole.ADMIN: "dashboards:chama_admin_dashboard",
    MembershipRole.TREASURER: "dashboards:treasurer_dashboard",
    MembershipRole.SECRETARY: "dashboards:secretary_dashboard",
    MembershipRole.AUDITOR: "dashboards:auditor_dashboard",
    MembershipRole.MEMBER: "dashboards:member_dashboard",
}


@dataclass
class DashboardActivity:
    icon: str
    description: str
    timestamp: datetime
    amount: Decimal = Decimal("0.00")


def _membership_queryset(user):
    return (
        Membership.objects.select_related("chama")
        .filter(
            user=user,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        )
        .order_by("joined_at")
    )


def _resolve_membership(request, memberships):
    chama_id = request.GET.get("chama_id") or request.session.get("active_chama_id")
    if chama_id:
        membership = memberships.filter(chama_id=chama_id).first()
        if membership:
            request.session["active_chama_id"] = str(membership.chama_id)
            return membership

    preference = UserPreference.objects.filter(user=request.user).first()
    if preference and preference.active_chama_id:
        membership = memberships.filter(chama_id=preference.active_chama_id).first()
        if membership:
            request.session["active_chama_id"] = str(membership.chama_id)
            return membership

    membership = memberships.first()
    if membership:
        request.session["active_chama_id"] = str(membership.chama_id)
    return membership


def _sum_amount(queryset, field_name="amount"):
    return queryset.aggregate(
        total=Coalesce(
            Sum(field_name),
            Value(
                Decimal("0.00"),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            ),
        )
    )["total"]


def _to_aware_datetime(value):
    if isinstance(value, datetime):
        if timezone.is_naive(value):
            return timezone.make_aware(value, timezone.get_current_timezone())
        return value

    combined = datetime.combine(value, time.min)
    return timezone.make_aware(combined, timezone.get_current_timezone())


def _ledger_balance(chama):
    credits = _sum_amount(
        LedgerEntry.objects.filter(chama=chama, direction=LedgerDirection.CREDIT)
    )
    debits = _sum_amount(
        LedgerEntry.objects.filter(chama=chama, direction=LedgerDirection.DEBIT)
    )
    return credits - debits


def _issue_open_count(chama):
    return (
        Issue.objects.filter(chama=chama)
        .exclude(status__in=[IssueStatus.CLOSED, IssueStatus.RESOLVED])
        .count()
    )


def _member_outstanding_balance(loan):
    outstanding = _sum_amount(
        loan.installments.exclude(status=InstallmentStatus.PAID),
        field_name="expected_amount",
    )
    if outstanding > Decimal("0.00"):
        return outstanding

    repaid = _sum_amount(Repayment.objects.filter(loan=loan))
    remaining = loan.principal - repaid
    return remaining if remaining > Decimal("0.00") else Decimal("0.00")


def resolve_dashboard_route_for_request(request) -> str:
    if request.user.is_superuser or request.user.is_staff:
        return "dashboards:chama_admin_dashboard"

    memberships = _membership_queryset(request.user)
    membership = _resolve_membership(request, memberships)
    if not membership:
        suspended_membership = (
            Membership.objects.filter(
                user=request.user,
                status=MemberStatus.SUSPENDED,
                is_approved=True,
            )
            .order_by("-updated_at")
            .first()
        )
        if suspended_membership:
            return reverse("chama:join_status", kwargs={"status_slug": "suspended"})

        latest_request = (
            MembershipRequest.objects.filter(user=request.user)
            .order_by("-created_at")
            .first()
        )
        if latest_request:
            status_map = {
                MembershipRequestStatus.PENDING: "pending",
                MembershipRequestStatus.NEEDS_INFO: "needs-info",
                MembershipRequestStatus.REJECTED: "rejected",
                MembershipRequestStatus.EXPIRED: "expired",
                MembershipRequestStatus.CANCELLED: "cancelled",
                MembershipRequestStatus.APPROVED: "approved",
            }
            status_slug = status_map.get(latest_request.status, "pending")
            return reverse("chama:join_status", kwargs={"status_slug": status_slug})

        return reverse("chama:join_chama")

    role = (
        get_effective_role(request.user, membership.chama_id, membership)
        or membership.role
    )
    return DASHBOARD_BY_ROLE.get(role, "dashboards:member_dashboard")


@method_decorator(login_required, name="dispatch")
class BaseDashboardView(TemplateView):
    required_roles: set[str] | None = None
    require_superadmin: bool = False

    def dispatch(self, request, *args, **kwargs):
        self.memberships = _membership_queryset(request.user)
        self.active_membership = _resolve_membership(request, self.memberships)
        self.available_chamas = [membership.chama for membership in self.memberships]

        if self.require_superadmin and not (
            request.user.is_superuser or request.user.is_staff
        ):
            return render(request, "errors/403.html", status=403)

        if self.required_roles is not None:
            effective_role = (
                get_effective_role(
                    request.user,
                    self.active_membership.chama_id,
                    self.active_membership,
                )
                if self.active_membership
                else None
            )
            if not self.active_membership or effective_role not in self.required_roles:
                return render(request, "errors/403.html", status=403)

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        active_chama = self.active_membership.chama if self.active_membership else None
        unread_notifications_qs = Notification.objects.filter(
            recipient=self.request.user,
            inbox_status=NotificationInboxStatus.UNREAD,
        )
        if active_chama:
            unread_notifications_qs = unread_notifications_qs.filter(chama=active_chama)

        context["user"] = self.request.user
        context["current_user"] = self.request.user
        context["active_membership"] = self.active_membership
        context["available_chamas"] = self.available_chamas
        context["chama_switch_options"] = self.available_chamas
        context["active_chama_id"] = (
            str(self.active_membership.chama_id) if self.active_membership else None
        )
        context["current_chama"] = active_chama
        context["unread_notifications_count"] = unread_notifications_qs.count()
        context["open_issues_count"] = (
            _issue_open_count(self.active_membership.chama)
            if self.active_membership
            else 0
        )
        return context


class SuperAdminDashboardView(BaseDashboardView):
    template_name = "dashboards/superadmin_dashboard.html"
    require_superadmin = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Super Admin Dashboard"
        context["system_users"] = Membership.objects.values("user").distinct().count()
        context["system_chamas"] = Membership.objects.values("chama").distinct().count()
        context["system_open_issues"] = Issue.objects.exclude(
            status__in=[IssueStatus.CLOSED, IssueStatus.RESOLVED]
        ).count()
        return context


class MemberDashboardView(BaseDashboardView):
    template_name = "dashboards/member_dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Member Dashboard"
        context["notifications_widget_items"] = Notification.objects.filter(
            recipient=self.request.user,
            inbox_status=NotificationInboxStatus.UNREAD,
        ).order_by("-created_at")[:5]

        preference = UserPreference.objects.filter(user=self.request.user).first()
        context["low_data_mode"] = (
            bool(preference.low_data_mode) if preference else False
        )

        context["active_loan_id"] = None
        context["active_loan_balance"] = Decimal("0.00")
        context["active_loan_next_due"] = "-"
        context["loan_disbursement_status"] = "No pending disbursement"

        if not self.active_membership:
            return context

        loan = (
            Loan.objects.prefetch_related("installments")
            .filter(
                chama=self.active_membership.chama,
                member=self.request.user,
                status__in=ACTIVE_LOAN_STATUSES,
            )
            .order_by("-requested_at")
            .first()
        )

        if loan is None:
            return context

        next_due = (
            loan.installments.filter(
                status__in=[InstallmentStatus.DUE, InstallmentStatus.OVERDUE]
            )
            .order_by("due_date", "created_at")
            .first()
        )

        disbursement_intent = (
            PaymentIntent.objects.filter(
                chama=self.active_membership.chama,
                intent_type=PaymentIntentType.LOAN_DISBURSEMENT,
                reference_type="LOAN",
                reference_id=loan.id,
            )
            .order_by("-created_at")
            .first()
        )

        context["active_loan_id"] = loan.id
        context["active_loan_balance"] = _member_outstanding_balance(loan)
        context["active_loan_next_due"] = next_due.due_date if next_due else "-"
        if disbursement_intent:
            context["loan_disbursement_status"] = disbursement_intent.status.replace(
                "_", " "
            ).title()

        return context


class ChamaAdminDashboardView(BaseDashboardView):
    template_name = "dashboards/chama_admin_dashboard.html"
    required_roles = {MembershipRole.CHAMA_ADMIN}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Chama Admin Dashboard"
        chama = self.active_membership.chama

        total_members = Membership.objects.filter(
            chama=chama,
            is_active=True,
            is_approved=True,
            exited_at__isnull=True,
        ).count()
        total_contributions = _sum_amount(Contribution.objects.filter(chama=chama))
        active_loans = Loan.objects.filter(
            chama=chama, status__in=ACTIVE_LOAN_STATUSES
        ).count()
        current_balance = _ledger_balance(chama)

        chama.total_members = total_members
        chama.total_contributions = total_contributions
        chama.active_loans = active_loans
        chama.current_balance = current_balance

        activities: list[DashboardActivity] = []

        for issue in Issue.objects.filter(chama=chama).order_by("-created_at")[:4]:
            activities.append(
                DashboardActivity(
                    icon="triangle-exclamation",
                    description=f"Issue opened: {issue.title}",
                    timestamp=issue.created_at,
                )
            )

        for meeting in Meeting.objects.filter(chama=chama).order_by("-date")[:4]:
            activities.append(
                DashboardActivity(
                    icon="calendar-days",
                    description=f"Meeting scheduled: {meeting.title}",
                    timestamp=meeting.date,
                )
            )

        for contribution in Contribution.objects.filter(chama=chama).order_by(
            "-created_at"
        )[:4]:
            activities.append(
                DashboardActivity(
                    icon="money-bill-wave",
                    description=f"Contribution recorded for {contribution.member.full_name}",
                    timestamp=_to_aware_datetime(contribution.date_paid),
                    amount=contribution.amount,
                )
            )

        activities.sort(key=lambda row: row.timestamp, reverse=True)

        context["chama"] = chama
        context["recent_activities"] = activities[:12]
        context["open_issues"] = _issue_open_count(chama)
        context["pending_membership_requests"] = MembershipRequest.objects.filter(
            chama=chama,
            status__in=[
                MembershipRequestStatus.PENDING,
                MembershipRequestStatus.NEEDS_INFO,
            ],
        ).count()
        return context


class TreasurerDashboardView(BaseDashboardView):
    template_name = "dashboards/treasurer_dashboard.html"
    required_roles = {MembershipRole.TREASURER, MembershipRole.CHAMA_ADMIN}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Treasurer Dashboard"
        chama = self.active_membership.chama
        today = timezone.localdate()
        week_start = today - timedelta(days=6)
        month_start = today.replace(day=1)

        total_contributions = _sum_amount(Contribution.objects.filter(chama=chama))
        total_expenses = _sum_amount(
            ManualAdjustment.objects.filter(
                chama=chama, direction=LedgerDirection.DEBIT
            )
        )
        total_balance = _ledger_balance(chama)
        active_members = Membership.objects.filter(
            chama=chama,
            is_active=True,
            is_approved=True,
            exited_at__isnull=True,
        ).count()
        active_loans = Loan.objects.filter(
            chama=chama, status__in=ACTIVE_LOAN_STATUSES
        ).count()

        monthly_income = _sum_amount(
            Contribution.objects.filter(chama=chama, date_paid__gte=month_start)
        ) + _sum_amount(
            Repayment.objects.filter(loan__chama=chama, date_paid__gte=month_start)
        )
        monthly_expenses = _sum_amount(
            ManualAdjustment.objects.filter(
                chama=chama,
                direction=LedgerDirection.DEBIT,
                created_at__date__gte=month_start,
            )
        )

        pending_disbursements_count = PaymentIntent.objects.filter(
            chama=chama,
            intent_type=PaymentIntentType.LOAN_DISBURSEMENT,
            status__in=[PaymentIntentStatus.INITIATED, PaymentIntentStatus.PENDING],
        ).count()
        repayments_today = _sum_amount(
            Repayment.objects.filter(loan__chama=chama, date_paid=today)
        )
        repayments_week = _sum_amount(
            Repayment.objects.filter(loan__chama=chama, date_paid__gte=week_start)
        )
        overdue_loans_count = (
            Loan.objects.filter(
                chama=chama,
                installments__status=InstallmentStatus.OVERDUE,
            )
            .distinct()
            .count()
        )

        activities: list[DashboardActivity] = []

        for repayment in (
            Repayment.objects.filter(loan__chama=chama)
            .select_related("loan")
            .order_by("-date_paid")[:5]
        ):
            activities.append(
                DashboardActivity(
                    icon="money-check-dollar",
                    description=f"Loan repayment posted ({repayment.loan_id})",
                    timestamp=_to_aware_datetime(repayment.date_paid),
                    amount=repayment.amount,
                )
            )

        for contribution in (
            Contribution.objects.filter(chama=chama)
            .select_related("member")
            .order_by("-date_paid")[:5]
        ):
            activities.append(
                DashboardActivity(
                    icon="hand-holding-dollar",
                    description=f"Contribution from {contribution.member.full_name}",
                    timestamp=_to_aware_datetime(contribution.date_paid),
                    amount=contribution.amount,
                )
            )

        for adjustment in ManualAdjustment.objects.filter(chama=chama).order_by(
            "-created_at"
        )[:5]:
            prefix = (
                "Expense"
                if adjustment.direction == LedgerDirection.DEBIT
                else "Adjustment"
            )
            activities.append(
                DashboardActivity(
                    icon="file-invoice-dollar",
                    description=f"{prefix}: {adjustment.reason}",
                    timestamp=adjustment.created_at,
                    amount=adjustment.amount,
                )
            )

        activities.sort(key=lambda row: row.timestamp, reverse=True)

        context.update(
            {
                "total_balance": total_balance,
                "total_contributions": total_contributions,
                "total_expenses": total_expenses,
                "active_members": active_members,
                "active_loans": active_loans,
                "monthly_income": monthly_income,
                "monthly_expenses": monthly_expenses,
                "pending_disbursements_count": pending_disbursements_count,
                "repayments_today": repayments_today,
                "repayments_week": repayments_week,
                "overdue_loans_count": overdue_loans_count,
                "financial_activities": activities[:12],
                "open_issues": _issue_open_count(chama),
            }
        )
        return context


class SecretaryDashboardView(BaseDashboardView):
    template_name = "dashboards/secretary_dashboard.html"
    required_roles = {MembershipRole.SECRETARY, MembershipRole.CHAMA_ADMIN}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Secretary Dashboard"
        chama = self.active_membership.chama
        now = timezone.now()
        month_start = timezone.localdate().replace(day=1)

        total_members = Membership.objects.filter(
            chama=chama, exited_at__isnull=True
        ).count()
        active_members = Membership.objects.filter(
            chama=chama,
            is_active=True,
            is_approved=True,
            exited_at__isnull=True,
        ).count()
        meetings_this_month = Meeting.objects.filter(
            chama=chama,
            date__date__gte=month_start,
        ).count()
        upcoming_meetings = Meeting.objects.filter(chama=chama, date__gte=now).count()
        pending_minutes = Meeting.objects.filter(
            chama=chama,
            minutes_status__in=[MinutesStatus.DRAFT, MinutesStatus.PENDING_APPROVAL],
        ).count()
        pending_membership_requests = MembershipRequest.objects.filter(
            chama=chama,
            status__in=[
                MembershipRequestStatus.PENDING,
                MembershipRequestStatus.NEEDS_INFO,
            ],
        ).count()
        minutes_recorded = (
            Meeting.objects.filter(chama=chama).exclude(minutes_text="").count()
        )
        notifications_sent = Notification.objects.filter(
            chama=chama,
            status=NotificationStatus.SENT,
        ).count()

        activities: list[DashboardActivity] = []

        for meeting in Meeting.objects.filter(chama=chama).order_by("-created_at")[:6]:
            activities.append(
                DashboardActivity(
                    icon="calendar-check",
                    description=f"Meeting updated: {meeting.title}",
                    timestamp=meeting.created_at,
                )
            )

        for issue in Issue.objects.filter(chama=chama).order_by("-updated_at")[:6]:
            activities.append(
                DashboardActivity(
                    icon="clipboard-list",
                    description=f"Issue {issue.status.replace('_', ' ')}: {issue.title}",
                    timestamp=issue.updated_at,
                )
            )

        activities.sort(key=lambda row: row.timestamp, reverse=True)

        context.update(
            {
                "total_members": total_members,
                "active_members": active_members,
                "meetings_this_month": meetings_this_month,
                "upcoming_meetings": upcoming_meetings,
                "pending_minutes": pending_minutes,
                "notifications_sent": notifications_sent,
                "minutes_recorded": minutes_recorded,
                "pending_membership_requests": pending_membership_requests,
                "admin_activities": activities[:12],
                "open_issues": _issue_open_count(chama),
            }
        )
        return context


class AuditorDashboardView(BaseDashboardView):
    template_name = "dashboards/auditor_dashboard.html"
    required_roles = {MembershipRole.AUDITOR, MembershipRole.CHAMA_ADMIN}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Auditor Dashboard"
        chama = self.active_membership.chama
        now = timezone.now()
        month_start = timezone.localdate().replace(day=1)

        transactions_reviewed = LedgerEntry.objects.filter(
            chama=chama,
            created_at__date__gte=month_start,
        ).count()
        clean_audits = PaymentReconciliationRun.objects.filter(
            chama=chama,
            status="SUCCESS",
        ).count()
        audits_completed = ReportRun.objects.filter(
            chama=chama,
            status=ReportRunStatus.SUCCESS,
        ).count()
        open_issues = _issue_open_count(chama)
        pending_reviews = (
            Loan.objects.filter(chama=chama, status=LoanStatus.REQUESTED).count()
            + PaymentIntent.objects.filter(
                chama=chama,
                status__in=[PaymentIntentStatus.INITIATED, PaymentIntentStatus.PENDING],
            ).count()
        )
        overdue_loans = (
            Loan.objects.filter(
                chama=chama,
                installments__status=InstallmentStatus.OVERDUE,
            )
            .distinct()
            .count()
        )
        failed_payments = PaymentIntent.objects.filter(
            chama=chama,
            status=PaymentIntentStatus.FAILED,
        ).count()
        high_priority_open = (
            Issue.objects.filter(
                chama=chama,
                priority__in=[IssuePriority.HIGH, IssuePriority.URGENT],
            )
            .exclude(status__in=[IssueStatus.CLOSED, IssueStatus.RESOLVED])
            .count()
        )

        risk_flags = overdue_loans + failed_payments + high_priority_open
        compliance_score = max(0, 100 - min(80, risk_flags * 5))

        activities: list[DashboardActivity] = []

        for run in PaymentReconciliationRun.objects.filter(chama=chama).order_by(
            "-run_at"
        )[:5]:
            activities.append(
                DashboardActivity(
                    icon="scale-balanced",
                    description=f"Reconciliation run: {run.status}",
                    timestamp=run.run_at,
                )
            )

        for report in ReportRun.objects.filter(chama=chama).order_by("-created_at")[:5]:
            activities.append(
                DashboardActivity(
                    icon="file-circle-check",
                    description=f"Report generated: {report.report_type}",
                    timestamp=report.created_at,
                )
            )

        for log in AutomationNotificationLog.objects.filter(chama=chama).order_by(
            "-created_at"
        )[:5]:
            activities.append(
                DashboardActivity(
                    icon="bell",
                    description=f"Automation notification: {log.status}",
                    timestamp=log.created_at,
                )
            )

        activities.sort(key=lambda row: row.timestamp, reverse=True)

        context.update(
            {
                "transactions_reviewed": transactions_reviewed,
                "audits_completed": audits_completed,
                "compliance_score": compliance_score,
                "open_issues": open_issues,
                "pending_reviews": pending_reviews,
                "risk_flags": risk_flags,
                "clean_audits": clean_audits,
                "audit_activities": activities[:12],
                "now": now,
            }
        )
        return context


# Function-based views for backward compatibility
@login_required
def superadmin_dashboard_view(request):
    return SuperAdminDashboardView.as_view()(request)


@login_required
def member_dashboard_view(request):
    return MemberDashboardView.as_view()(request)


@login_required
def chama_admin_dashboard_view(request):
    return ChamaAdminDashboardView.as_view()(request)


@login_required
def treasurer_dashboard_view(request):
    return TreasurerDashboardView.as_view()(request)


# ============================================================================
# TREASURER PAGES VIEWS
# ============================================================================

from apps.finance.models import (
    ContributionType,
    LoanProduct,
    Penalty,
)
from apps.payments.models import (
    CallbackLog,
)


@method_decorator(login_required, name="dispatch")
class TreasurerApprovalsBaseView(BaseDashboardView):
    """Base view for treasurer approval pages."""
    required_roles = {MembershipRole.TREASURER, MembershipRole.CHAMA_ADMIN}

    def get_pending_loans_count(self):
        return Loan.objects.filter(
            chama=self.active_membership.chama,
            status=LoanStatus.REQUESTED,
        ).count()

    def get_pending_withdrawals_count(self):
        return PaymentIntent.objects.filter(
            chama=self.active_membership.chama,
            intent_type=PaymentIntentType.WITHDRAWAL,
            status__in=[PaymentIntentStatus.INITIATED, PaymentIntentStatus.PENDING],
        ).count()

    def get_pending_disbursements_count(self):
        return PaymentIntent.objects.filter(
            chama=self.active_membership.chama,
            intent_type=PaymentIntentType.LOAN_DISBURSEMENT,
            status__in=[PaymentIntentStatus.INITIATED, PaymentIntentStatus.PENDING],
        ).count()


@login_required
def treasurer_approvals_view(request):
    """Main approvals center for treasurer."""
    class View(TreasurerApprovalsBaseView):
        template_name = "dashboards/treasurer/approvals.html"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            chama = self.active_membership.chama

            # Get counts for each approval type
            pending_loans = Loan.objects.filter(
                chama=chama, status=LoanStatus.REQUESTED
            ).select_related("member")

            pending_withdrawals = PaymentIntent.objects.filter(
                chama=chama,
                intent_type=PaymentIntentType.WITHDRAWAL,
                status__in=[PaymentIntentStatus.INITIATED, PaymentIntentStatus.PENDING],
            ).select_related("member")

            pending_disbursements = PaymentIntent.objects.filter(
                chama=chama,
                intent_type=PaymentIntentType.LOAN_DISBURSEMENT,
                status__in=[PaymentIntentStatus.INITIATED, PaymentIntentStatus.PENDING],
            ).select_related("member")

            context.update({
                "title": "Approvals Center",
                "pending_loans_count": pending_loans.count(),
                "pending_withdrawals_count": pending_withdrawals.count(),
                "pending_disbursements_count": pending_disbursements.count(),
                "pending_loans": pending_loans[:10],
                "pending_withdrawals": pending_withdrawals[:10],
                "pending_disbursements": pending_disbursements[:10],
            })
            return context

    return View.as_view()(request)


@login_required
def treasurer_loan_approvals_view(request):
    """Loan approvals page for treasurer."""
    class View(TreasurerApprovalsBaseView):
        template_name = "dashboards/treasurer/loan_approvals.html"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            chama = self.active_membership.chama

            # Get requested loans
            pending_loans = Loan.objects.filter(
                chama=chama, status=LoanStatus.REQUESTED
            ).select_related("member").prefetch_related("installments")

            # Get approved loans waiting for disbursement
            approved_loans = Loan.objects.filter(
                chama=chama, status=LoanStatus.APPROVED
            ).select_related("member")

            context.update({
                "title": "Loan Approvals",
                "pending_loans": pending_loans,
                "approved_loans": approved_loans,
                "pending_count": pending_loans.count(),
            })
            return context

    return View.as_view()(request)


@login_required
def treasurer_withdrawal_approvals_view(request):
    """Withdrawal approvals page for treasurer."""
    class View(TreasurerApprovalsBaseView):
        template_name = "dashboards/treasurer/withdrawal_approvals.html"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            chama = self.active_membership.chama

            pending_withdrawals = PaymentIntent.objects.filter(
                chama=chama,
                intent_type=PaymentIntentType.WITHDRAWAL,
                status__in=[PaymentIntentStatus.INITIATED, PaymentIntentStatus.PENDING],
            ).select_related("member").order_by("-created_at")

            completed_withdrawals = PaymentIntent.objects.filter(
                chama=chama,
                intent_type=PaymentIntentType.WITHDRAWAL,
                status=PaymentIntentStatus.COMPLETED,
            ).select_related("member").order_by("-created_at")[:20]

            context.update({
                "title": "Withdrawal Approvals",
                "pending_withdrawals": pending_withdrawals,
                "completed_withdrawals": completed_withdrawals,
                "pending_count": pending_withdrawals.count(),
            })
            return context

    return View.as_view()(request)


@login_required
def treasurer_disbursement_approvals_view(request):
    """Disbursement approvals page for treasurer."""
    class View(TreasurerApprovalsBaseView):
        template_name = "dashboards/treasurer/disbursement_approvals.html"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            chama = self.active_membership.chama

            pending_disbursements = PaymentIntent.objects.filter(
                chama=chama,
                intent_type=PaymentIntentType.LOAN_DISBURSEMENT,
                status__in=[PaymentIntentStatus.INITIATED, PaymentIntentStatus.PENDING],
            ).select_related("member").order_by("-created_at")

            completed_disbursements = PaymentIntent.objects.filter(
                chama=chama,
                intent_type=PaymentIntentType.LOAN_DISBURSEMENT,
                status=PaymentIntentStatus.COMPLETED,
            ).select_related("member").order_by("-created_at")[:20]

            context.update({
                "title": "Disbursement Approvals",
                "pending_disbursements": pending_disbursements,
                "completed_disbursements": completed_disbursements,
                "pending_count": pending_disbursements.count(),
            })
            return context

    return View.as_view()(request)


@login_required
def treasurer_wallet_view(request):
    """Wallet and cashflow page for treasurer."""
    class View(TreasurerApprovalsBaseView):
        template_name = "dashboards/treasurer/wallet.html"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            chama = self.active_membership.chama

            # Wallet balance
            total_balance = _ledger_balance(chama)

            # Pending amounts
            pending_disbursements = _sum_amount(
                PaymentIntent.objects.filter(
                    chama=chama,
                    intent_type=PaymentIntentType.LOAN_DISBURSEMENT,
                    status__in=[PaymentIntentStatus.INITIATED, PaymentIntentStatus.PENDING],
                )
            )

            pending_withdrawals = _sum_amount(
                PaymentIntent.objects.filter(
                    chama=chama,
                    intent_type=PaymentIntentType.WITHDRAWAL,
                    status__in=[PaymentIntentStatus.INITIATED, PaymentIntentStatus.PENDING],
                )
            )

            # Cashflow data - last 30 days
            today = timezone.localdate()
            thirty_days_ago = today - timedelta(days=30)

            contributions_30d = _sum_amount(
                Contribution.objects.filter(
                    chama=chama, date_paid__gte=thirty_days_ago
                )
            )
            repayments_30d = _sum_amount(
                Repayment.objects.filter(
                    loan__chama=chama, date_paid__gte=thirty_days_ago
                )
            )
            expenses_30d = _sum_amount(
                ManualAdjustment.objects.filter(
                    chama=chama,
                    direction=LedgerDirection.DEBIT,
                    created_at__date__gte=thirty_days_ago,
                )
            )

            # Cashflow breakdown by category
            contribution_income = _sum_amount(
                Contribution.objects.filter(chama=chama)
            )
            loan_repayments = _sum_amount(
                Repayment.objects.filter(loan__chama=chama)
            )

            context.update({
                "title": "Wallet & Cashflow",
                "total_balance": total_balance,
                "pending_disbursements": pending_disbursements,
                "pending_withdrawals": pending_withdrawals,
                "available_balance": total_balance - pending_disbursements - pending_withdrawals,
                "contributions_30d": contributions_30d,
                "repayments_30d": repayments_30d,
                "expenses_30d": expenses_30d,
                "total_inflow": contribution_income + loan_repayments,
                "contribution_income": contribution_income,
                "loan_repayments": loan_repayments,
            })
            return context

    return View.as_view()(request)


@login_required
def treasurer_contributions_view(request):
    """Contributions management page for treasurer."""
    class View(TreasurerApprovalsBaseView):
        template_name = "dashboards/treasurer/contributions.html"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            chama = self.active_membership.chama

            # Get contribution types
            contribution_types = ContributionType.objects.filter(chama=chama)

            # Get recent contributions
            recent_contributions = Contribution.objects.filter(
                chama=chama
            ).select_related("member").order_by("-date_paid")[:50]

            # Calculate totals
            total_contributions = _sum_amount(
                Contribution.objects.filter(chama=chama)
            )

            today = timezone.localdate()
            month_start = today.replace(day=1)
            monthly_contributions = _sum_amount(
                Contribution.objects.filter(
                    chama=chama, date_paid__gte=month_start
                )
            )

            context.update({
                "title": "Contributions",
                "contribution_types": contribution_types,
                "recent_contributions": recent_contributions,
                "total_contributions": total_contributions,
                "monthly_contributions": monthly_contributions,
                "member_count": Membership.objects.filter(
                    chama=chama, is_active=True, is_approved=True
                ).count(),
            })
            return context

    return View.as_view()(request)


@login_required
def treasurer_loans_view(request):
    """Loans management page for treasurer."""
    class View(TreasurerApprovalsBaseView):
        template_name = "dashboards/treasurer/loans.html"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            chama = self.active_membership.chama

            # Get loan counts by status
            pending_count = Loan.objects.filter(
                chama=chama, status=LoanStatus.REQUESTED
            ).count()

            active_count = Loan.objects.filter(
                chama=chama, status__in=ACTIVE_LOAN_STATUSES
            ).count()

            overdue_count = (
                Loan.objects.filter(
                    chama=chama,
                    installments__status=InstallmentStatus.OVERDUE,
                ).distinct().count()
            )

            # Get active loans
            active_loans = Loan.objects.filter(
                chama=chama, status__in=ACTIVE_LOAN_STATUSES
            ).select_related("member").prefetch_related("installments")[:20]

            # Calculate total outstanding
            total_outstanding = Decimal("0.00")
            for loan in Loan.objects.filter(chama=chama, status__in=ACTIVE_LOAN_STATUSES):
                outstanding = _sum_amount(
                    loan.installments.exclude(status=InstallmentStatus.PAID),
                    field_name="expected_amount",
                )
                if outstanding > Decimal("0.00"):
                    total_outstanding += outstanding
                else:
                    repaid = _sum_amount(Repayment.objects.filter(loan=loan))
                    remaining = loan.principal - repaid
                    if remaining > Decimal("0.00"):
                        total_outstanding += remaining

            context.update({
                "title": "Loans Management",
                "pending_count": pending_count,
                "active_count": active_count,
                "overdue_count": overdue_count,
                "active_loans": active_loans,
                "total_outstanding": total_outstanding,
            })
            return context

    return View.as_view()(request)


@login_required
def treasurer_loans_pending_view(request):
    """Pending loans page for treasurer."""
    class View(TreasurerApprovalsBaseView):
        template_name = "dashboards/treasurer/loans_pending.html"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            chama = self.active_membership.chama

            pending_loans = Loan.objects.filter(
                chama=chama, status=LoanStatus.REQUESTED
            ).select_related("member").prefetch_related("installments").order_by("-requested_at")

            context.update({
                "title": "Pending Loans",
                "pending_loans": pending_loans,
                "pending_count": pending_loans.count(),
            })
            return context

    return View.as_view()(request)


@login_required
def treasurer_loans_active_view(request):
    """Active loans page for treasurer."""
    class View(TreasurerApprovalsBaseView):
        template_name = "dashboards/treasurer/loans_active.html"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            chama = self.active_membership.chama

            active_loans = Loan.objects.filter(
                chama=chama, status__in=ACTIVE_LOAN_STATUSES
            ).select_related("member").prefetch_related("installments").order_by("-disbursed_at")

            context.update({
                "title": "Active Loans",
                "active_loans": active_loans,
                "active_count": active_loans.count(),
            })
            return context

    return View.as_view()(request)


@login_required
def treasurer_loans_overdue_view(request):
    """Overdue loans page for treasurer."""
    class View(TreasurerApprovalsBaseView):
        template_name = "dashboards/treasurer/loans_overdue.html"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            chama = self.active_membership.chama

            overdue_loans = Loan.objects.filter(
                chama=chama,
                installments__status=InstallmentStatus.OVERDUE,
            ).distinct().select_related("member").prefetch_related("installments")

            # Calculate total overdue amount
            total_overdue = Decimal("0.00")
            for loan in overdue_loans:
                overdue_amount = _sum_amount(
                    loan.installments.filter(status=InstallmentStatus.OVERDUE),
                    field_name="expected_amount",
                )
                total_overdue += overdue_amount

            context.update({
                "title": "Overdue Loans",
                "overdue_loans": overdue_loans,
                "overdue_count": overdue_loans.count(),
                "total_overdue": total_overdue,
            })
            return context

    return View.as_view()(request)


@login_required
def treasurer_disbursements_view(request):
    """Disbursements page for treasurer."""
    class View(TreasurerApprovalsBaseView):
        template_name = "dashboards/treasurer/disbursements.html"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            chama = self.active_membership.chama

            pending_disbursements = PaymentIntent.objects.filter(
                chama=chama,
                intent_type=PaymentIntentType.LOAN_DISBURSEMENT,
                status__in=[PaymentIntentStatus.INITIATED, PaymentIntentStatus.PENDING],
            ).select_related("member").order_by("-created_at")

            completed_disbursements = PaymentIntent.objects.filter(
                chama=chama,
                intent_type=PaymentIntentType.LOAN_DISBURSEMENT,
                status=PaymentIntentStatus.COMPLETED,
            ).select_related("member").order_by("-created_at")[:50]

            failed_disbursements = PaymentIntent.objects.filter(
                chama=chama,
                intent_type=PaymentIntentType.LOAN_DISBURSEMENT,
                status=PaymentIntentStatus.FAILED,
            ).select_related("member").order_by("-created_at")[:20]

            total_disbursed = _sum_amount(
                PaymentIntent.objects.filter(
                    chama=chama,
                    intent_type=PaymentIntentType.LOAN_DISBURSEMENT,
                    status=PaymentIntentStatus.COMPLETED,
                )
            )

            context.update({
                "title": "Disbursements",
                "pending_disbursements": pending_disbursements,
                "completed_disbursements": completed_disbursements,
                "failed_disbursements": failed_disbursements,
                "pending_count": pending_disbursements.count(),
                "total_disbursed": total_disbursed,
            })
            return context

    return View.as_view()(request)


@login_required
def treasurer_withdrawals_view(request):
    """Withdrawals page for treasurer."""
    class View(TreasurerApprovalsBaseView):
        template_name = "dashboards/treasurer/withdrawals.html"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            chama = self.active_membership.chama

            pending_withdrawals = PaymentIntent.objects.filter(
                chama=chama,
                intent_type=PaymentIntentType.WITHDRAWAL,
                status__in=[PaymentIntentStatus.INITIATED, PaymentIntentStatus.PENDING],
            ).select_related("member").order_by("-created_at")

            completed_withdrawals = PaymentIntent.objects.filter(
                chama=chama,
                intent_type=PaymentIntentType.WITHDRAWAL,
                status=PaymentIntentStatus.COMPLETED,
            ).select_related("member").order_by("-created_at")[:50]

            failed_withdrawals = PaymentIntent.objects.filter(
                chama=chama,
                intent_type=PaymentIntentType.WITHDRAWAL,
                status=PaymentIntentStatus.FAILED,
            ).select_related("member").order_by("-created_at")[:20]

            total_withdrawn = _sum_amount(
                PaymentIntent.objects.filter(
                    chama=chama,
                    intent_type=PaymentIntentType.WITHDRAWAL,
                    status=PaymentIntentStatus.COMPLETED,
                )
            )

            context.update({
                "title": "Withdrawals",
                "pending_withdrawals": pending_withdrawals,
                "completed_withdrawals": completed_withdrawals,
                "failed_withdrawals": failed_withdrawals,
                "pending_count": pending_withdrawals.count(),
                "total_withdrawn": total_withdrawn,
            })
            return context

    return View.as_view()(request)


@login_required
def treasurer_transactions_view(request):
    """Transactions page for treasurer."""
    class View(TreasurerApprovalsBaseView):
        template_name = "dashboards/treasurer/transactions.html"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            chama = self.active_membership.chama

            # Get all payment intents for the chama
            all_transactions = PaymentIntent.objects.filter(
                chama=chama
            ).select_related("member").order_by("-created_at")[:100]

            # Get counts by status
            pending_count = all_transactions.filter(
                status__in=[PaymentIntentStatus.INITIATED, PaymentIntentStatus.PENDING]
            ).count()
            completed_count = all_transactions.filter(
                status=PaymentIntentStatus.COMPLETED
            ).count()
            failed_count = all_transactions.filter(
                status=PaymentIntentStatus.FAILED
            ).count()

            context.update({
                "title": "Transactions",
                "transactions": all_transactions,
                "pending_count": pending_count,
                "completed_count": completed_count,
                "failed_count": failed_count,
                "total_count": all_transactions.count(),
            })
            return context

    return View.as_view()(request)


@login_required
def treasurer_ledger_view(request):
    """Ledger page for treasurer."""
    class View(TreasurerApprovalsBaseView):
        template_name = "dashboards/treasurer/ledger.html"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            chama = self.active_membership.chama

            # Get ledger entries
            ledger_entries = LedgerEntry.objects.filter(
                chama=chama
            ).select_related("created_by").order_by("-created_at")[:100]

            # Calculate totals
            total_credits = _sum_amount(
                LedgerEntry.objects.filter(chama=chama, direction=LedgerDirection.CREDIT)
            )
            total_debits = _sum_amount(
                LedgerEntry.objects.filter(chama=chama, direction=LedgerDirection.DEBIT)
            )
            balance = total_credits - total_debits

            # Get manual adjustments
            recent_adjustments = ManualAdjustment.objects.filter(
                chama=chama
            ).select_related("created_by").order_by("-created_at")[:20]

            context.update({
                "title": "Ledger",
                "ledger_entries": ledger_entries,
                "recent_adjustments": recent_adjustments,
                "total_credits": total_credits,
                "total_debits": total_debits,
                "balance": balance,
            })
            return context

    return View.as_view()(request)


@login_required
def treasurer_penalties_view(request):
    """Penalties page for treasurer."""
    class View(TreasurerApprovalsBaseView):
        template_name = "dashboards/treasurer/penalties.html"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            chama = self.active_membership.chama

            # Get penalties
            all_penalties = Penalty.objects.filter(
                chama=chama
            ).select_related("member", "issued_by").order_by("-created_at")[:50]

            pending_penalties = all_penalties.filter(is_paid=False)
            paid_penalties = all_penalties.filter(is_paid=True)

            total_pending = _sum_amount(pending_penalties, field_name="amount")
            total_collected = _sum_amount(paid_penalties, field_name="amount")

            context.update({
                "title": "Penalties",
                "all_penalties": all_penalties,
                "pending_penalties": pending_penalties,
                "paid_penalties": paid_penalties,
                "pending_count": pending_penalties.count(),
                "total_pending": total_pending,
                "total_collected": total_collected,
            })
            return context

    return View.as_view()(request)


@login_required
def treasurer_mpesa_view(request):
    """M-Pesa reconciliation page for treasurer."""
    class View(TreasurerApprovalsBaseView):
        template_name = "dashboards/treasurer/mpesa.html"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            chama = self.active_membership.chama

            # Get M-Pesa callbacks
            recent_callbacks = CallbackLog.objects.filter(
                chama=chama
            ).order_by("-created_at")[:50]

            # Get reconciliation runs
            reconciliation_runs = PaymentReconciliationRun.objects.filter(
                chama=chama
            ).order_by("-run_at")[:20]

            # Get failed callbacks count
            failed_callbacks_count = recent_callbacks.filter(
                is_valid=False
            ).count()

            # Get last successful reconciliation
            last_successful_run = reconciliation_runs.filter(
                status="SUCCESS"
            ).first()

            context.update({
                "title": "M-Pesa Reconciliation",
                "recent_callbacks": recent_callbacks,
                "reconciliation_runs": reconciliation_runs,
                "failed_callbacks_count": failed_callbacks_count,
                "last_successful_run": last_successful_run,
                "callbacks_count": recent_callbacks.count(),
            })
            return context

    return View.as_view()(request)


@login_required
def treasurer_reports_view(request):
    """Reports page for treasurer."""
    class View(TreasurerApprovalsBaseView):
        template_name = "dashboards/treasurer/reports.html"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            chama = self.active_membership.chama

            # Get report runs
            report_runs = ReportRun.objects.filter(
                chama=chama
            ).order_by("-created_at")[:20]

            context.update({
                "title": "Reports",
                "report_runs": report_runs,
                "report_count": report_runs.count(),
            })
            return context

    return View.as_view()(request)


@login_required
def treasurer_audit_view(request):
    """Audit and compliance page for treasurer."""
    class View(TreasurerApprovalsBaseView):
        template_name = "dashboards/treasurer/audit.html"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            chama = self.active_membership.chama

            # Get recent ledger entries for audit trail
            recent_ledger = LedgerEntry.objects.filter(
                chama=chama
            ).select_related("created_by").order_by("-created_at")[:50]

            # Get recent payment intents for audit
            recent_payments = PaymentIntent.objects.filter(
                chama=chama
            ).select_related("member").order_by("-created_at")[:50]

            # Get reconciliation runs
            reconciliation_runs = PaymentReconciliationRun.objects.filter(
                chama=chama
            ).order_by("-run_at")[:10]

            # Calculate compliance metrics
            successful_reconciliations = reconciliation_runs.filter(
                status="SUCCESS"
            ).count()
            total_reconciliations = reconciliation_runs.count()
            compliance_score = (
                (successful_reconciliations / total_reconciliations * 100)
                if total_reconciliations > 0
                else 100
            )

            context.update({
                "title": "Audit & Compliance",
                "recent_ledger": recent_ledger,
                "recent_payments": recent_payments,
                "reconciliation_runs": reconciliation_runs,
                "compliance_score": compliance_score,
                "successful_reconciliations": successful_reconciliations,
                "total_reconciliations": total_reconciliations,
            })
            return context

    return View.as_view()(request)


@login_required
def treasurer_notifications_view(request):
    """Notifications page for treasurer."""
    class View(TreasurerApprovalsBaseView):
        template_name = "dashboards/treasurer/notifications.html"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            chama = self.active_membership.chama

            # Get notifications for this chama
            notifications = Notification.objects.filter(
                chama=chama, recipient=self.request.user
            ).order_by("-created_at")[:50]

            unread_count = notifications.filter(
                inbox_status=NotificationInboxStatus.UNREAD
            ).count()

            context.update({
                "title": "Notifications",
                "notifications": notifications,
                "unread_count": unread_count,
            })
            return context

    return View.as_view()(request)


@login_required
def treasurer_ai_view(request):
    """AI Risk & Insights page for treasurer."""
    class View(TreasurerApprovalsBaseView):
        template_name = "dashboards/treasurer/ai.html"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            chama = self.active_membership.chama

            # Get overdue loans for risk assessment
            overdue_loans = Loan.objects.filter(
                chama=chama,
                installments__status=InstallmentStatus.OVERDUE,
            ).distinct().select_related("member")[:20]

            # Calculate risk metrics
            total_loans = Loan.objects.filter(chama=chama).count()
            overdue_count = overdue_loans.count()
            risk_percentage = (
                (overdue_count / total_loans * 100)
                if total_loans > 0
                else 0
            )

            context.update({
                "title": "AI Risk & Insights",
                "overdue_loans": overdue_loans,
                "overdue_count": overdue_count,
                "risk_percentage": risk_percentage,
                "total_loans": total_loans,
            })
            return context

    return View.as_view()(request)


@login_required
def treasurer_settings_view(request):
    """Settings (Policies) page for treasurer."""
    class View(TreasurerApprovalsBaseView):
        template_name = "dashboards/treasurer/settings.html"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            chama = self.active_membership.chama

            # Get loan products
            loan_products = LoanProduct.objects.filter(chama=chama)

            # Get contribution types
            contribution_types = ContributionType.objects.filter(chama=chama)

            context.update({
                "title": "Settings",
                "loan_products": loan_products,
                "contribution_types": contribution_types,
            })
            return context

    return View.as_view()(request)


@login_required
def treasurer_security_view(request):
    """Security page for treasurer."""
    class View(TreasurerApprovalsBaseView):
        template_name = "dashboards/treasurer/security.html"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            # Basic security context - would typically include
            # 2FA status, sessions, login history
            context.update({
                "title": "Security",
                "user": self.request.user,
            })
            return context

    return View.as_view()(request)


@login_required
def secretary_dashboard_view(request):
    return SecretaryDashboardView.as_view()(request)


@login_required
def auditor_dashboard_view(request):
    return AuditorDashboardView.as_view()(request)


@login_required
def dashboard_home_view(request):
    return redirect(resolve_dashboard_route_for_request(request))
