from django.urls import path

from apps.issues import views_frontend

app_name = "issues"

urlpatterns = [
    path("", views_frontend.IssueListPageView.as_view(), name="issue-list"),
    path("", views_frontend.IssueListPageView.as_view(), name="list"),
    path("create/", views_frontend.IssueCreatePageView.as_view(), name="issue-create"),
    path("mine/", views_frontend.IssueMyListPageView.as_view(), name="issue-my-list"),
    path(
        "reported-against-me/",
        views_frontend.IssueReportedAgainstMePageView.as_view(),
        name="issue-reported-against-me",
    ),
    path(
        "admin-board/",
        views_frontend.IssueAdminBoardPageView.as_view(),
        name="issue-admin-board",
    ),
    path(
        "warnings/",
        views_frontend.IssueWarningsListPageView.as_view(),
        name="issue-warnings-list",
    ),
    path(
        "suspensions/",
        views_frontend.IssueSuspensionsListPageView.as_view(),
        name="issue-suspensions-list",
    ),
    path(
        "<uuid:id>/", views_frontend.IssueDetailPageView.as_view(), name="issue-detail"
    ),
    path(
        "<uuid:id>/edit/", views_frontend.IssueEditPageView.as_view(), name="issue-edit"
    ),
    path(
        "<uuid:id>/assign/",
        views_frontend.IssueAssignPageView.as_view(),
        name="issue-assign",
    ),
    path(
        "<uuid:id>/status/",
        views_frontend.IssueStatusUpdatePageView.as_view(),
        name="issue-status-update",
    ),
    path(
        "<uuid:id>/warn/",
        views_frontend.IssueWarnUserPageView.as_view(),
        name="issue-warn-user",
    ),
    path(
        "<uuid:id>/suspend/",
        views_frontend.IssueSuspendUserPageView.as_view(),
        name="issue-suspend-user",
    ),
    # Legacy aliases used in templates
    path(
        "<uuid:id>/status/update/",
        views_frontend.IssueStatusUpdatePageView.as_view(),
        name="update_status",
    ),
    path(
        "<uuid:id>/warn-user/",
        views_frontend.IssueWarnUserPageView.as_view(),
        name="warn_user",
    ),
    path(
        "<uuid:id>/detail/",
        views_frontend.IssueDetailPageView.as_view(),
        name="detail",
    ),
]
