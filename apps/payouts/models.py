"""
Payout Workflow Models.

Implements the payout rotation system with eligibility checks,
multi-level approval workflow, and payment processing.
"""

import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.constants import CurrencyChoices
from core.models import BaseModel


class PayoutTriggerType(models.TextChoices):
    """Payout trigger mechanism."""

    MANUAL = "manual", "Manual Trigger (by Treasurer/Chairperson)"
    AUTO = "auto", "Auto Trigger (cycle complete)"
    SCHEDULED = "scheduled", "Scheduled Reminder"


class PayoutStatus(models.TextChoices):
    """Payout workflow status lifecycle."""

    TRIGGERED = "triggered", "Triggered"
    ROTATION_CHECK = "rotation_check", "Rotation Check"
    ELIGIBILITY_CHECK = "eligibility_check", "Eligibility Check"
    INELIGIBLE = "ineligible", "Ineligible (Skip/Defer)"
    AWAITING_TREASURER_REVIEW = "awaiting_treasurer_review", "Awaiting Treasurer Review"
    TREASURY_REJECTED = "treasury_rejected", "Rejected by Treasurer"
    AWAITING_CHAIR_APPROVAL = "awaiting_chair_approval", "Awaiting Chairperson Approval"
    CHAIR_REJECTED = "chair_rejected", "Rejected by Chairperson"
    APPROVED = "approved", "Approved for Payment"
    PROCESSING = "processing", "Processing Payment"
    SUCCESS = "success", "Payout Completed"
    FAILED = "failed", "Payout Failed"
    HOLD = "hold", "On Hold (Issue Flagged)"
    CANCELLED = "cancelled", "Cancelled"


class PayoutMethod(models.TextChoices):
    """Supported payout methods."""

    BANK_TRANSFER = "bank_transfer", "Bank Transfer"
    MPESA = "mpesa", "M-Pesa (B2C)"
    WALLET = "wallet", "Chama Wallet"


class EligibilityStatus(models.TextChoices):
    """Eligibility check result."""

    ELIGIBLE = "eligible", "Eligible"
    PENDING_PENALTIES = "pending_penalties", "Outstanding Penalties"
    ACTIVE_DISPUTES = "active_disputes", "Active Disputes"
    OVERDUE_LOANS = "overdue_loans", "Overdue Loans"
    INACTIVE_MEMBER = "inactive_member", "Inactive Member"
    INSUFFICIENT_FUNDS = "insufficient_funds", "Insufficient Funds"
    MULTIPLE_ISSUES = "multiple_issues", "Multiple Issues"


class PayoutRotation(BaseModel):
    """
    Rotation tracker for payout distribution.
    Maintains the queue of who is next to receive payout.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.OneToOneField(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="payout_rotation",
    )
    current_position = models.IntegerField(default=0, help_text="Index in rotation queue")
    rotation_cycle = models.IntegerField(
        default=1,
        help_text="Current cycle number (resets after full rotation)",
    )
    members_in_rotation = models.JSONField(
        default=list,
        help_text="Ordered list of member IDs in rotation",
    )
    last_completed_payout = models.ForeignKey(
        "Payout",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rotation_completion",
    )
    last_updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-chama__created_at"]
        indexes = [
            models.Index(fields=["chama"]),
        ]

    def __str__(self):
        return f"{self.chama.name} - Cycle {self.rotation_cycle}, Position {self.current_position}"

    def get_next_member(self):
        """Get the next member in rotation queue."""
        if not self.members_in_rotation:
            return None
        if self.current_position >= len(self.members_in_rotation):
            self.current_position = 0
            self.rotation_cycle += 1
        return self.members_in_rotation[self.current_position]

    def advance_rotation(self):
        """Move to next member in rotation."""
        if not self.members_in_rotation:
            return
        self.current_position += 1
        if self.current_position >= len(self.members_in_rotation):
            self.current_position = 0
            self.rotation_cycle += 1
        self.last_updated_at = timezone.now()
        self.save(update_fields=["current_position", "rotation_cycle", "last_updated_at"])


class Payout(BaseModel):
    """
    Payout instance representing a single payout cycle.
    Tracks the entire workflow from trigger through completion.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="payouts",
    )
    member = models.ForeignKey(
        "chama.Membership",
        on_delete=models.PROTECT,
        related_name="payouts_received",
        help_text="Member receiving payout",
    )
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text="Payout amount in KES",
    )
    currency = models.CharField(
        max_length=3,
        choices=CurrencyChoices.choices,
        default=CurrencyChoices.KES,
    )

    # Rotation tracking
    rotation_position = models.IntegerField(help_text="Position in rotation cycle")
    rotation_cycle = models.IntegerField(help_text="Cycle number for this payout")

    # Workflow tracking
    status = models.CharField(
        max_length=30,
        choices=PayoutStatus.choices,
        default=PayoutStatus.TRIGGERED,
        db_index=True,
    )
    trigger_type = models.CharField(
        max_length=20,
        choices=PayoutTriggerType.choices,
        default=PayoutTriggerType.MANUAL,
    )

    # Eligibility
    eligibility_status = models.CharField(
        max_length=30,
        choices=EligibilityStatus.choices,
        null=True,
        blank=True,
    )
    eligibility_issues = models.JSONField(
        default=list,
        help_text="List of eligibility issues found",
    )
    eligibility_checked_at = models.DateTimeField(null=True, blank=True)

    # Approvals
    approval_request = models.ForeignKey(
        "governance.ApprovalRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payouts",
    )
    treasurer_reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payouts_reviewed",
    )
    treasurer_reviewed_at = models.DateTimeField(null=True, blank=True)
    treasurer_rejection_reason = models.TextField(blank=True)

    chairperson_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payouts_approved",
    )
    chairperson_approved_at = models.DateTimeField(null=True, blank=True)
    chairperson_rejection_reason = models.TextField(blank=True)

    # Payment method
    payout_method = models.CharField(
        max_length=20,
        choices=PayoutMethod.choices,
        default=PayoutMethod.MPESA,
    )
    payment_intent = models.ForeignKey(
        "payments.PaymentIntent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payouts",
    )

    # On-hold tracking
    is_on_hold = models.BooleanField(default=False)
    hold_reason = models.TextField(blank=True)
    hold_flagged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payouts_on_hold",
    )
    hold_flagged_at = models.DateTimeField(null=True, blank=True)
    hold_resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payouts_hold_resolved",
    )
    hold_resolved_at = models.DateTimeField(null=True, blank=True)

    # Payment processing
    payment_started_at = models.DateTimeField(null=True, blank=True)
    payment_completed_at = models.DateTimeField(null=True, blank=True)
    payment_failed_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(blank=True)
    failure_code = models.CharField(max_length=50, blank=True)
    retry_count = models.IntegerField(default=0)
    max_retries = models.IntegerField(default=3)

    # Ledger tracking
    ledger_entry = models.ForeignKey(
        "finance.LedgerEntry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payouts",
    )
    receipt_generated_at = models.DateTimeField(null=True, blank=True)

    # Metadata
    metadata = models.JSONField(default=dict, blank=True)
    skip_reason = models.CharField(
        max_length=255,
        blank=True,
        help_text="Reason for skipping to next member",
    )
    defer_reason = models.CharField(
        max_length=255,
        blank=True,
        help_text="Reason for deferring to next cycle",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["chama", "status", "created_at"]),
            models.Index(fields=["member", "status"]),
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["rotation_cycle", "rotation_position"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=Decimal("0.00")),
                name="payout_amount_positive",
            ),
        ]

    def __str__(self):
        return (
            f"Payout #{self.rotation_position} - "
            f"{self.member.user.phone if self.member.user else 'Unknown'} "
            f"({self.status})"
        )

    @property
    def is_terminal(self) -> bool:
        """Check if payout is in terminal state."""
        return self.status in {
            PayoutStatus.SUCCESS,
            PayoutStatus.FAILED,
            PayoutStatus.CANCELLED,
            PayoutStatus.INELIGIBLE,
        }

    @property
    def is_eligible(self) -> bool:
        """Check if payout has eligible status."""
        return self.eligibility_status == EligibilityStatus.ELIGIBLE

    def can_retry(self) -> bool:
        """Check if payment can be retried."""
        return (
            self.status == PayoutStatus.FAILED
            and self.retry_count < self.max_retries
        )


class PayoutAuditLog(BaseModel):
    """
    Immutable audit trail for all payout state changes.
    Provides compliance and debugging information.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payout = models.ForeignKey(
        Payout,
        on_delete=models.CASCADE,
        related_name="audit_logs",
    )
    action = models.CharField(
        max_length=50,
        help_text="Action performed (e.g., TRIGGERED, APPROVED, REJECTED, PAID)",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payout_audit_actions",
    )
    previous_status = models.CharField(max_length=30, blank=True)
    new_status = models.CharField(max_length=30, blank=True)
    details = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    is_immutable = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["payout", "action"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.payout.id} - {self.action}"


class PayoutEligibilityCheck(BaseModel):
    """
    Detailed eligibility check results.
    Immutable record of why a member is/isn't eligible.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payout = models.OneToOneField(
        Payout,
        on_delete=models.CASCADE,
        related_name="eligibility_check",
    )
    member = models.ForeignKey(
        "chama.Membership",
        on_delete=models.CASCADE,
        related_name="eligibility_checks",
    )
    result = models.CharField(
        max_length=30,
        choices=EligibilityStatus.choices,
    )

    # Individual checks
    has_outstanding_penalties = models.BooleanField(default=False)
    penalty_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    active_penalties = models.JSONField(default=list, help_text="IDs of active penalties")

    has_active_disputes = models.BooleanField(default=False)
    active_disputes = models.JSONField(default=list, help_text="IDs of active disputes")

    has_overdue_loans = models.BooleanField(default=False)
    overdue_loan_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    overdue_loans = models.JSONField(default=list, help_text="IDs of overdue loans")

    member_is_active = models.BooleanField(default=True)
    wallet_has_funds = models.BooleanField(default=True)
    available_balance = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    checked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["member", "result"]),
        ]

    def __str__(self):
        return f"Eligibility check for {self.member.user.phone} - {self.result}"
