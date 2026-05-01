"""
Security models.

This module combines the persisted security schema used by the app's existing
migrations with newer device-tracking models that other services import.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class ActorStampedModel(models.Model):
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
            models.Index(
                fields=["user_identifier", "created_at"],
                name="security_lo_user_id_c0e698_idx",
            ),
            models.Index(
                fields=["ip_address", "created_at"],
                name="security_lo_ip_addr_7e4e10_idx",
            ),
            models.Index(
                fields=["success", "created_at"],
                name="security_lo_success_77e52e_idx",
            ),
        ]

    def __str__(self):
        status = "success" if self.success else "failed"
        return f"{status} login for {self.user_identifier} at {self.created_at}"


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
            models.Index(
                fields=["user_identifier", "locked_until"],
                name="security_ac_user_id_608238_idx",
            ),
            models.Index(
                fields=["user", "locked_until"],
                name="security_ac_user_id_085aac_idx",
            ),
        ]

    def __str__(self):
        return f"Lock for {self.user_identifier} until {self.locked_until}"

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
            models.Index(
                fields=["user", "is_revoked", "last_seen"],
                name="security_de_user_id_2be977_idx",
            ),
            models.Index(
                fields=["chama", "is_revoked", "last_seen"],
                name="security_de_chama_i_f871f7_idx",
            ),
            models.Index(
                fields=["session_key", "is_revoked"],
                name="security_de_session_769d8a_idx",
            ),
        ]

    def __str__(self):
        return f"{self.user} - {self.device_name or 'Unknown device'}"


class AuditLog(ActorStampedModel):
    GENESIS_HASH = "0" * 64

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
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
    trace_id = models.CharField(max_length=64, blank=True, db_index=True)
    chain_index = models.PositiveBigIntegerField(unique=True, db_index=True)
    prev_hash = models.CharField(max_length=64, default=GENESIS_HASH)
    event_hash = models.CharField(max_length=64, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["chama", "action_type", "created_at"],
                name="security_au_chama_i_c4be93_idx",
            ),
            models.Index(
                fields=["actor", "created_at"],
                name="security_au_actor_i_a5fe48_idx",
            ),
            models.Index(
                fields=["target_type", "created_at"],
                name="security_au_target__79223b_idx",
            ),
            models.Index(
                fields=["chain_index", "created_at"],
                name="security_au_chain_i_58b6d6_idx",
            ),
        ]

    def __str__(self):
        return f"{self.action_type} on {self.target_type}"

    def save(self, *args, **kwargs):
        if not self._state.adding and not getattr(self, "_allow_mutation", False):
            raise ValidationError("Security audit events are append-only and cannot be modified.")
        return super().save(*args, **kwargs)

    def delete(self, using=None, keep_parents=False):
        raise ValidationError("Security audit events are append-only and cannot be deleted.")


class AuditChainCheckpoint(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    checkpoint_date = models.DateField(unique=True, db_index=True)
    last_chain_index = models.PositiveBigIntegerField(default=0)
    last_event_hash = models.CharField(max_length=64, default=AuditLog.GENESIS_HASH)
    record_count = models.PositiveBigIntegerField(default=0)
    signature = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-checkpoint_date", "-created_at"]

    def __str__(self):
        return f"Audit checkpoint {self.checkpoint_date.isoformat()}"


class SecurityAlert(ActorStampedModel):
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

    class Level(models.TextChoices):
        INFO = "INFO", "Info"
        WARNING = "WARNING", "Warning"
        CRITICAL = "CRITICAL", "Critical"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
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
    alert_type = models.CharField(
        max_length=50,
        choices=AlertType.choices,
        db_index=True,
    )
    level = models.CharField(
        max_length=20,
        choices=Level.choices,
        db_index=True,
    )
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
            models.Index(
                fields=["user", "is_resolved"],
                name="security_se_user_id_0a4fd1_idx",
            ),
            models.Index(
                fields=["chama", "is_resolved"],
                name="security_se_chama_i_0b0183_idx",
            ),
            models.Index(
                fields=["alert_type", "created_at"],
                name="security_se_alert_t_df8e26_idx",
            ),
            models.Index(
                fields=["level", "is_resolved"],
                name="security_se_level_d6d194_idx",
            ),
        ]

    def __str__(self):
        return f"{self.level} {self.alert_type}: {self.title}"

    def resolve(self, actor=None):
        self.is_resolved = True
        self.resolved_at = timezone.now()
        self.resolved_by = actor
        self.save(update_fields=["is_resolved", "resolved_at", "resolved_by", "updated_at"])
        return self


class UserSession(ActorStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
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
            models.Index(
                fields=["user", "is_active"],
                name="security_us_user_id_9aa362_idx",
            ),
            models.Index(
                fields=["user", "last_activity"],
                name="security_us_user_id_eb55e9_idx",
            ),
            models.Index(
                fields=["expires_at"],
                name="security_us_expires_f5812f_idx",
            ),
        ]

    def __str__(self):
        return f"{self.user} session {self.session_key}"

    def revoke(self):
        self.is_active = False
        self.last_activity = timezone.now()
        self.save(update_fields=["is_active", "last_activity", "updated_at"])
        return self


class Permission(ActorStampedModel):
    class Scope(models.TextChoices):
        GLOBAL = "global", "Global"
        CHAMA = "chama", "Chama"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=64, unique=True, db_index=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    scope = models.CharField(
        max_length=16,
        choices=Scope.choices,
        default=Scope.CHAMA,
    )
    is_system = models.BooleanField(default=True, db_index=True)
    is_sensitive = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["scope", "is_system"], name="security_pe_scope_9c8189_idx"),
            models.Index(fields=["is_sensitive"], name="security_pe_is_sen_b55565_idx"),
        ]

    def __str__(self):
        return self.name


class Role(ActorStampedModel):
    class Scope(models.TextChoices):
        GLOBAL = "global", "Global"
        CHAMA = "chama", "Chama"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=64, unique=True, db_index=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    scope = models.CharField(
        max_length=16,
        choices=Scope.choices,
        default=Scope.CHAMA,
    )
    membership_role_key = models.CharField(max_length=32, blank=True, db_index=True)
    is_system = models.BooleanField(default=True, db_index=True)
    sort_order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ["sort_order", "name"]
        indexes = [
            models.Index(fields=["scope", "is_system"], name="security_ro_scope_e52822_idx"),
            models.Index(
                fields=["membership_role_key"],
                name="security_ro_members_914359_idx",
            ),
        ]

    def __str__(self):
        return self.name


class RolePermission(ActorStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.ForeignKey(
        Role,
        on_delete=models.CASCADE,
        related_name="role_permissions",
    )
    permission = models.ForeignKey(
        Permission,
        on_delete=models.CASCADE,
        related_name="permission_roles",
    )

    class Meta:
        ordering = ["role__sort_order", "permission__name"]
        constraints = [
            models.UniqueConstraint(
                fields=("role", "permission"),
                name="uniq_role_permission_assignment",
            ),
        ]
        indexes = [
            models.Index(
                fields=["role", "permission"],
                name="security_ro_role_id_1f0a5b_idx",
            ),
            models.Index(
                fields=["permission", "role"],
                name="security_ro_permis_0d777d_idx",
            ),
        ]

    def __str__(self):
        return f"{self.role} -> {self.permission}"


class TrustedDevice(models.Model):
    """
    Tracks trusted devices for users.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="trusted_devices",
    )
    fingerprint = models.CharField(max_length=64, db_index=True)
    device_name = models.CharField(max_length=255, blank=True, default="")
    device_type = models.CharField(
        max_length=20,
        choices=[
            ("mobile", "Mobile"),
            ("tablet", "Tablet"),
            ("desktop", "Desktop"),
            ("unknown", "Unknown"),
        ],
        default="unknown",
    )
    user_agent = models.TextField(blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    is_trusted = models.BooleanField(default=False)
    trusted_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_used_at"]
        indexes = [
            models.Index(fields=["user", "fingerprint"]),
            models.Index(fields=["user", "is_trusted"]),
            models.Index(fields=["expires_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "fingerprint"],
                name="unique_user_device_fingerprint",
            ),
        ]

    def __str__(self):
        status = "trusted" if self.is_trusted else "untrusted"
        return f"{self.device_name} ({status}) - {self.user}"

    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return True
        return timezone.now() >= self.expires_at

    @property
    def is_active_trusted(self) -> bool:
        return self.is_trusted and not self.is_expired


class DeviceLoginAttempt(models.Model):
    """
    Records login attempts for device-based security analysis.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="device_login_attempts",
    )
    fingerprint = models.CharField(max_length=64, db_index=True)
    ip_address = models.GenericIPAddressField()
    user_agent = models.TextField(blank=True, default="")
    success = models.BooleanField(default=False)
    failure_reason = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "success", "created_at"]),
            models.Index(fields=["ip_address", "created_at"]),
            models.Index(fields=["fingerprint", "created_at"]),
        ]

    def __str__(self):
        status = "success" if self.success else "failed"
        return f"{status} device login at {self.created_at}"


class SecurityEvent(models.Model):
    """
    Records general security events for audit and monitoring.
    """

    EVENT_TYPES = [
        ("login_success", "Login Success"),
        ("login_failed", "Login Failed"),
        ("account_locked", "Account Locked"),
        ("password_changed", "Password Changed"),
        ("password_reset", "Password Reset"),
        ("otp_sent", "OTP Sent"),
        ("otp_verified", "OTP Verified"),
        ("otp_failed", "OTP Failed"),
        ("device_trusted", "Device Trusted"),
        ("device_revoked", "Device Revoked"),
        ("suspicious_activity", "Suspicious Activity"),
        ("session_expired", "Session Expired"),
        ("token_refreshed", "Token Refreshed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="security_events",
    )
    event_type = models.CharField(max_length=50, choices=EVENT_TYPES, db_index=True)
    description = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "event_type", "created_at"]),
            models.Index(fields=["event_type", "created_at"]),
        ]

    def __str__(self):
        return f"{self.event_type} - {self.user} at {self.created_at}"


class AccountLockout(models.Model):
    """
    Tracks account lockouts for the newer device-tracking flow.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="account_lockout",
    )
    locked_at = models.DateTimeField(auto_now_add=True)
    locked_until = models.DateTimeField()
    reason = models.CharField(max_length=255, blank=True, default="")
    failed_attempts = models.PositiveIntegerField(default=0)
    last_failed_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-locked_at"]
        indexes = [
            models.Index(fields=["locked_until"]),
        ]

    def __str__(self):
        return f"Lockout for {self.user} until {self.locked_until}"

    @property
    def is_locked(self) -> bool:
        return timezone.now() < self.locked_until

    def unlock(self):
        self.locked_until = timezone.now()
        self.save(update_fields=["locked_until"])


class RefreshTokenRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="refresh_token_records",
    )
    family_id = models.UUIDField(db_index=True)
    jti = models.CharField(max_length=255, unique=True, db_index=True)
    parent_jti = models.CharField(max_length=255, blank=True, default="")
    device_name = models.CharField(max_length=255, blank=True, default="")
    device_id = models.CharField(max_length=255, blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")
    issued_at = models.DateTimeField(auto_now_add=True, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True, db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    revoked_reason = models.CharField(max_length=64, blank=True, default="")
    reuse_detected_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-issued_at"]
        indexes = [
            models.Index(fields=["user", "family_id"], name="security_re_user_id_432c1f_idx"),
            models.Index(fields=["family_id", "revoked_at"], name="security_re_family__db13d1_idx"),
            models.Index(fields=["user", "revoked_at"], name="security_re_user_id_0d2f73_idx"),
            models.Index(fields=["expires_at"], name="security_re_expires_348bb8_idx"),
        ]

    def __str__(self):
        return f"{self.user_id}:{self.jti}"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and self.expires_at > timezone.now()


class MemberPinSecret(models.Model):
    """
    Stores hashed PINs for transaction and withdrawal authentication.
    Separate secrets for transaction PIN and withdrawal PIN.
    """

    class PinType(models.TextChoices):
        TRANSACTION = "transaction", "Transaction PIN"
        WITHDRAWAL = "withdrawal", "Withdrawal PIN"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pin_secrets",
    )
    pin_type = models.CharField(
        max_length=16,
        choices=PinType.choices,
        db_index=True,
    )
    pin_hash = models.CharField(max_length=255, blank=True, default="")
    salt = models.CharField(max_length=32, blank=True, default="")
    failed_attempts = models.PositiveIntegerField(default=0)
    lockout_level = models.PositiveSmallIntegerField(default=0)
    is_locked = models.BooleanField(default=False)
    locked_until = models.DateTimeField(null=True, blank=True, db_index=True)
    rotated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pin_updates",
    )

    class Meta:
        unique_together = ["user", "pin_type"]
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["user", "pin_type"],
                name="security_me_user_id_90fa6a_idx",
            ),
            models.Index(
                fields=["locked_until"],
                name="security_me_locked__c16470_idx",
            ),
            models.Index(
                fields=["is_locked"],
                name="security_me_is_lock_17bb35_idx",
            ),
        ]

    def __str__(self):
        return f"{self.user} - {self.pin_type} PIN"

    @property
    def is_locked_out(self) -> bool:
        if not self.is_locked:
            return False
        if self.locked_until and timezone.now() >= self.locked_until:
            return False
        return True


class FraudCase(ActorStampedModel):
    """Fraud case for investigation."""

    class CaseType(models.TextChoices):
        TRANSACTION = "transaction", "Transaction Fraud"
        ACCOUNT = "account", "Account Takeover"
        IDENTITY = "identity", "Identity Fraud"
        LOAN = "loan", "Loan Fraud"
        AML = "aml", "AML Alert"

    class CaseStatus(models.TextChoices):
        OPEN = "open", "Open"
        INVESTIGATING = "investigating", "Investigating"
        ESCALATED = "escalated", "Escalated"
        RESOLVED_TRUE = "resolved_true_positive", "True Positive"
        RESOLVED_FALSE = "resolved_false_positive", "False Positive"
        FROZEN = "frozen", "Account Frozen"
        RELEASED = "released", "Released"

    class Severity(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case_id = models.CharField(max_length=32, unique=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="fraud_cases",
    )
    case_type = models.CharField(max_length=32, choices=CaseType.choices)
    severity = models.CharField(max_length=16, choices=Severity.choices)
    status = models.CharField(max_length=24, choices=CaseStatus.choices, default=CaseStatus.OPEN)
    fraud_score = models.PositiveSmallIntegerField(default=0)
    triggered_by = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    linked_events = models.JSONField(default=list, blank=True)
    resolution_summary = models.TextField(blank=True)
    frozen_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    sla_deadline = models.DateTimeField(null=True, blank=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_fraud_cases",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_fraud_cases",
    )
    review_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-severity", "-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["status", "sla_deadline"]),
            models.Index(fields=["assigned_to", "status"]),
            models.Index(fields=["severity", "status"]),
        ]

    def __str__(self):
        return f"FraudCase {self.case_id}: {self.case_type} ({self.status})"

    @classmethod
    def generate_case_id(cls) -> str:
        today = timezone.now().strftime("%Y%m%d")
        count = cls.objects.filter(created_at__date=timezone.now().date()).count() + 1
        return f"FC{today}{count:05d}"
