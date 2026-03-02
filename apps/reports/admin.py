from django.contrib import admin

from apps.reports.models import ReportRun, StatementDownloadHistory


@admin.register(ReportRun)
class ReportRunAdmin(admin.ModelAdmin):
    list_display = (
        "chama",
        "report_type",
        "format",
        "status",
        "is_async",
        "generated_by",
        "created_at",
    )
    list_filter = ("report_type", "format", "status", "created_at")
    search_fields = ("chama__name", "generated_by__full_name", "generated_by__phone")
    readonly_fields = ("created_at", "updated_at")


@admin.register(StatementDownloadHistory)
class StatementDownloadHistoryAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "chama",
        "report_type",
        "format",
        "file_name",
        "created_at",
    )
    list_filter = ("report_type", "format", "chama")
    search_fields = ("user__phone", "user__full_name", "file_name", "chama__name")
    readonly_fields = ("created_at", "updated_at")
