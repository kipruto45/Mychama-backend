from __future__ import annotations

from django.utils import timezone
from rest_framework import serializers

from apps.accounts.models import User
from apps.accounts.serializers import UserSerializer
from apps.chama.models import MemberStatus, Membership, MembershipRole
from apps.notifications.models import (
    BroadcastAnnouncement,
    BroadcastTarget,
    Notification,
    NotificationCategory,
    NotificationDelivery,
    NotificationEvent,
    NotificationEventThrottle,
    NotificationInboxStatus,
    NotificationLog,
    NotificationPreference,
    NotificationReadReceipt,
    NotificationTarget,
    NotificationTemplate,
    NotificationType,
    ScheduledAnnouncement,
)
from core.utils import normalize_kenyan_phone


def _render_template_value(value: str, context: dict) -> str:
    rendered = value
    for key, val in context.items():
        rendered = rendered.replace(f"{{{{ {key} }}}}", str(val))
        rendered = rendered.replace(f"{{{{{key}}}}}", str(val))
    return rendered


def _category_from_notification_type(notification_type: str) -> str:
    mapping = {
        NotificationType.PAYMENT_CONFIRMATION: NotificationCategory.PAYMENTS,
        NotificationType.BILLING_UPDATE: NotificationCategory.BILLING,
        NotificationType.LOAN_UPDATE: NotificationCategory.LOANS,
        NotificationType.CONTRIBUTION_REMINDER: NotificationCategory.CONTRIBUTIONS,
        NotificationType.MEETING_NOTIFICATION: NotificationCategory.MEETINGS,
        NotificationType.FINE_UPDATE: NotificationCategory.FINES,
        NotificationType.MEMBERSHIP_UPDATE: NotificationCategory.MEMBERSHIP,
        NotificationType.ISSUE_UPDATE: NotificationCategory.ISSUES,
        NotificationType.SECURITY_ALERT: NotificationCategory.SECURITY,
        NotificationType.GENERAL_ANNOUNCEMENT: NotificationCategory.SYSTEM,
        NotificationType.SYSTEM: NotificationCategory.SYSTEM,
    }
    return mapping.get(notification_type, NotificationCategory.SYSTEM)


class NotificationTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationTemplate
        fields = [
            "id",
            "chama",
            "name",
            "template_code",
            "channel",
            "locale",
            "type",
            "subject",
            "body",
            "is_active",
            "variables",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "chama", "created_at", "updated_at"]


class NotificationSerializer(serializers.ModelSerializer):
    recipient = UserSerializer(read_only=True)
    template = NotificationTemplateSerializer(read_only=True)

    class Meta:
        model = Notification
        fields = [
            "id",
            "chama",
            "recipient",
            "type",
            "category",
            "priority",
            "status",
            "inbox_status",
            "subject",
            "message",
            "html_message",
            "action_url",
            "metadata",
            "send_email",
            "send_sms",
            "send_push",
            "email",
            "phone",
            "scheduled_at",
            "sent_at",
            "read_at",
            "template",
            "context_data",
            "idempotency_key",
            "retry_count",
            "max_retries",
            "last_error",
            "next_retry_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "chama",
            "recipient",
            "status",
            "sent_at",
            "retry_count",
            "last_error",
            "next_retry_at",
            "created_at",
            "updated_at",
        ]


class NotificationCreateSerializer(serializers.Serializer):
    recipient_id = serializers.UUIDField(required=False)
    template_id = serializers.UUIDField(required=False)
    type = serializers.ChoiceField(choices=NotificationType.choices, required=False)
    category = serializers.ChoiceField(
        choices=NotificationCategory.choices,
        required=False,
    )
    priority = serializers.ChoiceField(
        choices=Notification._meta.get_field("priority").choices,
        required=False,
    )

    subject = serializers.CharField(required=False, allow_blank=True)
    message = serializers.CharField(required=False, allow_blank=True)
    html_message = serializers.CharField(required=False, allow_blank=True)
    action_url = serializers.CharField(required=False, allow_blank=True)
    metadata = serializers.JSONField(required=False)

    send_email = serializers.BooleanField(required=False, default=True)
    send_sms = serializers.BooleanField(required=False, default=False)
    send_push = serializers.BooleanField(required=False, default=False)

    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(max_length=16, required=False, allow_blank=True)

    scheduled_at = serializers.DateTimeField(required=False, allow_null=True)
    context_data = serializers.JSONField(required=False)
    idempotency_key = serializers.CharField(
        max_length=100, required=False, allow_blank=True
    )

    def validate(self, attrs):
        chama = self.context["chama"]
        request_user = self.context["request"].user

        recipient_id = attrs.get("recipient_id")
        if recipient_id:
            try:
                recipient = User.objects.get(id=recipient_id)
            except User.DoesNotExist as exc:
                raise serializers.ValidationError(
                    {"recipient_id": "Recipient not found."}
                ) from exc
        else:
            recipient = request_user

        is_member = Membership.objects.filter(
            user=recipient,
            chama=chama,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
        ).exists()
        if not is_member:
            raise serializers.ValidationError(
                {"recipient_id": "Recipient is not an approved member in this chama."}
            )

        attrs["recipient"] = recipient

        template = None
        template_id = attrs.get("template_id")
        if template_id:
            try:
                template = NotificationTemplate.objects.get(
                    id=template_id,
                    chama=chama,
                    is_active=True,
                )
            except NotificationTemplate.DoesNotExist as exc:
                raise serializers.ValidationError(
                    {"template_id": "Template not found."}
                ) from exc
            attrs["template"] = template

        context_data = attrs.get("context_data", {})

        notification_type = attrs.get("type") or (template.type if template else None)
        if not notification_type:
            raise serializers.ValidationError(
                {"type": "Notification type is required."}
            )

        attrs["type"] = notification_type
        attrs["category"] = attrs.get("category") or _category_from_notification_type(
            notification_type
        )

        if template:
            if not attrs.get("subject"):
                attrs["subject"] = _render_template_value(
                    template.subject or "", context_data
                )
            if not attrs.get("message"):
                attrs["message"] = _render_template_value(template.body, context_data)

        if not attrs.get("message"):
            raise serializers.ValidationError({"message": "Message is required."})

        send_email = attrs.get("send_email", True)
        send_sms = attrs.get("send_sms", False)
        send_push = attrs.get("send_push", False)

        if not (send_email or send_sms or send_push):
            raise serializers.ValidationError(
                "At least one delivery channel is required."
            )

        if send_email:
            email = attrs.get("email") or recipient.email
            if not email:
                raise serializers.ValidationError(
                    {"email": "Recipient email is required when send_email is true."}
                )
            attrs["email"] = email

        if send_sms:
            phone = attrs.get("phone") or recipient.phone
            if not phone:
                raise serializers.ValidationError(
                    {"phone": "Recipient phone is required when send_sms is true."}
                )
            try:
                attrs["phone"] = normalize_kenyan_phone(phone)
            except ValueError as exc:
                raise serializers.ValidationError({"phone": str(exc)}) from exc

        return attrs

    def create(self, validated_data):
        chama = self.context["chama"]
        request = self.context["request"]

        validated_data.pop("recipient_id", None)
        validated_data.pop("template_id", None)

        return Notification.objects.create(
            chama=chama,
            max_retries=4,
            created_by=request.user,
            updated_by=request.user,
            **validated_data,
        )


class NotificationLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationLog
        fields = [
            "id",
            "notification",
            "channel",
            "status",
            "provider_response",
            "error_message",
            "external_message_id",
            "sent_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "sent_at", "created_at", "updated_at"]


class NotificationDeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationDelivery
        fields = [
            "id",
            "notification",
            "channel",
            "to_address",
            "provider",
            "status",
            "provider_message_id",
            "attempts",
            "error_message",
            "last_attempt_at",
            "delivered_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class NotificationEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationEvent
        fields = [
            "id",
            "chama",
            "event_key",
            "event_type",
            "target",
            "target_roles",
            "target_user_ids",
            "segment",
            "channels",
            "category",
            "priority",
            "subject",
            "message",
            "action_url",
            "payload",
            "processed_at",
            "status",
            "recipient_count",
            "notification_count",
            "last_error",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class NotificationEventCreateSerializer(serializers.Serializer):
    event_key = serializers.CharField(max_length=180)
    event_type = serializers.CharField(max_length=100)
    target = serializers.ChoiceField(choices=NotificationTarget.choices)
    target_roles = serializers.ListField(
        child=serializers.ChoiceField(choices=MembershipRole.choices),
        required=False,
        default=list,
    )
    target_user_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
    segment = serializers.CharField(max_length=80, required=False, allow_blank=True)
    channels = serializers.ListField(
        child=serializers.ChoiceField(choices=["in_app", "email", "sms", "push"]),
        required=False,
        default=lambda: ["in_app"],
    )
    template_id = serializers.UUIDField(required=False)
    template_code = serializers.CharField(max_length=100, required=False, allow_blank=True)
    subject = serializers.CharField(max_length=200, required=False, allow_blank=True)
    message = serializers.CharField(required=False, allow_blank=True)
    action_url = serializers.CharField(required=False, allow_blank=True)
    payload = serializers.JSONField(required=False)
    metadata = serializers.JSONField(required=False)
    category = serializers.ChoiceField(
        choices=NotificationCategory.choices,
        required=False,
    )
    priority = serializers.CharField(required=False, default="normal")
    scheduled_at = serializers.DateTimeField(required=False, allow_null=True)
    enforce_once_daily = serializers.BooleanField(required=False, default=False)

    def validate_priority(self, value: str):
        normalized = str(value or "").strip().lower()
        aliases = {
            "medium": "normal",
            "med": "normal",
            "urgent": "critical",
        }
        normalized = aliases.get(normalized, normalized)
        valid_choices = {
            choice[0] for choice in Notification._meta.get_field("priority").choices
        }
        if normalized not in valid_choices:
            raise serializers.ValidationError("Unsupported priority.")
        return normalized

    def validate(self, attrs):
        target = attrs.get("target")
        if target == NotificationTarget.ROLE and not attrs.get("target_roles"):
            raise serializers.ValidationError(
                {"target_roles": "target_roles is required for role routing."}
            )
        if target == NotificationTarget.USER and not attrs.get("target_user_ids"):
            raise serializers.ValidationError(
                {"target_user_ids": "target_user_ids is required for user routing."}
            )
        if target == NotificationTarget.SEGMENT and not attrs.get("segment"):
            raise serializers.ValidationError(
                {"segment": "segment is required for segment routing."}
            )
        if not attrs.get("message") and not attrs.get("template_id") and not attrs.get("template_code"):
            raise serializers.ValidationError(
                {"message": "Provide a message or a template reference."}
            )
        return attrs


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)

    class Meta:
        model = NotificationPreference
        fields = [
            "id",
            "user",
            "chama",
            "sms_enabled",
            "email_enabled",
            "in_app_enabled",
            "critical_only_mode",
            "quiet_hours_start",
            "quiet_hours_end",
            "language",
            "email_contribution_reminders",
            "email_meeting_notifications",
            "email_payment_confirmations",
            "email_loan_updates",
            "email_general_announcements",
            "sms_contribution_reminders",
            "sms_meeting_notifications",
            "sms_payment_confirmations",
            "sms_loan_updates",
            "sms_general_announcements",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "chama", "created_at", "updated_at"]


class NotificationPreferenceUpsertSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = [
            "sms_enabled",
            "email_enabled",
            "in_app_enabled",
            "critical_only_mode",
            "quiet_hours_start",
            "quiet_hours_end",
            "language",
            "email_contribution_reminders",
            "email_meeting_notifications",
            "email_payment_confirmations",
            "email_loan_updates",
            "email_general_announcements",
            "sms_contribution_reminders",
            "sms_meeting_notifications",
            "sms_payment_confirmations",
            "sms_loan_updates",
            "sms_general_announcements",
        ]


class ScheduledAnnouncementCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    message = serializers.CharField()
    channels = serializers.ListField(
        child=serializers.ChoiceField(choices=["sms", "email", "in_app", "push"]),
        required=False,
        default=["sms", "email"],
    )
    scheduled_at = serializers.DateTimeField()


class ScheduledAnnouncementSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduledAnnouncement
        fields = [
            "id",
            "chama",
            "title",
            "message",
            "channels",
            "scheduled_at",
            "status",
            "executed_at",
            "error_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class BroadcastAnnouncementCreateSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    title = serializers.CharField(max_length=200)
    message = serializers.CharField()
    target = serializers.ChoiceField(choices=BroadcastTarget.choices)
    target_roles = serializers.ListField(
        child=serializers.ChoiceField(choices=MembershipRole.choices),
        required=False,
        default=list,
    )
    target_member_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
    channels = serializers.ListField(
        child=serializers.ChoiceField(choices=["in_app", "sms", "email"]),
        required=False,
        default=lambda: ["in_app"],
    )
    scheduled_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate(self, attrs):
        target = attrs.get("target")
        if target == BroadcastTarget.ROLE and not attrs.get("target_roles"):
            raise serializers.ValidationError(
                {"target_roles": "target_roles is required for role broadcast."}
            )
        if target == BroadcastTarget.SPECIFIC and not attrs.get("target_member_ids"):
            raise serializers.ValidationError(
                {
                    "target_member_ids": (
                        "target_member_ids is required for specific-member broadcast."
                    )
                }
            )
        return attrs


class BroadcastAnnouncementSerializer(serializers.ModelSerializer):
    class Meta:
        model = BroadcastAnnouncement
        fields = [
            "id",
            "chama",
            "title",
            "message",
            "target",
            "target_roles",
            "target_member_ids",
            "channels",
            "scheduled_at",
            "sent_at",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class NotificationReadReceiptSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationReadReceipt
        fields = [
            "id",
            "notification",
            "user",
            "read_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class NotificationInboxFilterSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField(required=False)
    status = serializers.ChoiceField(
        choices=NotificationInboxStatus.choices,
        required=False,
    )
    category = serializers.ChoiceField(
        choices=NotificationCategory.choices,
        required=False,
    )
    priority = serializers.ChoiceField(
        choices=Notification._meta.get_field("priority").choices,
        required=False,
    )


class NotificationReadAllSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField(required=False)


class NotificationEventThrottleSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationEventThrottle
        fields = [
            "id",
            "user",
            "chama",
            "event_type",
            "last_sent_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class NotificationDetailSerializer(serializers.ModelSerializer):
    deliveries = NotificationDeliverySerializer(many=True, read_only=True)

    class Meta:
        model = Notification
        fields = [
            "id",
            "chama",
            "recipient",
            "type",
            "category",
            "priority",
            "status",
            "inbox_status",
            "title",
            "subject",
            "message",
            "action_url",
            "metadata",
            "created_at",
            "read_at",
            "deliveries",
        ]

    title = serializers.SerializerMethodField()

    def get_title(self, obj):
        return obj.subject


class NotificationPreferenceRequestSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField(required=False)


class NotificationPreferencePutSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField(required=False)
    sms_enabled = serializers.BooleanField(required=False)
    email_enabled = serializers.BooleanField(required=False)
    in_app_enabled = serializers.BooleanField(required=False)
    critical_only_mode = serializers.BooleanField(required=False)
    quiet_hours_start = serializers.TimeField(required=False)
    quiet_hours_end = serializers.TimeField(required=False)
    language = serializers.ChoiceField(
        choices=NotificationPreference._meta.get_field("language").choices,
        required=False,
    )

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("At least one field is required.")
        return attrs


class NotificationReadSerializer(serializers.Serializer):
    read = serializers.BooleanField(default=True)


class NotificationBroadcastHistoryFilterSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField(required=False)


class NotificationMarkArchiveSerializer(serializers.Serializer):
    archive = serializers.BooleanField(default=True)


class NotificationReminderGuardSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationEventThrottle
        fields = ["event_type", "last_sent_at"]
        read_only_fields = fields


class NotificationSendResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.CharField()
    inbox_status = serializers.CharField()
    created_at = serializers.DateTimeField(default=timezone.now)


# ============================================================
# Mobile App Serializers
# ============================================================


class DeviceTokenSerializer(serializers.ModelSerializer):
    """Serializer for device tokens used in mobile app push notifications"""

    class Meta:
        model = None  # Will be set after import
        fields = ["id", "token", "platform", "is_active", "device_name", "app_version", "created_at", "updated_at"]
        read_only_fields = ["id", "is_active", "created_at", "updated_at"]

    def create(self, validated_data):
        from apps.notifications.models import DeviceToken
        # Update Meta dynamically
        self.Meta.model = DeviceToken
        
        user = self.context["request"].user
        validated_data["user"] = user
        
        # Use update_or_create for idempotency
        token = validated_data.get("token")
        device, created = DeviceToken.objects.update_or_create(
            token=token,
            defaults=validated_data,
        )
        return device


class DeviceTokenRegisterSerializer(serializers.Serializer):
    """Serializer for registering device token from mobile app"""
    token = serializers.CharField(max_length=255, required=True)
    platform = serializers.ChoiceField(choices=["android", "ios"], required=True)
    device_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    app_version = serializers.CharField(max_length=50, required=False, allow_blank=True)


class MobileNotificationSerializer(serializers.ModelSerializer):
    """Simplified notification serializer for mobile app"""

    class Meta:
        model = Notification
        fields = [
            "id",
            "type",
            "category",
            "subject",
            "message",
            "action_url",
            "metadata",
            "inbox_status",
            "created_at",
            "read_at",
        ]
        read_only_fields = fields


class MobileNotificationListSerializer(serializers.Serializer):
    """Serializer for paginated notification list response"""
    notifications = MobileNotificationSerializer(many=True)
    unread_count = serializers.IntegerField()
    total_count = serializers.IntegerField()
