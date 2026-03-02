from django.contrib import admin

from apps.meetings.models import (
    AgendaItem,
    Attendance,
    Meeting,
    MeetingVote,
    MinutesApproval,
    Resolution,
)


@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    list_display = ("title", "chama", "date", "created_by")
    list_filter = ("chama", "date")
    search_fields = ("title", "agenda", "chama__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ("meeting", "member", "status")
    list_filter = ("status", "meeting__chama")
    search_fields = ("meeting__title", "member__full_name", "member__phone")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Resolution)
class ResolutionAdmin(admin.ModelAdmin):
    list_display = ("meeting", "assigned_to", "due_date", "status")
    list_filter = ("status", "due_date", "meeting__chama")
    search_fields = ("text", "meeting__title", "assigned_to__full_name")
    readonly_fields = ("created_at", "updated_at", "completed_at")


@admin.register(AgendaItem)
class AgendaItemAdmin(admin.ModelAdmin):
    list_display = ("meeting", "title", "proposed_by", "status", "approved_by")
    list_filter = ("status", "meeting__chama")
    search_fields = ("title", "description", "meeting__title", "proposed_by__full_name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(MeetingVote)
class MeetingVoteAdmin(admin.ModelAdmin):
    list_display = ("meeting", "agenda_item", "voter", "choice")
    list_filter = ("choice", "meeting__chama")
    search_fields = ("meeting__title", "agenda_item__title", "voter__full_name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(MinutesApproval)
class MinutesApprovalAdmin(admin.ModelAdmin):
    list_display = ("meeting", "reviewer", "decision", "decided_at")
    list_filter = ("decision", "meeting__chama")
    search_fields = ("meeting__title", "reviewer__full_name", "note")
    readonly_fields = ("created_at", "updated_at", "decided_at")
