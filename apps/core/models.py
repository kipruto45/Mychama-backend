"""
Core models for the MyChama application.
Base models and mixins used across all modules.
"""

import uuid

from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    """Base model with created and updated timestamps."""
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UUIDModel(models.Model):
    """Base model with UUID primary key."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class SoftDeleteModel(models.Model):
    """Base model with soft delete functionality."""
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    def soft_delete(self):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_deleted', 'deleted_at'])

    def restore(self):
        self.is_deleted = False
        self.deleted_at = None
        self.save(update_fields=['is_deleted', 'deleted_at'])


class BaseModel(TimeStampedModel, UUIDModel, SoftDeleteModel):
    """Combined base model with all common fields."""
    
    class Meta:
        abstract = True


class AuditableModel(BaseModel):
    """Model with audit trail fields."""
    created_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='%(class)s_created'
    )
    updated_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='%(class)s_updated'
    )

    class Meta:
        abstract = True


class StatusModel(models.Model):
    """Mixin for models with status field."""
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('pending', 'Pending'),
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('suspended', 'Suspended'),
        ('archived', 'Archived'),
    ]
    
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='active',
        db_index=True
    )

    class Meta:
        abstract = True


class MetadataModel(models.Model):
    """Mixin for models with metadata JSON field."""
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        abstract = True
