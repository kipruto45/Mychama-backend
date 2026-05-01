from __future__ import annotations

from dataclasses import dataclass

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView

from apps.accounts.models import User
from apps.accounts.views_dashboards import resolve_dashboard_route_for_request
from apps.chama.models import Membership, MembershipRole, MemberStatus
from apps.chama.services import get_effective_role
from apps.issues.models import (
    Issue,
    IssueActivityAction,
    IssueEvidence,
    IssueCategory,
    IssuePriority,
    IssueStatus,
    Suspension,
    Warning,
)
from apps.issues.permissions import can_moderate_issue, can_view_issue
from apps.issues.services import (
    IssueServiceError,
    assign_issue,
    build_issue_stats,
    change_issue_status,
    issue_warning,
    log_issue_activity,
    suspend_reported_user,
)

READ_ALL_ROLES = {
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.SECRETARY,
    MembershipRole.AUDITOR,
}


class IssueCreateForm(forms.Form):
    title = forms.CharField(max_length=255)
    description = forms.CharField(widget=forms.Textarea)
    category = forms.CharField(max_length=20)
    priority = forms.CharField(max_length=20)
    reported_user_id = forms.UUIDField(required=False)
    attachment = forms.FileField(required=False)
    is_anonymous = forms.BooleanField(required=False)


class IssueAssignForm(forms.Form):
    handler_id = forms.UUIDField()
    assignment_type = forms.CharField(max_length=20, required=False)
    note = forms.CharField(required=False, widget=forms.Textarea)


class IssueStatusForm(forms.Form):
    status = forms.CharField(max_length=20)
    note = forms.CharField(required=False, widget=forms.Textarea)


class IssueWarnForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea)
    severity = forms.CharField(max_length=20)
    message_to_user = forms.CharField(required=False, widget=forms.Textarea)


class IssueSuspendForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea)
    start_date = forms.DateTimeField(required=False)
    end_date = forms.DateTimeField(required=False)
    message_to_user = forms.CharField(required=False, widget=forms.Textarea)


class IssueCommentForm(forms.Form):
    content = forms.CharField(required=False, widget=forms.Textarea)
    is_internal = forms.BooleanField(required=False)


class IssueEvidenceForm(forms.Form):
    attachment = forms.FileField(required=False)


@dataclass
class HandlerView:
    id: str
    full_name: str
    role: str
    status: str = "online"

    def get_full_name(self):
        return self.full_name


def _resolve_membership(request, *, chama_id=None):
    scoped_chama_id = (
        chama_id
        or request.GET.get("chama_id")
        or request.POST.get("chama_id")
        or request.session.get("active_chama_id")
    )
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


def _ticket_no(issue: Issue) -> str:
    return f"ISS-{str(issue.id).split('-')[0].upper()}"


def _decorate_issue(issue: Issue) -> Issue:
    issue.ticket_no = _ticket_no(issue)
    issue.reported_by = issue.created_by
    issue.comment_count = issue.comments.count() if hasattr(issue, "comments") else 0
    return issue


def _category_from_ui(value: str) -> str:
    mapping = {
        "finance": IssueCategory.FINANCIAL,
        "loan": IssueCategory.LOAN_DISPUTE,
        "meeting": IssueCategory.MEETING,
        "behavior": IssueCategory.BEHAVIOR,
        "technical": IssueCategory.TECHNICAL,
        "governance": IssueCategory.OTHER,
        "other": IssueCategory.OTHER,
    }
    return mapping.get(str(value).strip().lower(), IssueCategory.OTHER)


def _priority_from_ui(value: str) -> str:
    mapping = {
        "low": IssuePriority.LOW,
        "medium": IssuePriority.MEDIUM,
        "high": IssuePriority.HIGH,
        "critical": IssuePriority.URGENT,
        "urgent": IssuePriority.URGENT,
    }
    return mapping.get(str(value).strip().lower(), IssuePriority.MEDIUM)


def _role_scoped_issues(*, membership, user):
    queryset = Issue.objects.select_related(
        "created_by", "assigned_to", "reported_user", "chama"
    ).prefetch_related("comments")

    if not membership:
        return queryset.none()

    queryset = queryset.filter(chama=membership.chama)

    effective_role = get_effective_role(user, membership.chama_id, membership)

    if effective_role in READ_ALL_ROLES:
        return queryset

    if effective_role == MembershipRole.TREASURER:
        return queryset.filter(
            Q(category=IssueCategory.FINANCIAL)
            | Q(created_by=user)
            | Q(reported_user=user)
        )

    return queryset.filter(
        Q(created_by=user) | Q(reported_user=user)
    )


def _timeline_events(issue: Issue):
    events = []
    for log in issue.activity_logs.select_related("actor").order_by("created_at"):
        event_type = "activity"
        if log.action == IssueActivityAction.COMMENT_ADDED:
            event_type = "comment"
        elif log.action in {
            IssueActivityAction.STATUS_CHANGED,
            IssueActivityAction.CLOSED,
            IssueActivityAction.REOPENED,
        }:
            event_type = "status_change"
        elif log.action == IssueActivityAction.ASSIGNED:
            event_type = "assignment"
        elif log.action == IssueActivityAction.ATTACHMENT_ADDED:
            event_type = "evidence"

        events.append(
            {
                "type": event_type,
                "action": log.get_action_display(),
                "description": (log.meta or {}).get("note")
                or (log.meta or {}).get("reason")
                or "No additional details.",
                "user": log.actor,
                "timestamp": log.created_at,
            }
        )
    return events


@method_decorator(login_required, name="dispatch")
class IssueListPageView(TemplateView):
    template_name = "issues/issue_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["active_membership"] = membership

        queryset = _role_scoped_issues(membership=membership, user=self.request.user)
        query = (self.request.GET.get("q") or "").strip()
        status_filter = (self.request.GET.get("status") or "").strip().lower()
        category_filter = (self.request.GET.get("category") or "").strip().lower()

        if query:
            queryset = queryset.filter(
                Q(title__icontains=query)
                | Q(description__icontains=query)
                | Q(created_by__full_name__icontains=query)
                | Q(created_by__phone__icontains=query)
                | Q(reported_user__full_name__icontains=query)
                | Q(reported_user__phone__icontains=query)
            )
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if category_filter:
            queryset = queryset.filter(category=category_filter)

        context["issues"] = [_decorate_issue(item) for item in queryset.order_by("-created_at")[:200]]
        return context


@method_decorator(login_required, name="dispatch")
class IssueCreatePageView(TemplateView):
    template_name = "issues/issue_create.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = kwargs.get("form") or IssueCreateForm()
        context["active_membership"] = _resolve_membership(self.request)
        return context

    def post(self, request, *args, **kwargs):
        membership = _resolve_membership(request)
        if not membership:
            messages.error(request, "You must be an approved active member of a chama.")
            return redirect("chama:chama_create")

        form = IssueCreateForm(request.POST, request.FILES)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        reported_user_id = form.cleaned_data.get("reported_user_id")
        if reported_user_id:
            is_member = Membership.objects.filter(
                chama=membership.chama,
                user_id=reported_user_id,
                is_approved=True,
            ).exists()
            if not is_member:
                form.add_error("reported_user_id", "Reported user must be a chama member.")
                return self.render_to_response(self.get_context_data(form=form))

        issue = Issue.objects.create(
            chama=membership.chama,
            title=form.cleaned_data["title"],
            description=form.cleaned_data["description"],
            category=_category_from_ui(form.cleaned_data["category"]),
            priority=_priority_from_ui(form.cleaned_data["priority"]),
            reported_user_id=reported_user_id,
            is_anonymous=bool(form.cleaned_data.get("is_anonymous")),
            created_by=request.user,
            updated_by=request.user,
        )

        upload = form.cleaned_data.get("attachment")
        if upload:
            IssueEvidence.objects.create(
                issue=issue,
                uploaded_by=request.user,
                file=upload,
                created_by=request.user,
                updated_by=request.user,
            )
            log_issue_activity(
                issue,
                request.user,
                IssueActivityAction.ATTACHMENT_ADDED,
                {"filename": upload.name},
            )

        log_issue_activity(
            issue,
            request.user,
            IssueActivityAction.CREATED,
            {
                "category": issue.category,
                "severity": issue.severity,
            },
        )
        messages.success(request, "Issue created successfully.")
        dashboard_url = resolve_dashboard_route_for_request(request)
        return redirect(dashboard_url)


@method_decorator(login_required, name="dispatch")
class IssueDetailPageView(TemplateView):
    template_name = "issues/issue_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        issue = get_object_or_404(
            Issue.objects.select_related("created_by", "assigned_to", "reported_user", "chama"),
            id=self.kwargs["id"],
        )
        membership = _resolve_membership(self.request, chama_id=issue.chama_id)
        if not can_view_issue(self.request.user, membership, issue):
            return context

        issue = _decorate_issue(issue)
        comments = issue.comments.select_related("author").order_by("created_at")
        for comment in comments:
            comment.content = comment.message

        evidence_files = []
        for file in issue.attachments.all().order_by("-created_at"):
            extension = file.file.name.lower()
            file_type = "file"
            if extension.endswith(".pdf"):
                file_type = "pdf"
            elif extension.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                file_type = "image"
            evidence_files.append(
                {
                    "filename": file.file.name.split("/")[-1],
                    "size": file.size,
                    "url": file.file.url,
                    "type": file_type,
                }
            )

        context["active_membership"] = membership
        context["issue"] = issue
        context["id"] = issue.id
        context["comments"] = comments
        context["comment_form"] = IssueCommentForm()
        context["evidence_form"] = IssueEvidenceForm()
        context["evidence_files"] = evidence_files
        context["timeline_events"] = _timeline_events(issue)
        return context

    def dispatch(self, request, *args, **kwargs):
        issue = get_object_or_404(Issue, id=kwargs["id"])
        membership = _resolve_membership(request, chama_id=issue.chama_id)
        if not can_view_issue(request.user, membership, issue):
            return HttpResponseForbidden("You are not allowed to view this issue.")
        return super().dispatch(request, *args, **kwargs)


@method_decorator(login_required, name="dispatch")
class IssueEditPageView(TemplateView):
    template_name = "issues/issue_edit.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        issue = get_object_or_404(Issue, id=self.kwargs["id"])
        issue = _decorate_issue(issue)
        context["issue"] = issue
        context["id"] = issue.id
        context["active_membership"] = _resolve_membership(self.request, chama_id=issue.chama_id)
        return context

    def post(self, request, *args, **kwargs):
        issue = get_object_or_404(Issue, id=self.kwargs["id"])
        membership = _resolve_membership(request, chama_id=issue.chama_id)
        if not membership:
            return HttpResponseForbidden("You are not allowed to edit this issue.")

        if issue.created_by_id != request.user.id and not can_moderate_issue(request.user, membership, issue):
            return HttpResponseForbidden("You are not allowed to edit this issue.")

        issue.title = (request.POST.get("title") or issue.title).strip()
        issue.description = (request.POST.get("description") or issue.description).strip()
        issue.category = _category_from_ui(request.POST.get("category") or issue.category)
        issue.severity = _priority_from_ui(request.POST.get("priority")) or issue.severity
        issue.updated_by = request.user
        issue.save(update_fields=["title", "description", "category", "severity", "updated_by", "updated_at"])

        log_issue_activity(
            issue,
            request.user,
            IssueActivityAction.UPDATED,
            {"note": "Issue updated from frontend form."},
        )

        messages.success(request, "Issue updated successfully.")
        return redirect("issues:issue-detail", id=issue.id)


@method_decorator(login_required, name="dispatch")
class IssueAssignPageView(TemplateView):
    template_name = "issues/issue_assign.html"

    def _available_handlers(self, issue):
        memberships = Membership.objects.select_related("user").filter(
            chama=issue.chama,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        )
        handlers = []
        for membership in memberships:
            handlers.append(
                HandlerView(
                    id=str(membership.user_id),
                    full_name=membership.user.get_full_name(),
                    role=get_effective_role(
                        self.request.user,
                        issue.chama_id,
                        membership,
                    )
                    or membership.role,
                    status="online",
                )
            )
        return handlers

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        issue = get_object_or_404(Issue, id=self.kwargs["id"])
        issue = _decorate_issue(issue)
        context["issue"] = issue
        context["id"] = issue.id
        context["form"] = kwargs.get("form") or IssueAssignForm()
        context["available_handlers"] = self._available_handlers(issue)
        context["active_membership"] = _resolve_membership(self.request, chama_id=issue.chama_id)
        return context

    def post(self, request, *args, **kwargs):
        issue = get_object_or_404(Issue, id=self.kwargs["id"])
        membership = _resolve_membership(request, chama_id=issue.chama_id)
        if not can_moderate_issue(request.user, membership, issue):
            return HttpResponseForbidden("You are not allowed to assign this issue.")

        form = IssueAssignForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        assignee = get_object_or_404(User, id=form.cleaned_data["handler_id"])
        try:
            assign_issue(
                issue,
                assignee,
                actor=request.user,
                note=form.cleaned_data.get("note") or "",
            )
        except IssueServiceError as exc:
            form.add_error(None, str(exc))
            return self.render_to_response(self.get_context_data(form=form))

        messages.success(request, "Issue assigned successfully.")
        return redirect("issues:issue-detail", id=issue.id)


@method_decorator(login_required, name="dispatch")
class IssueStatusUpdatePageView(TemplateView):
    template_name = "issues/issue_status_update.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        issue = get_object_or_404(Issue, id=self.kwargs["id"])
        issue = _decorate_issue(issue)
        issue.reported_by = issue.created_by.get_full_name()
        context["issue"] = issue
        context["active_membership"] = _resolve_membership(self.request, chama_id=issue.chama_id)
        return context

    def post(self, request, *args, **kwargs):
        issue = get_object_or_404(Issue, id=self.kwargs["id"])
        membership = _resolve_membership(request, chama_id=issue.chama_id)
        if not can_moderate_issue(request.user, membership, issue):
            return HttpResponseForbidden("You are not allowed to update this issue.")

        form = IssueStatusForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Provide a valid status update payload.")
            return redirect("issues:issue-status-update", id=issue.id)

        new_status = str(form.cleaned_data["status"]).strip().lower()
        if new_status not in IssueStatus.values:
            messages.error(request, "Selected status is not supported.")
            return redirect("issues:issue-status-update", id=issue.id)

        try:
            change_issue_status(
                issue,
                new_status,
                actor=request.user,
                note=form.cleaned_data.get("note") or "",
            )
        except IssueServiceError as exc:
            messages.error(request, str(exc))
            return redirect("issues:issue-status-update", id=issue.id)

        messages.success(request, "Issue status updated successfully.")
        return redirect("issues:issue-detail", id=issue.id)


@method_decorator(login_required, name="dispatch")
class IssueAdminBoardPageView(TemplateView):
    template_name = "issues/issue_admin_board.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["active_membership"] = membership

        queryset = _role_scoped_issues(membership=membership, user=self.request.user)
        context["open_issues"] = queryset.filter(status=IssueStatus.OPEN)[:50]
        context["in_review_issues"] = queryset.filter(status=IssueStatus.PENDING_ASSIGNMENT)[:50]
        context["assigned_issues"] = queryset.filter(status=IssueStatus.ASSIGNED)[:50]
        context["resolved_issues"] = queryset.filter(status=IssueStatus.RESOLVED)[:50]
        if membership:
            context["issue_stats"] = build_issue_stats(membership.chama_id)
        return context


@method_decorator(login_required, name="dispatch")
class IssueWarnUserPageView(TemplateView):
    template_name = "issues/issue_warn_user.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        issue = get_object_or_404(Issue, id=self.kwargs["id"])
        issue = _decorate_issue(issue)
        context["issue"] = issue
        context["user"] = issue.reported_user or self.request.user
        context["id"] = issue.id
        context["active_membership"] = _resolve_membership(self.request, chama_id=issue.chama_id)
        return context

    def post(self, request, *args, **kwargs):
        issue = get_object_or_404(Issue, id=self.kwargs["id"])
        membership = _resolve_membership(request, chama_id=issue.chama_id)
        if not can_moderate_issue(request.user, membership, issue):
            return HttpResponseForbidden("You are not allowed to warn users on this issue.")
        if not issue.reported_user:
            messages.error(request, "This issue has no reported user.")
            return redirect("issues:issue-detail", id=issue.id)

        form = IssueWarnForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Provide valid warning details.")
            return redirect("issues:issue-warn-user", id=issue.id)

        try:
            issue_warning(
                issue,
                actor=request.user,
                reason=form.cleaned_data["reason"],
                severity=str(form.cleaned_data["severity"]).lower() or "medium",
                message_to_user=form.cleaned_data.get("message_to_user") or "",
            )
        except IssueServiceError as exc:
            messages.error(request, str(exc))
            return redirect("issues:issue-warn-user", id=issue.id)

        messages.success(request, "Warning issued successfully.")
        return redirect("issues:issue-detail", id=issue.id)


@method_decorator(login_required, name="dispatch")
class IssueSuspendUserPageView(TemplateView):
    template_name = "issues/issue_suspend_user.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        issue = get_object_or_404(Issue, id=self.kwargs["id"])
        issue = _decorate_issue(issue)
        context["issue"] = issue
        context["user"] = issue.reported_user or self.request.user
        context["id"] = issue.id
        context["now"] = timezone.localtime()
        context["active_membership"] = _resolve_membership(self.request, chama_id=issue.chama_id)
        return context

    def post(self, request, *args, **kwargs):
        issue = get_object_or_404(Issue, id=self.kwargs["id"])
        membership = _resolve_membership(request, chama_id=issue.chama_id)
        if not can_moderate_issue(request.user, membership, issue):
            return HttpResponseForbidden("You are not allowed to suspend users on this issue.")
        if not issue.reported_user:
            messages.error(request, "This issue has no reported user.")
            return redirect("issues:issue-detail", id=issue.id)

        form = IssueSuspendForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Provide valid suspension details.")
            return redirect("issues:issue-suspend-user", id=issue.id)

        try:
            suspend_reported_user(
                issue,
                actor=request.user,
                reason=form.cleaned_data["reason"],
                starts_at=form.cleaned_data.get("start_date") or timezone.now(),
                ends_at=form.cleaned_data.get("end_date"),
                message_to_user=form.cleaned_data.get("message_to_user") or "",
            )
        except IssueServiceError as exc:
            messages.error(request, str(exc))
            return redirect("issues:issue-suspend-user", id=issue.id)

        messages.success(request, "Suspension applied successfully.")
        return redirect("issues:issue-detail", id=issue.id)


@method_decorator(login_required, name="dispatch")
class IssueWarningsListPageView(TemplateView):
    template_name = "issues/issue_warnings_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["active_membership"] = membership
        effective_role = (
            get_effective_role(self.request.user, membership.chama_id, membership)
            if membership
            else None
        )
        if membership and effective_role in READ_ALL_ROLES.union({MembershipRole.TREASURER}):
            context["warnings"] = Warning.objects.select_related("user", "issued_by", "issue").filter(
                chama=membership.chama
            ).order_by("-issued_at")[:200]
        else:
            context["warnings"] = []
        return context


@method_decorator(login_required, name="dispatch")
class IssueSuspensionsListPageView(TemplateView):
    template_name = "issues/issue_suspensions_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["active_membership"] = membership
        effective_role = (
            get_effective_role(self.request.user, membership.chama_id, membership)
            if membership
            else None
        )
        if membership and effective_role in READ_ALL_ROLES.union({MembershipRole.TREASURER}):
            context["suspensions"] = Suspension.objects.select_related(
                "user", "suspended_by", "issue"
            ).filter(chama=membership.chama).order_by("-starts_at")[:200]
        else:
            context["suspensions"] = []
        return context


@method_decorator(login_required, name="dispatch")
class IssueMyListPageView(TemplateView):
    template_name = "issues/issue_my_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["active_membership"] = membership
        if membership:
            context["my_issues"] = Issue.objects.filter(
                chama=membership.chama,
                created_by=self.request.user,
            ).order_by("-created_at")[:200]
        else:
            context["my_issues"] = []
        return context


@method_decorator(login_required, name="dispatch")
class IssueReportedAgainstMePageView(TemplateView):
    template_name = "issues/issue_reported_against_me.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["active_membership"] = membership
        if membership:
            context["reported_issues"] = Issue.objects.filter(
                chama=membership.chama,
                reported_user=self.request.user,
            ).order_by("-created_at")[:200]
        else:
            context["reported_issues"] = []
        return context
