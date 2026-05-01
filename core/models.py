import uuid

from django.conf import settings
from django.db import models


class BaseModel(models.Model):
    """Abstract base model with UUID primary key, timestamps, and audit fields."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="%(app_label)s_%(class)s_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="%(app_label)s_%(class)s_updated",
    )

    class Meta:
        abstract = True


class AuditModel(BaseModel):
    """
    Backward-compatible alias for existing app models.

    New models should inherit directly from BaseModel.
    """

    class Meta:
        abstract = True


class SoftDeleteModel(BaseModel):
    """Abstract model with soft delete"""

    is_deleted = models.BooleanField(default=False)

    class Meta:
        abstract = True


class AuditLog(models.Model):
    """Structured audit trail for sensitive state transitions."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    chama_id = models.UUIDField(null=True, blank=True, db_index=True)
    action = models.CharField(max_length=100, db_index=True)
    entity_type = models.CharField(max_length=100)
    entity_id = models.UUIDField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    trace_id = models.CharField(max_length=64, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["action", "created_at"]),
            models.Index(fields=["entity_type", "created_at"]),
        ]

    def __str__(self):
        return (
            f"{self.action} ({self.entity_type}) @ {self.created_at:%Y-%m-%d %H:%M:%S}"
        )


class ActivityLog(models.Model):
    """Structured user activity timeline for non-admin actions."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activity_logs",
    )
    chama_id = models.UUIDField(null=True, blank=True, db_index=True)
    action = models.CharField(max_length=100, db_index=True)
    entity_type = models.CharField(max_length=100)
    entity_id = models.UUIDField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    trace_id = models.CharField(max_length=64, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["chama_id", "action", "created_at"]),
            models.Index(fields=["actor", "created_at"]),
            models.Index(fields=["entity_type", "created_at"]),
        ]

    def __str__(self):
        return (
            f"{self.action} ({self.entity_type}) @ {self.created_at:%Y-%m-%d %H:%M:%S}"
        )
