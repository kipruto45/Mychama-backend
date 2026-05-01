from django.urls import path

from apps.notifications.views import (
    AnnouncementFeedView,
    BulkNotificationView,
    CommunicationAnalyticsView,
    CommunicationBroadcastCancelView,
    CommunicationDeliveryLogListView,
    CommunicationEventCatalogView,
    CommunicationRetryDeliveryView,
    NotificationArchiveView,
    NotificationBroadcastHistoryView,
    NotificationBroadcastView,
    NotificationDetailView,
    NotificationEventListCreateView,
    NotificationInboxView,
    NotificationListCreateView,
    NotificationLogListView,
    NotificationMarkReadView,
    NotificationOperationsView,
    NotificationPreferenceListCreateView,
    NotificationPreferenceMeView,
    NotificationPreferencesView,
    NotificationReadAllView,
    NotificationReadByIdView,
    NotificationsHealthCheckView,
    NotificationStreamTokenView,
    NotificationTemplateDetailView,
    NotificationTemplateListCreateView,
    NotificationUnreadCountView,
    OTPEmailDeliveryCallbackView,
    OTPSMSDeliveryCallbackView,
    ScheduledAnnouncementDetailView,
    ScheduledAnnouncementListCreateView,
    TestNotificationView,
    notification_stream,
)
from apps.notifications.views_mobile import (
    DeviceTokenRegisterView,
    DeviceTokenUnregisterView,
    MobileMarkAllReadView,
    MobileMarkReadView,
    MobileNotificationListView,
    MobileTestSendView,
    MobileUnreadCountView,
)
from apps.notifications.webhooks import (
    AfricaTalkingWebhookView,
    MailgunWebhookView,
    SendGridWebhookView,
    WhatsAppWebhookView,
)

app_name = "notifications"

urlpatterns = [
    # OTP Delivery Callbacks (existing)
    path(
        "callbacks/otp/sms",
        OTPSMSDeliveryCallbackView.as_view(),
        name="otp-sms-callback",
    ),
    path(
        "callbacks/otp/email",
        OTPEmailDeliveryCallbackView.as_view(),
        name="otp-email-callback",
    ),
    # Webhook Endpoints for Delivery Status
    path(
        "webhooks/email/sendgrid",
        SendGridWebhookView.as_view(),
        name="sendgrid-webhook",
    ),
    path(
        "webhooks/email/mailgun",
        MailgunWebhookView.as_view(),
        name="mailgun-webhook",
    ),
    path(
        "webhooks/sms/africastalking",
        AfricaTalkingWebhookView.as_view(),
        name="africas-talking-webhook",
    ),
    path(
        "webhooks/whatsapp",
        WhatsAppWebhookView.as_view(),
        name="whatsapp-webhook",
    ),
    # Mobile App Endpoints
    path("devices/register", DeviceTokenRegisterView.as_view(), name="device-register"),
    path("devices/unregister", DeviceTokenUnregisterView.as_view(), name="device-unregister"),
    path("stream", notification_stream, name="stream"),
    path("stream/token", NotificationStreamTokenView.as_view(), name="stream-token"),
    path("", NotificationInboxView.as_view(), name="inbox"),
    path("unread-count", NotificationUnreadCountView.as_view(), name="unread-count"),
    path("mobile", MobileNotificationListView.as_view(), name="mobile-inbox"),
    path("mobile/unread-count", MobileUnreadCountView.as_view(), name="mobile-unread-count"),
    path("mobile/mark-read", MobileMarkReadView.as_view(), name="mobile-mark-read"),
    path("mobile/mark-all-read", MobileMarkAllReadView.as_view(), name="mobile-mark-all-read"),
    path("mobile/test-send", MobileTestSendView.as_view(), name="mobile-test-send"),
    path("<uuid:id>/read", NotificationReadByIdView.as_view(), name="read"),
    path("<uuid:id>/archive", NotificationArchiveView.as_view(), name="archive"),
    path("read-all", NotificationReadAllView.as_view(), name="read-all"),
    path("preferences", NotificationPreferencesView.as_view(), name="preferences"),
    path("announcements/feed", AnnouncementFeedView.as_view(), name="announcements-feed"),
    path("broadcast", NotificationBroadcastView.as_view(), name="broadcast"),
    path("admin/event-catalog", CommunicationEventCatalogView.as_view(), name="communication-event-catalog"),
    path("admin/analytics", CommunicationAnalyticsView.as_view(), name="communication-analytics"),
    path("admin/delivery-logs", CommunicationDeliveryLogListView.as_view(), name="communication-delivery-logs"),
    path("admin/delivery-logs/<uuid:id>/retry", CommunicationRetryDeliveryView.as_view(), name="communication-delivery-retry"),
    path("admin/broadcasts/<uuid:id>/cancel", CommunicationBroadcastCancelView.as_view(), name="communication-broadcast-cancel"),
    path(
        "broadcast/history",
        NotificationBroadcastHistoryView.as_view(),
        name="broadcast-history",
    ),
    path("scheduled", ScheduledAnnouncementListCreateView.as_view(), name="scheduled-announcements-root"),
    path(
        "scheduled/<uuid:id>",
        ScheduledAnnouncementDetailView.as_view(),
        name="scheduled-announcement-detail-root",
    ),
    path("test", TestNotificationView.as_view(), name="test-notification-root"),
    path(
        "<uuid:chama_id>/templates",
        NotificationTemplateListCreateView.as_view(),
        name="template-list",
    ),
    path(
        "<uuid:chama_id>/templates/<uuid:id>",
        NotificationTemplateDetailView.as_view(),
        name="template-detail",
    ),
    path(
        "<uuid:chama_id>/notifications",
        NotificationListCreateView.as_view(),
        name="notification-list",
    ),
    path(
        "<uuid:chama_id>/notifications/<uuid:id>",
        NotificationDetailView.as_view(),
        name="notification-detail",
    ),
    path(
        "<uuid:chama_id>/logs",
        NotificationLogListView.as_view(),
        name="notification-log-list",
    ),
    path(
        "<uuid:chama_id>/events",
        NotificationEventListCreateView.as_view(),
        name="notification-events",
    ),
    path(
        "<uuid:chama_id>/operations",
        NotificationOperationsView.as_view(),
        name="notification-operations",
    ),
    path(
        "<uuid:chama_id>/preferences",
        NotificationPreferenceListCreateView.as_view(),
        name="preference-list",
    ),
    path(
        "<uuid:chama_id>/preferences/me",
        NotificationPreferenceMeView.as_view(),
        name="preference-me",
    ),
    path(
        "<uuid:chama_id>/bulk-send",
        BulkNotificationView.as_view(),
        name="bulk-send",
    ),
    path(
        "<uuid:chama_id>/announcements/scheduled",
        ScheduledAnnouncementListCreateView.as_view(),
        name="scheduled-announcements",
    ),
    path(
        "<uuid:chama_id>/announcements/scheduled/<uuid:id>",
        ScheduledAnnouncementDetailView.as_view(),
        name="scheduled-announcement-detail",
    ),
    path(
        "<uuid:chama_id>/notifications/<uuid:id>/read",
        NotificationMarkReadView.as_view(),
        name="notification-mark-read",
    ),
    path(
        "<uuid:chama_id>/test",
        TestNotificationView.as_view(),
        name="test-notification",
    ),
    # Health check endpoint (not scoped to chama)
    path(
        "health/",
        NotificationsHealthCheckView.as_view(),
        name="notifications-health",
    ),
]
