from django.urls import path

from apps.issues.views import (
    IssueAppealCreateView,
    IssueAppealListView,
    IssueAppealReviewView,
    IssueAssignView,
    IssueAttachmentCreateView,
    IssueCloseView,
    IssueCommentCreateView,
    IssueDetailView,
    IssueLiftSuspensionView,
    IssueListCreateView,
    IssueMediationNoteListCreateView,
    IssueReopenView,
    IssueEscalateView,
    IssueStatsView,
    IssueStatusUpdateView,
    IssueSuspendView,
    IssueWarnView,
)

app_name = "issues"

urlpatterns = [
    path("", IssueListCreateView.as_view(), name="issue-list-create"),
    path("appeals", IssueAppealListView.as_view(), name="issue-appeal-list"),
    path("stats", IssueStatsView.as_view(), name="issue-stats"),
    path("<uuid:id>/", IssueDetailView.as_view(), name="issue-detail"),
    path("<uuid:id>/comments", IssueCommentCreateView.as_view(), name="issue-comment"),
    path(
        "<uuid:id>/attachments",
        IssueAttachmentCreateView.as_view(),
        name="issue-attachment",
    ),
    path("<uuid:id>/assign", IssueAssignView.as_view(), name="issue-assign"),
    path("<uuid:id>/status", IssueStatusUpdateView.as_view(), name="issue-status"),
    path("<uuid:id>/close", IssueCloseView.as_view(), name="issue-close"),
    path("<uuid:id>/reopen", IssueReopenView.as_view(), name="issue-reopen"),
    path("<uuid:id>/warn", IssueWarnView.as_view(), name="issue-warn"),
    path("<uuid:id>/suspend", IssueSuspendView.as_view(), name="issue-suspend"),
    path(
        "<uuid:id>/mediation-notes",
        IssueMediationNoteListCreateView.as_view(),
        name="issue-mediation-notes",
    ),
    path("<uuid:id>/escalate", IssueEscalateView.as_view(), name="issue-escalate"),
    path("<uuid:id>/appeal", IssueAppealCreateView.as_view(), name="issue-appeal"),
    path(
        "appeals/<uuid:appeal_id>/review",
        IssueAppealReviewView.as_view(),
        name="issue-appeal-review",
    ),
    path(
        "<uuid:id>/lift-suspension",
        IssueLiftSuspensionView.as_view(),
        name="issue-lift-suspension",
    ),
]
