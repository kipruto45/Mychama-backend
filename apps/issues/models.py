import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import BaseModel


class IssueCategory(models.TextChoices):
    FINANCE = "finance", "Finance"
    LOAN = "loan", "Loan"
    MEETING = "meeting", "Meeting"
    BEHAVIOR = "behavior", "Behavior"
    TECHNICAL = "technical", "Technical"
    OTHER = "other", "Other"


class IssuePriority(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"
    URGENT = "urgent", "Urgent"


class IssueStatus(models.TextChoices):
    OPEN = "open", "Open"
    IN_REVIEW = "in_review", "In Review"
    ASSIGNED = "assigned", "Assigned"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"
    REOPENED = "reopened", "Reopened"
    REJECTED = "rejected", "Rejected"
    ESCALATED = "escalated", "Escalated"


class IssueReportType(models.TextChoices):
    HARASSMENT = "harassment", "Harassment"
    FRAUD = "fraud", "Fraud"
    MISCONDUCT = "misconduct", "Misconduct"
    ABUSE = "abuse", "Abuse"
    SPAM = "spam", "Spam"
    OTHER = "other", "Other"


class IssueActivityAction(models.TextChoices):
    CREATED = "created", "Created"
    UPDATED = "updated", "Updated"
    STATUS_CHANGED = "status_changed", "Status Changed"
    ASSIGNED = "assigned", "Assigned"
    COMMENT_ADDED = "comment_added", "Comment Added"
    ATTACHMENT_ADDED = "attachment_added", "Attachment Added"
    WARNED = "warned", "Warned"
    SUSPENDED = "suspended", "Suspended"
    SUSPENSION_LIFTED = "suspension_lifted", "Suspension Lifted"
    APPEALED = "appealed", "Appealed"
    APPEAL_REVIEWED = "appeal_reviewed", "Appeal Reviewed"
    MEDIATION_NOTE_ADDED = "mediation_note_added", "Mediation Note Added"
    REOPENED = "reopened", "Reopened"
    CLOSED = "closed", "Closed"


class WarningSeverity(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


class WarningStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    REVOKED = "revoked", "Revoked"


class AppealStatus(models.TextChoices):
    OPEN = "open", "Open"
    IN_REVIEW = "in_review", "In Review"
    ACCEPTED = "accepted", "Accepted"
    REJECTED = "rejected", "Rejected"


class Issue(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="issues",
    )
    title = models.CharField(max_length=255)
    description = models.TextField()
    category = models.CharField(
        max_length=20,
        choices=IssueCategory.choices,
        default=IssueCategory.OTHER,
    )
    priority = models.CharField(
        max_length=20,
        choices=IssuePriority.choices,
        default=IssuePriority.MEDIUM,
    )
    status = models.CharField(
        max_length=20,
        choices=IssueStatus.choices,
        default=IssueStatus.OPEN,
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_issues",
    )
    reported_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issues_reported_against",
    )
    loan = models.ForeignKey(
        "finance.Loan",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issues",
    )
    report_type = models.CharField(
        max_length=20,
        choices=IssueReportType.choices,
        blank=True,
    )
    is_anonymous = models.BooleanField(default=False)
    due_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    def __init__(self, *args, **kwargs):
        # Backward compatibility: `reported_by` previously represented creator.
        reported_by = kwargs.pop("reported_by", None)
        if reported_by is not None and "created_by" not in kwargs:
            kwargs["created_by"] = reported_by
        super().__init__(*args, **kwargs)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(report_type="") | Q(reported_user__isnull=False),
                name="issue_report_type_requires_reported_user",
            )
        ]
        indexes = [
            models.Index(fields=["chama", "status", "priority"]),
            models.Index(fields=["chama", "category", "created_at"]),
            models.Index(fields=["loan", "status"]),
            models.Index(fields=["created_by", "status"]),
            models.Index(fields=["assigned_to", "status"]),
            models.Index(fields=["reported_user", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.status})"


class IssueComment(BaseModel):
    issue = models.ForeignKey(
        Issue,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="issue_comments",
    )
    message = models.TextField()
    is_internal = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["issue", "created_at"]),
            models.Index(fields=["author", "created_at"]),
            models.Index(fields=["issue", "is_internal"]),
        ]

    def __str__(self) -> str:
        return f"Comment {self.id} ({self.issue_id})"


class IssueAttachment(BaseModel):
    issue = models.ForeignKey(
        Issue,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="issue_attachments",
    )
    file = models.FileField(upload_to="issue_attachments/%Y/%m/%d")
    content_type = models.CharField(max_length=120, blank=True)
    size = models.PositiveBigIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["issue", "created_at"]),
            models.Index(fields=["uploaded_by", "created_at"]),
        ]

    def save(self, *args, **kwargs):
        if self.file:
            self.size = getattr(self.file, "size", 0) or 0
            self.content_type = getattr(self.file, "content_type", "") or ""
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"Attachment {self.id} ({self.issue_id})"


class IssueActivityLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    issue = models.ForeignKey(
        Issue,
        on_delete=models.CASCADE,
        related_name="activity_logs",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issue_activity_logs",
    )
    action = models.CharField(max_length=40, choices=IssueActivityAction.choices)
    meta = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["issue", "created_at"]),
            models.Index(fields=["action", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.issue_id} - {self.action}"


class Warning(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="issue_warnings",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="warnings",
    )
    issue = models.ForeignKey(
        Issue,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="warnings",
    )
    reason = models.TextField()
    severity = models.CharField(
        max_length=20,
        choices=WarningSeverity.choices,
        default=WarningSeverity.MEDIUM,
    )
    message_to_user = models.TextField()
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_warnings",
    )
    issued_at = models.DateTimeField(default=timezone.now, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=WarningStatus.choices,
        default=WarningStatus.ACTIVE,
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="revoked_warnings",
    )
    revocation_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-issued_at"]
        indexes = [
            models.Index(fields=["chama", "user", "status"]),
            models.Index(fields=["issue", "status"]),
        ]

    def __str__(self) -> str:
        return f"Warning {self.user_id} ({self.status})"


class Suspension(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="suspensions",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="suspensions",
    )
    issue = models.ForeignKey(
        Issue,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="suspensions",
    )
    reason = models.TextField()
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(null=True, blank=True)
    suspended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_suspensions",
    )
    is_active = models.BooleanField(default=True)
    lifted_at = models.DateTimeField(null=True, blank=True)
    lifted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lifted_suspensions",
    )
    lift_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-starts_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__isnull=True)
                | Q(ends_at__gte=models.F("starts_at")),
                name="issue_suspension_ends_after_starts",
            )
        ]
        indexes = [
            models.Index(fields=["chama", "user", "is_active"]),
            models.Index(fields=["issue", "is_active"]),
            models.Index(fields=["starts_at"]),
            models.Index(fields=["ends_at"]),
        ]

    def __str__(self) -> str:
        return f"Suspension {self.user_id} ({self.is_active})"

    @property
    def is_expired(self) -> bool:
        return bool(self.ends_at and timezone.now() >= self.ends_at)


class IssueAppeal(BaseModel):
    issue = models.ForeignKey(
        Issue,
        on_delete=models.CASCADE,
        related_name="appeals",
    )
    appellant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="issue_appeals",
    )
    message = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=AppealStatus.choices,
        default=AppealStatus.OPEN,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_issue_appeals",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["issue", "status", "created_at"]),
            models.Index(fields=["appellant", "status", "created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Appeal {self.issue_id} ({self.status})"


class IssueMediationNote(BaseModel):
    issue = models.ForeignKey(
        Issue,
        on_delete=models.CASCADE,
        related_name="mediation_notes",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="issue_mediation_notes",
    )
    note = models.TextField()
    is_private = models.BooleanField(default=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["issue", "created_at"]),
            models.Index(fields=["author", "created_at"]),
            models.Index(fields=["issue", "is_private"]),
        ]

    def __str__(self):
        return f"Mediation note {self.id} ({self.issue_id})"
