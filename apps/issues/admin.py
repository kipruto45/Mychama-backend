from django.contrib import admin

from apps.issues.models import (
    Issue,
    IssueActivityLog,
    IssueAttachment,
    IssueComment,
    IssueMediationNote,
    Suspension,
    Warning,
)


@admin.register(Issue)
class IssueAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "chama",
        "category",
        "priority",
        "status",
        "assigned_to",
        "reported_user",
        "loan",
        "created_at",
    )
    list_filter = (
        "status",
        "category",
        "priority",
        "is_anonymous",
        "chama",
    )
    search_fields = (
        "title",
        "description",
        "created_by__full_name",
        "reported_user__full_name",
        "reported_user__phone",
        "loan__id",
    )
    autocomplete_fields = (
        "assigned_to",
        "reported_user",
        "loan",
        "created_by",
        "updated_by",
    )


@admin.register(IssueComment)
class IssueCommentAdmin(admin.ModelAdmin):
    list_display = ("issue", "author", "is_internal", "created_at")
    list_filter = ("is_internal", "created_at")
    search_fields = ("issue__title", "author__full_name", "message")
    autocomplete_fields = ("issue", "author", "created_by", "updated_by")


@admin.register(IssueAttachment)
class IssueAttachmentAdmin(admin.ModelAdmin):
    list_display = ("issue", "uploaded_by", "content_type", "size", "created_at")
    list_filter = ("content_type", "created_at")
    search_fields = ("issue__title", "uploaded_by__full_name", "file")
    autocomplete_fields = ("issue", "uploaded_by", "created_by", "updated_by")


@admin.register(IssueActivityLog)
class IssueActivityLogAdmin(admin.ModelAdmin):
    list_display = ("issue", "actor", "action", "created_at")
    list_filter = ("action", "created_at")
    search_fields = ("issue__title", "actor__full_name", "meta")
    autocomplete_fields = ("issue", "actor")


@admin.register(Warning)
class WarningAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "chama",
        "severity",
        "status",
        "issued_by",
        "issued_at",
    )
    list_filter = ("severity", "status", "chama")
    search_fields = ("user__full_name", "user__phone", "reason", "message_to_user")
    autocomplete_fields = (
        "chama",
        "user",
        "issue",
        "issued_by",
        "revoked_by",
        "created_by",
        "updated_by",
    )


@admin.register(Suspension)
class SuspensionAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "chama",
        "is_active",
        "starts_at",
        "ends_at",
        "suspended_by",
        "lifted_by",
    )
    list_filter = ("is_active", "chama", "starts_at")
    search_fields = ("user__full_name", "user__phone", "reason", "lift_reason")
    autocomplete_fields = (
        "chama",
        "user",
        "issue",
        "suspended_by",
        "lifted_by",
        "created_by",
        "updated_by",
    )


@admin.register(IssueMediationNote)
class IssueMediationNoteAdmin(admin.ModelAdmin):
    list_display = ("issue", "author", "is_private", "created_at")
    list_filter = ("is_private", "created_at")
    search_fields = ("issue__title", "author__full_name", "note")
    autocomplete_fields = ("issue", "author", "created_by", "updated_by")
