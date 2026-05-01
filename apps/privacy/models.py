"""
Privacy and Data Protection Models

Implements Kenya Data Protection Act 2019 compliance:
- Consent management
- Data subject rights requests
- Retention policies
- PII access logging
"""

import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class ConsentCategory(models.TextChoices):
    MARKETING = "marketing", "Marketing Communications"
    ANALYTICS = "analytics", "Analytics & Improvements"
    THIRD_PARTY_SHARING = "third_party_sharing", "Third-Party Data Sharing"
    CREDIT_CHECK = "credit_check", "Credit Reference Checks"
    PROFILING = "profiling", "Automated Decision Profiling"


class ConsentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    GRANTED = "granted", "Granted"
    DENIED = "denied", "Denied"
    WITHDRAWN = "withdrawn", "Withdrawn"


class DataSubjectRequestType(models.TextChoices):
    ACCESS = "access", "Right to Access"
    RECTIFICATION = "rectification", "Right to Correction"
    ERASURE = "erasure", "Right to Deletion"
    PORTABILITY = "portability", "Right to Data Portability"
    OBJECTION = "objection", "Right to Object"


class DataSubjectRequestStatus(models.TextChoices):
    PENDING = "pending", "Pending Review"
    IN_PROGRESS = "in_progress", "In Progress"
    COMPLETED = "completed", "Completed"
    REJECTED = "rejected", "Rejected"
    EXPIRED = "expired", "Expired"


class RetentionPolicy(models.TextChoices):
    FINANCIAL = "financial", "Financial Records (7 years)"
    KYC = "kyc", "KYC Documents (5 years)"
    SECURITY = "security", "Security Logs (1 year)"
    MARKETING = "marketing", "Marketing Data (until withdrawn)"
    TAX = "tax", "Tax Records (7 years)"
    CONSENT = "consent", "Consent Records (2 years post-withdrawal)"


class PIIAccessType(models.TextChoices):
    READ = "read", "Data Read"
    EXPORT = "export", "Data Export"
    MODIFY = "modify", "Data Modification"
    DELETE = "delete", "Data Deletion"
    SUPPORT = "support", "Support Access"


class UserConsent(models.Model):
    """
    Tracks user consent for different processing activities.
    Aligned to Kenya DPA consent requirements.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="consents",
    )
    category = models.CharField(
        max_length=32,
        choices=ConsentCategory.choices,
        db_index=True,
    )
    status = models.CharField(
        max_length=16,
        choices=ConsentStatus.choices,
        default=ConsentStatus.PENDING,
        db_index=True,
    )
    granted_at = models.DateTimeField(null=True, blank=True)
    denied_at = models.DateTimeField(null=True, blank=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    policy_version = models.CharField(max_length=16)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    reason = models.TextField(blank=True, help_text="Reason for denial/withdrawal")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["user", "category"]
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["category", "status"]),
            models.Index(fields=["granted_at"]),
        ]

    def __str__(self):
        return f"{self.user} - {self.category}: {self.status}"

    def grant(self, ip_address: str = None, user_agent: str = ""):
        """Grant consent."""
        self.status = ConsentStatus.GRANTED
        self.granted_at = timezone.now()
        self.denied_at = None
        self.withdrawn_at = None
        self.ip_address = ip_address
        self.user_agent = user_agent
        self.save(update_fields=["status", "granted_at", "denied_at", "withdrawn_at", "ip_address", "user_agent", "updated_at"])

    def deny(self, reason: str = "", ip_address: str = None):
        """Deny consent."""
        self.status = ConsentStatus.DENIED
        self.denied_at = timezone.now()
        self.reason = reason
        self.ip_address = ip_address
        self.save(update_fields=["status", "denied_at", "reason", "ip_address", "updated_at"])

    def withdraw(self, reason: str = "", ip_address: str = None):
        """Withdraw consent."""
        self.status = ConsentStatus.WITHDRAWN
        self.withdrawn_at = timezone.now()
        self.reason = reason
        self.ip_address = ip_address
        self.save(update_fields=["status", "withdrawn_at", "reason", "ip_address", "updated_at"])


class DataSubjectRequest(models.Model):
    """
    Handles data subject rights requests under Kenya DPA.
    Includes access, correction, deletion, and portability requests.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_type = models.CharField(
        max_length=32,
        choices=DataSubjectRequestType.choices,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=DataSubjectRequestStatus.choices,
        default=DataSubjectRequestStatus.PENDING,
        db_index=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="data_requests",
    )
    request_token = models.CharField(max_length=64, unique=True, db_index=True)
    description = models.TextField(blank=True)
    requested_data = models.JSONField(default=dict, blank=True)
    provided_data = models.JSONField(default=dict, blank=True)
    rejection_reason = models.TextField(blank=True)
    financial_exception = models.BooleanField(
        default=False,
        help_text="Whether deletion request has financial retention exception",
    )
    kyc_exception = models.BooleanField(
        default=False,
        help_text="Whether deletion request has KYC retention exception",
    )
    expires_at = models.DateTimeField(db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_requests",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["status", "expires_at"]),
            models.Index(fields=["request_type", "status"]),
        ]

    def __str__(self):
        return f"{self.request_type} - {self.user} ({self.status})"

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def start_processing(self):
        """Mark request as in progress."""
        self.status = DataSubjectRequestStatus.IN_PROGRESS
        self.started_at = timezone.now()
        self.save(update_fields=["status", "started_at", "updated_at"])

    def complete(self, provided_data: dict):
        """Complete the request with provided data."""
        self.status = DataSubjectRequestStatus.COMPLETED
        self.completed_at = timezone.now()
        self.provided_data = provided_data
        self.save(update_fields=["status", "completed_at", "provided_data", "updated_at"])

    def reject(self, reason: str, reviewer):
        """Reject the request."""
        self.status = DataSubjectRequestStatus.REJECTED
        self.rejection_reason = reason
        self.reviewed_by = reviewer
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "rejection_reason", "reviewed_by", "completed_at", "updated_at"])


class PIIAccessEvent(models.Model):
    """
    Logs every access to PII for audit purposes.
    Required for regulatory compliance and breach investigation.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pii_access_events",
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pii_access_by",
        null=True,
        blank=True,
    )
    access_type = models.CharField(
        max_length=16,
        choices=PIIAccessType.choices,
        db_index=True,
    )
    fields_accessed = models.JSONField(
        default=list,
        help_text="List of PII fields accessed",
    )
    purpose = models.CharField(max_length=255, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    session_key = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["target_user", "created_at"]),
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["access_type", "created_at"]),
        ]

    def __str__(self):
        return f"{self.access_type} on {self.target_user} at {self.created_at}"


class RetentionSchedule(models.Model):
    """
    Defines data retention schedules for different data types.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    data_category = models.CharField(
        max_length=32,
        choices=RetentionPolicy.choices,
        unique=True,
    )
    retention_months = models.PositiveIntegerField(
        help_text="Retention period in months",
    )
    legal_basis = models.TextField(
        help_text="Legal basis for retention",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["data_category"]

    def __str__(self):
        return f"{self.data_category}: {self.retention_months} months"


class DataProcessingAgreement(models.Model):
    """
    Tracks Data Processing Agreements with third-party vendors.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vendor_name = models.CharField(max_length=255)
    vendor_contact = models.EmailField()
    service_description = models.TextField()
    data_types_processed = models.JSONField(
        default=list,
        help_text="Types of data processed",
    )
    processing_purpose = models.TextField()
    dpa_signed_date = models.DateField(null=True, blank=True)
    dpa_expiry_date = models.DateField(null=True, blank=True)
    data_location = models.CharField(
        max_length=100,
        help_text="Where data is processed/stored",
    )
    breach_notification_hours = models.PositiveIntegerField(
        default=24,
        help_text="Hours to notify of breach",
    )
    is_active = models.BooleanField(default=True)
    document = models.FileField(upload_to="dpa/", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"DPA with {self.vendor_name}"


class PrivacyPolicyVersion(models.Model):
    """
    Tracks privacy policy versions and acceptance.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.CharField(max_length=16, unique=True)
    effective_date = models.DateField()
    summary = models.TextField(blank=True)
    full_policy = models.TextField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_date"]

    def __str__(self):
        return f"Privacy Policy v{self.version}"


class PolicyAcceptance(models.Model):
    """
    Tracks user acceptance of policy versions.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="policy_acceptances",
    )
    policy_version = models.ForeignKey(
        PrivacyPolicyVersion,
        on_delete=models.CASCADE,
        related_name="acceptances",
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    accepted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["user", "policy_version"]
        ordering = ["-accepted_at"]

    def __str__(self):
        return f"{self.user} accepted v{self.policy_version.version}"
