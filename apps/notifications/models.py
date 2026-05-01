from __future__ import annotations

from datetime import time

from django.conf import settings
from django.db import models

from core.models import BaseModel


class DevicePlatform(models.TextChoices):
    ANDROID = "android", "Android"
    IOS = "ios", "iOS"


class DeviceToken(BaseModel):
    """FCM device tokens for push notifications"""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="device_tokens",
    )
    token = models.CharField(max_length=255, unique=True)
    platform = models.CharField(
        max_length=20,
        choices=DevicePlatform.choices,
        default=DevicePlatform.ANDROID,
    )
    is_active = models.BooleanField(default=True)
    device_name = models.CharField(max_length=100, blank=True)
    app_version = models.CharField(max_length=50, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["platform", "is_active"]),
        ]
        verbose_name = "Device Token"
        verbose_name_plural = "Device Tokens"

    def __str__(self) -> str:
        return f"{self.user.email} - {self.platform}"


class NotificationType(models.TextChoices):
    GENERAL_ANNOUNCEMENT = "general_announcement", "General Announcement"
    CONTRIBUTION_REMINDER = "contribution_reminder", "Contribution Reminder"
    MEETING_NOTIFICATION = "meeting_notification", "Meeting Notification"
    PAYMENT_CONFIRMATION = "payment_confirmation", "Payment Confirmation"
    LOAN_UPDATE = "loan_update", "Loan Update"
    FINE_UPDATE = "fine_update", "Fine Update"
    MEMBERSHIP_UPDATE = "membership_update", "Membership Update"
    BILLING_UPDATE = "billing_update", "Billing Update"
    ISSUE_UPDATE = "issue_update", "Issue Update"
    SECURITY_ALERT = "security_alert", "Security Alert"
    SYSTEM = "system", "System"


class NotificationCategory(models.TextChoices):
    PAYMENTS = "payments", "Payments"
    CONTRIBUTIONS = "contributions", "Contributions"
    LOANS = "loans", "Loans"
    FINES = "fines", "Fines"
    MEETINGS = "meetings", "Meetings"
    MEMBERSHIP = "membership", "Membership"
    GOVERNANCE = "governance", "Governance"
    BILLING = "billing", "Billing"
    ISSUES = "issues", "Issues"
    SECURITY = "security", "Security"
    SYSTEM = "system", "System"
    INVITE = "invite", "Invite"


class NotificationPriority(models.TextChoices):
    LOW = "low", "Low"
    NORMAL = "normal", "Normal"
    HIGH = "high", "High"
    CRITICAL = "critical", "Critical"


class NotificationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class NotificationInboxStatus(models.TextChoices):
    UNREAD = "unread", "Unread"
    READ = "read", "Read"
    ARCHIVED = "archived", "Archived"


class NotificationChannel(models.TextChoices):
    IN_APP = "in_app", "In-App"
    EMAIL = "email", "Email"
    SMS = "sms", "SMS"
    PUSH = "push", "Push"
    WHATSAPP = "whatsapp", "WhatsApp"
    TELEGRAM = "telegram", "Telegram"


class NotificationDeliveryStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    SENT = "sent", "Sent"
    DELIVERED = "delivered", "Delivered"
    FAILED = "failed", "Failed"


class NotificationTarget(models.TextChoices):
    USER = "user", "User"
    ROLE = "role", "Role Group"
    CHAMA = "chama", "Chama Wide"
    SEGMENT = "segment", "Segment"


class NotificationEventStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSED = "processed", "Processed"
    FAILED = "failed", "Failed"


class NotificationEvent(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="notification_events",
    )
    event_key = models.CharField(max_length=180, unique=True)
    event_type = models.CharField(max_length=100)
    target = models.CharField(
        max_length=20,
        choices=NotificationTarget.choices,
        default=NotificationTarget.USER,
    )
    target_roles = models.JSONField(default=list, blank=True)
    target_user_ids = models.JSONField(default=list, blank=True)
    segment = models.CharField(max_length=80, blank=True)
    channels = models.JSONField(default=list, blank=True)
    category = models.CharField(
        max_length=20,
        choices=NotificationCategory.choices,
        default=NotificationCategory.SYSTEM,
    )
    priority = models.CharField(
        max_length=10,
        choices=NotificationPriority.choices,
        default=NotificationPriority.NORMAL,
    )
    subject = models.CharField(max_length=200, blank=True)
    message = models.TextField(blank=True)
    action_url = models.CharField(max_length=255, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=NotificationEventStatus.choices,
        default=NotificationEventStatus.PENDING,
    )
    recipient_count = models.PositiveIntegerField(default=0)
    notification_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["chama", "event_type", "created_at"]),
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["event_key"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.event_type}:{self.event_key}:{self.status}"


class NotificationTemplate(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="notification_templates",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=100)
    template_code = models.CharField(max_length=100, blank=True, default="")
    channel = models.CharField(
        max_length=20,
        choices=NotificationChannel.choices,
        default=NotificationChannel.IN_APP,
    )
    locale = models.CharField(max_length=10, default="en")
    type = models.CharField(max_length=50, choices=NotificationType.choices)
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField()
    is_active = models.BooleanField(default=True)
    variables = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["chama", "name"],
                name="uniq_notification_template_per_chama",
            )
        ]
        indexes = [
            models.Index(fields=["chama", "type", "is_active"]),
            models.Index(fields=["chama", "name"]),
            models.Index(fields=["chama", "template_code", "channel"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.type})"


class Notification(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.SET_NULL,
        related_name="notifications",
        null=True,
        blank=True,
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    type = models.CharField(max_length=50, choices=NotificationType.choices)
    category = models.CharField(
        max_length=20,
        choices=NotificationCategory.choices,
        default=NotificationCategory.SYSTEM,
    )
    priority = models.CharField(
        max_length=10,
        choices=NotificationPriority.choices,
        default=NotificationPriority.NORMAL,
    )
    status = models.CharField(
        max_length=20,
        choices=NotificationStatus.choices,
        default=NotificationStatus.PENDING,
    )
    inbox_status = models.CharField(
        max_length=20,
        choices=NotificationInboxStatus.choices,
        default=NotificationInboxStatus.UNREAD,
    )

    subject = models.CharField(max_length=200, blank=True)
    message = models.TextField()
    html_message = models.TextField(blank=True)
    action_url = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    send_email = models.BooleanField(default=True)
    send_sms = models.BooleanField(default=False)
    send_push = models.BooleanField(default=False)

    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=16, blank=True)

    scheduled_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    template = models.ForeignKey(
        NotificationTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
    )
    context_data = models.JSONField(default=dict, blank=True)
    idempotency_key = models.CharField(
        max_length=100, unique=True, null=True, blank=True
    )

    retry_count = models.PositiveIntegerField(default=0)
    max_retries = models.PositiveIntegerField(default=3)
    last_error = models.TextField(blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    
    # Expiration for auto-cleanup
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["chama", "status", "priority"]),
            models.Index(fields=["recipient", "status"]),
            models.Index(fields=["recipient", "inbox_status", "created_at"]),
            models.Index(fields=["chama", "category", "created_at"]),
            models.Index(fields=["scheduled_at"]),
            models.Index(fields=["idempotency_key"]),
            # Optimized indexes for unread count
            models.Index(fields=["recipient", "inbox_status"]),
            models.Index(fields=["recipient", "created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.type} -> {self.recipient} ({self.status})"


class NotificationDelivery(BaseModel):
    notification = models.ForeignKey(
        Notification,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    channel = models.CharField(max_length=20, choices=NotificationChannel.choices)
    to_address = models.CharField(max_length=255, blank=True)
    provider = models.CharField(max_length=50, blank=True)
    status = models.CharField(
        max_length=20,
        choices=NotificationDeliveryStatus.choices,
        default=NotificationDeliveryStatus.QUEUED,
    )
    provider_message_id = models.CharField(max_length=128, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["notification", "channel", "created_at"]),
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["notification", "channel"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.channel}:{self.status}:{self.notification_id}"


class NotificationLog(BaseModel):
    notification = models.ForeignKey(
        Notification,
        on_delete=models.CASCADE,
        related_name="logs",
    )
    channel = models.CharField(max_length=10, choices=NotificationChannel.choices)
    status = models.CharField(max_length=20, choices=NotificationStatus.choices)
    provider_response = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    external_message_id = models.CharField(max_length=128, blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["notification", "channel"]),
            models.Index(fields=["status", "sent_at"]),
        ]
        ordering = ["-sent_at"]

    def __str__(self) -> str:
        return f"{self.channel} {self.status} ({self.notification_id})"


class NotificationLanguage(models.TextChoices):
    EN = "en", "English"
    SW = "sw", "Kiswahili"


class NotificationPreference(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preferences",
    )
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="notification_preferences",
    )

    sms_enabled = models.BooleanField(default=True)
    email_enabled = models.BooleanField(default=True)
    in_app_enabled = models.BooleanField(default=True)
    critical_only_mode = models.BooleanField(default=False)
    quiet_hours_start = models.TimeField(default=time(hour=21))
    quiet_hours_end = models.TimeField(default=time(hour=7))
    language = models.CharField(
        max_length=4,
        choices=NotificationLanguage.choices,
        default=NotificationLanguage.EN,
    )

    email_contribution_reminders = models.BooleanField(default=True)
    email_meeting_notifications = models.BooleanField(default=True)
    email_payment_confirmations = models.BooleanField(default=True)
    email_loan_updates = models.BooleanField(default=True)
    email_general_announcements = models.BooleanField(default=True)

    sms_contribution_reminders = models.BooleanField(default=False)
    sms_meeting_notifications = models.BooleanField(default=True)
    sms_payment_confirmations = models.BooleanField(default=True)
    sms_loan_updates = models.BooleanField(default=False)
    sms_general_announcements = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "chama"],
                name="uniq_notification_preference_per_user_chama",
            )
        ]
        indexes = [
            models.Index(fields=["user", "chama"]),
            models.Index(fields=["chama", "language"]),
        ]

    def __str__(self) -> str:
        return f"Preferences for {self.user} @ {self.chama.name}"


class BroadcastTarget(models.TextChoices):
    ALL = "all", "All Members"
    ROLE = "role", "Role"
    SPECIFIC = "specific", "Specific Members"


class BroadcastAnnouncementStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SENT = "sent", "Sent"
    CANCELLED = "cancelled", "Cancelled"
    FAILED = "failed", "Failed"


class BroadcastAnnouncement(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="broadcast_announcements",
    )
    title = models.CharField(max_length=200)
    message = models.TextField()
    target = models.CharField(
        max_length=20,
        choices=BroadcastTarget.choices,
        default=BroadcastTarget.ALL,
    )
    segment = models.CharField(max_length=80, blank=True)
    target_roles = models.JSONField(default=list, blank=True)
    target_member_ids = models.JSONField(default=list, blank=True)
    channels = models.JSONField(default=list, blank=True)
    action_url = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    priority = models.CharField(
        max_length=10,
        choices=NotificationPriority.choices,
        default=NotificationPriority.NORMAL,
    )
    scheduled_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=BroadcastAnnouncementStatus.choices,
        default=BroadcastAnnouncementStatus.PENDING,
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["chama", "status", "scheduled_at"]),
            models.Index(fields=["chama", "target", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.chama_id}:{self.title}:{self.status}"


class NotificationEventThrottle(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_event_throttles",
    )
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.SET_NULL,
        related_name="notification_event_throttles",
        null=True,
        blank=True,
    )
    event_type = models.CharField(max_length=100)
    last_sent_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "chama", "event_type"],
                name="uniq_notification_event_throttle",
            )
        ]
        indexes = [
            models.Index(fields=["event_type", "last_sent_at"]),
            models.Index(fields=["chama", "event_type", "last_sent_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.chama_id}:{self.event_type}"


class ScheduledAnnouncementStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SENT = "sent", "Sent"
    CANCELLED = "cancelled", "Cancelled"
    FAILED = "failed", "Failed"


class ScheduledAnnouncement(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="scheduled_announcements",
    )
    title = models.CharField(max_length=200)
    message = models.TextField()
    channels = models.JSONField(default=list, blank=True)
    scheduled_at = models.DateTimeField()
    status = models.CharField(
        max_length=20,
        choices=ScheduledAnnouncementStatus.choices,
        default=ScheduledAnnouncementStatus.PENDING,
    )
    executed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-scheduled_at"]
        indexes = [
            models.Index(fields=["chama", "status", "scheduled_at"]),
            models.Index(fields=["scheduled_at", "status"]),
        ]

    def __str__(self):
        return f"{self.chama_id}:{self.title}:{self.status}"


class WebhookEventType(models.TextChoices):
    NOTIFICATION_SENT = "notification.sent", "Notification Sent"
    NOTIFICATION_DELIVERED = "notification.delivered", "Notification Delivered"
    NOTIFICATION_FAILED = "notification.failed", "Notification Failed"
    OTP_SENT = "otp.sent", "OTP Sent"
    OTP_VERIFIED = "otp.verified", "OTP Verified"
    PAYMENT_RECEIVED = "payment.received", "Payment Received"
    PAYMENT_FAILED = "payment.failed", "Payment Failed"
    LOAN_APPROVED = "loan.approved", "Loan Approved"
    LOAN_REJECTED = "loan.rejected", "Loan Rejected"
    MEETING_SCHEDULED = "meeting.scheduled", "Meeting Scheduled"
    MEMBERSHIP_APPROVED = "membership.approved", "Membership Approved"


class Webhook(BaseModel):
    """
    Webhook configuration for external integrations.
    Allows chama admins to configure URLs for receiving event notifications.
    """
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="notification_webhooks",
    )
    name = models.CharField(max_length=100, help_text="Webhook name for identification")
    url = models.URLField(max_length=500, help_text="Webhook endpoint URL")
    secret = models.CharField(
        max_length=64,
        blank=True,
        help_text="Secret for HMAC-SHA256 signature verification",
    )
    events = models.JSONField(
        default=list,
        help_text="List of event types to subscribe to",
    )
    is_active = models.BooleanField(default=True)
    
    # Retry configuration
    retry_enabled = models.BooleanField(default=True)
    retry_max_attempts = models.PositiveIntegerField(default=3)
    retry_delay_seconds = models.PositiveIntegerField(default=60)
    
    # Status tracking
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    last_status_code = models.IntegerField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    
    # Rate limiting
    requests_today = models.PositiveIntegerField(default=0)
    requests_reset_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["chama", "name"],
                name="unique_webhook_name_per_chama",
            )
        ]
        indexes = [
            models.Index(fields=["chama", "is_active"]),
            models.Index(fields=["chama", "events"]),
        ]

    def __str__(self):
        return f"{self.chama_id}:{self.name}"

    def generate_secret(self):
        """Generate a new webhook secret."""
        import secrets
        self.secret = secrets.token_hex(32)
        return self.secret

    def verify_signature(self, payload: str, signature: str) -> bool:
        """Verify HMAC-SHA256 signature."""
        if not self.secret or not signature:
            return False
        import hashlib
        import hmac
        expected = hmac.new(
            self.secret.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)


class WebhookDeliveryLog(BaseModel):
    """
    Log of webhook delivery attempts.
    """
    webhook = models.ForeignKey(
        Webhook,
        on_delete=models.CASCADE,
        related_name="delivery_logs",
    )
    event_type = models.CharField(max_length=50)
    payload = models.JSONField(default=dict)
    
    status_code = models.IntegerField(null=True, blank=True)
    response_body = models.TextField(blank=True)
    success = models.BooleanField(default=False)
    
    attempts = models.PositiveIntegerField(default=1)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["webhook", "success"]),
            models.Index(fields=["webhook", "created_at"]),
            models.Index(fields=["event_type", "created_at"]),
        ]

    def __str__(self):
        return f"{self.webhook_id}:{self.event_type}:{self.success}"


class NotificationReadReceipt(BaseModel):
    notification = models.ForeignKey(
        Notification,
        on_delete=models.CASCADE,
        related_name="read_receipts",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_read_receipts",
    )
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-read_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["notification", "user"],
                name="uniq_notification_read_receipt",
            )
        ]
        indexes = [
            models.Index(fields=["notification", "read_at"]),
            models.Index(fields=["user", "read_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.notification_id}:{self.user_id}"
