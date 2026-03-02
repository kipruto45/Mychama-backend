from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import BaseModel


class LoginAttempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="security_login_attempts",
    )
    user_identifier = models.CharField(max_length=255, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    device_info = models.TextField(blank=True)
    success = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user_identifier", "created_at"]),
            models.Index(fields=["ip_address", "created_at"]),
            models.Index(fields=["success", "created_at"]),
        ]

    def __str__(self) -> str:
        status = "success" if self.success else "failed"
        return f"{self.user_identifier}:{status}:{self.created_at:%Y-%m-%d %H:%M:%S}"


class AccountLock(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="security_account_locks",
    )
    user_identifier = models.CharField(max_length=255, db_index=True)
    locked_until = models.DateTimeField(db_index=True)
    reason = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user_identifier", "locked_until"]),
            models.Index(fields=["user", "locked_until"]),
        ]

    def __str__(self) -> str:
        return f"{self.user_identifier} locked until {self.locked_until:%Y-%m-%d %H:%M:%S}"

    @property
    def is_active(self) -> bool:
        return self.locked_until > timezone.now()


class DeviceSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="device_sessions",
    )
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="device_sessions",
    )
    device_name = models.CharField(max_length=255, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    session_key = models.CharField(max_length=128, blank=True, db_index=True)
    last_seen = models.DateTimeField(default=timezone.now, db_index=True)
    is_revoked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-last_seen"]
        indexes = [
            models.Index(fields=["user", "is_revoked", "last_seen"]),
            models.Index(fields=["chama", "is_revoked", "last_seen"]),
            models.Index(fields=["session_key", "is_revoked"]),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.device_name or 'device'}:{'revoked' if self.is_revoked else 'active'}"


class AuditLog(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="security_audit_logs",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="security_audit_logs",
    )
    action_type = models.CharField(max_length=100, db_index=True)
    target_type = models.CharField(max_length=100)
    target_id = models.CharField(max_length=100, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["chama", "action_type", "created_at"]),
            models.Index(fields=["actor", "created_at"]),
            models.Index(fields=["target_type", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.action_type} -> {self.target_type}:{self.target_id}"


class UserSession(BaseModel):
    """User session tracking across all chama activities - user-level (not chama-specific)"""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="user_sessions",
    )
    session_key = models.CharField(max_length=128, unique=True, db_index=True)
    device_name = models.CharField(max_length=255, blank=True)
    device_id = models.CharField(max_length=255, blank=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    last_activity = models.DateTimeField(default=timezone.now, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    chama_context = models.ForeignKey(
        "chama.Chama",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="active_user_sessions",
    )

    class Meta:
        ordering = ["-last_activity"]
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["user", "last_activity"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self) -> str:
        status = "active" if self.is_active else "inactive"
        return f"{self.user_id}:session:{status}"

    @property
    def is_expired(self) -> bool:
        return self.expires_at < timezone.now()

    def deactivate(self):
        """Safely deactivate this session"""
        self.is_active = False
        self.save(update_fields=["is_active", "updated_at"])


class SecurityAlert(BaseModel):
    """Security alerts - user-specific or system-wide"""

    class AlertLevel(models.TextChoices):
        INFO = "INFO", "Info"
        WARNING = "WARNING", "Warning"
        CRITICAL = "CRITICAL", "Critical"

    class AlertType(models.TextChoices):
        SUSPICIOUS_LOGIN = "SUSPICIOUS_LOGIN", "Suspicious Login"
        MULTIPLE_FAILED_LOGINS = "MULTIPLE_FAILED_LOGINS", "Multiple Failed Logins"
        UNUSUAL_LOCATION = "UNUSUAL_LOCATION", "Unusual Location"
        DEVICE_COMPROMISED = "DEVICE_COMPROMISED", "Device Compromised"
        RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED", "Rate Limit Exceeded"
        PAYMENT_ANOMALY = "PAYMENT_ANOMALY", "Payment Anomaly"
        PERMISSION_DENIED = "PERMISSION_DENIED", "Permission Denied"
        TOKEN_EXPIRED = "TOKEN_EXPIRED", "Token Expired"
        API_ABUSE = "API_ABUSE", "API Abuse"
        OTHER = "OTHER", "Other"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="security_alerts",
    )
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="security_alerts",
    )
    alert_type = models.CharField(max_length=50, choices=AlertType.choices, db_index=True)
    level = models.CharField(max_length=20, choices=AlertLevel.choices, db_index=True)
    title = models.CharField(max_length=255)
    message = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    is_resolved = models.BooleanField(default=False, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_security_alerts",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "is_resolved"]),
            models.Index(fields=["chama", "is_resolved"]),
            models.Index(fields=["alert_type", "created_at"]),
            models.Index(fields=["level", "is_resolved"]),
        ]

    def __str__(self) -> str:
        return f"[{self.level}] {self.alert_type}: {self.title}"

    def resolve(self, resolved_by_user):
        """Mark alert as resolved"""
        self.is_resolved = True
        self.resolved_by = resolved_by_user
        self.resolved_at = timezone.now()
        self.save(update_fields=["is_resolved", "resolved_by", "resolved_at", "updated_at"])
