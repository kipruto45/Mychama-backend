from __future__ import annotations

import logging
from collections.abc import Callable

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.automations.models import JobRun, JobRunStatus, NotificationLog, ScheduledJob
from apps.notifications.services import NotificationService
from core.audit import create_audit_log

logger = logging.getLogger(__name__)


class AutomationService:
    @staticmethod
    def get_quiet_hours() -> tuple[int, int]:
        start = int(getattr(settings, "AUTOMATION_QUIET_HOURS_START", 21))
        end = int(getattr(settings, "AUTOMATION_QUIET_HOURS_END", 7))
        return start, end

    @staticmethod
    def is_quiet_hours(now=None) -> bool:
        now = now or timezone.localtime(timezone.now())
        start, end = AutomationService.get_quiet_hours()
        hour = now.hour
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

    @staticmethod
    def _notification_throttle_key(*, user_id, chama_id, channel):
        date_key = timezone.localdate().isoformat()
        return f"automation:notif:{chama_id}:{user_id}:{channel}:{date_key}"

    @staticmethod
    def should_send_notification(*, user_id, chama_id, channel):
        if channel.lower() == "sms" and AutomationService.is_quiet_hours():
            return False, "quiet_hours"

        limit = int(getattr(settings, "AUTOMATION_NOTIFICATION_DAILY_LIMIT", 20))
        key = AutomationService._notification_throttle_key(
            user_id=user_id,
            chama_id=chama_id,
            channel=channel,
        )
        current = int(cache.get(key, 0))
        if current >= limit:
            return False, "throttled"

        cache.set(key, current + 1, timeout=86400)
        return True, "allowed"

    @staticmethod
    def send_notification_with_policy(*, user, chama, message: str, channels: list[str], **kwargs):
        sent = 0
        logs = []
        base_idempotency_key = kwargs.get("idempotency_key")
        for channel in channels:
            allowed, reason = AutomationService.should_send_notification(
                user_id=user.id,
                chama_id=chama.id,
                channel=channel,
            )
            if not allowed:
                log = NotificationLog.objects.create(
                    user=user,
                    chama=chama,
                    channel=channel,
                    message=message,
                    status="SKIPPED",
                    provider_response={"reason": reason},
                    created_by=kwargs.get("actor"),
                    updated_by=kwargs.get("actor"),
                )
                logs.append(log)
                continue

            notification = NotificationService.send_notification(
                user=user,
                chama=chama,
                message=message,
                channels=[channel],
                subject=kwargs.get("subject", ""),
                notification_type=kwargs.get("notification_type", "system"),
                category=kwargs.get("category"),
                priority=kwargs.get("priority"),
                html_message=kwargs.get("html_message", ""),
                action_url=kwargs.get("action_url", ""),
                metadata=kwargs.get("metadata"),
                scheduled_at=kwargs.get("scheduled_at"),
                context_data=kwargs.get("context_data"),
                idempotency_key=(
                    f"{base_idempotency_key}:{channel}"
                    if base_idempotency_key
                    else None
                ),
                enforce_once_daily=kwargs.get("enforce_once_daily", False),
                actor=kwargs.get("actor"),
            )
            sent += 1
            log = NotificationLog.objects.create(
                user=user,
                chama=chama,
                channel=channel,
                message=message,
                status="SENT",
                provider_response={"notification_id": str(notification.id)},
                created_by=kwargs.get("actor"),
                updated_by=kwargs.get("actor"),
            )
            logs.append(log)

        return {"sent": sent, "logs": [str(item.id) for item in logs]}


class AutomationJobRunner:
    @staticmethod
    def run_job(
        *,
        name: str,
        schedule: str,
        callback: Callable[[], dict],
        description: str = "",
        actor=None,
    ):
        job, _ = ScheduledJob.objects.get_or_create(
            name=name,
            defaults={
                "description": description,
                "schedule": schedule,
                "is_enabled": True,
                "created_by": actor,
                "updated_by": actor,
            },
        )

        if not job.is_enabled:
            return {"status": "disabled", "job": job.name}

        lock_key = f"automation:lock:{job.name}"
        lock_seconds = int(getattr(settings, "AUTOMATION_JOB_LOCK_SECONDS", 600))
        acquired = cache.add(lock_key, "1", timeout=lock_seconds)
        if not acquired:
            return {"status": "locked", "job": job.name}

        run = JobRun.objects.create(
            job=job,
            started_at=timezone.now(),
            status=JobRunStatus.PARTIAL,
            created_by=actor,
            updated_by=actor,
        )

        try:
            result = callback() or {}
            run.status = JobRunStatus.SUCCESS
            run.meta = result
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "meta", "finished_at", "updated_at"])

            create_audit_log(
                actor=actor,
                chama_id=None,
                action="automation_job_success",
                entity_type="ScheduledJob",
                entity_id=job.id,
                metadata={"job": job.name, "run_id": str(run.id)},
            )
            return {"status": "success", "run_id": str(run.id), "result": result}
        except Exception as exc:  # noqa: BLE001
            logger.exception("Automation job failed: %s", name)
            run.status = JobRunStatus.FAILED
            run.error = str(exc)
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "error", "finished_at", "updated_at"])
            create_audit_log(
                actor=actor,
                chama_id=None,
                action="automation_job_failed",
                entity_type="ScheduledJob",
                entity_id=job.id,
                metadata={"job": job.name, "run_id": str(run.id), "error": str(exc)},
            )
            return {"status": "failed", "run_id": str(run.id), "error": str(exc)}
        finally:
            cache.delete(lock_key)
