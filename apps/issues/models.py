import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import BaseModel


class IssueCategory(models.TextChoices):
    PAYMENT_DISPUTE = "payment_dispute", "Payment Dispute"
    MEMBER_CONDUCT = "member_conduct", "Member Conduct"
    GOVERNANCE = "governance", "Governance"
    FINANCIAL = "financial", "Financial"
    OPERATIONAL = "operational", "Operational"
    LOAN_DISPUTE = "loan_dispute", "Loan Dispute"


class IssuePriority(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"
    CRITICAL = "critical", "Critical"


class IssueStatus(models.TextChoices):
    OPEN = "open", "Open"
    PENDING_ASSIGNMENT = "pending_assignment", "Pending Assignment"
    ASSIGNED = "assigned", "Assigned"
    CLARIFICATION_REQUESTED = "clarification_requested", "Clarification Requested"
    UNDER_INVESTIGATION = "under_investigation", "Under Investigation"
    IN_PROGRESS = "in_progress", "In Progress"
    RESOLUTION_PROPOSED = "resolution_proposed", "Resolution Proposed"
    AWAITING_CHAIRPERSON_APPROVAL = "awaiting_chairperson_approval", "Awaiting Chairperson Approval"
    RESOLVED = "resolved", "Resolved"
    DISMISSED = "dismissed", "Dismissed"
    ESCALATED = "escalated", "Escalated"
    IN_VOTE = "in_vote", "In Vote"
    REOPENED = "reopened", "Reopened"
    CLOSED = "closed", "Closed"


class IssueSourceType(models.TextChoices):
    MEMBER = "member", "Member"
    CHAIRPERSON = "chairperson", "Chairperson"
    TREASURER = "treasurer", "Treasurer"
    ADMIN = "admin", "Admin"
    SYSTEM = "system", "System"


class IssueScope(models.TextChoices):
    PERSONAL = "personal", "Personal"
    GROUP = "group", "Group"
    FINANCIAL = "financial", "Financial"
    OPERATIONAL = "operational", "Operational"


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
    REASSIGNED = "reassigned", "Reassigned"
    CLARIFICATION_REQUESTED = "clarification_requested", "Clarification Requested"
    CLARIFICATION_PROVIDED = "clarification_provided", "Clarification Provided"
    COMMENT_ADDED = "comment_added", "Comment Added"
    EVIDENCE_ADDED = "evidence_added", "Evidence Added"
    INVESTIGATION_STARTED = "investigation_started", "Investigation Started"
    INVESTIGATION_UPDATE = "investigation_update", "Investigation Update"
    RESOLUTION_PROPOSED = "resolution_proposed", "Resolution Proposed"
    RESOLUTION_APPROVED = "resolution_approved", "Resolution Approved"
    RESOLUTION_REJECTED = "resolution_rejected", "Resolution Rejected"
    RESOLUTION_EXECUTED = "resolution_executed", "Resolution Executed"
    DISMISSED = "dismissed", "Dismissed"
    ESCALATED = "escalated", "Escalated"
    VOTE_STARTED = "vote_started", "Vote Started"
    VOTE_COMPLETED = "vote_completed", "Vote Completed"
    WARNED = "warned", "Warned"
    SUSPENDED = "suspended", "Suspended"
    SUSPENSION_LIFTED = "suspension_lifted", "Suspension Lifted"
    REOPENED = "reopened", "Reopened"
    CLOSED = "closed", "Closed"
    RATED = "rated", "Rated"


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


class IssueCommentType(models.TextChoices):
    PUBLIC_UPDATE = "public_update", "Public Update"
    INTERNAL_NOTE = "internal_note", "Internal Note"
    CLARIFICATION = "clarification", "Clarification"
    RESOLUTION_NOTE = "resolution_note", "Resolution Note"


class IssueCommentVisibility(models.TextChoices):
    MEMBER_VISIBLE = "member_visible", "Member Visible"
    INTERNAL_ONLY = "internal_only", "Internal Only"


class IssueResolutionType(models.TextChoices):
    LEDGER_ADJUSTMENT = "ledger_adjustment", "Ledger Adjustment"
    REFUND = "refund", "Refund"
    PENALTY_WAIVER = "penalty_waiver", "Penalty Waiver"
    WARNING = "warning", "Warning"
    SUSPENSION = "suspension", "Suspension"
    DISMISSAL = "dismissal", "Dismissal"
    MEMBER_NOTIFICATION = "member_notification", "Member Notification"
    MANUAL_ACTION = "manual_action", "Manual Action"
    OTHER = "other", "Other"


class IssueResolutionStatus(models.TextChoices):
    PROPOSED = "proposed", "Proposed"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    EXECUTED = "executed", "Executed"


class IssueEscalationType(models.TextChoices):
    COMMITTEE = "committee", "Committee"
    FULL_GROUP_VOTE = "full_group_vote", "Full Group Vote"


class IssueReopenDecision(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class IssueEvidenceType(models.TextChoices):
    DOCUMENT = "document", "Document"
    IMAGE = "image", "Image"
    SCREENSHOT = "screenshot", "Screenshot"
    RECEIPT = "receipt", "Receipt"
    OTHER = "other", "Other"


class IssueAutoTriggerType(models.TextChoices):
    MISSED_PAYMENT = "missed_payment", "Missed Payment"
    OVERDUE_LOAN = "overdue_loan", "Overdue Loan"
    QUORUM_FAILURE = "quorum_failure", "Quorum Failure"
    OTHER = "other", "Other"


class Issue(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="issues",
    )
    issue_code = models.CharField(max_length=20, unique=True, db_index=True, null=True, blank=True)
    title = models.CharField(max_length=255)
    description = models.TextField()
    category = models.CharField(
        max_length=30,
        choices=IssueCategory.choices,
        default=IssueCategory.OPERATIONAL,
    )
    severity = models.CharField(
        max_length=20,
        choices=IssuePriority.choices,
        default=IssuePriority.MEDIUM,
    )
    status = models.CharField(
        max_length=30,
        choices=IssueStatus.choices,
        default=IssueStatus.OPEN,
    )
    source_type = models.CharField(
        max_length=20,
        choices=IssueSourceType.choices,
        default=IssueSourceType.MEMBER,
    )
    issue_scope = models.CharField(
        max_length=20,
        choices=IssueScope.choices,
        default=IssueScope.PERSONAL,
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_issues",
    )
    assigned_role = models.CharField(
        max_length=30,
        choices=[
            ("chairperson", "Chairperson"),
            ("treasurer", "Treasurer"),
            ("committee", "Committee"),
            ("admin", "Admin"),
        ],
        blank=True,
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
    reopened_count = models.PositiveIntegerField(default=0)
    
    escalation_type = models.CharField(
        max_length=30,
        choices=IssueEscalationType.choices,
        blank=True,
    )
    escalation_reason = models.TextField(blank=True)
    
    chairperson_approved = models.BooleanField(default=False)
    chairperson_approved_at = models.DateTimeField(null=True, blank=True)
    chairperson_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chairperson_approved_issues",
    )

    def __init__(self, *args, **kwargs):
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
            models.Index(fields=["chama", "status", "severity"]),
            models.Index(fields=["chama", "category", "created_at"]),
            models.Index(fields=["loan", "status"]),
            models.Index(fields=["created_by", "status"]),
            models.Index(fields=["assigned_to", "status"]),
            models.Index(fields=["reported_user", "status"]),
            models.Index(fields=["issue_code"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.issue_code} - {self.title} ({self.status})"

    def generate_issue_code(self):
        prefix = "ISS"
        year = timezone.now().year
        count = Issue.objects.filter(chama=self.chama).count() + 1
        self.issue_code = f"{prefix}-{year}-{count:05d}"


class IssueEvidence(BaseModel):
    issue = models.ForeignKey(
        Issue,
        on_delete=models.CASCADE,
        related_name="evidences",
        null=True,
        blank=True,
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issue_evidences",
    )
    file = models.FileField(upload_to="issue_evidence/%Y/%m/%d")
    evidence_type = models.CharField(
        max_length=20,
        choices=IssueEvidenceType.choices,
        default=IssueEvidenceType.OTHER,
    )
    caption = models.CharField(max_length=500, blank=True)
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
        return f"Evidence {self.id} ({self.issue_id})"


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
    body = models.TextField(default="")
    comment_type = models.CharField(
        max_length=20,
        choices=IssueCommentType.choices,
        default=IssueCommentType.PUBLIC_UPDATE,
    )
    visibility = models.CharField(
        max_length=20,
        choices=IssueCommentVisibility.choices,
        default=IssueCommentVisibility.MEMBER_VISIBLE,
    )
    is_clarification_response = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["issue", "created_at"]),
            models.Index(fields=["author", "created_at"]),
            models.Index(fields=["issue", "visibility"]),
            models.Index(fields=["issue", "comment_type"]),
        ]

    def __str__(self) -> str:
        return f"Comment {self.id} ({self.issue_id})"


class IssueStatusHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    issue = models.ForeignKey(
        Issue,
        on_delete=models.CASCADE,
        related_name="status_history",
        null=True,
        blank=True,
    )
    from_status = models.CharField(
        max_length=30,
        choices=IssueStatus.choices,
        blank=True,
    )
    to_status = models.CharField(
        max_length=30,
        choices=IssueStatus.choices,
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issue_status_changes",
    )
    reason = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name_plural = "Issue status histories"

    def __str__(self) -> str:
        return f"{self.issue_id}: {self.from_status} -> {self.to_status}"


class IssueAssignmentHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    issue = models.ForeignKey(
        Issue,
        on_delete=models.CASCADE,
        related_name="assignment_history",
        null=True,
        blank=True,
    )
    assigned_from = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issue_assignments_from",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issue_assignments_to",
    )
    assigned_role = models.CharField(max_length=30, blank=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issue_assignments_made",
    )
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Assignment {self.id} ({self.issue_id})"


class IssueResolution(BaseModel):
    issue = models.ForeignKey(
        Issue,
        on_delete=models.CASCADE,
        related_name="resolutions",
        null=True,
        blank=True,
    )
    proposed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="proposed_resolutions",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_resolutions",
    )
    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rejected_resolutions",
    )
    resolution_type = models.CharField(
        max_length=30,
        choices=IssueResolutionType.choices,
    )
    summary = models.TextField()
    detailed_action_taken = models.TextField(blank=True)
    financial_adjustment_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=IssueResolutionStatus.choices,
        default=IssueResolutionStatus.PROPOSED,
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["issue", "status"]),
            models.Index(fields=["proposed_by", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"Resolution {self.id} ({self.issue_id}) - {self.status}"


class IssueReopenRequest(BaseModel):
    issue = models.ForeignKey(
        Issue,
        on_delete=models.CASCADE,
        related_name="reopen_requests",
        null=True,
        blank=True,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="issue_reopen_requests",
        null=True,
        blank=True,
    )
    reason = models.TextField()
    decision = models.CharField(
        max_length=20,
        choices=IssueReopenDecision.choices,
        default=IssueReopenDecision.PENDING,
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issue_reopen_decisions",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["issue", "decision"]),
            models.Index(fields=["requested_by", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"Reopen Request {self.id} ({self.issue_id})"


class IssueRating(BaseModel):
    issue = models.ForeignKey(
        Issue,
        on_delete=models.CASCADE,
        related_name="ratings",
        null=True,
        blank=True,
    )
    rated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="issue_ratings",
        null=True,
        blank=True,
    )
    score = models.PositiveSmallIntegerField()
    feedback = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["issue", "rated_by"],
                name="unique_issue_rating_per_user",
            )
        ]
        indexes = [
            models.Index(fields=["issue", "rated_by"]),
            models.Index(fields=["rated_by", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"Rating {self.score}/5 for {self.issue_id}"


class IssueAutoTriggerLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trigger_type = models.CharField(
        max_length=30,
        choices=IssueAutoTriggerType.choices,
    )
    linked_object_type = models.CharField(max_length=100, blank=True)
    linked_object_id = models.UUIDField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    generated_issue = models.ForeignKey(
        Issue,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auto_trigger_logs",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"AutoTrigger {self.trigger_type} -> {self.generated_issue_id}"


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
