from django.urls import path

from apps.meetings.views import (
    ActionItemsDashboardView,
    MeetingAgendaItemStatusView,
    MeetingAgendaItemsView,
    MeetingAttendanceListView,
    MeetingAttendanceMarkView,
    MeetingAttendanceScanView,
    MeetingCancelView,
    MeetingDetailView,
    MeetingListCreateView,
    MeetingMinutesApprovalView,
    MeetingMinutesArchiveView,
    MeetingMinutesUploadView,
    MeetingResolutionListCreateView,
    MeetingResolutionStatusView,
    MeetingSummaryView,
    MeetingVoteSummaryView,
    MeetingVoteView,
)

app_name = "meetings"

urlpatterns = [
    path("", MeetingListCreateView.as_view(), name="meeting-list-create"),
    path("minutes/archive", MeetingMinutesArchiveView.as_view(), name="meeting-minutes-archive"),
    path(
        "action-items/dashboard",
        ActionItemsDashboardView.as_view(),
        name="action-items-dashboard",
    ),
    path("<uuid:id>/", MeetingDetailView.as_view(), name="meeting-detail"),
    path(
        "<uuid:id>/cancel",
        MeetingCancelView.as_view(),
        name="meeting-cancel",
    ),
    path(
        "<uuid:id>/attendance",
        MeetingAttendanceListView.as_view(),
        name="meeting-attendance-list",
    ),
    path(
        "<uuid:id>/attendance/mark",
        MeetingAttendanceMarkView.as_view(),
        name="meeting-attendance-mark",
    ),
    path(
        "<uuid:id>/attendance/scan",
        MeetingAttendanceScanView.as_view(),
        name="meeting-attendance-scan",
    ),
    path(
        "<uuid:id>/minutes/upload",
        MeetingMinutesUploadView.as_view(),
        name="meeting-minutes-upload",
    ),
    path(
        "<uuid:id>/minutes/approve",
        MeetingMinutesApprovalView.as_view(),
        name="meeting-minutes-approve",
    ),
    path("<uuid:id>/summary", MeetingSummaryView.as_view(), name="meeting-summary"),
    path("<uuid:id>/agenda", MeetingAgendaItemsView.as_view(), name="meeting-agenda"),
    path(
        "<uuid:id>/agenda/<uuid:agenda_id>/status",
        MeetingAgendaItemStatusView.as_view(),
        name="meeting-agenda-status",
    ),
    path(
        "<uuid:id>/resolutions",
        MeetingResolutionListCreateView.as_view(),
        name="meeting-resolutions",
    ),
    path(
        "<uuid:id>/resolutions/<uuid:resolution_id>/status",
        MeetingResolutionStatusView.as_view(),
        name="meeting-resolution-status",
    ),
    path("<uuid:id>/votes", MeetingVoteView.as_view(), name="meeting-vote"),
    path(
        "<uuid:id>/votes/summary",
        MeetingVoteSummaryView.as_view(),
        name="meeting-vote-summary",
    ),
]
