from django.contrib import admin

from apps.notifications.models import (
    BroadcastAnnouncement,
    Notification,
    NotificationDelivery,
    NotificationEventThrottle,
    NotificationLog,
    NotificationPreference,
    NotificationReadReceipt,
    NotificationTemplate,
    ScheduledAnnouncement,
    Webhook,
    WebhookDeliveryLog,
)


@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "is_active", "created_at", "updated_at")
    list_filter = ("type", "is_active", "created_at")
    search_fields = ("name", "subject", "body")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        "recipient",
        "chama",
        "type",
        "category",
        "priority",
        "status",
        "inbox_status",
        "send_email",
        "send_sms",
        "scheduled_at",
        "sent_at",
        "read_at",
        "retry_count",
    )
    list_filter = (
        "type",
        "category",
        "priority",
        "status",
        "inbox_status",
        "send_email",
        "send_sms",
        "scheduled_at",
        "sent_at",
    )
    search_fields = (
        "recipient__email",
        "recipient__full_name",
        "recipient__phone",
        "chama__name",
        "subject",
        "message",
        "action_url",
    )
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
        "sent_at",
        "read_at",
        "retry_count",
        "last_error",
    )
    date_hierarchy = "created_at"


@admin.register(NotificationDelivery)
class NotificationDeliveryAdmin(admin.ModelAdmin):
    list_display = (
        "notification",
        "channel",
        "provider",
        "status",
        "provider_message_id",
        "attempts",
        "created_at",
    )
    list_filter = ("channel", "provider", "status", "created_at")
    search_fields = (
        "notification__recipient__email",
        "notification__recipient__phone",
        "provider_message_id",
        "error_message",
    )
    readonly_fields = ("created_at", "updated_at")


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ("notification", "channel", "status", "sent_at")
    list_filter = ("channel", "status", "sent_at")
    search_fields = (
        "notification__recipient__email",
        "notification__subject",
        "error_message",
        "external_message_id",
    )
    readonly_fields = ("id", "sent_at", "provider_response")
    date_hierarchy = "sent_at"


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "chama",
        "sms_enabled",
        "email_enabled",
        "in_app_enabled",
        "language",
        "created_at",
        "updated_at",
    )
    list_filter = (
        "chama",
        "sms_enabled",
        "email_enabled",
        "in_app_enabled",
        "language",
        "created_at",
    )
    search_fields = ("user__email", "user__full_name", "user__phone", "chama__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(BroadcastAnnouncement)
class BroadcastAnnouncementAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "chama",
        "target",
        "status",
        "scheduled_at",
        "sent_at",
        "created_at",
    )
    list_filter = ("target", "status", "scheduled_at", "created_at")
    search_fields = ("title", "message", "chama__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(NotificationEventThrottle)
class NotificationEventThrottleAdmin(admin.ModelAdmin):
    list_display = ("user", "chama", "event_type", "last_sent_at", "updated_at")
    list_filter = ("event_type", "last_sent_at")
    search_fields = ("user__phone", "user__full_name", "chama__name", "event_type")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ScheduledAnnouncement)
class ScheduledAnnouncementAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "chama",
        "scheduled_at",
        "status",
        "executed_at",
        "created_at",
    )
    list_filter = ("status", "scheduled_at", "chama")
    search_fields = ("title", "message", "chama__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(NotificationReadReceipt)
class NotificationReadReceiptAdmin(admin.ModelAdmin):
    list_display = ("notification", "user", "read_at", "created_at")
    list_filter = ("read_at",)
    search_fields = (
        "user__email",
        "user__full_name",
        "notification__subject",
        "notification__message",
    )
    readonly_fields = ("created_at", "updated_at", "read_at")


@admin.register(Webhook)
class WebhookAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "chama",
        "url",
        "is_active",
        "last_triggered_at",
        "last_status_code",
        "requests_today",
        "created_at",
    )
    list_filter = ("is_active", "chama", "created_at")
    search_fields = ("name", "url", "chama__name")
    readonly_fields = (
        "created_at",
        "updated_at",
        "last_triggered_at",
        "last_status_code",
        "last_error",
        "requests_today",
        "requests_reset_at",
    )
    date_hierarchy = "created_at"
    
    fieldsets = (
        (None, {
            "fields": ("chama", "name", "url", "events")
        }),
        ("Security", {
            "fields": ("secret",)
        }),
        ("Retry Configuration", {
            "fields": ("retry_enabled", "retry_max_attempts", "retry_delay_seconds")
        }),
        ("Status", {
            "fields": ("is_active", "last_triggered_at", "last_status_code", "last_error", "requests_today", "requests_reset_at")
        }),
    )
    
    actions = ["regenerate_secret", "test_webhook"]
    
    def regenerate_secret(self, request, queryset):
        for webhook in queryset:
            webhook.generate_secret()
            webhook.save()
        self.message_user(request, f"Regenerated secrets for {queryset.count()} webhooks")
    regenerate_secret.short_description = "Regenerate webhook secrets"
    
    def test_webhook(self, request, queryset):
        # Show a test form
        pass
    test_webhook.short_description = "Test webhook"


@admin.register(WebhookDeliveryLog)
class WebhookDeliveryLogAdmin(admin.ModelAdmin):
    list_display = (
        "webhook",
        "event_type",
        "success",
        "status_code",
        "attempts",
        "delivered_at",
        "created_at",
    )
    list_filter = ("success", "event_type", "created_at")
    search_fields = ("webhook__name", "webhook__chama__name", "event_type")
    readonly_fields = (
        "created_at",
        "updated_at",
        "webhook",
        "event_type",
        "payload",
        "status_code",
        "response_body",
        "success",
        "attempts",
        "next_retry_at",
        "delivered_at",
    )
    date_hierarchy = "created_at"
    
    def has_add_permission(self, request):
        return False
