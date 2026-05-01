# Governance Module Models
# Constitution/Rules, Approvals Engine, Role Management

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Max
from django.utils import timezone

from core.models import BaseModel


class RuleCategory(models.TextChoices):
    CONSTITUTION = "constitution", "Constitution"
    CONTRIBUTIONS = "contributions", "Contributions"
    MEETINGS = "meetings", "Meetings"
    LOANS = "loans", "Loans"
    FINES = "fines", "Fines"
    INVESTMENTS = "investments", "Investments"
    WITHDRAWALS = "withdrawals", "Withdrawals"
    MEMBERSHIP = "membership", "Membership"
    VOTING = "voting", "Voting"
    OTHER = "other", "Other"


class RuleStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PENDING_APPROVAL = "pending_approval", "Pending Approval"
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"
    REJECTED = "rejected", "Rejected"


class AcknowledgmentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACKNOWLEDGED = "acknowledged", "Acknowledged"
    EXPIRED = "expired", "Expired"


class ChamaRule(BaseModel):
    """
    Constitutional rules and policies for a chama.
    Supports versioning - each change creates a new version.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="rules",
    )
    category = models.CharField(max_length=30, choices=RuleCategory.choices)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    content = models.TextField(help_text="Rule content in markdown format")
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(
        max_length=20,
        choices=RuleStatus.choices,
        default=RuleStatus.DRAFT,
    )
    effective_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    previous_version = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="next_versions",
    )
    requires_acknowledgment = models.BooleanField(default=False)
    acknowledgment_deadline_days = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Days within which members must acknowledge"
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_rules",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_rules",
    )

    class Meta:
        ordering = ["-version", "-created_at"]
        indexes = [
            models.Index(fields=["chama", "category", "status"]),
            models.Index(fields=["chama", "effective_date"]),
            models.Index(fields=["status", "effective_date"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["chama", "category", "version"],
                name="unique_rule_version_per_category",
            ),
        ]

    def save(self, *args, **kwargs):
        if self._state.adding and self.chama_id and self.category:
            latest_version = (
                ChamaRule.objects.filter(chama_id=self.chama_id, category=self.category)
                .aggregate(max_version=Max("version"))
                .get("max_version")
                or 0
            )
            # Auto-increment by default to preserve unique chama/category/version
            # when callers create multiple versions directly via ORM.
            if not self.version or self.version <= latest_version:
                self.version = latest_version + 1
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.chama.name} - {self.title} (v{self.version})"

    def get_acknowledgment_rate(self):
        """Calculate what percentage of members have acknowledged"""
        total = self.acknowledgments.count()
        if total == 0:
            return 0
        acknowledged = self.acknowledgments.filter(
            status=AcknowledgmentStatus.ACKNOWLEDGED
        ).count()
        return (acknowledged / total) * 100


class RuleAcknowledgment(BaseModel):
    """
    Track member acknowledgments of rules.
    Required for compliance in regulated chamas.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rule = models.ForeignKey(
        ChamaRule,
        on_delete=models.CASCADE,
        related_name="acknowledgments",
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="rule_acknowledgments",
    )
    status = models.CharField(
        max_length=20,
        choices=AcknowledgmentStatus.choices,
        default=AcknowledgmentStatus.PENDING,
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    device_info = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["rule", "member"],
                name="unique_acknowledgment_per_rule_member",
            ),
        ]
        indexes = [
            models.Index(fields=["rule", "status"]),
            models.Index(fields=["member", "status"]),
        ]

    def __str__(self):
        return f"{self.member} - {self.rule.title} ({self.status})"


# ============================================
# APPROVALS ENGINE
# ============================================

class ApprovalType(models.TextChoices):
    LOAN = "loan", "Loan"
    EXPENSE = "expense", "Expense"
    INVESTMENT = "investment", "Investment"
    WITHDRAWAL = "withdrawal", "Withdrawal"
    PAYOUT = "payout", "Payout"
    FINE_WAIVER = "fine_waiver", "Fine Waiver"
    ROLE_CHANGE = "role_change", "Role Change"
    RULE_CHANGE = "rule_change", "Rule Change"
    CONTRIBUTION_ADJUSTMENT = "contribution_adjustment", "Contribution Adjustment"
    MEMBER_ADMISSION = "member_admission", "Member Admission"
    MEMBER_EXPULSION = "member_expulsion", "Member Expulsion"
    BUDGET = "budget", "Budget"
    OTHER = "other", "Other"


class ApprovalStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"
    EXPIRED = "expired", "Expired"


class ApprovalLevel(models.TextChoices):
    FIRST = "first", "First Approval"
    SECOND = "second", "Second Approval"
    FINAL = "final", "Final Approval"


class ApprovalRequest(BaseModel):
    """
    Generic approval workflow for any type of request.
    Supports multi-level approvals with thresholds.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="approval_requests",
    )
    approval_type = models.CharField(max_length=30, choices=ApprovalType.choices)
    reference_type = models.CharField(
        max_length=100,
        help_text="Model name this approval is for (e.g., 'Loan', 'Expense')",
    )
    reference_id = models.UUIDField(
        help_text="ID of the object being approved",
    )
    reference_display = models.CharField(
        max_length=255,
        help_text="Human-readable reference (e.g., 'Loan #123')",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        null=True,
        blank=True,
    )
    currency = models.CharField(max_length=3, default="KES")
    
    # Requester info
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approval_requests_made",
    )
    
    # Approval workflow
    status = models.CharField(
        max_length=20,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING,
    )
    required_level = models.CharField(
        max_length=20,
        choices=ApprovalLevel.choices,
        default=ApprovalLevel.FIRST,
    )
    current_level = models.CharField(
        max_length=20,
        choices=ApprovalLevel.choices,
        default=ApprovalLevel.FIRST,
    )
    
    # Multi-level approval thresholds
    first_level_approver_role = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="Role required for first level approval (e.g., 'treasurer')",
    )
    second_level_approver_role = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="Role required for second level approval (e.g., 'chairperson')",
    )
    first_level_threshold = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Amount threshold for first level approval",
    )
    
    # Metadata
    due_date = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Deadline for approval decision",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approval_requests_resolved",
    )
    
    # Link to meeting/voting (optional)
    meeting = models.ForeignKey(
        "meetings.Meeting",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approval_requests",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["chama", "approval_type", "status"]),
            models.Index(fields=["requested_by", "status"]),
            models.Index(fields=["reference_type", "reference_id"]),
            models.Index(fields=["due_date", "status"]),
        ]

    def __str__(self):
        return f"{self.approval_type}: {self.title} ({self.status})"

    def get_approvers_needed(self):
        """Return list of roles needed to approve based on amount"""
        approvers = []
        if self.first_level_approver_role:
            approvers.append(self.first_level_approver_role)
        if self.second_level_approver_role:
            approvers.append(self.second_level_approver_role)
        return approvers

    def is_approved(self):
        return self.status == ApprovalStatus.APPROVED

    def is_pending(self):
        return self.status == ApprovalStatus.PENDING


class ApprovalStep(BaseModel):
    """
    Individual approval steps in the workflow.
    Tracks who approved what and when.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    approval_request = models.ForeignKey(
        ApprovalRequest,
        on_delete=models.CASCADE,
        related_name="approval_steps",
    )
    level = models.CharField(max_length=20, choices=ApprovalLevel.choices)
    approver_role = models.CharField(max_length=50)
    
    # Approval decision
    status = models.CharField(
        max_length=20,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING,
    )
    decision_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approval_steps_decided",
    )
    comment = models.TextField(blank=True)
    
    # Conditions
    conditions = models.JSONField(
        default=dict,
        blank=True,
        help_text="Conditions attached to approval (e.g., 'must submit collateral')",
    )

    class Meta:
        ordering = ["level", "-created_at"]
        indexes = [
            models.Index(fields=["approval_request", "level"]),
            models.Index(fields=["decided_by", "status"]),
        ]

    def __str__(self):
        return f"{self.approval_request.title} - {self.level} ({self.status})"


# ============================================
# ROLE MANAGEMENT WITH EFFECTIVE DATES
# ============================================

class RoleChangeType(models.TextChoices):
    APPOINTMENT = "appointment", "Appointment"
    DEMOTION = "demotion", "Demotion"
    RESIGNATION = "resignation", "Resignation"
    REVOCATION = "revocation", "Revocation"
    ACTING = "acting", "Acting Role"
    EXPIRED = "expired", "Expired"


class RoleChangeStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"
    EFFECTIVE = "effective", "Effective"
    EXPIRED = "expired", "Expired"


class RoleChange(BaseModel):
    """
    Track role changes with effective dates and expiry.
    Supports "acting" roles that automatically expire.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="role_changes",
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="role_changes",
    )
    
    # Role change details
    change_type = models.CharField(max_length=20, choices=RoleChangeType.choices)
    old_role = models.CharField(max_length=50, blank=True)
    new_role = models.CharField(max_length=50)
    
    # Effective dates
    effective_date = models.DateField(
        help_text="Date when the role change takes effect",
    )
    expiry_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date when the role change expires (for acting roles)",
    )
    
    # Status
    status = models.CharField(
        max_length=20,
        choices=RoleChangeStatus.choices,
        default=RoleChangeStatus.PENDING,
    )
    
    # Approval
    approval_request = models.ForeignKey(
        ApprovalRequest,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="role_changes",
    )
    
    # Details
    reason = models.TextField(blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="role_changes_approved",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    
    # Acting role specifics
    is_acting = models.BooleanField(
        default=False,
        help_text="Whether this is an acting/temporary role",
    )
    acting_reason = models.TextField(
        blank=True,
        help_text="Reason for acting role (e.g., 'primary holder on leave')",
    )
    
    # Metadata
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="role_changes_revoked",
    )
    revocation_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-effective_date", "-created_at"]
        indexes = [
            models.Index(fields=["chama", "member", "status"]),
            models.Index(fields=["chama", "new_role", "status"]),
            models.Index(fields=["expiry_date", "status"]),
            models.Index(fields=["effective_date", "status"]),
        ]

    def __str__(self):
        return f"{self.member} - {self.new_role} ({self.effective_date})"

    def is_active(self):
        """Check if role change is currently active"""
        if self.status != RoleChangeStatus.EFFECTIVE:
            return False
        today = timezone.now().date()
        if self.expiry_date and today > self.expiry_date:
            return False
        return True

    def is_expiring_soon(self, days=7):
        """Check if acting role is expiring within given days"""
        if not self.is_acting or not self.expiry_date:
            return False
        today = timezone.now().date()
        return 0 <= (self.expiry_date - today).days <= days


class RoleDelegation(BaseModel):
    """
    Temporary delegation of role powers to another member.
    Used when a role holder is unavailable.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="governance_role_delegations",
    )
    delegator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="delegations_given",
        help_text="Original role holder",
    )
    delegate = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="delegations_received",
        help_text="Person receiving delegation",
    )
    role = models.CharField(max_length=50)
    
    # Delegation period
    start_date = models.DateField()
    end_date = models.DateField()
    
    # Status
    is_active = models.BooleanField(default=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="delegations_revoked",
    )
    
    # Scope
    can_delegate_further = models.BooleanField(default=False)
    restrictions = models.JSONField(
        default=dict,
        blank=True,
        help_text="Specific restrictions on delegation (e.g., cannot approve loans over 100k)",
    )

    class Meta:
        ordering = ["-start_date"]
        indexes = [
            models.Index(fields=["chama", "delegate", "is_active"]),
            models.Index(fields=["delegator", "is_active"]),
            models.Index(fields=["end_date", "is_active"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["chama", "delegator", "delegate", "role", "start_date"],
                name="unique_delegation",
            ),
        ]

    def __str__(self):
        return f"{self.delegator} -> {self.delegate}: {self.role}"

    def is_valid(self):
        """Check if delegation is currently valid"""
        if not self.is_active:
            return False
        today = timezone.now().date()
        return self.start_date <= today <= self.end_date


class MotionStatus(models.TextChoices):
    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"
    CANCELLED = "cancelled", "Cancelled"


class MotionVoteChoice(models.TextChoices):
    YES = "yes", "Yes"
    NO = "no", "No"
    ABSTAIN = "abstain", "Abstain"


class MotionVoteType(models.TextChoices):
    ORDINARY = "ordinary", "Ordinary Vote"
    SPECIAL = "special", "Special Vote (2/3 majority)"
    UNANIMOUS = "unanimous", "Unanimous Vote"


class Motion(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="motions",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_motions",
    )
    status = models.CharField(
        max_length=20,
        choices=MotionStatus.choices,
        default=MotionStatus.OPEN,
    )
    start_time = models.DateTimeField(default=timezone.now)
    end_time = models.DateTimeField()
    quorum_percent = models.PositiveSmallIntegerField(default=50)
    vote_type = models.CharField(
        max_length=20,
        choices=MotionVoteType.choices,
        default=MotionVoteType.ORDINARY,
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="closed_motions",
    )
    eligible_roles = models.JSONField(
        default=list,
        blank=True,
        help_text="Optional role filter for eligible voters.",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["chama", "status", "end_time"]),
            models.Index(fields=["chama", "created_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quorum_percent__gte=1) & models.Q(quorum_percent__lte=100),
                name="governance_motion_quorum_between_1_100",
            ),
        ]

    def __str__(self):
        return f"{self.chama.name}: {self.title}"

    @property
    def is_open(self) -> bool:
        now = timezone.now()
        return (
            self.status == MotionStatus.OPEN
            and self.start_time <= now
            and self.end_time > now
        )


class MotionVote(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    motion = models.ForeignKey(
        Motion,
        on_delete=models.CASCADE,
        related_name="votes",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="motion_votes",
    )
    vote = models.CharField(max_length=10, choices=MotionVoteChoice.choices)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["motion", "user"],
                name="unique_motion_vote_per_user",
            ),
        ]
        indexes = [
            models.Index(fields=["motion", "vote"]),
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self):
        return f"{self.user} - {self.motion} - {self.vote}"


class MotionResult(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    motion = models.OneToOneField(
        Motion,
        on_delete=models.CASCADE,
        related_name="result",
    )
    total_votes = models.PositiveIntegerField(default=0)
    yes_votes = models.PositiveIntegerField(default=0)
    no_votes = models.PositiveIntegerField(default=0)
    abstain_votes = models.PositiveIntegerField(default=0)
    eligible_voters = models.PositiveIntegerField(default=0)
    quorum_met = models.BooleanField(default=False)
    passed = models.BooleanField(default=False)
    calculated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-calculated_at"]

    def __str__(self):
        return f"Result for {self.motion.title}"
