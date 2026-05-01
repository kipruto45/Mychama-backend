from django.contrib import admin

from apps.issues.models import (
    Issue,
    IssueActivityLog,
    IssueEvidence,
    IssueComment,
    IssueStatusHistory,
    IssueAssignmentHistory,
    IssueResolution,
    IssueReopenRequest,
    IssueRating,
    IssueAutoTriggerLog,
    IssueMediationNote,
    IssueAppeal,
    Suspension,
    Warning,
)


@admin.register(Issue)
class IssueAdmin(admin.ModelAdmin):
    list_display = (
        "issue_code",
        "title",
        "chama",
        "category",
        "severity",
        "status",
        "source_type",
        "assigned_to",
        "reported_user",
        "loan",
        "created_at",
    )
    list_filter = (
        "status",
        "category",
        "severity",
        "source_type",
        "issue_scope",
        "is_anonymous",
        "chama",
    )
    search_fields = (
        "title",
        "description",
        "issue_code",
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


@admin.register(IssueEvidence)
class IssueEvidenceAdmin(admin.ModelAdmin):
    list_display = ("issue", "uploaded_by", "evidence_type", "content_type", "size", "created_at")
    list_filter = ("evidence_type", "created_at")
    search_fields = ("issue__title", "uploaded_by__full_name", "caption")
    autocomplete_fields = ("issue", "uploaded_by", "created_by", "updated_by")


@admin.register(IssueComment)
class IssueCommentAdmin(admin.ModelAdmin):
    list_display = ("issue", "author", "comment_type", "visibility", "created_at")
    list_filter = ("comment_type", "visibility", "created_at")
    search_fields = ("issue__title", "author__full_name", "body")
    autocomplete_fields = ("issue", "author", "created_by", "updated_by")


@admin.register(IssueStatusHistory)
class IssueStatusHistoryAdmin(admin.ModelAdmin):
    list_display = ("issue", "from_status", "to_status", "changed_by", "created_at")
    list_filter = ("from_status", "to_status")
    search_fields = ("issue__title", "changed_by__full_name", "reason")
    autocomplete_fields = ("issue", "changed_by")


@admin.register(IssueAssignmentHistory)
class IssueAssignmentHistoryAdmin(admin.ModelAdmin):
    list_display = ("issue", "assigned_from", "assigned_to", "assigned_role", "assigned_by", "created_at")
    list_filter = ("assigned_role",)
    search_fields = ("issue__title", "assigned_to__full_name", "note")
    autocomplete_fields = ("issue", "assigned_from", "assigned_to", "assigned_by")


@admin.register(IssueResolution)
class IssueResolutionAdmin(admin.ModelAdmin):
    list_display = ("issue", "proposed_by", "resolution_type", "status", "created_at")
    list_filter = ("resolution_type", "status")
    search_fields = ("issue__title", "proposed_by__full_name", "summary")
    autocomplete_fields = ("issue", "proposed_by", "approved_by", "rejected_by", "created_by", "updated_by")


@admin.register(IssueReopenRequest)
class IssueReopenRequestAdmin(admin.ModelAdmin):
    list_display = ("issue", "requested_by", "decision", "decided_by", "created_at")
    list_filter = ("decision",)
    search_fields = ("issue__title", "requested_by__full_name", "reason")
    autocomplete_fields = ("issue", "requested_by", "decided_by", "created_by", "updated_by")


@admin.register(IssueRating)
class IssueRatingAdmin(admin.ModelAdmin):
    list_display = ("issue", "rated_by", "score", "created_at")
    list_filter = ("score",)
    search_fields = ("issue__title", "rated_by__full_name", "feedback")
    autocomplete_fields = ("issue", "rated_by", "created_by", "updated_by")


@admin.register(IssueAutoTriggerLog)
class IssueAutoTriggerLogAdmin(admin.ModelAdmin):
    list_display = ("trigger_type", "linked_object_type", "generated_issue", "created_at")
    list_filter = ("trigger_type",)
    search_fields = ("trigger_type", "linked_object_type", "metadata")


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


@admin.register(IssueAppeal)
class IssueAppealAdmin(admin.ModelAdmin):
    list_display = ("issue", "appellant", "status", "reviewed_by", "created_at")
    list_filter = ("status",)
    search_fields = ("issue__title", "appellant__full_name", "message")
    autocomplete_fields = ("issue", "appellant", "reviewed_by", "created_by", "updated_by")