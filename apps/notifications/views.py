from __future__ import annotations

import hmac
import json
import time
import uuid

from django.conf import settings
from django.core import signing
from django.db import models, transaction
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import filters, generics, permissions, serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.gating import BillingAccessMixin, require_feature
from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus
from apps.chama.permissions import (
    IsApprovedActiveMember,
    IsChamaAdmin,
    IsTreasurerOrAdmin,
    get_membership,
)
from apps.chama.services import get_effective_role
from apps.notifications.event_catalog import NOTIFICATION_EVENT_CATALOG
from apps.notifications.models import (
    BroadcastAnnouncement,
    BroadcastAnnouncementStatus,
    BroadcastTarget,
    Notification,
    NotificationCategory,
    NotificationDelivery,
    NotificationDeliveryStatus,
    NotificationEvent,
    NotificationInboxStatus,
    NotificationLog,
    NotificationPreference,
    NotificationReadReceipt,
    NotificationStatus,
    NotificationTemplate,
    NotificationType,
    ScheduledAnnouncement,
)
from apps.notifications.serializers import (
    AdminDeliveryLogSerializer,
    AnnouncementFeedItemSerializer,
    AnnouncementFeedQuerySerializer,
    BroadcastAnnouncementCreateSerializer,
    BroadcastAnnouncementSerializer,
    CommunicationAnalyticsQuerySerializer,
    CommunicationDeliveryLogQuerySerializer,
    NotificationBroadcastHistoryFilterSerializer,
    NotificationCreateSerializer,
    NotificationDeliverySerializer,
    NotificationEventCreateSerializer,
    NotificationEventSerializer,
    NotificationInboxFilterSerializer,
    NotificationLogSerializer,
    NotificationMarkArchiveSerializer,
    NotificationPreferencePutSerializer,
    NotificationPreferenceRequestSerializer,
    NotificationPreferenceSerializer,
    NotificationPreferenceUpsertSerializer,
    NotificationReadAllSerializer,
    NotificationReadReceiptSerializer,
    NotificationSerializer,
    NotificationTemplateSerializer,
    ScheduledAnnouncementCreateSerializer,
    ScheduledAnnouncementSerializer,
)
from apps.notifications.services import NotificationService
from core.audit import create_audit_log
from core.schema import error_response_serializer, success_response_serializer
from core.throttles import NotificationDispatchRateThrottle

SCHEMA_CHAMA_ID = "00000000-0000-0000-0000-000000000000"
otp_callback_response = inline_serializer(
    name="OTPCallbackResponse",
    fields={
        "detail": serializers.CharField(),
        "matched": serializers.BooleanField(required=False),
        "events_received": serializers.IntegerField(required=False),
        "events_matched": serializers.IntegerField(required=False),
    },
)
notification_inbox_response = inline_serializer(
    name="NotificationInboxResponse",
    fields={
        "count": serializers.IntegerField(),
        "page": serializers.IntegerField(),
        "page_size": serializers.IntegerField(),
        "total_pages": serializers.IntegerField(),
        "next": serializers.CharField(allow_null=True),
        "previous": serializers.CharField(allow_null=True),
        "results": NotificationSerializer(many=True),
    },
)
notification_unread_count_response = inline_serializer(
    name="NotificationUnreadCountResponse",
    fields={
        "unread_count": serializers.IntegerField(),
        "by_category": serializers.DictField(child=serializers.IntegerField()),
    },
)
notification_stream_token_response = inline_serializer(
    name="NotificationStreamTokenResponse",
    fields={
        "stream_token": serializers.CharField(),
        "expires_in": serializers.IntegerField(),
    },
)
bulk_notification_response = inline_serializer(
    name="BulkNotificationResponse",
    fields={
        "created_count": serializers.IntegerField(),
        "notification_ids": serializers.ListField(child=serializers.CharField()),
        "errors": serializers.ListField(child=serializers.JSONField()),
    },
)
communication_catalog_response = inline_serializer(
    name="CommunicationEventCatalogResponse",
    fields={
        "results": serializers.ListField(child=serializers.JSONField()),
        "count": serializers.IntegerField(),
    },
)
notification_operations_response = inline_serializer(
    name="NotificationOperationsResponse",
    fields={
        "summary": serializers.JSONField(),
        "usage": serializers.JSONField(),
        "recent_events": NotificationEventSerializer(many=True),
        "recent_failures": NotificationDeliverySerializer(many=True),
    },
)
communication_analytics_response = inline_serializer(
    name="CommunicationAnalyticsResponse",
    fields={
        "summary": serializers.JSONField(),
        "by_channel": serializers.DictField(child=serializers.IntegerField()),
        "by_status": serializers.DictField(child=serializers.IntegerField()),
        "recent_campaigns": BroadcastAnnouncementSerializer(many=True),
        "top_templates": serializers.ListField(child=serializers.JSONField()),
    },
)
communication_delivery_log_response = inline_serializer(
    name="CommunicationDeliveryLogResponse",
    fields={
        "results": AdminDeliveryLogSerializer(many=True),
        "count": serializers.IntegerField(),
        "page": serializers.IntegerField(),
        "page_size": serializers.IntegerField(),
        "has_next": serializers.BooleanField(),
    },
)
notifications_health_response = inline_serializer(
    name="NotificationsHealthResponse",
    fields={
        "smtp": serializers.CharField(),
        "sms": serializers.CharField(),
        "redis": serializers.CharField(),
        "celery": serializers.CharField(),
        "details": serializers.JSONField(),
    },
)


class ChamaScopeMixin:
    chama_lookup_url_kwarg = "chama_id"

    def _parse_uuid(self, value: str | None, field_name: str) -> str | None:
        if value in [None, ""]:
            return None
        try:
            return str(uuid.UUID(str(value)))
        except ValueError as exc:
            raise ValidationError({"detail": f"Invalid {field_name}."}) from exc

    def get_scoped_chama_id(self, *, required: bool = True) -> str | None:
        if getattr(self, "swagger_fake_view", False):
            return SCHEMA_CHAMA_ID if required else None
        url_chama_id = self._parse_uuid(
            self.kwargs.get(self.chama_lookup_url_kwarg),
            "chama id in URL",
        )
        header_chama_id = self._parse_uuid(
            self.request.headers.get("X-CHAMA-ID"),
            "X-CHAMA-ID header",
        )
        query_chama_id = self._parse_uuid(
            self.request.query_params.get("chama_id"),
            "chama_id query parameter",
        )
        body_chama_id = None
        if isinstance(getattr(self.request, "data", None), dict):
            body_chama_id = self._parse_uuid(
                self.request.data.get("chama_id"),
                "chama_id in body",
            )

        candidates = [
            item
            for item in [url_chama_id, header_chama_id, query_chama_id, body_chama_id]
            if item
        ]

        resolved = None
        for value in candidates:
            if resolved and resolved != value:
                raise ValidationError(
                    {"detail": "Conflicting chama scope provided in request."}
                )
            resolved = value

        if not resolved and required:
            raise ValidationError({"detail": "Chama scope is required."})
        return resolved

    def get_scoped_chama(self, *, required: bool = True):
        chama_id = self.get_scoped_chama_id(required=required)
        if not chama_id:
            return None
        if getattr(self, "swagger_fake_view", False):
            return Chama(id=chama_id)
        return get_object_or_404(Chama, id=chama_id)

    def require_membership(self, *, roles: set[str] | None = None):
        chama_id = self.get_scoped_chama_id(required=True)
        membership = get_membership(self.request.user, chama_id)
        if not membership:
            raise ValidationError({"detail": "You are not an approved active member."})
        effective_role = get_effective_role(self.request.user, chama_id, membership)
        if roles and effective_role not in roles:
            raise ValidationError({"detail": "You do not have permission for this action."})
        return membership


class NotificationsBillingMixin(BillingAccessMixin):
    billing_feature_key = "notifications_access"


class OTPCallbackAuthMixin:
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def _expected_callback_token(self) -> str:
        return ""

    def _provided_callback_token(self) -> str:
        header_name = getattr(
            settings,
            "OTP_CALLBACK_TOKEN_HEADER",
            "X-OTP-Callback-Token",
        )
        explicit_token = str(self.request.headers.get(header_name, "")).strip()
        if explicit_token:
            return explicit_token

        authorization = str(self.request.headers.get("Authorization", "")).strip()
        if authorization.lower().startswith("bearer "):
            return authorization[7:].strip()
        return ""

    def _is_authorized(self) -> bool:
        expected_token = str(self._expected_callback_token() or "").strip()
        if not expected_token:
            return True
        provided_token = self._provided_callback_token()
        if not provided_token:
            return False
        return hmac.compare_digest(provided_token, expected_token)


class OTPSMSDeliveryCallbackView(OTPCallbackAuthMixin, APIView):
    parser_classes = [JSONParser, FormParser, MultiPartParser]
    serializer_class = otp_callback_response

    def _expected_callback_token(self) -> str:
        return getattr(settings, "OTP_SMS_CALLBACK_TOKEN", "")

    @staticmethod
    def _map_status(raw_status: str, error_message: str) -> str:
        from apps.accounts.models import OTPDeliveryStatus

        normalized = str(raw_status or "").strip().lower()
        if error_message:
            return OTPDeliveryStatus.FAILED
        if any(term in normalized for term in ["fail", "reject", "expired", "undeliver"]):
            return OTPDeliveryStatus.FAILED
        if any(term in normalized for term in ["success", "deliver"]):
            return OTPDeliveryStatus.DELIVERED
        return OTPDeliveryStatus.SENT

    @extend_schema(
        tags=["Notifications"],
        operation_id="process_sms_otp_delivery_callback",
        request=OpenApiTypes.OBJECT,
        responses={200: otp_callback_response, 202: otp_callback_response},
    )
    def post(self, request, *args, **kwargs):
        if not self._is_authorized():
            return Response(
                {"detail": "Forbidden callback source."},
                status=status.HTTP_403_FORBIDDEN,
            )

        payload = request.data if isinstance(request.data, dict) else {}
        if not payload:
            return Response(
                {"detail": "Invalid callback payload."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        provider_name = str(payload.get("provider") or getattr(settings, "SMS_PROVIDER", "")).strip()
        provider_message_id = str(
            payload.get("id")
            or payload.get("messageId")
            or payload.get("message_id")
            or ""
        ).strip()
        destination = str(
            payload.get("phoneNumber")
            or payload.get("phone")
            or payload.get("to")
            or ""
        ).strip()
        raw_status = str(
            payload.get("status")
            or payload.get("deliveryStatus")
            or payload.get("delivery_status")
            or ""
        ).strip()
        error_message = str(
            payload.get("failureReason")
            or payload.get("reason")
            or payload.get("error")
            or ""
        ).strip()

        from apps.accounts.models import OTPDeliveryChannel
        from apps.accounts.services import OTPService

        delivery_log = OTPService.record_delivery_callback(
            channel=OTPDeliveryChannel.SMS,
            status=self._map_status(raw_status, error_message),
            provider_name=provider_name,
            provider_message_id=provider_message_id,
            destination=destination,
            error_message=error_message,
            provider_payload=payload,
        )

        return Response(
            {
                "detail": "Callback processed.",
                "matched": bool(delivery_log),
            },
            status=status.HTTP_200_OK if delivery_log else status.HTTP_202_ACCEPTED,
        )


class OTPEmailDeliveryCallbackView(OTPCallbackAuthMixin, APIView):
    parser_classes = [JSONParser]
    serializer_class = otp_callback_response

    def _expected_callback_token(self) -> str:
        return getattr(settings, "OTP_EMAIL_CALLBACK_TOKEN", "")

    @staticmethod
    def _map_status(raw_event: str, error_message: str) -> str:
        from apps.accounts.models import OTPDeliveryStatus

        normalized = str(raw_event or "").strip().lower()
        failure_events = {
            "bounce",
            "bounced",
            "dropped",
            "spamreport",
            "unsubscribe",
            "group_unsubscribe",
            "blocked",
        }
        delivered_events = {"delivered", "open", "click"}

        if error_message and normalized not in {"processed", "deferred"}:
            return OTPDeliveryStatus.FAILED
        if normalized in failure_events:
            return OTPDeliveryStatus.FAILED
        if normalized in delivered_events:
            return OTPDeliveryStatus.DELIVERED
        return OTPDeliveryStatus.SENT

    @staticmethod
    def _extract_message_id(event: dict) -> str:
        message_id = str(
            event.get("sg_message_id")
            or event.get("smtp-id")
            or event.get("smtp_id")
            or event.get("message_id")
            or ""
        ).strip()
        return message_id.strip("<>")

    @extend_schema(
        tags=["Notifications"],
        operation_id="process_email_otp_delivery_callback",
        request=OpenApiTypes.OBJECT,
        responses={200: otp_callback_response},
    )
    def post(self, request, *args, **kwargs):
        if not self._is_authorized():
            return Response(
                {"detail": "Forbidden callback source."},
                status=status.HTTP_403_FORBIDDEN,
            )

        raw_events = request.data
        if isinstance(raw_events, dict):
            events = [raw_events]
        elif isinstance(raw_events, list):
            events = [item for item in raw_events if isinstance(item, dict)]
        else:
            events = []

        if not events:
            return Response(
                {"detail": "Invalid callback payload."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.accounts.models import OTPDeliveryChannel
        from apps.accounts.services import OTPService

        matched = 0
        provider_name = "sendgrid"

        for event in events:
            error_message = str(
                event.get("reason")
                or event.get("response")
                or event.get("error")
                or ""
            ).strip()
            delivery_log = OTPService.record_delivery_callback(
                channel=OTPDeliveryChannel.EMAIL,
                status=self._map_status(event.get("event", ""), error_message),
                provider_name=str(event.get("provider") or provider_name).strip(),
                provider_message_id=self._extract_message_id(event),
                destination=str(event.get("email") or event.get("to") or "").strip(),
                error_message=error_message,
                provider_payload=event,
            )
            if delivery_log:
                matched += 1

        return Response(
            {
                "detail": "Callback processed.",
                "events_received": len(events),
                "events_matched": matched,
            },
            status=status.HTTP_200_OK,
        )


class NotificationTemplateListCreateView(
    ChamaScopeMixin, NotificationsBillingMixin, generics.ListCreateAPIView
):
    serializer_class = NotificationTemplateSerializer
    permission_classes = [permissions.IsAuthenticated, IsChamaAdmin]
    filter_backends = [filters.SearchFilter]
    search_fields = ["name", "type"]
    billing_feature_key = "notification_templates"

    @require_feature('notification_templates')
    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return NotificationTemplate.objects.none()
        return NotificationTemplate.objects.filter(chama_id=self.get_scoped_chama_id())

    def perform_create(self, serializer):
        template = serializer.save(
            chama=self.get_scoped_chama(),
            created_by=self.request.user,
            updated_by=self.request.user,
        )
        create_audit_log(
            actor=self.request.user,
            chama_id=template.chama_id,
            action="notification_template_created",
            entity_type="NotificationTemplate",
            entity_id=template.id,
            metadata={"name": template.name, "channel": template.channel, "type": template.type},
        )


class NotificationTemplateDetailView(
    ChamaScopeMixin, NotificationsBillingMixin, generics.RetrieveUpdateDestroyAPIView
):
    serializer_class = NotificationTemplateSerializer
    permission_classes = [permissions.IsAuthenticated, IsChamaAdmin]
    lookup_field = "id"
    lookup_url_kwarg = "id"
    billing_feature_key = "notification_templates"

    @require_feature('notification_templates')
    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return NotificationTemplate.objects.none()
        return NotificationTemplate.objects.filter(chama_id=self.get_scoped_chama_id())

    def perform_update(self, serializer):
        template = serializer.save(updated_by=self.request.user)
        create_audit_log(
            actor=self.request.user,
            chama_id=template.chama_id,
            action="notification_template_updated",
            entity_type="NotificationTemplate",
            entity_id=template.id,
            metadata={"name": template.name, "channel": template.channel, "type": template.type},
        )

    def perform_destroy(self, instance):
        create_audit_log(
            actor=self.request.user,
            chama_id=instance.chama_id,
            action="notification_template_deleted",
            entity_type="NotificationTemplate",
            entity_id=instance.id,
            metadata={"name": instance.name, "channel": instance.channel, "type": instance.type},
        )
        instance.delete()


class NotificationListCreateView(
    ChamaScopeMixin, NotificationsBillingMixin, generics.ListCreateAPIView
):
    filter_backends = [filters.SearchFilter]
    search_fields = ["type", "status", "priority", "subject", "message"]

    def get_permissions(self):
        if self.request.method == "POST":
            return [
                permissions.IsAuthenticated(),
                IsTreasurerOrAdmin(),
            ]
        return [permissions.IsAuthenticated(), IsApprovedActiveMember()]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return NotificationCreateSerializer
        return NotificationSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["chama"] = self.get_scoped_chama()
        return context

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Notification.objects.none()
        chama_id = self.get_scoped_chama_id()
        queryset = Notification.objects.select_related("recipient", "template").filter(
            chama_id=chama_id
        )

        recipient_id = self.request.query_params.get("recipient_id")
        if recipient_id:
            queryset = queryset.filter(recipient_id=recipient_id)

        return queryset

    def get_throttles(self):
        if self.request.method == "POST":
            return [NotificationDispatchRateThrottle()]
        return super().get_throttles()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        notification = serializer.save()
        NotificationService.queue_notification(notification)

        output = NotificationSerializer(notification)
        headers = self.get_success_headers(output.data)
        return Response(output.data, status=status.HTTP_201_CREATED, headers=headers)


class NotificationDetailView(ChamaScopeMixin, NotificationsBillingMixin, generics.RetrieveAPIView):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated, IsApprovedActiveMember]
    lookup_field = "id"
    lookup_url_kwarg = "id"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Notification.objects.none()
        return Notification.objects.select_related("recipient", "template").filter(
            chama_id=self.get_scoped_chama_id()
        )


class NotificationEventListCreateView(ChamaScopeMixin, NotificationsBillingMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsTreasurerOrAdmin]
    throttle_classes = [NotificationDispatchRateThrottle]
    billing_feature_key = "broadcast_notifications"

    def get_serializer_class(self):
        if self.request.method == "POST":
            return NotificationEventCreateSerializer
        return NotificationEventSerializer

    @extend_schema(
        tags=["Notifications"],
        operation_id="list_notification_events",
        responses={200: NotificationEventSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        queryset = NotificationEvent.objects.filter(
            chama_id=self.get_scoped_chama_id()
        ).order_by("-created_at")[:100]
        return Response(NotificationEventSerializer(queryset, many=True).data)

    @extend_schema(
        tags=["Notifications"],
        operation_id="create_notification_event",
        request=NotificationEventCreateSerializer,
        responses={201: NotificationEventSerializer},
    )
    def post(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()
        serializer = NotificationEventCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        event = NotificationService.publish_event(
            chama=chama,
            event_key=serializer.validated_data["event_key"],
            event_type=serializer.validated_data["event_type"],
            target=serializer.validated_data["target"],
            target_roles=serializer.validated_data.get("target_roles", []),
            target_user_ids=[
                str(item) for item in serializer.validated_data.get("target_user_ids", [])
            ],
            segment=serializer.validated_data.get("segment", ""),
            channels=serializer.validated_data.get("channels", ["in_app"]),
            template_id=serializer.validated_data.get("template_id"),
            template_code=serializer.validated_data.get("template_code", ""),
            subject=serializer.validated_data.get("subject", ""),
            message=serializer.validated_data.get("message", ""),
            action_url=serializer.validated_data.get("action_url", ""),
            payload=serializer.validated_data.get("payload", {}),
            metadata=serializer.validated_data.get("metadata", {}),
            category=serializer.validated_data.get("category"),
            priority=serializer.validated_data.get("priority", "normal"),
            scheduled_at=serializer.validated_data.get("scheduled_at"),
            enforce_once_daily=serializer.validated_data.get("enforce_once_daily", False),
            actor=request.user,
        )
        return Response(NotificationEventSerializer(event).data, status=status.HTTP_201_CREATED)


class NotificationLogListView(ChamaScopeMixin, NotificationsBillingMixin, generics.ListAPIView):
    serializer_class = NotificationLogSerializer
    permission_classes = [permissions.IsAuthenticated, IsTreasurerOrAdmin]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return NotificationLog.objects.none()
        return NotificationLog.objects.select_related("notification").filter(
            notification__chama_id=self.get_scoped_chama_id()
        )


class NotificationOperationsView(ChamaScopeMixin, NotificationsBillingMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsTreasurerOrAdmin]
    serializer_class = notification_operations_response

    @extend_schema(
        tags=["Notifications"],
        operation_id="get_notification_operations_summary",
        responses={200: notification_operations_response},
    )
    def get(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()
        recent_events = NotificationEvent.objects.filter(chama=chama).order_by("-created_at")[:10]
        recent_failures = (
            NotificationDelivery.objects.select_related("notification")
            .filter(
                notification__chama=chama,
                status=NotificationDeliveryStatus.FAILED,
            )
            .order_by("-updated_at")[:10]
        )

        from apps.billing.metering import sync_usage_limits

        usage = sync_usage_limits(chama)
        return Response(
            {
                "summary": {
                    "pending_notifications": Notification.objects.filter(
                        chama=chama,
                        status=NotificationStatus.PENDING,
                    ).count(),
                    "queued_deliveries": NotificationDelivery.objects.filter(
                        notification__chama=chama,
                        status=NotificationDeliveryStatus.QUEUED,
                    ).count(),
                    "failed_deliveries": NotificationDelivery.objects.filter(
                        notification__chama=chama,
                        status=NotificationDeliveryStatus.FAILED,
                    ).count(),
                    "unread_inbox": Notification.objects.filter(
                        chama=chama,
                        inbox_status=NotificationInboxStatus.UNREAD,
                    ).count(),
                },
                "usage": usage,
                "recent_events": NotificationEventSerializer(recent_events, many=True).data,
                "recent_failures": NotificationDeliverySerializer(
                    recent_failures,
                    many=True,
                ).data,
            }
        )


class NotificationPreferenceListCreateView(ChamaScopeMixin, NotificationsBillingMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsApprovedActiveMember]
    skip_billing_access = True

    def get_serializer_class(self):
        if self.request.method == "POST":
            return NotificationPreferenceUpsertSerializer
        return NotificationPreferenceSerializer

    @extend_schema(
        tags=["Notifications"],
        operation_id="list_notification_preferences",
        responses={200: NotificationPreferenceSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        queryset = NotificationPreference.objects.filter(
            chama_id=self.get_scoped_chama_id(),
            user=request.user,
        )
        return Response(NotificationPreferenceSerializer(queryset, many=True).data)

    @extend_schema(
        tags=["Notifications"],
        operation_id="upsert_notification_preferences",
        request=NotificationPreferenceUpsertSerializer,
        responses={200: NotificationPreferenceSerializer},
    )
    def post(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()
        preference, _ = NotificationPreference.objects.get_or_create(
            chama=chama,
            user=request.user,
            defaults={"created_by": request.user, "updated_by": request.user},
        )

        serializer = NotificationPreferenceUpsertSerializer(
            preference,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        updated = serializer.save(updated_by=request.user)
        create_audit_log(
            actor=request.user,
            chama_id=chama.id,
            action="notification_preferences_updated",
            entity_type="NotificationPreference",
            entity_id=updated.id,
            metadata={"user_id": str(request.user.id)},
        )
        return Response(
            NotificationPreferenceSerializer(updated).data, status=status.HTTP_200_OK
        )


class NotificationPreferenceMeView(ChamaScopeMixin, NotificationsBillingMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsApprovedActiveMember]
    skip_billing_access = True

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return NotificationPreferenceUpsertSerializer
        return NotificationPreferenceSerializer

    def get_object(self):
        chama = self.get_scoped_chama()
        preference, _ = NotificationPreference.objects.get_or_create(
            chama=chama,
            user=self.request.user,
            defaults={"created_by": self.request.user, "updated_by": self.request.user},
        )
        return preference

    @extend_schema(
        tags=["Notifications"],
        operation_id="get_my_notification_preferences",
        responses={200: NotificationPreferenceSerializer},
    )
    def get(self, request, *args, **kwargs):
        preference = self.get_object()
        return Response(NotificationPreferenceSerializer(preference).data)

    @extend_schema(
        tags=["Notifications"],
        operation_id="patch_my_notification_preferences",
        request=NotificationPreferenceUpsertSerializer,
        responses={200: NotificationPreferenceSerializer},
    )
    def patch(self, request, *args, **kwargs):
        preference = self.get_object()
        serializer = NotificationPreferenceUpsertSerializer(
            preference,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        updated = serializer.save(updated_by=request.user)
        create_audit_log(
            actor=request.user,
            chama_id=preference.chama_id,
            action="notification_preferences_updated",
            entity_type="NotificationPreference",
            entity_id=updated.id,
            metadata={"user_id": str(request.user.id)},
        )
        return Response(NotificationPreferenceSerializer(updated).data)


class BulkNotificationView(ChamaScopeMixin, NotificationsBillingMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsChamaAdmin]
    throttle_classes = [NotificationDispatchRateThrottle]
    billing_feature_key = "broadcast_notifications"
    serializer_class = NotificationCreateSerializer

    @extend_schema(
        tags=["Notifications"],
        operation_id="create_bulk_notifications",
        request=OpenApiTypes.OBJECT,
        responses={201: bulk_notification_response},
    )
    def post(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()
        recipient_ids = request.data.get("recipient_ids", [])
        payload = request.data.get("notification", {})

        if not recipient_ids:
            return Response(
                {"detail": "recipient_ids is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not isinstance(recipient_ids, list):
            return Response(
                {"detail": "recipient_ids must be a list."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        created = []
        errors = []
        for recipient_id in recipient_ids:
            serializer = NotificationCreateSerializer(
                data={**payload, "recipient_id": recipient_id},
                context={"request": request, "chama": chama},
            )
            if not serializer.is_valid():
                errors.append(
                    {"recipient_id": recipient_id, "errors": serializer.errors}
                )
                continue

            notification = serializer.save()
            NotificationService.queue_notification(notification)
            created.append(str(notification.id))

        return Response(
            {
                "created_count": len(created),
                "notification_ids": created,
                "errors": errors,
            },
            status=status.HTTP_201_CREATED,
        )


class TestNotificationView(ChamaScopeMixin, NotificationsBillingMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsApprovedActiveMember]
    throttle_classes = [NotificationDispatchRateThrottle]
    serializer_class = NotificationSerializer

    @extend_schema(
        tags=["Notifications"],
        operation_id="send_test_notification",
        responses={201: NotificationSerializer},
    )
    def post(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()

        serializer = NotificationCreateSerializer(
            data={
                "recipient_id": str(request.user.id),
                "type": "system",
                "priority": "normal",
                "subject": "Test notification",
                "message": "This is a system test notification.",
                "send_email": bool(request.user.email),
                "send_sms": True,
            },
            context={"request": request, "chama": chama},
        )
        serializer.is_valid(raise_exception=True)
        notification = serializer.save()
        NotificationService.queue_notification(notification)

        return Response(
            NotificationSerializer(notification).data, status=status.HTTP_201_CREATED
        )


class ScheduledAnnouncementListCreateView(ChamaScopeMixin, NotificationsBillingMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsChamaAdmin]
    throttle_classes = [NotificationDispatchRateThrottle]
    billing_feature_key = "scheduled_notifications"

    def get_serializer_class(self):
        if self.request.method == "POST":
            return ScheduledAnnouncementCreateSerializer
        return ScheduledAnnouncementSerializer

    @require_feature('scheduled_notifications')
    @extend_schema(
        tags=["Notifications"],
        operation_id="list_scheduled_announcements",
        responses={200: ScheduledAnnouncementSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()
        queryset = ScheduledAnnouncement.objects.filter(chama=chama).order_by(
            "-scheduled_at",
            "-created_at",
        )
        return Response(ScheduledAnnouncementSerializer(queryset, many=True).data)

    @extend_schema(
        tags=["Notifications"],
        operation_id="create_scheduled_announcement",
        request=ScheduledAnnouncementCreateSerializer,
        responses={201: ScheduledAnnouncementSerializer},
    )
    def post(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()
        serializer = ScheduledAnnouncementCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        scheduled = ScheduledAnnouncement.objects.create(
            chama=chama,
            title=serializer.validated_data["title"],
            message=serializer.validated_data["message"],
            channels=serializer.validated_data["channels"],
            scheduled_at=serializer.validated_data["scheduled_at"],
            created_by=request.user,
            updated_by=request.user,
        )

        recipients = Membership.objects.select_related("user").filter(
            chama=chama,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
        )
        created_count = 0
        for membership in recipients:
            notification = NotificationService.send_notification(
                user=membership.user,
                chama=chama,
                message=serializer.validated_data["message"],
                channels=serializer.validated_data["channels"],
                subject=serializer.validated_data["title"],
                notification_type="general_announcement",
                priority=serializer.validated_data.get("priority"),
                scheduled_at=serializer.validated_data["scheduled_at"],
                idempotency_key=(
                    f"scheduled-announcement:{scheduled.id}:{membership.user_id}"
                ),
                actor=request.user,
            )
            if notification:
                created_count += 1

        if serializer.validated_data["scheduled_at"] <= timezone.now():
            scheduled.status = "sent"
            scheduled.executed_at = timezone.now()
            scheduled.save(update_fields=["status", "executed_at", "updated_at"])

        create_audit_log(
            actor=request.user,
            chama_id=chama.id,
            action="scheduled_communication_created",
            entity_type="ScheduledAnnouncement",
            entity_id=scheduled.id,
            metadata={
                "channels": scheduled.channels,
                "priority": serializer.validated_data.get("priority"),
                "scheduled_at": scheduled.scheduled_at.isoformat(),
                "recipients_queued": created_count,
            },
        )

        payload = ScheduledAnnouncementSerializer(scheduled).data
        payload["recipients_queued"] = created_count
        return Response(payload, status=status.HTTP_201_CREATED)


class ScheduledAnnouncementDetailView(ChamaScopeMixin, NotificationsBillingMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsChamaAdmin]
    serializer_class = ScheduledAnnouncementSerializer

    @extend_schema(
        tags=["Notifications"],
        operation_id="cancel_scheduled_announcement",
        responses={200: ScheduledAnnouncementSerializer},
    )
    def delete(self, request, id, *args, **kwargs):
        announcement = get_object_or_404(ScheduledAnnouncement, id=id)
        membership = get_membership(request.user, announcement.chama_id)
        effective_role = (
            get_effective_role(request.user, announcement.chama_id, membership)
            if membership
            else None
        )
        if not membership or effective_role != MembershipRole.CHAMA_ADMIN:
            return Response(
                {"detail": "Only chama admins can cancel scheduled announcements."},
                status=status.HTTP_403_FORBIDDEN,
            )

        announcement.status = "cancelled"
        announcement.error_message = ""
        announcement.save(update_fields=["status", "error_message", "updated_at"])

        Notification.objects.filter(
            chama=announcement.chama,
            idempotency_key__startswith=f"scheduled-announcement:{announcement.id}:",
            status=NotificationStatus.PENDING,
        ).update(
            status=NotificationStatus.CANCELLED,
            next_retry_at=None,
            updated_at=timezone.now(),
        )

        create_audit_log(
            actor=request.user,
            chama_id=announcement.chama_id,
            action="scheduled_communication_cancelled",
            entity_type="ScheduledAnnouncement",
            entity_id=announcement.id,
            metadata={"title": announcement.title},
        )

        return Response(
            ScheduledAnnouncementSerializer(announcement).data,
            status=status.HTTP_200_OK,
        )


class NotificationMarkReadView(ChamaScopeMixin, NotificationsBillingMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsApprovedActiveMember]
    serializer_class = NotificationReadReceiptSerializer

    @extend_schema(
        tags=["Notifications"],
        operation_id="mark_notification_read",
        responses={200: NotificationReadReceiptSerializer},
    )
    def post(self, request, *args, **kwargs):
        chama_id = self.get_scoped_chama_id()
        notification = get_object_or_404(
            Notification,
            id=self.kwargs["id"],
            chama_id=chama_id,
        )
        if notification.recipient_id != request.user.id:
            return Response(
                {"detail": "You can only mark your own notifications as read."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if notification.inbox_status != NotificationInboxStatus.READ:
            notification.inbox_status = NotificationInboxStatus.READ
            notification.read_at = timezone.now()
            notification.save(update_fields=["inbox_status", "read_at", "updated_at"])
            create_audit_log(
                actor=request.user,
                chama_id=notification.chama_id,
                action="notification_marked_read",
                entity_type="Notification",
                entity_id=notification.id,
                metadata={"channel": "in_app"},
            )

        receipt, _ = NotificationReadReceipt.objects.get_or_create(
            notification=notification,
            user=request.user,
            defaults={"created_by": request.user, "updated_by": request.user},
        )
        return Response(
            NotificationReadReceiptSerializer(receipt).data,
            status=status.HTTP_200_OK,
        )


class NotificationInboxView(ChamaScopeMixin, NotificationsBillingMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    skip_billing_access = True
    serializer_class = notification_inbox_response

    @extend_schema(
        tags=["Notifications"],
        operation_id="list_notification_inbox",
        parameters=[NotificationInboxFilterSerializer],
        responses={200: notification_inbox_response},
    )
    def get(self, request, *args, **kwargs):
        filter_serializer = NotificationInboxFilterSerializer(data=request.query_params)
        filter_serializer.is_valid(raise_exception=True)

        chama_id = self.get_scoped_chama_id(required=False)
        queryset = Notification.objects.select_related("recipient", "chama").filter(
            recipient=request.user
        )

        if chama_id:
            if not get_membership(request.user, chama_id):
                return Response(
                    {"detail": "You are not an approved active member in this chama."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            queryset = queryset.filter(chama_id=chama_id)

        inbox_status = filter_serializer.validated_data.get("status")
        if inbox_status:
            queryset = queryset.filter(inbox_status=inbox_status)

        category = filter_serializer.validated_data.get("category")
        if category:
            queryset = queryset.filter(category=category)

        priority = filter_serializer.validated_data.get("priority")
        if priority:
            queryset = queryset.filter(priority=priority)

        # Efficient pagination
        page = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("page_size", 20))
        start = (page - 1) * page_size
        end = start + page_size
        
        total_count = queryset.count()
        results = queryset.order_by("-created_at")[start:end]
        
        return Response({
            "count": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": (total_count + page_size - 1) // page_size,
            "next": f"?page={page + 1}" if end < total_count else None,
            "previous": f"?page={page - 1}" if page > 1 else None,
            "results": NotificationSerializer(results, many=True).data
        })


class NotificationUnreadCountView(ChamaScopeMixin, NotificationsBillingMixin, APIView):
    """
    Fast endpoint for unread notification count.
    Uses optimized DB index for quick count.
    """
    permission_classes = [permissions.IsAuthenticated]
    skip_billing_access = True
    serializer_class = notification_unread_count_response

    @extend_schema(
        tags=["Notifications"],
        operation_id="get_notification_unread_count",
        responses={200: notification_unread_count_response},
    )
    def get(self, request, *args, **kwargs):
        chama_id = self.get_scoped_chama_id(required=False)

        # Start with the most optimized query
        queryset = Notification.objects.filter(
            recipient=request.user,
            inbox_status=NotificationInboxStatus.UNREAD,
        )

        if chama_id:
            if not get_membership(request.user, chama_id):
                return Response(
                    {"detail": "You are not an approved active member in this chama."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            queryset = queryset.filter(chama_id=chama_id)

        # Use .count() which is optimized by Django
        unread_count = queryset.count()

        # Get counts by category for additional info
        category_counts = {}
        for category in NotificationCategory.values:
            category_counts[category] = queryset.filter(category=category).count()

        return Response({
            "unread_count": unread_count,
            "by_category": category_counts,
        })


class NotificationStreamTokenView(ChamaScopeMixin, NotificationsBillingMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    skip_billing_access = True
    serializer_class = notification_stream_token_response

    @extend_schema(
        tags=["Notifications"],
        operation_id="create_notification_stream_token",
        responses={200: notification_stream_token_response},
    )
    def get(self, request, *args, **kwargs):
        chama_id = self.get_scoped_chama_id(required=False)
        if chama_id and not get_membership(request.user, chama_id):
            return Response(
                {"detail": "You are not an approved active member in this chama."},
                status=status.HTTP_403_FORBIDDEN,
            )

        ttl_seconds = int(getattr(settings, "NOTIFICATION_STREAM_TOKEN_TTL_SECONDS", 21600))
        token = signing.dumps(
            {
                "user_id": str(request.user.id),
                "chama_id": chama_id,
            },
            salt="notifications.stream",
        )
        return Response(
            {
                "stream_token": token,
                "expires_in": ttl_seconds,
            }
        )


def notification_stream(request):
    token = str(request.GET.get("token") or "").strip()
    if not token:
        return HttpResponse(status=400)

    try:
        payload = signing.loads(
            token,
            salt="notifications.stream",
            max_age=int(getattr(settings, "NOTIFICATION_STREAM_TOKEN_TTL_SECONDS", 21600)),
        )
    except signing.SignatureExpired:
        return HttpResponse(status=401)
    except signing.BadSignature:
        return HttpResponse(status=403)

    user_id = str(payload.get("user_id") or "").strip()
    chama_id = str(
        request.GET.get("chama_id")
        or payload.get("chama_id")
        or ""
    ).strip()

    if not user_id:
        return HttpResponse(status=403)

    if chama_id:
        membership = Membership.objects.filter(
            user_id=user_id,
            chama_id=chama_id,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        ).first()
        if not membership:
            return HttpResponse(status=403)

    interval_seconds = max(
        5,
        int(getattr(settings, "NOTIFICATION_STREAM_INTERVAL_SECONDS", 10)),
    )
    max_iterations = max(
        1,
        int(getattr(settings, "NOTIFICATION_STREAM_MAX_ITERATIONS", 6)),
    )

    def build_snapshot():
        queryset = Notification.objects.filter(
            recipient_id=user_id,
            inbox_status=NotificationInboxStatus.UNREAD,
        )
        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)

        latest = (
            queryset.order_by("-created_at")
            .values("id", "subject", "category", "created_at")[:5]
        )
        latest_notifications = [
            {
                "id": str(item["id"]),
                "subject": item["subject"],
                "category": item["category"],
                "created_at": item["created_at"].isoformat() if item["created_at"] else None,
            }
            for item in latest
        ]
        return {
            "unread_count": queryset.count(),
            "chama_id": chama_id or None,
            "latest_notifications": latest_notifications,
            "timestamp": timezone.now().isoformat(),
        }

    def event_stream():
        last_payload = None
        for _ in range(max_iterations):
            snapshot = build_snapshot()
            serialized = json.dumps(snapshot)
            if serialized != last_payload:
                yield f"event: unread_count\ndata: {serialized}\n\n"
                last_payload = serialized
            else:
                yield ": keepalive\n\n"
            time.sleep(interval_seconds)

    response = StreamingHttpResponse(
        event_stream(),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


class NotificationReadByIdView(ChamaScopeMixin, NotificationsBillingMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    skip_billing_access = True
    serializer_class = NotificationSerializer

    @transaction.atomic
    @extend_schema(
        tags=["Notifications"],
        operation_id="mark_notification_read_by_id",
        responses={200: NotificationSerializer},
    )
    def post(self, request, id, *args, **kwargs):
        notification = get_object_or_404(Notification, id=id, recipient=request.user)

        if not get_membership(request.user, notification.chama_id):
            return Response(
                {"detail": "You are not an approved active member in this chama."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if notification.inbox_status != NotificationInboxStatus.READ:
            notification.inbox_status = NotificationInboxStatus.READ
            notification.read_at = timezone.now()
            notification.save(update_fields=["inbox_status", "read_at", "updated_at"])

        NotificationReadReceipt.objects.get_or_create(
            notification=notification,
            user=request.user,
            defaults={"created_by": request.user, "updated_by": request.user},
        )

        return Response(NotificationSerializer(notification).data, status=status.HTTP_200_OK)


class NotificationArchiveView(ChamaScopeMixin, NotificationsBillingMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    skip_billing_access = True
    serializer_class = NotificationMarkArchiveSerializer

    @extend_schema(
        tags=["Notifications"],
        operation_id="archive_notification",
        request=NotificationMarkArchiveSerializer,
        responses={200: NotificationSerializer},
    )
    def post(self, request, id, *args, **kwargs):
        serializer = NotificationMarkArchiveSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)

        notification = get_object_or_404(Notification, id=id, recipient=request.user)
        if not get_membership(request.user, notification.chama_id):
            return Response(
                {"detail": "You are not an approved active member in this chama."},
                status=status.HTTP_403_FORBIDDEN,
            )

        notification.inbox_status = (
            NotificationInboxStatus.ARCHIVED
            if serializer.validated_data.get("archive", True)
            else NotificationInboxStatus.UNREAD
        )
        notification.save(update_fields=["inbox_status", "updated_at"])
        return Response(NotificationSerializer(notification).data, status=status.HTTP_200_OK)


class NotificationReadAllView(ChamaScopeMixin, NotificationsBillingMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    skip_billing_access = True
    serializer_class = NotificationReadAllSerializer

    @extend_schema(
        tags=["Notifications"],
        operation_id="mark_all_notifications_read",
        request=NotificationReadAllSerializer,
        responses={
            200: inline_serializer(
                name="NotificationReadAllResponse",
                fields={"updated": serializers.IntegerField()},
            )
        },
    )
    def post(self, request, *args, **kwargs):
        serializer = NotificationReadAllSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        chama_id = serializer.validated_data.get("chama_id")
        queryset = Notification.objects.filter(
            recipient=request.user,
            inbox_status=NotificationInboxStatus.UNREAD,
        )

        if chama_id:
            if not get_membership(request.user, chama_id):
                return Response(
                    {"detail": "You are not an approved active member in this chama."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            queryset = queryset.filter(chama_id=chama_id)

        now = timezone.now()
        notification_ids = list(queryset.values_list("id", flat=True))
        updated_count = queryset.update(
            inbox_status=NotificationInboxStatus.READ,
            read_at=now,
            updated_at=now,
        )

        receipts = [
            NotificationReadReceipt(
                notification_id=notification_id,
                user=request.user,
                created_by=request.user,
                updated_by=request.user,
            )
            for notification_id in notification_ids
        ]
        NotificationReadReceipt.objects.bulk_create(receipts, ignore_conflicts=True)

        return Response({"updated": updated_count}, status=status.HTTP_200_OK)


class NotificationPreferencesView(ChamaScopeMixin, NotificationsBillingMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    skip_billing_access = True

    def get_serializer_class(self):
        if self.request.method in {"PUT", "PATCH"}:
            return NotificationPreferencePutSerializer
        return NotificationPreferenceRequestSerializer

    def _resolve_preference(self, request, chama_id: str | None = None):
        resolved_chama_id = chama_id or self.get_scoped_chama_id(required=False)
        if not resolved_chama_id:
            membership = (
                Membership.objects.filter(
                    user=request.user,
                    is_active=True,
                    is_approved=True,
                    status=MemberStatus.ACTIVE,
                    exited_at__isnull=True,
                )
                .order_by("joined_at")
                .first()
            )
            if not membership:
                raise ValidationError(
                    {"detail": "Provide chama_id or join an approved active chama first."}
                )
            resolved_chama_id = str(membership.chama_id)

        membership = get_membership(request.user, resolved_chama_id)
        if not membership:
            raise ValidationError(
                {"detail": "You are not an approved active member in this chama."}
            )

        preference, _ = NotificationPreference.objects.get_or_create(
            user=request.user,
            chama_id=resolved_chama_id,
            defaults={"created_by": request.user, "updated_by": request.user},
        )
        return preference

    @extend_schema(
        tags=["Notifications"],
        operation_id="get_notification_preferences",
        parameters=[NotificationPreferenceRequestSerializer],
        responses={200: NotificationPreferenceSerializer},
    )
    def get(self, request, *args, **kwargs):
        serializer = NotificationPreferenceRequestSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        preference = self._resolve_preference(
            request,
            chama_id=(
                str(serializer.validated_data["chama_id"])
                if serializer.validated_data.get("chama_id")
                else None
            ),
        )
        return Response(NotificationPreferenceSerializer(preference).data)

    @extend_schema(
        tags=["Notifications"],
        operation_id="update_notification_preferences",
        request=NotificationPreferencePutSerializer,
        responses={200: NotificationPreferenceSerializer},
    )
    def put(self, request, *args, **kwargs):
        serializer = NotificationPreferencePutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        preference = self._resolve_preference(
            request,
            chama_id=(
                str(serializer.validated_data["chama_id"])
                if serializer.validated_data.get("chama_id")
                else None
            ),
        )
        update_payload = {
            key: value
            for key, value in serializer.validated_data.items()
            if key != "chama_id"
        }

        update_serializer = NotificationPreferenceUpsertSerializer(
            preference,
            data=update_payload,
            partial=True,
        )
        update_serializer.is_valid(raise_exception=True)
        preference = update_serializer.save(updated_by=request.user)
        return Response(NotificationPreferenceSerializer(preference).data)

    @extend_schema(
        tags=["Notifications"],
        operation_id="partial_update_notification_preferences",
        request=NotificationPreferencePutSerializer,
        responses={200: NotificationPreferenceSerializer},
    )
    def patch(self, request, *args, **kwargs):
        return self.put(request, *args, **kwargs)


class NotificationBroadcastView(ChamaScopeMixin, NotificationsBillingMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [NotificationDispatchRateThrottle]
    billing_feature_key = "broadcast_notifications"
    serializer_class = BroadcastAnnouncementCreateSerializer

    @extend_schema(
        tags=["Notifications"],
        operation_id="create_notification_broadcast",
        request=BroadcastAnnouncementCreateSerializer,
        responses={201: BroadcastAnnouncementSerializer},
    )
    def post(self, request, *args, **kwargs):
        serializer = BroadcastAnnouncementCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        chama_id = str(serializer.validated_data["chama_id"])
        membership = get_membership(request.user, chama_id)
        effective_role = (
            get_effective_role(request.user, chama_id, membership) if membership else None
        )
        if not membership or effective_role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.SECRETARY,
            MembershipRole.TREASURER,
        }:
            return Response(
                {"detail": "Only admin/secretary/treasurer can broadcast."},
                status=status.HTTP_403_FORBIDDEN,
            )

        chama = get_object_or_404(Chama, id=chama_id)

        announcement = BroadcastAnnouncement.objects.create(
            chama=chama,
            title=serializer.validated_data["title"],
            message=serializer.validated_data["message"],
            target=serializer.validated_data["target"],
            segment=serializer.validated_data.get("segment", ""),
            target_roles=serializer.validated_data.get("target_roles", []),
            target_member_ids=[str(item) for item in serializer.validated_data.get("target_member_ids", [])],
            channels=serializer.validated_data.get("channels", ["in_app"]),
            action_url=serializer.validated_data.get("action_url", ""),
            metadata=serializer.validated_data.get("metadata", {}),
            priority=serializer.validated_data.get("priority", "normal"),
            scheduled_at=serializer.validated_data.get("scheduled_at"),
            created_by=request.user,
            updated_by=request.user,
        )

        target_map = {
            BroadcastTarget.ALL: "chama",
            BroadcastTarget.ROLE: "role",
            BroadcastTarget.SPECIFIC: "segment" if announcement.segment else "user",
        }
        event = NotificationService.publish_event(
            chama=chama,
            event_key=f"broadcast:{announcement.id}",
            event_type="general_announcement",
            target=target_map.get(announcement.target, "chama"),
            segment=announcement.segment,
            target_roles=announcement.target_roles,
            target_user_ids=announcement.target_member_ids,
            channels=announcement.channels,
            subject=announcement.title,
            message=announcement.message,
            action_url=announcement.action_url,
            category=NotificationCategory.SYSTEM,
            priority=announcement.priority,
            payload={
                "announcement_id": str(announcement.id),
                **announcement.metadata,
            },
            metadata=announcement.metadata,
            scheduled_at=announcement.scheduled_at,
            actor=request.user,
        )

        immediate = not announcement.scheduled_at or announcement.scheduled_at <= timezone.now()
        if immediate:
            announcement.status = BroadcastAnnouncementStatus.SENT
            announcement.sent_at = timezone.now()
            announcement.save(update_fields=["status", "sent_at", "updated_at"])

        create_audit_log(
            actor=request.user,
            chama_id=chama.id,
            action="admin_broadcast_created",
            entity_type="BroadcastAnnouncement",
            entity_id=announcement.id,
            metadata={
                "target": announcement.target,
                "segment": announcement.segment,
                "channels": announcement.channels,
                "priority": announcement.priority,
                "scheduled_at": announcement.scheduled_at.isoformat() if announcement.scheduled_at else None,
                "queued_count": event.notification_count,
            },
        )

        payload = BroadcastAnnouncementSerializer(announcement).data
        payload["queued_count"] = event.notification_count
        payload["event_id"] = str(event.id)
        return Response(payload, status=status.HTTP_201_CREATED)


class NotificationBroadcastHistoryView(ChamaScopeMixin, NotificationsBillingMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    billing_feature_key = "broadcast_notifications"
    serializer_class = BroadcastAnnouncementSerializer

    @extend_schema(
        tags=["Notifications"],
        operation_id="list_notification_broadcast_history",
        parameters=[NotificationBroadcastHistoryFilterSerializer],
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request, *args, **kwargs):
        serializer = NotificationBroadcastHistoryFilterSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        chama_id = (
            str(serializer.validated_data["chama_id"])
            if serializer.validated_data.get("chama_id")
            else self.get_scoped_chama_id(required=False)
        )
        if not chama_id:
            return Response(
                {"detail": "chama_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        membership = get_membership(request.user, chama_id)
        effective_role = (
            get_effective_role(request.user, chama_id, membership) if membership else None
        )
        if not membership or effective_role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.SECRETARY,
            MembershipRole.TREASURER,
            MembershipRole.AUDITOR,
        }:
            return Response(
                {"detail": "You do not have permission to view broadcast history."},
                status=status.HTTP_403_FORBIDDEN,
            )

        queryset = BroadcastAnnouncement.objects.filter(chama_id=chama_id).order_by(
            "-created_at"
        )

        status_filter = serializer.validated_data.get("status")
        channel_filter = serializer.validated_data.get("channel")
        search = serializer.validated_data.get("search")
        page = serializer.validated_data.get("page", 1)
        page_size = serializer.validated_data.get("page_size", 20)

        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if channel_filter:
            queryset = queryset.filter(channels__contains=[channel_filter])
        if search:
            queryset = queryset.filter(
                models.Q(title__icontains=search)
                | models.Q(message__icontains=search)
            )

        total_count = queryset.count()
        offset = (page - 1) * page_size
        items = queryset[offset : offset + page_size]
        return Response(
            {
                "results": BroadcastAnnouncementSerializer(items, many=True).data,
                "count": total_count,
                "page": page,
                "page_size": page_size,
                "has_next": offset + page_size < total_count,
            }
        )


class AnnouncementFeedView(NotificationsBillingMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = AnnouncementFeedItemSerializer
    skip_billing_access = True

    @extend_schema(
        tags=["Notifications"],
        operation_id="list_member_announcements_feed",
        parameters=[AnnouncementFeedQuerySerializer],
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request, *args, **kwargs):
        serializer = AnnouncementFeedQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        memberships = Membership.objects.filter(
            user=request.user,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        ).select_related("chama")
        chama_ids = list(memberships.values_list("chama_id", flat=True))
        if not chama_ids:
            return Response(
                {
                    "results": [],
                    "count": 0,
                    "page": 1,
                    "page_size": serializer.validated_data.get("page_size", 20),
                    "has_next": False,
                }
            )

        chama_id = serializer.validated_data.get("chama_id")
        if chama_id:
            scoped = str(chama_id)
            membership = get_membership(request.user, scoped)
            if not membership:
                return Response(
                    {"detail": "You are not an approved active member in this chama."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            chama_ids = [scoped]

        unread_only = serializer.validated_data.get("unread")
        page = serializer.validated_data.get("page", 1)
        page_size = serializer.validated_data.get("page_size", 20)

        queryset = (
            Notification.objects.select_related("chama")
            .filter(
                recipient=request.user,
                chama_id__in=chama_ids,
                type=NotificationType.GENERAL_ANNOUNCEMENT,
                status__in=[
                    NotificationStatus.PENDING,
                    NotificationStatus.PROCESSING,
                    NotificationStatus.SENT,
                ],
            )
            .filter(models.Q(scheduled_at__isnull=True) | models.Q(scheduled_at__lte=timezone.now()))
            .order_by("-sent_at", "-created_at")
        )
        if unread_only:
            queryset = queryset.filter(inbox_status=NotificationInboxStatus.UNREAD)

        total_count = queryset.count()
        offset = (page - 1) * page_size
        items = list(queryset[offset : offset + page_size])

        results = [
            {
                "id": str(item.id),
                "title": item.subject,
                "message": item.message,
                "chama_id": str(item.chama_id) if item.chama_id else None,
                "chama_name": item.chama.name if item.chama else "",
                "created_at": item.created_at,
                "sent_at": item.sent_at,
                "action_url": item.action_url or "",
                "priority": item.priority,
                "inbox_status": item.inbox_status,
                "read_at": item.read_at,
            }
            for item in items
        ]

        return Response(
            {
                "results": results,
                "count": total_count,
                "page": page,
                "page_size": page_size,
                "has_next": offset + page_size < total_count,
            }
        )


class CommunicationEventCatalogView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = communication_catalog_response

    @extend_schema(
        tags=["Notifications"],
        operation_id="list_communication_event_catalog",
        responses={200: communication_catalog_response},
    )
    def get(self, request, *args, **kwargs):
        return Response({"results": NOTIFICATION_EVENT_CATALOG, "count": len(NOTIFICATION_EVENT_CATALOG)})


class CommunicationAnalyticsView(ChamaScopeMixin, NotificationsBillingMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    billing_feature_key = "broadcast_notifications"
    serializer_class = communication_analytics_response

    @extend_schema(
        tags=["Notifications"],
        operation_id="get_communication_analytics",
        parameters=[CommunicationAnalyticsQuerySerializer],
        responses={200: communication_analytics_response},
    )
    def get(self, request, *args, **kwargs):
        serializer = CommunicationAnalyticsQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        chama_id = (
            str(serializer.validated_data["chama_id"])
            if serializer.validated_data.get("chama_id")
            else self.get_scoped_chama_id(required=True)
        )
        membership = get_membership(request.user, chama_id)
        effective_role = get_effective_role(request.user, chama_id, membership) if membership else None
        if not membership or effective_role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.SECRETARY,
            MembershipRole.TREASURER,
            MembershipRole.AUDITOR,
        }:
            return Response({"detail": "You do not have permission to view communication analytics."}, status=status.HTTP_403_FORBIDDEN)

        deliveries = NotificationDelivery.objects.filter(notification__chama_id=chama_id)
        notifications = Notification.objects.filter(chama_id=chama_id)
        broadcasts = BroadcastAnnouncement.objects.filter(chama_id=chama_id)
        date_from = serializer.validated_data.get("date_from")
        date_to = serializer.validated_data.get("date_to")
        if date_from:
            deliveries = deliveries.filter(created_at__gte=date_from)
            notifications = notifications.filter(created_at__gte=date_from)
            broadcasts = broadcasts.filter(created_at__gte=date_from)
        if date_to:
            deliveries = deliveries.filter(created_at__lte=date_to)
            notifications = notifications.filter(created_at__lte=date_to)
            broadcasts = broadcasts.filter(created_at__lte=date_to)

        channel_counts = {
            channel: deliveries.filter(channel=channel).count()
            for channel in ["in_app", "email", "sms"]
        }
        status_counts = {
            state: deliveries.filter(status=state).count()
            for state in NotificationDeliveryStatus.values
        }
        total_deliveries = deliveries.count()
        failed_deliveries = status_counts.get(NotificationDeliveryStatus.FAILED, 0)
        sent_deliveries = status_counts.get(NotificationDeliveryStatus.SENT, 0) + status_counts.get(NotificationDeliveryStatus.DELIVERED, 0)

        top_templates = list(
            Notification.objects.filter(chama_id=chama_id, template__isnull=False)
            .values("template__id", "template__name")
            .annotate(usage_count=models.Count("id"))
            .order_by("-usage_count")[:5]
        )

        return Response(
            {
                "summary": {
                    "total_notifications": notifications.count(),
                    "total_deliveries": total_deliveries,
                    "sent_deliveries": sent_deliveries,
                    "failed_deliveries": failed_deliveries,
                    "queued_notifications": notifications.filter(status=NotificationStatus.PENDING).count(),
                    "broadcasts_count": broadcasts.count(),
                    "delivery_success_rate": round((sent_deliveries / total_deliveries) * 100, 2) if total_deliveries else 0,
                },
                "by_channel": channel_counts,
                "by_status": status_counts,
                "recent_campaigns": BroadcastAnnouncementSerializer(
                    broadcasts.order_by("-created_at")[:5],
                    many=True,
                ).data,
                "top_templates": top_templates,
            }
        )


class CommunicationDeliveryLogListView(ChamaScopeMixin, NotificationsBillingMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    billing_feature_key = "broadcast_notifications"
    serializer_class = AdminDeliveryLogSerializer

    @extend_schema(
        tags=["Notifications"],
        operation_id="list_communication_delivery_logs",
        parameters=[CommunicationDeliveryLogQuerySerializer],
        responses={200: communication_delivery_log_response},
    )
    def get(self, request, *args, **kwargs):
        serializer = CommunicationDeliveryLogQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        chama_id = (
            str(serializer.validated_data["chama_id"])
            if serializer.validated_data.get("chama_id")
            else self.get_scoped_chama_id(required=True)
        )
        membership = get_membership(request.user, chama_id)
        effective_role = get_effective_role(request.user, chama_id, membership) if membership else None
        if not membership or effective_role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.SECRETARY,
            MembershipRole.TREASURER,
            MembershipRole.AUDITOR,
        }:
            return Response({"detail": "You do not have permission to view delivery logs."}, status=status.HTTP_403_FORBIDDEN)

        queryset = NotificationDelivery.objects.select_related(
            "notification", "notification__recipient"
        ).filter(notification__chama_id=chama_id).order_by("-created_at")

        channel = serializer.validated_data.get("channel")
        state = serializer.validated_data.get("status")
        priority = serializer.validated_data.get("priority")
        search = serializer.validated_data.get("search")
        page = serializer.validated_data.get("page", 1)
        page_size = serializer.validated_data.get("page_size", 20)

        if channel:
            queryset = queryset.filter(channel=channel)
        if state:
            queryset = queryset.filter(status=state)
        if priority:
            queryset = queryset.filter(notification__priority=priority)
        if search:
            queryset = queryset.filter(
                models.Q(to_address__icontains=search)
                | models.Q(notification__subject__icontains=search)
                | models.Q(notification__message__icontains=search)
                | models.Q(provider_message_id__icontains=search)
            )

        total_count = queryset.count()
        offset = (page - 1) * page_size
        items = queryset[offset : offset + page_size]
        return Response(
            {
                "results": AdminDeliveryLogSerializer(items, many=True).data,
                "count": total_count,
                "page": page,
                "page_size": page_size,
                "has_next": offset + page_size < total_count,
            }
        )


class CommunicationRetryDeliveryView(ChamaScopeMixin, NotificationsBillingMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [NotificationDispatchRateThrottle]
    billing_feature_key = "broadcast_notifications"
    serializer_class = NotificationDeliverySerializer

    @extend_schema(
        tags=["Notifications"],
        operation_id="retry_communication_delivery",
        responses={
            200: inline_serializer(
                name="CommunicationRetryDeliveryResponse",
                fields={
                    "detail": serializers.CharField(),
                    "notification_id": serializers.CharField(),
                },
            )
        },
    )
    def post(self, request, id, *args, **kwargs):
        delivery = get_object_or_404(
            NotificationDelivery.objects.select_related("notification"),
            id=id,
        )
        chama_id = str(delivery.notification.chama_id)
        membership = get_membership(request.user, chama_id)
        effective_role = get_effective_role(request.user, chama_id, membership) if membership else None
        if not membership or effective_role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.SECRETARY,
            MembershipRole.TREASURER,
        }:
            return Response({"detail": "You do not have permission to retry failed deliveries."}, status=status.HTTP_403_FORBIDDEN)
        if delivery.status != NotificationDeliveryStatus.FAILED:
            return Response({"detail": "Only failed deliveries can be retried."}, status=status.HTTP_400_BAD_REQUEST)

        NotificationService.retry_notification(delivery.notification, actor=request.user)
        return Response({"detail": "Retry queued.", "notification_id": str(delivery.notification_id)})


class CommunicationBroadcastCancelView(ChamaScopeMixin, NotificationsBillingMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [NotificationDispatchRateThrottle]
    billing_feature_key = "broadcast_notifications"
    serializer_class = BroadcastAnnouncementSerializer

    @extend_schema(
        tags=["Notifications"],
        operation_id="cancel_notification_broadcast",
        responses={200: BroadcastAnnouncementSerializer},
    )
    def post(self, request, id, *args, **kwargs):
        announcement = get_object_or_404(BroadcastAnnouncement, id=id)
        membership = get_membership(request.user, announcement.chama_id)
        effective_role = get_effective_role(request.user, announcement.chama_id, membership) if membership else None
        if not membership or effective_role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.SECRETARY,
        }:
            return Response({"detail": "You do not have permission to cancel this communication."}, status=status.HTTP_403_FORBIDDEN)

        if announcement.status == BroadcastAnnouncementStatus.SENT:
            return Response({"detail": "Sent communications cannot be cancelled."}, status=status.HTTP_400_BAD_REQUEST)

        announcement.status = BroadcastAnnouncementStatus.CANCELLED
        announcement.save(update_fields=["status", "updated_at"])
        Notification.objects.filter(
            chama=announcement.chama,
            context_data__event_key=f"broadcast:{announcement.id}",
            status=NotificationStatus.PENDING,
        ).update(status=NotificationStatus.CANCELLED, updated_at=timezone.now())
        create_audit_log(
            actor=request.user,
            chama_id=announcement.chama_id,
            action="admin_broadcast_cancelled",
            entity_type="BroadcastAnnouncement",
            entity_id=announcement.id,
            metadata={"title": announcement.title},
        )
        return Response(BroadcastAnnouncementSerializer(announcement).data)


class NotificationsHealthCheckView(APIView):
    """
    Health check endpoint for notification services.
    Checks SMTP (Mailgun), AfricaTalking SMS, Redis, and Celery.
    """
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    serializer_class = notifications_health_response

    @extend_schema(
        tags=["Notifications"],
        operation_id="get_notifications_health_check",
        responses={200: notifications_health_response},
    )
    def get(self, request, *args, **kwargs):
        from django.conf import settings
        from django.core.mail import get_connection
        from django_redis import get_redis_connection

        result = {
            "smtp": "unknown",
            "sms": "unknown",
            "redis": "unknown",
            "celery": "unknown",
            "details": {},
        }

        # Check SMTP settings
        try:
            email_backend = getattr(settings, "EMAIL_BACKEND", "")
            if "smtp" in email_backend.lower():
                # Try to open a connection
                connection = get_connection()
                connection.open()
                connection.close()
                result["smtp"] = "ok"
                result["details"]["smtp"] = {
                    "backend": getattr(settings, "EMAIL_BACKEND", "unknown"),
                    "host": getattr(settings, "EMAIL_HOST", "not configured"),
                    "port": getattr(settings, "EMAIL_PORT", "not configured"),
                }
            else:
                result["smtp"] = "error"
                result["details"]["smtp"] = "Email backend is not SMTP"
        except Exception as e:
            result["smtp"] = "error"
            result["details"]["smtp"] = str(e)

        # Check AfricaTalking SMS
        try:
            sms_provider = getattr(settings, "SMS_PROVIDER", "")
            if sms_provider == "africastalking":
                api_key = getattr(settings, "AFRICAS_TALKING_API_KEY", None)
                username = getattr(settings, "AFRICAS_TALKING_USERNAME", None)
                if api_key and username:
                    result["sms"] = "ok"
                    result["details"]["sms"] = {
                        "provider": "africastalking",
                        "username": username,
                        "sender_id": getattr(settings, "AFRICAS_TALKING_SENDER_ID", "not configured"),
                    }
                else:
                    result["sms"] = "error"
                    result["details"]["sms"] = "AFRICAS_TALKING_API_KEY or USERNAME not configured"
            else:
                result["sms"] = "error"
                result["details"]["sms"] = f"Unknown SMS provider: {sms_provider}"
        except Exception as e:
            result["sms"] = "error"
            result["details"]["sms"] = str(e)

        # Check Redis
        try:
            redis_url = getattr(settings, "CACHE_URL", None) or getattr(settings, "REDIS_URL", None)
            if redis_url:
                redis_conn = get_redis_connection("default")
                redis_conn.ping()
                result["redis"] = "ok"
                result["details"]["redis"] = {
                    "url": redis_url.split("@")[1] if "@" in redis_url else redis_url,  # Hide password
                }
            else:
                result["redis"] = "error"
                result["details"]["redis"] = "REDIS_URL not configured"
        except Exception as e:
            result["redis"] = "error"
            result["details"]["redis"] = str(e)

        # Check Celery
        try:
            celery_broker = getattr(settings, "CELERY_BROKER_URL", None)
            if celery_broker:
                # Try to inspect celery
                from celery.task.control import inspect
                inspector = inspect()
                stats = inspector.stats()
                if stats:
                    result["celery"] = "ok"
                else:
                    result["celery"] = "warning"
                    result["details"]["celery"] = "Celery broker reachable but no workers active"
            else:
                result["celery"] = "error"
                result["details"]["celery"] = "CELERY_BROKER_URL not configured"
        except Exception as e:
            result["celery"] = "error"
            result["details"]["celery"] = str(e)

        # Determine overall status
        is_healthy = (
            result["smtp"] == "ok" and
            result["sms"] == "ok" and
            result["redis"] == "ok"
        )

        return Response({
            "status": "healthy" if is_healthy else "degraded",
            **result,
        }, status=200 if is_healthy else 503)
