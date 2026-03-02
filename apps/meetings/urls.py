from django.urls import path

from apps.meetings.views import (
    ActionItemsDashboardView,
    MeetingAgendaItemsView,
    MeetingAgendaItemStatusView,
    MeetingAttendanceMarkView,
    MeetingAttendanceScanView,
    MeetingDetailView,
    MeetingListCreateView,
    MeetingMinutesUploadView,
    MeetingMinutesArchiveView,
    MeetingMinutesApprovalView,
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
    path("<uuid:id>/votes", MeetingVoteView.as_view(), name="meeting-vote"),
    path(
        "<uuid:id>/votes/summary",
        MeetingVoteSummaryView.as_view(),
        name="meeting-vote-summary",
    ),
]
