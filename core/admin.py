from django.contrib import admin

from core.models import ActivityLog, AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "action",
        "entity_type",
        "entity_id",
        "chama_id",
        "actor",
        "trace_id",
    )
    list_filter = ("action", "entity_type", "created_at")
    search_fields = ("trace_id", "action", "entity_type")
    readonly_fields = (
        "id",
        "actor",
        "chama_id",
        "action",
        "entity_type",
        "entity_id",
        "metadata",
        "trace_id",
        "created_at",
    )
    ordering = ("-created_at",)


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "action",
        "entity_type",
        "entity_id",
        "chama_id",
        "actor",
        "trace_id",
    )
    list_filter = ("action", "entity_type", "created_at")
    search_fields = ("trace_id", "action", "entity_type")
    readonly_fields = (
        "id",
        "actor",
        "chama_id",
        "action",
        "entity_type",
        "entity_id",
        "metadata",
        "trace_id",
        "created_at",
    )
    ordering = ("-created_at",)
