from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView

from apps.chama.models import Membership, MemberStatus
from apps.finance.models import Contribution
from apps.meetings.models import Meeting
from apps.reports.models import ReportRun
from apps.reports.services import ReportService


def _resolve_membership(request):
    scoped_chama_id = request.GET.get("chama_id") or request.session.get("active_chama_id")
    memberships = Membership.objects.select_related("chama").filter(
        user=request.user,
        is_active=True,
        is_approved=True,
        status=MemberStatus.ACTIVE,
        exited_at__isnull=True,
    )

    membership = None
    if scoped_chama_id:
        membership = memberships.filter(chama_id=scoped_chama_id).first()

    if membership is None:
        membership = memberships.order_by("joined_at").first()

    if membership:
        request.session["active_chama_id"] = str(membership.chama_id)

    return membership


@method_decorator(login_required, name="dispatch")
class ReportListView(TemplateView):
    template_name = "reports/report_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["title"] = "Reports"
        context["active_membership"] = membership

        if not membership:
            context["recent_reports"] = []
            return context

        context["recent_reports"] = ReportRun.objects.filter(chama=membership.chama).order_by("-created_at")[:10]
        return context


@method_decorator(login_required, name="dispatch")
class FinancialReportView(TemplateView):
    template_name = "reports/financial_report.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["title"] = "Financial Report"
        context["active_membership"] = membership

        if not membership:
            context["report_data"] = {}
            return context

        today = timezone.localdate()
        try:
            report_data = ReportService.build_chama_summary(
                chama_id=membership.chama_id,
                month=today.month,
                year=today.year,
            )
        except Exception:  # noqa: BLE001
            report_data = {}

        context["report_data"] = report_data
        return context


@method_decorator(login_required, name="dispatch")
class MemberReportView(TemplateView):
    template_name = "reports/member_report.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["title"] = "Member Report"
        context["active_membership"] = membership

        if not membership:
            context["report_data"] = {}
            return context

        try:
            report_data = ReportService.build_member_statement(
                chama_id=membership.chama_id,
                member_id=self.request.user.id,
            )
        except Exception:  # noqa: BLE001
            report_data = {}

        context["report_data"] = report_data
        return context


@method_decorator(login_required, name="dispatch")
class MeetingReportView(TemplateView):
    template_name = "reports/meeting_report.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["title"] = "Meeting Report"
        context["active_membership"] = membership

        if not membership:
            context["meetings"] = []
            return context

        context["meetings"] = Meeting.objects.filter(chama=membership.chama).order_by("-date")[:30]
        return context


@method_decorator(login_required, name="dispatch")
class ContributionReportView(TemplateView):
    template_name = "reports/contribution_report.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["title"] = "Contribution Report"
        context["active_membership"] = membership

        if not membership:
            context["contributions"] = []
            context["total_contributions"] = Decimal("0.00")
            return context

        contributions = Contribution.objects.filter(chama=membership.chama).select_related("member").order_by("-date_paid")
        context["contributions"] = contributions[:100]
        context["total_contributions"] = sum((entry.amount for entry in contributions[:100]), Decimal("0.00"))
        return context


@method_decorator(login_required, name="dispatch")
class ReportActivityLogView(TemplateView):
    template_name = "reports/report_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["title"] = "Report Activity Log"
        context["active_membership"] = membership

        if not membership:
            context["recent_reports"] = []
            return context

        context["recent_reports"] = ReportRun.objects.filter(chama=membership.chama).order_by("-created_at")[:50]
        return context


# Function-based views for backward compatibility
@login_required
def report_list_view(request):
    return ReportListView.as_view()(request)


@login_required
def financial_report_view(request):
    return FinancialReportView.as_view()(request)


@login_required
def member_report_view(request):
    return MemberReportView.as_view()(request)


@login_required
def meeting_report_view(request):
    return MeetingReportView.as_view()(request)


@login_required
def contribution_report_view(request):
    return ContributionReportView.as_view()(request)


@login_required
def activity_log_view(request):
    return ReportActivityLogView.as_view()(request)
