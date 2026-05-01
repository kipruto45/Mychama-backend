from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models import BaseModel


class JobRunStatus(models.TextChoices):
    SUCCESS = "SUCCESS", "Success"
    PARTIAL = "PARTIAL", "Partial"
    FAILED = "FAILED", "Failed"


class ScheduledJob(BaseModel):
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    is_enabled = models.BooleanField(default=True)
    schedule = models.CharField(max_length=120, help_text="Cron or textual schedule")

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["name", "is_enabled"])]

    def __str__(self) -> str:
        return self.name


class JobRun(BaseModel):
    job = models.ForeignKey(
        ScheduledJob,
        on_delete=models.CASCADE,
        related_name="runs",
    )
    started_at = models.DateTimeField(db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=JobRunStatus.choices)
    meta = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["job", "started_at"]),
            models.Index(fields=["status", "started_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.job.name} ({self.status})"


class NotificationDeliveryStatus(models.TextChoices):
    SENT = "SENT", "Sent"
    SKIPPED = "SKIPPED", "Skipped"
    FAILED = "FAILED", "Failed"


class NotificationLog(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="automation_notification_logs",
    )
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="automation_notification_logs",
    )
    channel = models.CharField(max_length=20)
    message = models.TextField()
    status = models.CharField(max_length=20, choices=NotificationDeliveryStatus.choices)
    provider_response = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["chama", "channel", "created_at"]),
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.channel}:{self.status}:{self.user_id}"


class AutomationRule(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="automation_rules",
    )
    rule_type = models.CharField(max_length=120)
    config = models.JSONField(default=dict, blank=True)
    is_enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["chama", "rule_type"]
        constraints = [
            models.UniqueConstraint(
                fields=["chama", "rule_type"],
                name="uniq_automation_rule_per_chama",
            )
        ]

    def __str__(self) -> str:
        return f"{self.chama_id}:{self.rule_type}"
