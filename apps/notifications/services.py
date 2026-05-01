from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta

from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus
from apps.notifications.email import EmailAttachment, send_email_message
from apps.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationChannel,
    NotificationDelivery,
    NotificationDeliveryStatus,
    NotificationEvent,
    NotificationEventStatus,
    NotificationEventThrottle,
    NotificationInboxStatus,
    NotificationLog,
    NotificationPreference,
    NotificationPriority,
    NotificationStatus,
    NotificationTarget,
    NotificationTemplate,
    NotificationType,
)
from apps.notifications.sms import send_sms_message
from core.audit import create_audit_log
from core.request_context import get_correlation_id

logger = logging.getLogger(__name__)


class NotificationService:
    REMINDER_TYPES = {
        NotificationType.CONTRIBUTION_REMINDER,
        NotificationType.LOAN_UPDATE,
        NotificationType.MEETING_NOTIFICATION,
    }
    RETRY_DELAYS_SECONDS = (120, 600, 3600)

    @staticmethod
    def queue_notification(notification: Notification):
        NotificationService._ensure_delivery_records(notification)

        if notification.scheduled_at and notification.scheduled_at > timezone.now():
            return

        # Keep tests deterministic and avoid external broker dependency.
        if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False) or os.environ.get(
            "PYTEST_CURRENT_TEST"
        ):
            NotificationService.process_notification(str(notification.id))
            return

        from apps.notifications.tasks import process_notification

        try:
            process_notification.delay(str(notification.id))
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to queue notification %s, processing inline.",
                notification.id,
            )
            NotificationService.process_notification(str(notification.id))

    @staticmethod
    def _normalize_priority(priority: str | None) -> str:
        return priority or NotificationPriority.NORMAL

    @staticmethod
    def publish_event(
        *,
        chama,
        event_key: str,
        event_type: str,
        target: str,
        channels: Iterable[str],
        subject: str = "",
        message: str = "",
        action_url: str = "",
        category: str | None = None,
        priority: str = NotificationPriority.NORMAL,
        payload: dict | None = None,
        metadata: dict | None = None,
        template_id=None,
        template_code: str = "",
        target_roles: list[str] | None = None,
        target_user_ids: list[str] | None = None,
        segment: str = "",
        scheduled_at=None,
        enforce_once_daily: bool = False,
        actor=None,
    ) -> NotificationEvent:
        chama_obj = NotificationService._resolve_chama(chama=chama, user=actor)
        payload_data = payload or {}
        metadata_data = metadata or {}
        channel_list = [str(channel) for channel in channels]
        priority = NotificationService._normalize_priority(priority)

        event, created = NotificationEvent.objects.get_or_create(
            event_key=event_key,
            defaults={
                "chama": chama_obj,
                "event_type": event_type,
                "target": target,
                "target_roles": list(target_roles or []),
                "target_user_ids": [str(item) for item in (target_user_ids or [])],
                "segment": segment,
                "channels": channel_list,
                "category": category
                or NotificationService._category_from_event_type(event_type),
                "priority": priority,
                "subject": subject,
                "message": message,
                "action_url": action_url,
                "payload": payload_data,
                "created_by": actor,
                "updated_by": actor,
            },
        )

        if not created and event.status == NotificationEventStatus.PROCESSED:
            return event

        try:
            memberships = NotificationService._resolve_event_recipients(
                chama=chama_obj,
                target=target,
                target_roles=target_roles or [],
                target_user_ids=target_user_ids or [],
                segment=segment,
                payload=payload_data,
            )
            template = NotificationService._resolve_event_template(
                chama=chama_obj,
                template_id=template_id,
                template_code=template_code,
                channels=channel_list,
            )

            created_notifications = 0
            recipient_ids = set()
            for membership in memberships:
                user = membership.user
                recipient_ids.add(str(user.id))
                notification_content = NotificationService._build_event_content(
                    event=event,
                    user=user,
                    payload=payload_data,
                    metadata=metadata_data,
                    template=template,
                    subject=subject,
                    message=message,
                    action_url=action_url,
                )
                notification = NotificationService.send_notification(
                    user=user,
                    chama=chama_obj,
                    channels=channel_list,
                    subject=notification_content["subject"],
                    message=notification_content["message"],
                    notification_type=event_type,
                    category=event.category,
                    priority=event.priority,
                    action_url=notification_content["action_url"],
                    metadata=notification_content["metadata"],
                    scheduled_at=scheduled_at,
                    context_data={
                        **payload_data,
                        "event_type": event_type,
                        "event_key": event.event_key,
                    },
                    idempotency_key=NotificationService._event_notification_idempotency_key(
                        event.event_key,
                        user.id,
                    ),
                    enforce_once_daily=enforce_once_daily,
                    actor=actor,
                )
                if notification:
                    created_notifications += 1

            event.status = NotificationEventStatus.PROCESSED
            event.processed_at = timezone.now()
            event.recipient_count = len(recipient_ids)
            event.notification_count = created_notifications
            event.last_error = ""
            event.save(
                update_fields=[
                    "status",
                    "processed_at",
                    "recipient_count",
                    "notification_count",
                    "last_error",
                    "updated_at",
                ]
            )
            create_audit_log(
                actor=actor,
                chama_id=chama_obj.id if chama_obj else None,
                action="notification_event_processed",
                entity_type="NotificationEvent",
                entity_id=event.id,
                metadata={
                    "event_type": event_type,
                    "event_key": event.event_key,
                    "target": target,
                    "recipient_count": event.recipient_count,
                    "notification_count": event.notification_count,
                },
            )
            return event
        except Exception as exc:  # noqa: BLE001
            event.status = NotificationEventStatus.FAILED
            event.last_error = str(exc)
            event.save(update_fields=["status", "last_error", "updated_at"])
            create_audit_log(
                actor=actor,
                chama_id=chama_obj.id if chama_obj else None,
                action="notification_event_failed",
                entity_type="NotificationEvent",
                entity_id=event.id,
                metadata={
                    "event_type": event_type,
                    "event_key": event.event_key,
                    "error": str(exc),
                },
            )
            raise

    @staticmethod
    def send_notification(
        user,
        message: str,
        channels: Iterable[str],
        *,
        chama=None,
        subject: str = "",
        notification_type: str = NotificationType.SYSTEM,
        category: str | None = None,
        priority: str = NotificationPriority.NORMAL,
        html_message: str = "",
        action_url: str = "",
        metadata: dict | None = None,
        scheduled_at=None,
        context_data: dict | None = None,
        idempotency_key: str | None = None,
        enforce_once_daily: bool = False,
        actor=None,
    ) -> Notification:
        if not message:
            raise ValueError("message is required.")

        chama_obj = NotificationService._resolve_chama(chama=chama, user=user)
        priority = NotificationService._normalize_priority(priority)
        context = context_data or {}
        meta = metadata or {}
        action_context = {
            **context,
            **({"chama_id": str(chama_obj.id)} if chama_obj else {}),
        }
        action_descriptor = NotificationService._infer_action_descriptor(
            notification_type=notification_type,
            context_data=action_context,
            action_url=action_url,
        )
        action_url = action_descriptor["action_url"]
        meta = {
            **meta,
            **action_descriptor["metadata"],
        }

        normalized_channels = {
            channel.strip().lower()
            for channel in channels
            if channel and channel.strip()
        }

        send_email = "email" in normalized_channels and bool(getattr(user, "email", ""))
        send_sms = "sms" in normalized_channels and bool(getattr(user, "phone", ""))
        
        # SMS Only for CRITICAL notifications by default (unless explicitly requested)
        # This prevents flooding users with SMS for non-urgent notifications
        sms_explicitly_requested = "sms" in normalized_channels
        if send_sms and not sms_explicitly_requested:
            # Only allow SMS if priority is HIGH or CRITICAL
            if priority not in [NotificationPriority.HIGH, NotificationPriority.CRITICAL]:
                send_sms = False
                logger.debug(f"SMS disabled for non-critical notification: {notification_type}")
        
        send_push = bool(
            {"push", "in_app"}.intersection(normalized_channels)
        )

        # Workflow rule: announcement emails are reserved for critical alerts only.
        if (
            send_email
            and notification_type == NotificationType.GENERAL_ANNOUNCEMENT
            and priority != NotificationPriority.CRITICAL
        ):
            send_email = False

        preference = None
        if chama_obj:
            preference = NotificationPreference.objects.filter(
                user=user,
                chama=chama_obj,
            ).first()
        if preference:
            send_email, send_sms, send_push = NotificationService._apply_preferences(
                notification_type=notification_type,
                priority=priority,
                preference=preference,
                send_email=send_email,
                send_sms=send_sms,
                send_push=send_push,
            )

            # Queue SMS-only notifications until quiet hours end.
            if send_sms and not send_email and not send_push:
                if NotificationService._is_in_quiet_hours(
                    now=timezone.localtime(timezone.now()),
                    start=preference.quiet_hours_start,
                    end=preference.quiet_hours_end,
                ):
                    quiet_until = NotificationService._next_allowed_time(
                        now=timezone.localtime(timezone.now()),
                        quiet_end=preference.quiet_hours_end,
                    )
                    if not scheduled_at or scheduled_at < quiet_until:
                        scheduled_at = quiet_until

        event_type = str(context.get("event_type") or notification_type)
        should_enforce_daily = enforce_once_daily or (
            notification_type in NotificationService.REMINDER_TYPES
        )
        if should_enforce_daily and NotificationService._already_sent_today(
            user_id=user.id,
            chama_id=chama_obj.id if chama_obj else None,
            event_type=event_type,
        ):
            existing = (
                Notification.objects.filter(
                    recipient=user,
                    chama=chama_obj,
                    type=notification_type,
                    created_at__date=timezone.localdate(),
                )
                .order_by("-created_at")
                .first()
            )
            if existing:
                return existing

        status_value = NotificationStatus.PENDING
        last_error = ""
        if not (send_email or send_sms or send_push):
            status_value = NotificationStatus.CANCELLED
            last_error = (
                "All selected channels are disabled by preferences or missing contacts."
            )

        defaults = {
            "chama": chama_obj,
            "recipient": user,
            "type": notification_type,
            "category": category
            or NotificationService._category_from_notification_type(notification_type),
            "priority": priority,
            "status": status_value,
            "inbox_status": NotificationInboxStatus.UNREAD,
            "subject": subject,
            "message": message,
            "html_message": html_message,
            "action_url": action_url,
            "metadata": meta,
            "send_email": send_email,
            "send_sms": send_sms,
            "send_push": send_push,
            "email": user.email or "",
            "phone": user.phone or "",
            "scheduled_at": scheduled_at,
            "context_data": {
                **context,
                "trace_id": get_correlation_id() or "",
            },
            "last_error": last_error,
            "max_retries": len(NotificationService.RETRY_DELAYS_SECONDS) + 1,
            "created_by": actor,
            "updated_by": actor,
        }

        if idempotency_key:
            notification = NotificationService._get_or_create_idempotent_notification(
                idempotency_key=idempotency_key,
                defaults=defaults,
            )
        else:
            notification = Notification.objects.create(**defaults)

        if notification.status == NotificationStatus.PENDING:
            NotificationService._ensure_delivery_records(notification)
            NotificationService.queue_notification(notification)
            create_audit_log(
                actor=actor,
                chama_id=chama_obj.id if chama_obj else None,
                action="notification_queued",
                entity_type="Notification",
                entity_id=notification.id,
                metadata={
                    "notification_type": notification.type,
                    "status": notification.status,
                    "send_email": notification.send_email,
                    "send_sms": notification.send_sms,
                    "send_push": notification.send_push,
                    "idempotency_key": notification.idempotency_key or "",
                    "event_type": event_type,
                },
            )
        return notification

    @staticmethod
    @transaction.atomic
    def process_notification(notification_id):
        notification = (
            Notification.objects.select_related("recipient", "chama")
            .filter(id=notification_id)
            .first()
        )
        if not notification:
            logger.warning("Notification %s not found", notification_id)
            return

        if notification.status not in {
            NotificationStatus.PENDING,
            NotificationStatus.FAILED,
        }:
            return

        if notification.scheduled_at and notification.scheduled_at > timezone.now():
            return

        notification.status = NotificationStatus.PROCESSING
        notification.save(update_fields=["status", "updated_at"])

        NotificationService._ensure_delivery_records(notification)
        failures: list[str] = []

        email_delivery = None
        sms_delivery = None
        in_app_delivery = None
        push_delivery = None

        if notification.send_email and notification.email:
            email_delivery = NotificationService._get_delivery(
                notification=notification,
                channel=NotificationChannel.EMAIL,
                to_address=notification.email,
            )
        if notification.send_sms and notification.phone:
            sms_delivery = NotificationService._get_delivery(
                notification=notification,
                channel=NotificationChannel.SMS,
                to_address=notification.phone,
            )
        if notification.send_push:
            in_app_delivery = NotificationService._get_delivery(
                notification=notification,
                channel=NotificationChannel.IN_APP,
            )
            push_delivery = NotificationService._get_delivery(
                notification=notification,
                channel=NotificationChannel.PUSH,
            )

        if (
            notification.send_email
            and notification.email
            and email_delivery
            and not NotificationService._delivery_complete(email_delivery)
        ):
            try:
                NotificationService.send_email_for_notification(
                    notification,
                    delivery=email_delivery,
                )
            except Exception as exc:  # noqa: BLE001
                failures.append(str(exc))

        if (
            notification.send_sms
            and notification.phone
            and sms_delivery
            and not NotificationService._delivery_complete(sms_delivery)
        ):
            try:
                NotificationService.send_sms_for_notification(
                    notification,
                    delivery=sms_delivery,
                )
            except Exception as exc:  # noqa: BLE001
                failures.append(str(exc))

        if (
            notification.send_push
            and in_app_delivery
            and not NotificationService._delivery_complete(in_app_delivery)
        ):
            NotificationLog.objects.create(
                notification=notification,
                channel=NotificationChannel.IN_APP,
                status=NotificationStatus.SENT,
                provider_response={"provider": "in_app"},
            )
            NotificationService._mark_delivery_sent(
                delivery=in_app_delivery,
                provider="in_app",
            )
        if (
            notification.send_push
            and push_delivery
            and not NotificationService._delivery_complete(push_delivery)
        ):
            try:
                NotificationService.send_push_for_notification(
                    notification,
                    delivery=push_delivery,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Push delivery failed for notification %s",
                    notification.id,
                )

        if failures:
            NotificationService._mark_notification_failure(notification, failures)
            return

        notification.status = NotificationStatus.SENT
        notification.sent_at = timezone.now()
        notification.last_error = ""
        notification.next_retry_at = None
        notification.save(
            update_fields=[
                "status",
                "sent_at",
                "last_error",
                "next_retry_at",
                "updated_at",
            ]
        )

        NotificationService._touch_event_throttle(notification)

        create_audit_log(
            actor=notification.updated_by or notification.created_by,
            chama_id=notification.chama_id,
            action="notification_sent",
            entity_type="Notification",
            entity_id=notification.id,
            metadata={
                "send_email": notification.send_email,
                "send_sms": notification.send_sms,
                "send_push": notification.send_push,
            },
        )

    @staticmethod
    def send_email_for_notification(
        notification: Notification,
        *,
        delivery: NotificationDelivery | None = None,
    ):
        delivery_record = delivery or NotificationService._get_delivery(
            notification=notification,
            channel=NotificationChannel.EMAIL,
            to_address=notification.email,
        )
        NotificationService._register_delivery_attempt(delivery_record)
        try:
            html_body = notification.html_message or ""
            attachments: list[EmailAttachment] = []

            if (
                notification.type == NotificationType.PAYMENT_CONFIRMATION
                and not html_body
                and isinstance(notification.metadata, dict)
                and notification.metadata.get("payment_intent_id")
            ):
                try:
                    from django.template.loader import render_to_string

                    from apps.deeplinks.deeplinks_service import DeepLinksService
                    from apps.payments.receipt_pdf import render_receipt_pdf
                    from apps.payments.unified_models import (
                        PaymentIntent,
                        PaymentReceipt,
                    )

                    intent_id = str(notification.metadata.get("payment_intent_id") or "").strip()
                    intent = (
                        PaymentIntent.objects.select_related("chama", "user")
                        .filter(id=intent_id)
                        .first()
                    )
                    receipt = (
                        PaymentReceipt.objects.select_related("payment_intent", "payment_intent__chama", "payment_intent__user")
                        .filter(payment_intent_id=intent_id)
                        .first()
                    )

                    receipt_url = ""
                    if intent:
                        receipt_url = (
                            DeepLinksService.generate_payment_link(str(intent.id)).get("universal_link")
                            or ""
                        )

                    if receipt:
                        pdf_bytes = render_receipt_pdf(receipt=receipt)
                        attachments.append(
                            EmailAttachment(
                                filename=f"MyChama-Receipt-{receipt.receipt_number}.pdf",
                                content=pdf_bytes,
                                mimetype="application/pdf",
                            )
                        )

                    chama_name = (
                        getattr(intent.chama, "name", "")
                        if intent and getattr(intent, "chama", None)
                        else str(notification.metadata.get("chama_name") or "") or "MyChama"
                    )
                    user_name = (
                        getattr(intent.user, "full_name", "")
                        if intent and getattr(intent, "user", None)
                        else getattr(notification.recipient, "full_name", "")
                        or notification.email
                    )

                    provider_reference = ""
                    if receipt and isinstance(receipt.metadata, dict):
                        provider_reference = str(receipt.metadata.get("provider_reference") or "")

                    issued_at_value = ""
                    if receipt and getattr(receipt, "issued_at", None):
                        issued_at_value = timezone.localtime(receipt.issued_at).strftime("%Y-%m-%d %H:%M")

                    currency = str(notification.metadata.get("currency") or (intent.currency if intent else "") or "KES")
                    amount = str(notification.metadata.get("amount") or (intent.amount if intent else "0.00"))
                    purpose = str(notification.metadata.get("purpose") or (intent.purpose if intent else "") or "payment")
                    payment_method = str(notification.metadata.get("payment_method") or (intent.payment_method if intent else "") or "payment")

                    subject_value = notification.subject or "Payment receipt"
                    html_body = render_to_string(
                        "emails/payments/01-payment-receipt.html",
                        {
                            "subject": subject_value,
                            "user_name": user_name,
                            "chama_name": chama_name,
                            "currency": currency,
                            "amount": amount,
                            "purpose": purpose.replace("_", " ").title(),
                            "payment_method": payment_method.replace("_", " ").title(),
                            "receipt_number": getattr(receipt, "receipt_number", "") if receipt else "",
                            "reference_number": getattr(receipt, "reference_number", "") if receipt else "",
                            "provider_reference": provider_reference,
                            "issued_at": issued_at_value,
                            "receipt_url": receipt_url,
                            "company_name": getattr(settings, "COMPANY_NAME", "MyChama Technologies"),
                            "company_address": getattr(settings, "COMPANY_ADDRESS", "Nairobi, Kenya"),
                        },
                    )
                except Exception:  # noqa: BLE001
                    html_body = ""
                    attachments = []

            result = send_email_message(
                subject=notification.subject,
                recipient_list=[notification.email],
                body=notification.message,
                html_body=html_body,
                attachments=attachments,
            )
            NotificationLog.objects.create(
                notification=notification,
                channel=NotificationChannel.EMAIL,
                status=NotificationStatus.SENT,
                provider_response={
                    "provider": result.provider,
                    "raw": result.raw_response or {},
                    "sent_count": result.sent_count,
                },
            )
            NotificationService._mark_delivery_sent(
                delivery=delivery_record,
                provider=result.provider,
            )
            return result
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Email delivery failed for notification %s",
                notification.id,
            )
            NotificationLog.objects.create(
                notification=notification,
                channel=NotificationChannel.EMAIL,
                status=NotificationStatus.FAILED,
                error_message=str(exc),
            )
            NotificationService._mark_delivery_failed(
                delivery=delivery_record,
                provider="email_backend",
                error_message=str(exc),
            )
            raise

    @staticmethod
    def _enforce_sms_billing_limit(notification: Notification):
        if not notification.chama_id:
            return

        from apps.billing.metering import usage_within_limit
        from apps.billing.models import UsageMetric

        usage = usage_within_limit(notification.chama, UsageMetric.SMS, 1)
        if not usage["allowed"]:
            raise ValueError(
                "Your current subscription has exhausted its monthly SMS allocation."
            )

    @staticmethod
    def send_sms_for_notification(
        notification: Notification,
        *,
        delivery: NotificationDelivery | None = None,
    ):
        delivery_record = delivery or NotificationService._get_delivery(
            notification=notification,
            channel=NotificationChannel.SMS,
            to_address=notification.phone,
        )
        NotificationService._register_delivery_attempt(delivery_record)
        try:
            NotificationService._enforce_sms_billing_limit(notification)
            result = send_sms_message(
                phone_number=notification.phone,
                message=notification.message,
            )
            if notification.chama_id:
                from apps.billing.metering import increment_usage
                from apps.billing.models import UsageMetric

                increment_usage(notification.chama, UsageMetric.SMS, 1)
            NotificationLog.objects.create(
                notification=notification,
                channel=NotificationChannel.SMS,
                status=NotificationStatus.SENT,
                provider_response={
                    "provider": result.provider,
                    "raw": result.raw_response or {},
                    "provider_message_id": result.provider_message_id,
                },
                external_message_id=result.provider_message_id,
            )
            NotificationService._mark_delivery_sent(
                delivery=delivery_record,
                provider=result.provider,
                provider_message_id=result.provider_message_id or "",
            )
            return result
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "SMS delivery failed for notification %s",
                notification.id,
            )
            NotificationLog.objects.create(
                notification=notification,
                channel=NotificationChannel.SMS,
                status=NotificationStatus.FAILED,
                error_message=str(exc),
            )
            NotificationService._mark_delivery_failed(
                delivery=delivery_record,
                provider="sms_provider",
                error_message=str(exc),
            )
            raise

    @staticmethod
    def _ensure_delivery_records(notification: Notification):
        if notification.send_email and notification.email:
            NotificationService._get_delivery(
                notification=notification,
                channel=NotificationChannel.EMAIL,
                to_address=notification.email,
            )
        if notification.send_sms and notification.phone:
            NotificationService._get_delivery(
                notification=notification,
                channel=NotificationChannel.SMS,
                to_address=notification.phone,
            )
        if notification.send_push:
            NotificationService._get_delivery(
                notification=notification,
                channel=NotificationChannel.IN_APP,
            )
            NotificationService._get_delivery(
                notification=notification,
                channel=NotificationChannel.PUSH,
            )

    @staticmethod
    def _get_delivery(
        *,
        notification: Notification,
        channel: str,
        to_address: str = "",
    ) -> NotificationDelivery:
        delivery = (
            NotificationDelivery.objects.filter(
                notification=notification,
                channel=channel,
            )
            .order_by("-created_at")
            .first()
        )
        if delivery:
            updated_fields = []
            if to_address and delivery.to_address != to_address:
                delivery.to_address = to_address
                updated_fields.append("to_address")
            if delivery.status == "":
                delivery.status = NotificationDeliveryStatus.QUEUED
                updated_fields.append("status")
            if updated_fields:
                delivery.save(update_fields=[*updated_fields, "updated_at"])
            return delivery

        return NotificationDelivery.objects.create(
            notification=notification,
            channel=channel,
            to_address=to_address,
            status=NotificationDeliveryStatus.QUEUED,
            attempts=0,
        )

    @staticmethod
    def _register_delivery_attempt(delivery: NotificationDelivery):
        delivery.attempts += 1
        delivery.last_attempt_at = timezone.now()
        delivery.save(update_fields=["attempts", "last_attempt_at", "updated_at"])

    @staticmethod
    def _mark_delivery_sent(
        *,
        delivery: NotificationDelivery,
        provider: str,
        provider_message_id: str = "",
    ):
        delivery.provider = provider
        delivery.provider_message_id = provider_message_id
        delivery.status = NotificationDeliveryStatus.SENT
        delivery.error_message = ""
        delivery.delivered_at = delivery.delivered_at or timezone.now()
        delivery.last_attempt_at = timezone.now()
        delivery.save(
            update_fields=[
                "provider",
                "provider_message_id",
                "status",
                "error_message",
                "delivered_at",
                "last_attempt_at",
                "updated_at",
            ]
        )

    @staticmethod
    def _mark_delivery_failed(
        *,
        delivery: NotificationDelivery,
        provider: str,
        error_message: str,
    ):
        delivery.provider = provider
        delivery.status = NotificationDeliveryStatus.FAILED
        delivery.error_message = error_message
        delivery.last_attempt_at = timezone.now()
        delivery.save(
            update_fields=[
                "provider",
                "status",
                "error_message",
                "last_attempt_at",
                "updated_at",
            ]
        )

    @staticmethod
    def _delivery_complete(delivery: NotificationDelivery) -> bool:
        return delivery.status in {
            NotificationDeliveryStatus.SENT,
            NotificationDeliveryStatus.DELIVERED,
        }

    @staticmethod
    def _resolve_chama(*, chama, user):
        if isinstance(chama, Chama):
            return chama
        if chama:
            return get_object_or_404(Chama, id=chama)

        membership = (
            Membership.objects.filter(
                user=user,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
            )
            .select_related("chama")
            .first()
        )
        if not membership:
            # Platform-scoped notifications (e.g., KYC/OTP before joining a chama).
            return None
        return membership.chama

    @staticmethod
    def _get_or_create_idempotent_notification(*, idempotency_key: str, defaults: dict):
        try:
            notification, _ = Notification.objects.get_or_create(
                idempotency_key=idempotency_key,
                defaults={**defaults, "idempotency_key": idempotency_key},
            )
            return notification
        except IntegrityError:
            return Notification.objects.get(idempotency_key=idempotency_key)

    @staticmethod
    def _mark_notification_failure(notification: Notification, failures: list[str]):
        notification.status = NotificationStatus.FAILED
        notification.retry_count += 1
        notification.last_error = "; ".join(failures)

        if notification.retry_count >= notification.max_retries:
            notification.next_retry_at = None
        else:
            retry_index = max(notification.retry_count - 1, 0)
            if retry_index < len(NotificationService.RETRY_DELAYS_SECONDS):
                notification.next_retry_at = timezone.now() + timedelta(
                    seconds=NotificationService.RETRY_DELAYS_SECONDS[retry_index]
                )
            else:
                notification.next_retry_at = None

        notification.save(
            update_fields=[
                "status",
                "retry_count",
                "last_error",
                "next_retry_at",
                "updated_at",
            ]
        )
        create_audit_log(
            actor=notification.updated_by or notification.created_by,
            chama_id=notification.chama_id,
            action="notification_failed",
            entity_type="Notification",
            entity_id=notification.id,
            metadata={
                "retry_count": notification.retry_count,
                "max_retries": notification.max_retries,
                "failures": failures,
            },
        )

    @staticmethod
    def _apply_preferences(
        *,
        notification_type: str,
        priority: str,
        preference: NotificationPreference,
        send_email: bool,
        send_sms: bool,
        send_push: bool,
    ):
        send_email = send_email and preference.email_enabled
        send_sms = send_sms and preference.sms_enabled
        send_push = send_push and preference.in_app_enabled

        if notification_type == NotificationType.CONTRIBUTION_REMINDER:
            send_email = send_email and preference.email_contribution_reminders
            send_sms = send_sms and preference.sms_contribution_reminders
        elif notification_type == NotificationType.MEETING_NOTIFICATION:
            send_email = send_email and preference.email_meeting_notifications
            send_sms = send_sms and preference.sms_meeting_notifications
        elif notification_type == NotificationType.PAYMENT_CONFIRMATION:
            send_email = send_email and preference.email_payment_confirmations
            send_sms = send_sms and preference.sms_payment_confirmations
        elif notification_type == NotificationType.LOAN_UPDATE:
            send_email = send_email and preference.email_loan_updates
            send_sms = send_sms and preference.sms_loan_updates
        elif notification_type == NotificationType.GENERAL_ANNOUNCEMENT:
            # Workflow: announcements may use SMS based on member preferences, but email is
            # reserved for critical announcements and should not be suppressed by
            # the general-announcements toggle once the sender marks it critical.
            send_sms = send_sms and preference.sms_general_announcements
            if priority != NotificationPriority.CRITICAL:
                send_email = False

        if (
            preference.critical_only_mode
            and priority != NotificationPriority.CRITICAL
        ):
            # Keep in-app delivery, suppress external channels for non-critical events.
            send_email = False
            send_sms = False
        return send_email, send_sms, send_push

    @staticmethod
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
            NotificationType.SYSTEM: NotificationCategory.SYSTEM,
            NotificationType.GENERAL_ANNOUNCEMENT: NotificationCategory.SYSTEM,
        }
        return mapping.get(notification_type, NotificationCategory.SYSTEM)

    @staticmethod
    def _category_from_event_type(event_type: str) -> str:
        return NotificationService._category_from_notification_type(event_type)

    @staticmethod
    def _resolve_event_template(
        *,
        chama: Chama,
        template_id=None,
        template_code: str = "",
        channels: Iterable[str],
    ) -> NotificationTemplate | None:
        if template_id:
            return NotificationTemplate.objects.filter(
                id=template_id,
                chama=chama,
                is_active=True,
            ).first()

        code = str(template_code or "").strip()
        if not code:
            return None

        preferred_channel = NotificationChannel.IN_APP
        for channel in channels:
            normalized = str(channel or "").strip().lower()
            if normalized in {NotificationChannel.EMAIL, NotificationChannel.SMS, NotificationChannel.IN_APP}:
                preferred_channel = normalized
                break

        return (
            NotificationTemplate.objects.filter(
                chama=chama,
                template_code=code,
                is_active=True,
            )
            .order_by(
                models.Case(
                    models.When(channel=preferred_channel, then=0),
                    default=1,
                    output_field=models.IntegerField(),
                ),
                "created_at",
            )
            .first()
        )

    @staticmethod
    def _build_event_content(
        *,
        event: NotificationEvent,
        user,
        payload: dict,
        metadata: dict,
        template: NotificationTemplate | None,
        subject: str,
        message: str,
        action_url: str,
    ) -> dict:
        context = {
            **payload,
            "user_name": getattr(user, "full_name", "") or getattr(user, "email", ""),
            "recipient_name": getattr(user, "full_name", "") or getattr(user, "email", ""),
            "chama_name": event.chama.name,
        }
        resolved_subject = subject or event.subject
        resolved_message = message or event.message
        if template:
            resolved_subject = resolved_subject or template.subject
            resolved_message = resolved_message or template.body

        return {
            "subject": NotificationService._render_template_value(
                resolved_subject,
                context,
            ),
            "message": NotificationService._render_template_value(
                resolved_message,
                context,
            ),
            "action_url": action_url or event.action_url,
            "metadata": {
                **metadata,
                "event_key": event.event_key,
                "event_type": event.event_type,
            },
        }

    @staticmethod
    def _infer_action_descriptor(
        *,
        notification_type: str,
        context_data: dict,
        action_url: str,
    ) -> dict:
        try:
            from apps.deeplinks.deeplinks_service import DeepLinksService
        except Exception:  # noqa: BLE001
            return {"action_url": action_url, "metadata": {}}

        route = ""
        params: dict[str, str] = {}
        chama_id = str(context_data.get("chama_id") or "").strip() or None

        if context_data.get("meeting_id"):
            route = "meeting/detail"
            params["meeting_id"] = str(context_data["meeting_id"])
        elif context_data.get("loan_id"):
            route = "loan/detail"
            params["loan_id"] = str(context_data["loan_id"])
        elif context_data.get("payment_id"):
            route = "payment/detail"
            params["payment_id"] = str(context_data["payment_id"])
        elif context_data.get("announcement_id"):
            route = "notification"
            params["announcement_id"] = str(context_data["announcement_id"])
        elif context_data.get("invite_code"):
            route = "invite"
            params["invite_code"] = str(context_data["invite_code"])
        elif notification_type == NotificationType.MEETING_NOTIFICATION:
            route = "meeting/detail"
        elif notification_type == NotificationType.LOAN_UPDATE:
            route = "loan/detail"
        elif notification_type in {
            NotificationType.PAYMENT_CONFIRMATION,
            NotificationType.CONTRIBUTION_REMINDER,
        }:
            route = "payment/detail"
        elif notification_type == NotificationType.GENERAL_ANNOUNCEMENT:
            route = "notification"

        if not route:
            return {"action_url": action_url, "metadata": {}}

        deep_link = DeepLinksService.generate_deep_link(
            route=route,
            params=params or None,
            chama_id=chama_id,
        )
        universal_link = DeepLinksService.generate_universal_link(
            route=route,
            params=params or None,
            chama_id=chama_id,
        )
        resolved_action_url = action_url or universal_link
        return {
            "action_url": resolved_action_url,
            "metadata": {
                "deep_link": deep_link,
                "universal_link": universal_link,
                "deep_link_route": route,
                "deep_link_params": params,
            },
        }

    @staticmethod
    def _push_badge_count(user) -> int:
        return Notification.objects.filter(
            recipient=user,
            inbox_status=NotificationInboxStatus.UNREAD,
        ).count()

    @staticmethod
    def send_push_for_notification(
        notification: Notification,
        *,
        delivery: NotificationDelivery | None = None,
    ):
        from apps.notifications.push import send_push_to_user

        delivery_record = delivery or NotificationService._get_delivery(
            notification=notification,
            channel=NotificationChannel.PUSH,
        )
        NotificationService._register_delivery_attempt(delivery_record)

        if not getattr(settings, "PUSH_NOTIFICATION_ENABLED", True):
            NotificationService._mark_delivery_sent(
                delivery=delivery_record,
                provider="push_disabled",
            )
            return None

        result = send_push_to_user(
            user=notification.recipient,
            title=notification.subject or "MyChama update",
            body=notification.message,
            data={
                "notification_id": str(notification.id),
                "chama_id": str(notification.chama_id),
                "type": notification.type,
                "action_url": notification.action_url,
                "deep_link": str((notification.metadata or {}).get("deep_link") or ""),
            },
            image_url=str((notification.metadata or {}).get("image_url") or "") or None,
            badge=NotificationService._push_badge_count(notification.recipient),
        )
        if not result.success:
            NotificationLog.objects.create(
                notification=notification,
                channel=NotificationChannel.PUSH,
                status=NotificationStatus.FAILED,
                error_message=result.error or "Push delivery failed",
            )
            NotificationService._mark_delivery_failed(
                delivery=delivery_record,
                provider="push_provider",
                error_message=result.error or "Push delivery failed",
            )
            raise ValueError(result.error or "Push delivery failed")

        NotificationLog.objects.create(
            notification=notification,
            channel=NotificationChannel.PUSH,
            status=NotificationStatus.SENT,
            provider_response={
                "provider": "push_provider",
                "message_id": result.message_id or "",
                "badge": NotificationService._push_badge_count(notification.recipient),
            },
            external_message_id=result.message_id or "",
        )
        NotificationService._mark_delivery_sent(
            delivery=delivery_record,
            provider="push_provider",
            provider_message_id=result.message_id or "",
        )
        return result

    @staticmethod
    def _render_template_value(value: str, context: dict) -> str:
        rendered = value or ""
        for key, val in context.items():
            rendered = rendered.replace(f"{{{{ {key} }}}}", str(val))
            rendered = rendered.replace(f"{{{{{key}}}}}", str(val))
        return rendered

    @staticmethod
    def _resolve_event_recipients(
        *,
        chama: Chama,
        target: str,
        target_roles: list[str],
        target_user_ids: list[str],
        segment: str,
        payload: dict,
    ):
        memberships = Membership.objects.select_related("user").filter(
            chama=chama,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        )

        if target == NotificationTarget.CHAMA:
            return memberships
        if target == NotificationTarget.ROLE:
            return memberships.filter(role__in=target_roles)
        if target == NotificationTarget.USER:
            return memberships.filter(user_id__in=target_user_ids)
        if target == NotificationTarget.SEGMENT:
            return NotificationService._resolve_segment_memberships(
                memberships=memberships,
                segment=segment,
                payload=payload,
                target_user_ids=target_user_ids,
            )
        return memberships.none()

    @staticmethod
    def _resolve_segment_memberships(
        *,
        memberships,
        segment: str,
        payload: dict,
        target_user_ids: list[str],
    ):
        explicit_ids = target_user_ids or payload.get("target_user_ids") or payload.get("user_ids") or payload.get("member_ids") or []
        normalized_segment = str(segment or "").strip().lower()

        if explicit_ids:
            return memberships.filter(user_id__in=explicit_ids)

        if normalized_segment in {"leadership", "leaders"}:
            return memberships.filter(
                role__in=[
                    MembershipRole.CHAMA_ADMIN,
                    MembershipRole.TREASURER,
                    MembershipRole.SECRETARY,
                    MembershipRole.AUDITOR,
                ]
            )

        if normalized_segment in {"admins", "admins_and_treasurers"}:
            return memberships.filter(
                role__in=[
                    MembershipRole.CHAMA_ADMIN,
                    MembershipRole.TREASURER,
                ]
            )

        if normalized_segment in {"treasurers"}:
            return memberships.filter(role=MembershipRole.TREASURER)

        if normalized_segment in {"suspended_users", "suspended_members"}:
            return Membership.objects.select_related("user").filter(
                chama=memberships.first().chama if memberships.exists() else None,
                is_active=True,
                status__in=["suspended", "inactive"],
                exited_at__isnull=True,
            )

        if normalized_segment in {"custom_selected", "selected_users"}:
            return memberships.filter(
                user_id__in=payload.get("target_user_ids", []) or payload.get("user_ids", [])
            )

        if normalized_segment in {"overdue_contributors", "users_with_overdue_contributions"}:
            from apps.finance.models import (
                ContributionSchedule,
                ContributionScheduleStatus,
            )

            overdue_member_ids = ContributionSchedule.objects.filter(
                chama=memberships.first().chama if memberships.exists() else None,
                status=ContributionScheduleStatus.OVERDUE,
                is_active=True,
            ).values_list("member_id", flat=True)
            return memberships.filter(user_id__in=overdue_member_ids)

        if normalized_segment in {"upcoming_meeting_attendees", "upcoming_meeting_members"}:
            from apps.meetings.models import Meeting

            within_hours = int(payload.get("within_hours") or 48)
            now = timezone.now()
            has_meeting = Meeting.objects.filter(
                chama=memberships.first().chama if memberships.exists() else None,
                cancelled_at__isnull=True,
                date__gte=now,
                date__lte=now + timedelta(hours=within_hours),
            ).exists()
            return memberships if has_meeting else memberships.none()

        if normalized_segment in {"kyc_pending", "kyc_approved", "kyc_rejected"}:
            from apps.accounts.models import MemberKYC

            status_map = {
                "kyc_pending": "pending",
                "kyc_approved": "approved",
                "kyc_rejected": "rejected",
            }
            member_ids = MemberKYC.objects.filter(
                chama=memberships.first().chama if memberships.exists() else None,
                status=status_map[normalized_segment],
            ).values_list("user_id", flat=True)
            return memberships.filter(user_id__in=member_ids)

        if normalized_segment in {"invited_users"}:
            from apps.chama.models import Invite, InviteStatus

            invitee_ids = Invite.objects.filter(
                chama=memberships.first().chama if memberships.exists() else None,
                status=InviteStatus.PENDING,
                invitee_user__isnull=False,
            ).values_list("invitee_user_id", flat=True)
            return memberships.filter(user_id__in=invitee_ids)

        return memberships.none()

    @staticmethod
    def retry_notification(notification: Notification, *, actor=None):
        notification.status = NotificationStatus.PENDING
        notification.last_error = ""
        notification.next_retry_at = None
        notification.updated_by = actor or notification.updated_by or notification.created_by
        notification.save(
            update_fields=["status", "last_error", "next_retry_at", "updated_by", "updated_at"]
        )
        NotificationService.queue_notification(notification)
        create_audit_log(
            actor=actor,
            chama_id=notification.chama_id,
            action="notification_retry_requested",
            entity_type="Notification",
            entity_id=notification.id,
            metadata={"delivery_channels": [delivery.channel for delivery in notification.deliveries.all()]},
        )
        return notification

    @staticmethod
    def _is_in_quiet_hours(*, now: datetime, start, end) -> bool:
        if not start or not end or start == end:
            return False

        current = now.timetz().replace(tzinfo=None)
        if start < end:
            return start <= current < end
        return current >= start or current < end

    @staticmethod
    def _next_allowed_time(*, now: datetime, quiet_end):
        candidate = now.replace(
            hour=quiet_end.hour,
            minute=quiet_end.minute,
            second=0,
            microsecond=0,
        )
        if candidate <= now:
            candidate = candidate + timedelta(days=1)
        return candidate

    @staticmethod
    def _already_sent_today(*, user_id, chama_id, event_type: str) -> bool:
        tracker = NotificationEventThrottle.objects.filter(
            user_id=user_id,
            chama_id=chama_id,
            event_type=event_type,
        ).first()
        if not tracker:
            return False
        return timezone.localtime(tracker.last_sent_at).date() == timezone.localdate()

    @staticmethod
    def _touch_event_throttle(notification: Notification):
        event_type = (
            notification.context_data.get("event_type")
            if isinstance(notification.context_data, dict)
            else None
        ) or notification.type

        NotificationEventThrottle.objects.update_or_create(
            user_id=notification.recipient_id,
            chama_id=notification.chama_id,
            event_type=str(event_type),
            defaults={
                "last_sent_at": timezone.now(),
                "created_by": notification.created_by,
                "updated_by": notification.updated_by,
            },
        )

    @staticmethod
    def _event_notification_idempotency_key(event_key: str, user_id) -> str:
        stable_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{event_key}:{user_id}",
        )
        return f"notification-event:{stable_id.hex}"


# Notification helper functions for easy triggering from other apps

def create_notification(
    recipient,
    chama,
    notification_type: str,
    title: str,
    message: str,
    priority: str = NotificationPriority.NORMAL,
    category: str = NotificationCategory.SYSTEM,
    action_url: str = "",
    metadata: dict = None,
    send_email: bool = False,
    send_sms: bool = False,
):
    """
    Create a notification for a user via the central event router.
    """
    channels = ["in_app"]
    if send_email:
        channels.append("email")
    if send_sms:
        channels.append("sms")

    try:
        return NotificationService.publish_event(
            chama=chama,
            event_key=f"direct-notification:{uuid.uuid4()}",
            event_type=notification_type,
            target=NotificationTarget.USER,
            target_user_ids=[str(recipient.id)],
            channels=channels,
            subject=title,
            message=message,
            action_url=action_url,
            category=category,
            priority=priority,
            payload=metadata or {},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to publish direct notification: %s", exc)
        return None


def notify_loan_approved(loan):
    """Send notification when a loan is approved."""
    return create_notification(
        recipient=loan.member,
        chama=loan.chama,
        notification_type=NotificationType.LOAN_UPDATE,
        title="Loan Approved",
        message=f"Your loan application of KES {loan.principal:,.2f} has been approved.",
        priority=NotificationPriority.HIGH,
        category=NotificationCategory.LOANS,
        action_url=f"/member/loans/{loan.id}",
        metadata={
            "loan_id": str(loan.id),
            "amount": str(loan.principal),
            "status": "approved",
        },
    )


def notify_loan_rejected(loan, reason: str = ""):
    """Send notification when a loan is rejected."""
    amount = getattr(loan, "principal", None)
    if amount is None:
        amount = getattr(loan, "requested_amount", 0)
    target_id = getattr(loan, "id", "")
    msg = f"Your loan application of KES {amount:,.2f} was rejected."
    if reason:
        msg += f" Reason: {reason}"
    return create_notification(
        recipient=loan.member,
        chama=loan.chama,
        notification_type=NotificationType.LOAN_UPDATE,
        title="Loan Rejected",
        message=msg,
        priority=NotificationPriority.HIGH,
        category=NotificationCategory.LOANS,
        action_url=f"/member/loans/{target_id}",
        metadata={
            "loan_id": str(target_id),
            "amount": str(amount),
            "status": "rejected",
            "reason": reason,
        },
    )


def notify_loan_disbursed(loan):
    """Send notification when a loan is disbursed."""
    return create_notification(
        recipient=loan.member,
        chama=loan.chama,
        notification_type=NotificationType.LOAN_UPDATE,
        title="Loan Disbursed",
        message=f"Your loan of KES {loan.principal:,.2f} has been disbursed to your wallet.",
        priority=NotificationPriority.HIGH,
        category=NotificationCategory.LOANS,
        action_url=f"/member/loans/{loan.id}",
        metadata={
            "loan_id": str(loan.id),
            "amount": str(loan.principal),
            "status": "disbursed",
        },
    )


def notify_loan_repayment_received(loan, repayment):
    """Send notification when a loan repayment is received."""
    return create_notification(
        recipient=loan.member,
        chama=loan.chama,
        notification_type=NotificationType.PAYMENT_CONFIRMATION,
        title="Payment Received",
        message=f"We received KES {repayment.amount:,.2f} towards your loan.",
        priority=NotificationPriority.NORMAL,
        category=NotificationCategory.PAYMENTS,
        action_url=f"/member/loans/{loan.id}",
        metadata={
            "loan_id": str(loan.id),
            "repayment_id": str(repayment.id),
            "amount": str(repayment.amount),
        },
    )


def notify_loan_overdue(loan):
    return create_notification(
        recipient=loan.member,
        chama=loan.chama,
        notification_type=NotificationType.LOAN_UPDATE,
        title="Loan Repayment Overdue",
        message=(
            f"Your loan has overdue installments. Outstanding due is "
            f"KES {loan.total_due:,.2f}. Please repay to avoid escalation."
        ),
        priority=NotificationPriority.HIGH,
        category=NotificationCategory.LOANS,
        action_url=f"/member/loans/{loan.id}",
        metadata={"loan_id": str(loan.id), "status": "overdue", "total_due": str(loan.total_due)},
        send_email=True,
        send_sms=True,
    )


def notify_loan_defaulted(loan):
    return create_notification(
        recipient=loan.member,
        chama=loan.chama,
        notification_type=NotificationType.LOAN_UPDATE,
        title="Loan Defaulted",
        message=(
            f"Your loan has moved to default status. Outstanding due is "
            f"KES {loan.total_due:,.2f}. Recovery actions may begin."
        ),
        priority=NotificationPriority.HIGH,
        category=NotificationCategory.LOANS,
        action_url=f"/member/loans/{loan.id}",
        metadata={"loan_id": str(loan.id), "status": "defaulted", "total_due": str(loan.total_due)},
        send_email=True,
        send_sms=True,
    )


def notify_loan_restructure_reviewed(loan, approved: bool):
    title = "Loan Restructure Approved" if approved else "Loan Restructure Rejected"
    message = (
        "Your loan restructure request was approved."
        if approved
        else "Your loan restructure request was rejected."
    )
    return create_notification(
        recipient=loan.member,
        chama=loan.chama,
        notification_type=NotificationType.LOAN_UPDATE,
        title=title,
        message=message,
        priority=NotificationPriority.HIGH,
        category=NotificationCategory.LOANS,
        action_url=f"/member/loans/{loan.id}",
        metadata={"loan_id": str(loan.id), "status": "restructured" if approved else "restructure_rejected"},
    )


def notify_loan_recovery_action(loan, action_type: str):
    return create_notification(
        recipient=loan.member,
        chama=loan.chama,
        notification_type=NotificationType.LOAN_UPDATE,
        title="Loan Recovery Update",
        message=f"A recovery action ({action_type}) has been recorded for your loan.",
        priority=NotificationPriority.HIGH,
        category=NotificationCategory.LOANS,
        action_url=f"/member/loans/{loan.id}",
        metadata={"loan_id": str(loan.id), "recovery_action": action_type},
    )


def notify_contribution_recorded(contribution):
    """Send notification when a contribution is recorded."""
    return create_notification(
        recipient=contribution.recorded_by,
        chama=contribution.chama,
        notification_type=NotificationType.PAYMENT_CONFIRMATION,
        title="Contribution Recorded",
        message=f"Your contribution of KES {contribution.amount:,.2f} has been recorded.",
        priority=NotificationPriority.NORMAL,
        category=NotificationCategory.PAYMENTS,
        action_url="/member/contributions",
        metadata={
            "contribution_id": str(contribution.id),
            "amount": str(contribution.amount),
        },
    )


def notify_withdrawal_requested(withdrawal):
    """Notify treasurer of a new withdrawal request."""
    return NotificationService.publish_event(
        chama=withdrawal.chama,
        event_key=f"withdrawal-requested:{withdrawal.id}",
        event_type=NotificationType.PAYMENT_CONFIRMATION,
        target=NotificationTarget.ROLE,
        target_roles=[MembershipRole.TREASURER, MembershipRole.CHAMA_ADMIN],
        channels=["in_app", "email"],
        subject="Withdrawal Request",
        message=f"A withdrawal of KES {withdrawal.amount:,.2f} has been requested.",
        priority=NotificationPriority.HIGH,
        category=NotificationCategory.PAYMENTS,
        action_url=f"/treasurer/withdrawals/{withdrawal.id}",
        payload={
            "withdrawal_id": str(withdrawal.id),
            "amount": str(withdrawal.amount),
            "requested_by": str(withdrawal.requested_by_id) if withdrawal.requested_by_id else None,
        },
    )


def notify_withdrawal_approved(withdrawal):
    """Send notification when a withdrawal is approved."""
    return create_notification(
        recipient=withdrawal.requested_by,
        chama=withdrawal.chama,
        notification_type=NotificationType.PAYMENT_CONFIRMATION,
        title="Withdrawal Approved",
        message=f"Your withdrawal of KES {withdrawal.amount:,.2f} has been approved.",
        priority=NotificationPriority.HIGH,
        category=NotificationCategory.PAYMENTS,
        action_url="/member/wallet",
        metadata={
            "withdrawal_id": str(withdrawal.id),
            "amount": str(withdrawal.amount),
            "status": "approved",
        },
    )


def notify_withdrawal_rejected(withdrawal, reason: str = ""):
    """Send notification when a withdrawal is rejected."""
    msg = f"Your withdrawal of KES {withdrawal.amount:,.2f} was rejected."
    if reason:
        msg += f" Reason: {reason}"
    return create_notification(
        recipient=withdrawal.requested_by,
        chama=withdrawal.chama,
        notification_type=NotificationType.PAYMENT_CONFIRMATION,
        title="Withdrawal Rejected",
        message=msg,
        priority=NotificationPriority.HIGH,
        category=NotificationCategory.PAYMENTS,
        action_url="/member/wallet",
        metadata={
            "withdrawal_id": str(withdrawal.id),
            "amount": str(withdrawal.amount),
            "status": "rejected",
            "reason": reason,
        },
    )


def notify_meeting_created(meeting):
    """Send notification when a new meeting is scheduled."""
    return NotificationService.publish_event(
        chama=meeting.chama,
        event_key=f"meeting-created:{meeting.id}",
        event_type=NotificationType.MEETING_NOTIFICATION,
        target=NotificationTarget.CHAMA,
        channels=["in_app", "email"],
        subject="New Meeting Scheduled",
        message=f"A new meeting '{meeting.title}' has been scheduled.",
        priority=NotificationPriority.NORMAL,
        category=NotificationCategory.MEETINGS,
        action_url=f"/member/meetings/{meeting.id}",
        payload={
            "meeting_id": str(meeting.id),
            "title": meeting.title,
        },
    )


def notify_membership_approved(membership):
    """Send notification when membership is approved."""
    return create_notification(
        recipient=membership.user,
        chama=membership.chama,
        notification_type=NotificationType.MEMBERSHIP_UPDATE,
        title="Membership Approved",
        message=f"Welcome to {membership.chama.name}! Your membership has been approved.",
        priority=NotificationPriority.HIGH,
        category=NotificationCategory.MEMBERSHIP,
        action_url="/member/dashboard",
        metadata={
            "chama_id": str(membership.chama_id),
            "role": membership.role,
        },
    )


def notify_membership_rejected(membership, reason: str = ""):
    """Send notification when membership is rejected."""
    msg = f"Your membership request for {membership.chama.name} was not approved."
    if reason:
        msg += f" Reason: {reason}"
    return create_notification(
        recipient=membership.user,
        chama=membership.chama,
        notification_type=NotificationType.MEMBERSHIP_UPDATE,
        title="Membership Update",
        message=msg,
        priority=NotificationPriority.NORMAL,
        category=NotificationCategory.MEMBERSHIP,
        action_url="/chamas",
        metadata={
            "chama_id": str(membership.chama_id),
            "status": "rejected",
            "reason": reason,
        },
    )
