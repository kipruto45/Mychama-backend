from django.contrib import admin

from apps.automations.models import (
    AutomationRule,
    JobRun,
    NotificationLog,
    ScheduledJob,
)


@admin.register(ScheduledJob)
class ScheduledJobAdmin(admin.ModelAdmin):
    list_display = ("name", "is_enabled", "schedule", "created_at")
    list_filter = ("is_enabled", "created_at")
    search_fields = ("name", "description")


@admin.register(JobRun)
class JobRunAdmin(admin.ModelAdmin):
    list_display = ("job", "status", "started_at", "finished_at")
    list_filter = ("status", "started_at")
    search_fields = ("job__name", "error")


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ("chama", "user", "channel", "status", "created_at")
    list_filter = ("channel", "status", "created_at")
    search_fields = ("user__phone", "message")


@admin.register(AutomationRule)
class AutomationRuleAdmin(admin.ModelAdmin):
    list_display = ("chama", "rule_type", "is_enabled", "created_at")
    list_filter = ("is_enabled", "rule_type")
    search_fields = ("chama__name", "rule_type")
