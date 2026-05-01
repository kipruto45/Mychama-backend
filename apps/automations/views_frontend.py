from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from apps.automations.models import JobRun, NotificationLog, ScheduledJob
from apps.chama.models import Membership, MembershipRole, MemberStatus
from apps.chama.services import get_effective_role
from apps.payments.models import PaymentReconciliationRun

AUTOMATION_READ_ROLES = {
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.TREASURER,
    MembershipRole.SECRETARY,
    MembershipRole.AUDITOR,
}


def _resolve_membership(request):
    chama_id = request.GET.get("chama_id") or request.headers.get("X-CHAMA-ID")
    memberships = Membership.objects.filter(
        user=request.user,
        is_active=True,
        is_approved=True,
        status=MemberStatus.ACTIVE,
        exited_at__isnull=True,
    ).select_related("chama")
    if chama_id:
        return memberships.filter(chama_id=chama_id).first()
    return memberships.first()


@login_required
def automations_dashboard_view(request):
    membership = _resolve_membership(request)
    role = get_effective_role(request.user, membership.chama_id, membership) if membership else None
    if not membership or role not in AUTOMATION_READ_ROLES:
        return render(request, "errors/403.html", status=403)

    jobs = ScheduledJob.objects.order_by("name")
    runs = JobRun.objects.select_related("job").order_by("-started_at")[:30]
    run_summary = {
        "success": runs.filter(status="SUCCESS").count(),
        "partial": runs.filter(status="PARTIAL").count(),
        "failed": runs.filter(status="FAILED").count(),
    }
    return render(
        request,
        "automations/dashboard.html",
        {
            "membership": membership,
            "jobs": jobs,
            "runs": runs,
            "run_summary": run_summary,
        },
    )


@login_required
def automation_job_detail_view(request, name):
    membership = _resolve_membership(request)
    role = get_effective_role(request.user, membership.chama_id, membership) if membership else None
    if not membership or role not in AUTOMATION_READ_ROLES:
        return render(request, "errors/403.html", status=403)

    job = get_object_or_404(ScheduledJob, name=name)
    runs = JobRun.objects.filter(job=job).order_by("-started_at")[:100]
    return render(
        request,
        "automations/job_detail.html",
        {
            "membership": membership,
            "job": job,
            "runs": runs,
            "run_summary": {
                "success": runs.filter(status="SUCCESS").count(),
                "partial": runs.filter(status="PARTIAL").count(),
                "failed": runs.filter(status="FAILED").count(),
            },
        },
    )


@login_required
def automation_notification_log_view(request):
    membership = _resolve_membership(request)
    role = get_effective_role(request.user, membership.chama_id, membership) if membership else None
    if not membership or role not in AUTOMATION_READ_ROLES:
        return render(request, "errors/403.html", status=403)
    logs = (
        NotificationLog.objects.filter(chama_id=membership.chama_id).order_by(
            "-created_at"
        )
        if membership
        else NotificationLog.objects.none()
    )
    return render(
        request,
        "automations/notification_log.html",
        {"membership": membership, "logs": logs[:100]},
    )


@login_required
def reconciliation_report_view(request):
    membership = _resolve_membership(request)
    role = get_effective_role(request.user, membership.chama_id, membership) if membership else None
    if not membership or role not in AUTOMATION_READ_ROLES:
        return render(request, "errors/403.html", status=403)
    runs = (
        PaymentReconciliationRun.objects.filter(chama_id=membership.chama_id).order_by(
            "-run_at"
        )
        if membership
        else PaymentReconciliationRun.objects.none()
    )
    return render(
        request,
        "automations/reconciliation_report.html",
        {"membership": membership, "runs": runs[:30]},
    )
