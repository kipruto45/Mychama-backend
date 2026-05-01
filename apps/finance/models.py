from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.constants import CurrencyChoices, MethodChoices
from core.models import BaseModel


class ContributionFrequency(models.TextChoices):
    WEEKLY = "weekly", "Weekly"
    MONTHLY = "monthly", "Monthly"
    QUARTERLY = "quarterly", "Quarterly"
    ANNUALLY = "annually", "Annually"


class LoanInterestType(models.TextChoices):
    FLAT = "flat", "Flat Rate"
    REDUCING = "reducing", "Reducing Balance"


class LoanStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    REVIEW = "review", "Review"
    APPROVED = "approved", "Approved"
    DISBURSING = "disbursing", "Disbursing"
    DISBURSED = "disbursed", "Disbursed"
    ACTIVE = "active", "Active"
    DUE_SOON = "due_soon", "Due Soon"
    OVERDUE = "overdue", "Overdue"
    RESTRUCTURED = "restructured", "Restructured"
    PAID = "paid", "Paid"
    CLOSED = "closed", "Closed"
    CLEARED = "cleared", "Cleared"
    DEFAULTED = "defaulted", "Defaulted"
    DEFAULTED_RECOVERING = "defaulted_recovering", "Defaulted Recovering"
    WRITTEN_OFF = "written_off", "Written Off"
    RECOVERED_FROM_OFFSET = "recovered_from_offset", "Recovered From Offset"
    RECOVERED_FROM_GUARANTOR = "recovered_from_guarantor", "Recovered From Guarantor"
    REJECTED = "rejected", "Rejected"


class LoanApplicationStatus(models.TextChoices):
    SUBMITTED = "submitted", "Submitted"
    IN_REVIEW = "in_review", "In Review"
    TREASURER_APPROVED = "treasurer_approved", "Treasurer Approved"
    COMMITTEE_APPROVED = "committee_approved", "Committee Approved"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    DISBURSED = "disbursed", "Disbursed"
    CANCELLED = "cancelled", "Cancelled"


class LoanPenaltyType(models.TextChoices):
    FIXED = "fixed", "Fixed Amount"
    PERCENTAGE = "percentage", "Percentage"


class LoanEligibilityStatus(models.TextChoices):
    ELIGIBLE = "eligible", "Eligible"
    INELIGIBLE = "ineligible", "Ineligible"


class LoanApprovalStage(models.TextChoices):
    TREASURER_REVIEW = "treasurer_review", "Treasurer Review"
    COMMITTEE_APPROVAL = "committee_approval", "Committee Approval"
    ADMIN_APPROVAL = "admin_approval", "Admin Approval"
    DISBURSEMENT = "disbursement", "Disbursement"


class LoanApprovalDecision(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class ContributionGoalStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class LoanGuarantorStatus(models.TextChoices):
    PROPOSED = "proposed", "Proposed"
    ACCEPTED = "accepted", "Accepted"
    AT_RISK = "at_risk", "At Risk"
    REJECTED = "rejected", "Rejected"
    RELEASED = "released", "Released"


class LoanTopUpStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    DISBURSED = "disbursed", "Disbursed"


class LoanRestructureStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    APPLIED = "applied", "Applied"


class InstallmentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    DUE = "due", "Due"
    PARTIAL = "partial", "Partially Paid"
    PAID = "paid", "Paid"
    OVERDUE = "overdue", "Overdue"


class PenaltyStatus(models.TextChoices):
    UNPAID = "unpaid", "Unpaid"
    PARTIAL = "partial", "Partially Paid"
    PAID = "paid", "Paid"
    WAIVED = "waived", "Waived"


class ExpenseStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    PAID = "paid", "Paid"
    CANCELLED = "cancelled", "Cancelled"


class LoanRecoveryActionType(models.TextChoices):
    REMINDER = "reminder", "Reminder"
    PENALTY_APPLIED = "penalty_applied", "Penalty Applied"
    GUARANTOR_NOTIFIED = "guarantor_notified", "Guarantor Notified"
    GUARANTOR_RECOVERY = "guarantor_recovery", "Guarantor Recovery"
    OFFSET_FROM_SAVINGS = "offset_from_savings", "Offset From Savings"
    RESTRUCTURE_REQUESTED = "restructure_requested", "Restructure Requested"
    RESTRUCTURE_APPROVED = "restructure_approved", "Restructure Approved"
    PENALTY_WAIVED = "penalty_waived", "Penalty Waived"
    WRITE_OFF = "write_off", "Write Off"
    MANUAL_NOTE = "manual_note", "Manual Note"


class LedgerEntryType(models.TextChoices):
    CONTRIBUTION = "contribution", "Contribution"
    WALLET_TOPUP = "wallet_topup", "Wallet Top-up"
    WALLET_TRANSFER = "wallet_transfer", "Wallet Transfer"
    PAYOUT = "payout", "Payout"
    LOAN_DISBURSEMENT = "loan_disbursement", "Loan Disbursement"
    LOAN_REPAYMENT = "loan_repayment", "Loan Repayment"
    WITHDRAWAL = "withdrawal", "Withdrawal"
    EXPENSE = "expense", "Expense"
    FEE = "fee", "Fee"
    PENALTY = "penalty", "Penalty"
    ADJUSTMENT = "adjustment", "Adjustment"


class LedgerDirection(models.TextChoices):
    DEBIT = "debit", "Debit"
    CREDIT = "credit", "Credit"


class LedgerStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"
    REVERSED = "reversed", "Reversed"


class AccountType(models.TextChoices):
    ASSET = "asset", "Asset"
    LIABILITY = "liability", "Liability"
    EQUITY = "equity", "Equity"
    INCOME = "income", "Income"
    EXPENSE = "expense", "Expense"


class JournalEntrySource(models.TextChoices):
    CONTRIBUTION = "contribution", "Contribution"
    EXPENSE = "expense", "Expense"
    LOAN = "loan", "Loan"
    LOAN_REPAYMENT = "loan_repayment", "Loan Repayment"
    PENALTY = "penalty", "Penalty"
    ADJUSTMENT = "adjustment", "Adjustment"
    PAYMENT = "payment", "Payment"
    SNAPSHOT = "snapshot", "Snapshot"


class WalletOwnerType(models.TextChoices):
    USER = "USER", "User"
    CHAMA = "CHAMA", "Chama"


class Wallet(BaseModel):
    """
    Wallet model - stores available and locked balances.
    
    Golden rule: Never update balances directly from UI actions.
    Only update from successful LedgerTransactions.
    """
    owner_type = models.CharField(
        max_length=20,
        choices=WalletOwnerType.choices,
    )
    owner_id = models.CharField(max_length=64)
    
    available_balance = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    locked_balance = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    currency = models.CharField(
        max_length=3,
        choices=CurrencyChoices.choices,
        default=CurrencyChoices.KES,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner_type", "owner_id"],
                name="uniq_wallet_per_owner",
            ),
        ]
        indexes = [
            models.Index(fields=["owner_type", "owner_id"]),
        ]

    def __str__(self) -> str:
        return f"Wallet({self.owner_type}:{self.owner_id}) - {self.available_balance}"

    def save(self, *args, **kwargs):
        self.owner_id = str(self.owner_id)
        super().save(*args, **kwargs)

    @property
    def total_balance(self) -> Decimal:
        return self.available_balance + self.locked_balance


class Account(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="accounts",
    )
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=120)
    type = models.CharField(max_length=20, choices=AccountType.choices)
    is_active = models.BooleanField(default=True)
    system_managed = models.BooleanField(default=False)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["chama", "code"],
                name="uniq_finance_account_code_per_chama",
            ),
            models.UniqueConstraint(
                fields=["chama", "name"],
                name="uniq_finance_account_name_per_chama",
            ),
        ]
        indexes = [
            models.Index(fields=["chama", "type", "is_active"]),
            models.Index(fields=["chama", "system_managed"]),
        ]
        ordering = ["type", "code", "name"]

    def __str__(self) -> str:
        return f"{self.chama.name} {self.code}"


class JournalEntry(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="journal_entries",
    )
    reference = models.CharField(max_length=100, db_index=True)
    description = models.TextField()
    source_type = models.CharField(
        max_length=30,
        choices=JournalEntrySource.choices,
        default=JournalEntrySource.ADJUSTMENT,
    )
    source_id = models.UUIDField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_journal_entries",
    )
    posted_at = models.DateTimeField(default=timezone.now, db_index=True)
    idempotency_key = models.CharField(max_length=100)
    is_reversal = models.BooleanField(default=False)
    reversal_of = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reversal_entries",
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["chama", "idempotency_key"],
                name="uniq_journal_idempotency_per_chama",
            ),
            models.CheckConstraint(
                condition=~Q(id=models.F("reversal_of")),
                name="journal_reversal_not_self",
            ),
        ]
        indexes = [
            models.Index(fields=["chama", "source_type", "posted_at"]),
            models.Index(fields=["reference", "posted_at"]),
        ]
        ordering = ["-posted_at", "-created_at"]

    def __str__(self) -> str:
        return f"{self.reference} {self.source_type}"


class LoanProduct(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="loan_products",
    )
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)

    max_loan_amount = models.DecimalField(max_digits=12, decimal_places=2)
    contribution_multiple = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="If > 0, max amount can also be derived as total contributions * multiple.",
    )

    interest_type = models.CharField(
        max_length=20,
        choices=LoanInterestType.choices,
        default=LoanInterestType.FLAT,
    )
    interest_rate = models.DecimalField(max_digits=5, decimal_places=2)
    min_duration_months = models.PositiveIntegerField(default=1)
    max_duration_months = models.PositiveIntegerField(default=12)
    grace_period_days = models.PositiveIntegerField(default=0)

    late_penalty_type = models.CharField(
        max_length=20,
        choices=LoanPenaltyType.choices,
        default=LoanPenaltyType.FIXED,
    )
    late_penalty_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    early_repayment_discount_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Optional discount applied on future interest when loan is cleared early.",
    )

    minimum_membership_months = models.PositiveIntegerField(default=0)
    minimum_contribution_months = models.PositiveIntegerField(default=0)
    block_if_unpaid_penalties = models.BooleanField(default=True)
    block_if_overdue_loans = models.BooleanField(default=True)
    require_treasurer_review = models.BooleanField(default=True)
    require_separate_disburser = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["chama", "name"],
                name="uniq_loan_product_name_per_chama",
            ),
            models.UniqueConstraint(
                fields=["chama", "is_default"],
                condition=Q(is_default=True),
                name="uniq_default_loan_product_per_chama",
            ),
            models.CheckConstraint(
                condition=Q(max_loan_amount__gt=Decimal("0.00")),
                name="loan_product_max_amount_positive",
            ),
            models.CheckConstraint(
                condition=Q(contribution_multiple__gte=Decimal("0.00")),
                name="loan_product_contribution_multiple_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(interest_rate__gte=Decimal("0.00")),
                name="loan_product_interest_rate_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(max_duration_months__gte=models.F("min_duration_months")),
                name="loan_product_duration_bounds_valid",
            ),
            models.CheckConstraint(
                condition=Q(late_penalty_value__gte=Decimal("0.00")),
                name="loan_product_late_penalty_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(early_repayment_discount_percent__gte=Decimal("0.00"))
                & Q(early_repayment_discount_percent__lte=Decimal("100.00")),
                name="loan_product_early_discount_between_0_100",
            ),
        ]
        indexes = [
            models.Index(fields=["chama", "is_active"]),
            models.Index(fields=["chama", "is_default"]),
        ]
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.chama.name} - {self.name}"


class ContributionType(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="contribution_types",
    )
    name = models.CharField(max_length=100)
    frequency = models.CharField(
        max_length=20,
        choices=ContributionFrequency.choices,
        default=ContributionFrequency.MONTHLY,
    )
    default_amount = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["chama", "name"],
                name="uniq_contribution_type_per_chama",
            ),
            models.CheckConstraint(
                condition=Q(default_amount__gt=Decimal("0.00")),
                name="contribution_type_default_amount_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["chama", "is_active"]),
            models.Index(fields=["name"]),
        ]
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.chama.name} - {self.name}"


class Contribution(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="contributions",
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="contributions",
    )
    contribution_type = models.ForeignKey(
        ContributionType,
        on_delete=models.PROTECT,
        related_name="contributions",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    refunded_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    date_paid = models.DateField()
    method = models.CharField(
        max_length=20,
        choices=MethodChoices.choices,
        default=MethodChoices.MPESA,
    )
    receipt_code = models.CharField(max_length=100, unique=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_contributions",
    )
    refunded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="refunded_contributions",
    )
    refunded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="contribution_amount_positive",
            ),
            models.CheckConstraint(
                condition=Q(refunded_amount__gte=Decimal("0.00")),
                name="contribution_refunded_amount_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(refunded_amount__lte=models.F("amount")),
                name="contribution_refunded_amount_lte_amount",
            ),
        ]
        indexes = [
            models.Index(fields=["chama", "date_paid"]),
            models.Index(fields=["member", "date_paid"]),
            models.Index(fields=["receipt_code"]),
        ]
        ordering = ["-date_paid", "-created_at"]

    def __str__(self) -> str:
        return f"{self.member} - {self.amount}"

    @property
    def net_amount(self) -> Decimal:
        return max(Decimal(self.amount) - Decimal(self.refunded_amount), Decimal("0.00"))


class ContributionGoal(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="contribution_goals",
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="contribution_goals",
    )
    title = models.CharField(max_length=160)
    target_amount = models.DecimalField(max_digits=14, decimal_places=2)
    current_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=ContributionGoalStatus.choices,
        default=ContributionGoalStatus.ACTIVE,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(target_amount__gt=Decimal("0.00")),
                name="contribution_goal_target_positive",
            ),
            models.CheckConstraint(
                condition=Q(current_amount__gte=Decimal("0.00")),
                name="contribution_goal_current_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["chama", "member", "is_active"]),
            models.Index(fields=["status", "due_date"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.member} goal {self.title}"


class ContributionScheduleStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PAID = "paid", "Paid"
    MISSED = "missed", "Missed"
    SKIPPED = "skipped", "Skipped"


class ContributionSchedule(BaseModel):
    """
    Tracks scheduled contributions for members in a chama.
    Used for automation reminders and compliance tracking.
    """
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="contribution_schedules",
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="contribution_schedules",
    )
    scheduled_date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    frequency = models.CharField(
        max_length=20,
        choices=ContributionFrequency.choices,
    )
    status = models.CharField(
        max_length=20,
        choices=ContributionScheduleStatus.choices,
        default=ContributionScheduleStatus.PENDING,
    )
    contribution = models.ForeignKey(
        "Contribution",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="schedules",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=["chama", "scheduled_date"]),
            models.Index(fields=["member", "status"]),
            models.Index(fields=["scheduled_date", "status"]),
        ]
        ordering = ["-scheduled_date"]

    def __str__(self) -> str:
        return f"{self.member} - {self.scheduled_date} - {self.status}"


class LoanApplication(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="loan_applications",
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="loan_applications",
    )
    loan_product = models.ForeignKey(
        LoanProduct,
        on_delete=models.PROTECT,
        related_name="loan_applications",
        null=True,
        blank=True,
    )
    requested_amount = models.DecimalField(max_digits=12, decimal_places=2)
    requested_term_months = models.PositiveIntegerField()
    purpose = models.TextField(blank=True)
    status = models.CharField(
        max_length=30,
        choices=LoanApplicationStatus.choices,
        default=LoanApplicationStatus.SUBMITTED,
    )
    eligibility_status = models.CharField(
        max_length=20,
        choices=LoanEligibilityStatus.choices,
        default=LoanEligibilityStatus.INELIGIBLE,
    )
    recommended_max_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    eligible_amount_at_application = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    savings_balance_at_application = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    contribution_count_at_application = models.PositiveIntegerField(default=0)
    repayment_history_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    contribution_consistency_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    installment_estimate = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    total_repayment_estimate = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    loan_multiplier_at_application = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    risk_notes = models.JSONField(default=list, blank=True)
    next_steps = models.JSONField(default=list, blank=True)
    approval_requirements = models.JSONField(default=dict, blank=True)
    eligibility_snapshot = models.JSONField(default=dict, blank=True)
    rejection_reason = models.TextField(blank=True, db_column="rejected_reason")
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_loan_applications",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_loan_applications",
    )
    disbursed_at = models.DateTimeField(null=True, blank=True)
    created_loan = models.OneToOneField(
        "Loan",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_application_record",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(requested_amount__gt=Decimal("0.00")),
                name="loan_application_amount_positive",
            ),
            models.CheckConstraint(
                condition=Q(requested_term_months__gt=0),
                name="loan_application_term_positive",
            ),
            models.CheckConstraint(
                condition=Q(recommended_max_amount__gte=Decimal("0.00")),
                name="loan_application_recommended_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(eligible_amount_at_application__gte=Decimal("0.00")),
                name="loan_application_eligible_amount_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(savings_balance_at_application__gte=Decimal("0.00")),
                name="loan_application_savings_snapshot_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(repayment_history_score__gte=Decimal("0.00"))
                & Q(repayment_history_score__lte=Decimal("100.00")),
                name="loan_application_repayment_score_between_0_100",
            ),
            models.CheckConstraint(
                condition=Q(contribution_consistency_score__gte=Decimal("0.00"))
                & Q(contribution_consistency_score__lte=Decimal("100.00")),
                name="loan_application_consistency_score_between_0_100",
            ),
            models.CheckConstraint(
                condition=Q(installment_estimate__gte=Decimal("0.00")),
                name="loan_application_installment_estimate_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(total_repayment_estimate__gte=Decimal("0.00")),
                name="loan_application_total_repayment_estimate_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(loan_multiplier_at_application__gte=Decimal("0.00")),
                name="loan_application_multiplier_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["chama", "status", "submitted_at"]),
            models.Index(fields=["member", "status", "submitted_at"]),
            models.Index(fields=["status", "submitted_at"]),
            models.Index(fields=["loan_product", "status"]),
        ]
        ordering = ["-submitted_at", "-created_at"]

    def __str__(self) -> str:
        return f"LoanApplication {self.requested_amount} - {self.member}"


class Loan(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama", on_delete=models.CASCADE, related_name="loans"
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="loans",
    )
    loan_product = models.ForeignKey(
        LoanProduct,
        on_delete=models.PROTECT,
        related_name="loans",
        null=True,
        blank=True,
    )
    purpose = models.TextField(blank=True)
    principal = models.DecimalField(max_digits=12, decimal_places=2)
    outstanding_principal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    outstanding_interest = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    outstanding_penalty = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    total_due = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    interest_type = models.CharField(
        max_length=20,
        choices=LoanInterestType.choices,
        default=LoanInterestType.FLAT,
    )
    interest_rate = models.DecimalField(max_digits=5, decimal_places=2)
    duration_months = models.PositiveIntegerField()
    grace_period_days = models.PositiveIntegerField(default=0)
    late_penalty_type = models.CharField(
        max_length=20,
        choices=LoanPenaltyType.choices,
        default=LoanPenaltyType.FIXED,
    )
    late_penalty_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    early_repayment_discount_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    eligibility_status = models.CharField(
        max_length=20,
        choices=LoanEligibilityStatus.choices,
        default=LoanEligibilityStatus.ELIGIBLE,
    )
    eligibility_reason = models.TextField(blank=True)
    recommended_max_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    status = models.CharField(
        max_length=30,
        choices=LoanStatus.choices,
        default=LoanStatus.REQUESTED,
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    defaulted_at = models.DateTimeField(null=True, blank=True)
    repaid_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_loans",
    )
    disbursed_at = models.DateTimeField(null=True, blank=True)
    disbursement_reference = models.CharField(max_length=120, blank=True)
    rejection_reason = models.TextField(blank=True)
    disbursed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="disbursed_loans",
    )

    # Escalation workflow fields
    escalation_level = models.CharField(
        max_length=20,
        choices=[
            ("none", "None"),
            ("reminder", "Reminder"),
            ("warning", "Warning"),
            ("escalated", "Escalated"),
            ("recovery", "Recovery"),
        ],
        default="none",
        help_text="Current escalation level for overdue loan"
    )
    escalation_started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When escalation process started"
    )
    last_reminder_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When last reminder was sent to borrower"
    )
    last_escalation_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When last escalation was sent to admin/treasurer"
    )
    recovery_meeting_scheduled = models.BooleanField(
        default=False,
        help_text="Whether recovery meeting has been scheduled"
    )
    recovery_meeting_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date of recovery meeting"
    )
    recovery_notes = models.TextField(
        blank=True,
        help_text="Manual recovery notes"
    )
    recovery_officer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="recovery_loans",
        help_text="Officer assigned to recovery"
    )

    # Final status tracking
    final_status = models.CharField(
        max_length=30,
        choices=[
            ("active", "Active"),
            ("repaid", "Repaid"),
            ("restructured", "Restructured"),
            ("written_off", "Written Off"),
            ("defaulted_recovering", "Defaulted Recovering"),
            ("defaulted_unrecovered", "Defaulted Unrecovered"),
        ],
        default="active",
        help_text="Final status of the loan"
    )
    final_status_date = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When final status was set"
    )
    final_status_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="final_status_loans",
        help_text="User who set the final status"
    )
    write_off_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Amount written off"
    )
    write_off_reason = models.TextField(
        blank=True,
        help_text="Reason for write-off"
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(principal__gt=Decimal("0.00")),
                name="loan_principal_positive",
            ),
            models.CheckConstraint(
                condition=Q(interest_rate__gte=Decimal("0.00")),
                name="loan_interest_rate_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(duration_months__gt=0),
                name="loan_duration_positive",
            ),
            models.CheckConstraint(
                condition=Q(late_penalty_value__gte=Decimal("0.00")),
                name="loan_late_penalty_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(early_repayment_discount_percent__gte=Decimal("0.00"))
                & Q(early_repayment_discount_percent__lte=Decimal("100.00")),
                name="loan_early_discount_between_0_100",
            ),
            models.CheckConstraint(
                condition=Q(recommended_max_amount__gte=Decimal("0.00")),
                name="loan_recommended_max_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(outstanding_principal__gte=Decimal("0.00")),
                name="loan_outstanding_principal_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(outstanding_interest__gte=Decimal("0.00")),
                name="loan_outstanding_interest_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(outstanding_penalty__gte=Decimal("0.00")),
                name="loan_outstanding_penalty_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(total_due__gte=Decimal("0.00")),
                name="loan_total_due_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["chama", "status"]),
            models.Index(fields=["member", "status"]),
            models.Index(fields=["loan_product", "status"]),
            models.Index(fields=["requested_at"]),
            models.Index(fields=["chama", "due_date"]),
            models.Index(fields=["status", "due_date"]),
            models.Index(fields=["escalation_level"]),
            models.Index(fields=["chama", "escalation_level"]),
            models.Index(fields=["recovery_meeting_scheduled"]),
            models.Index(fields=["recovery_officer"]),
            models.Index(fields=["final_status"]),
            models.Index(fields=["chama", "final_status"]),
            models.Index(fields=["final_status_date"]),
        ]
        ordering = ["-requested_at"]

    def __str__(self) -> str:
        return f"Loan {self.principal} - {self.member}"


class LoanGuarantor(BaseModel):
    loan = models.ForeignKey(
        Loan,
        on_delete=models.CASCADE,
        related_name="guarantors",
    )
    guarantor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="loan_guarantees",
    )
    guaranteed_amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(
        max_length=20,
        choices=LoanGuarantorStatus.choices,
        default=LoanGuarantorStatus.PROPOSED,
    )
    review_note = models.TextField(blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    
    # Notification and exposure tracking
    notified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When guarantor was notified about loan status"
    )
    exposure_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Current exposure amount for this guarantor"
    )
    recovery_triggered = models.BooleanField(
        default=False,
        help_text="Whether recovery has been triggered for this guarantor"
    )
    recovery_triggered_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When recovery was triggered"
    )
    recovery_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Amount recovered from guarantor"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["loan", "guarantor"],
                name="uniq_loan_guarantor_per_loan",
            ),
            models.CheckConstraint(
                condition=Q(guaranteed_amount__gt=Decimal("0.00")),
                name="loan_guarantor_amount_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["loan", "status"]),
            models.Index(fields=["guarantor", "status"]),
            models.Index(fields=["recovery_triggered"]),
            models.Index(fields=["guarantor", "recovery_triggered"]),
        ]
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.loan_id}:{self.guarantor_id}:{self.status}"


class LoanApplicationGuarantor(BaseModel):
    loan_application = models.ForeignKey(
        LoanApplication,
        on_delete=models.CASCADE,
        related_name="guarantors",
    )
    guarantor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="loan_application_guarantees",
    )
    guaranteed_amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(
        max_length=20,
        choices=LoanGuarantorStatus.choices,
        default=LoanGuarantorStatus.PROPOSED,
    )
    review_note = models.TextField(blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["loan_application", "guarantor"],
                name="uniq_loan_application_guarantor",
            ),
            models.CheckConstraint(
                condition=Q(guaranteed_amount__gt=Decimal("0.00")),
                name="loan_application_guaranteed_amount_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["loan_application", "status"]),
            models.Index(fields=["guarantor", "status"]),
        ]
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.loan_application_id}:{self.guarantor_id}:{self.status}"


class LoanTopUpRequest(BaseModel):
    loan = models.ForeignKey(
        Loan,
        on_delete=models.CASCADE,
        related_name="topup_requests",
    )
    requested_amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=LoanTopUpStatus.choices,
        default=LoanTopUpStatus.REQUESTED,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_loan_topups",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)
    created_loan = models.ForeignKey(
        Loan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_topup_requests",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(requested_amount__gt=Decimal("0.00")),
                name="loan_topup_requested_amount_positive",
            )
        ]
        indexes = [
            models.Index(fields=["loan", "status", "created_at"]),
            models.Index(fields=["status", "created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Topup {self.loan_id} ({self.status})"


class LoanRestructureRequest(BaseModel):
    loan = models.ForeignKey(
        Loan,
        on_delete=models.CASCADE,
        related_name="restructure_requests",
    )
    requested_duration_months = models.PositiveIntegerField()
    requested_interest_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    reason = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=LoanRestructureStatus.choices,
        default=LoanRestructureStatus.REQUESTED,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_loan_restructures",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(requested_duration_months__gt=0),
                name="loan_restructure_duration_positive",
            ),
            models.CheckConstraint(
                condition=Q(requested_interest_rate__isnull=True)
                | Q(requested_interest_rate__gte=Decimal("0.00")),
                name="loan_restructure_interest_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["loan", "status", "created_at"]),
            models.Index(fields=["status", "created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Restructure {self.loan_id} ({self.status})"


class LoanRestructure(BaseModel):
    loan = models.ForeignKey(
        Loan,
        on_delete=models.CASCADE,
        related_name="restructures",
    )
    source_request = models.ForeignKey(
        LoanRestructureRequest,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="applied_restructures",
    )
    old_terms_snapshot = models.JSONField(default=dict, blank=True)
    new_terms_snapshot = models.JSONField(default=dict, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_loan_restructures",
    )
    approved_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["loan", "approved_at"]),
        ]
        ordering = ["-approved_at", "-created_at"]

    def __str__(self) -> str:
        return f"Applied restructure {self.loan_id} @ {self.approved_at}"


class InstallmentSchedule(BaseModel):
    loan = models.ForeignKey(
        Loan, on_delete=models.CASCADE, related_name="installments"
    )
    due_date = models.DateField()
    expected_amount = models.DecimalField(max_digits=12, decimal_places=2)
    expected_principal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    expected_interest = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    expected_penalty = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    paid_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    paid_principal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    paid_interest = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    paid_penalty = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=InstallmentStatus.choices,
        default=InstallmentStatus.DUE,
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(expected_amount__gt=Decimal("0.00")),
                name="installment_expected_amount_positive",
            ),
            models.CheckConstraint(
                condition=Q(expected_principal__gte=Decimal("0.00")),
                name="installment_expected_principal_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(expected_interest__gte=Decimal("0.00")),
                name="installment_expected_interest_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(expected_penalty__gte=Decimal("0.00")),
                name="installment_expected_penalty_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(paid_amount__gte=Decimal("0.00")),
                name="installment_paid_amount_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(paid_principal__gte=Decimal("0.00")),
                name="installment_paid_principal_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(paid_interest__gte=Decimal("0.00")),
                name="installment_paid_interest_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(paid_penalty__gte=Decimal("0.00")),
                name="installment_paid_penalty_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["loan", "due_date"]),
            models.Index(fields=["status", "due_date"]),
        ]
        ordering = ["due_date", "created_at"]

    def __str__(self) -> str:
        return f"{self.loan_id} installment due {self.due_date}"


class LoanEligibilityCheck(BaseModel):
    loan = models.ForeignKey(
        Loan,
        on_delete=models.CASCADE,
        related_name="eligibility_checks",
    )
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="loan_eligibility_checks",
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="loan_eligibility_checks",
    )
    requested_amount = models.DecimalField(max_digits=12, decimal_places=2)
    recommended_max_amount = models.DecimalField(max_digits=12, decimal_places=2)
    duration_months = models.PositiveIntegerField(default=1)
    status = models.CharField(
        max_length=20,
        choices=LoanEligibilityStatus.choices,
        default=LoanEligibilityStatus.INELIGIBLE,
    )
    reasons = models.JSONField(default=list, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["loan", "created_at"]),
            models.Index(fields=["chama", "member", "created_at"]),
            models.Index(fields=["status", "created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Eligibility {self.member_id} ({self.status})"


class LoanApplicationApproval(BaseModel):
    loan_application = models.ForeignKey(
        LoanApplication,
        on_delete=models.CASCADE,
        related_name="approval_logs",
    )
    stage = models.CharField(max_length=30, choices=LoanApprovalStage.choices)
    decision = models.CharField(
        max_length=20,
        choices=LoanApprovalDecision.choices,
        default=LoanApprovalDecision.PENDING,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="loan_application_approval_logs",
    )
    note = models.TextField(blank=True)
    acted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["loan_application", "stage", "acted_at"]),
            models.Index(fields=["decision", "acted_at"]),
        ]
        ordering = ["acted_at", "created_at"]

    def __str__(self) -> str:
        return f"{self.loan_application_id} {self.stage} {self.decision}"


class LoanApprovalLog(BaseModel):
    loan = models.ForeignKey(
        Loan,
        on_delete=models.CASCADE,
        related_name="approval_logs",
    )
    stage = models.CharField(max_length=30, choices=LoanApprovalStage.choices)
    decision = models.CharField(
        max_length=20,
        choices=LoanApprovalDecision.choices,
        default=LoanApprovalDecision.PENDING,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="loan_approval_logs",
    )
    note = models.TextField(blank=True)
    acted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["loan", "stage", "acted_at"]),
            models.Index(fields=["decision", "acted_at"]),
        ]
        ordering = ["acted_at"]

    def __str__(self) -> str:
        return f"{self.loan_id} {self.stage} {self.decision}"


class LoanAuditLog(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="loan_audit_logs",
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="loan_audit_logs",
    )
    loan_application = models.ForeignKey(
        LoanApplication,
        on_delete=models.CASCADE,
        related_name="audit_logs",
        null=True,
        blank=True,
    )
    loan = models.ForeignKey(
        Loan,
        on_delete=models.CASCADE,
        related_name="audit_logs",
        null=True,
        blank=True,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="performed_loan_audit_logs",
    )
    action = models.CharField(max_length=64)
    status_from = models.CharField(max_length=30, blank=True)
    status_to = models.CharField(max_length=30, blank=True)
    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["chama", "action", "created_at"]),
            models.Index(fields=["member", "action", "created_at"]),
            models.Index(fields=["loan_application", "created_at"]),
            models.Index(fields=["loan", "created_at"]),
        ]
        ordering = ["-created_at", "-updated_at"]

    def __str__(self) -> str:
        return f"{self.action}:{self.loan_application_id or self.loan_id}"


class LoanRecoveryAction(BaseModel):
    loan = models.ForeignKey(
        Loan,
        on_delete=models.CASCADE,
        related_name="recovery_actions",
    )
    action_type = models.CharField(
        max_length=40,
        choices=LoanRecoveryActionType.choices,
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    
    # Additional tracking fields
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="performed_recovery_actions",
        help_text="User who performed the action"
    )
    guarantor = models.ForeignKey(
        "LoanGuarantor",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="recovery_actions",
        help_text="Guarantor involved in recovery action"
    )
    offset_from_savings = models.BooleanField(
        default=False,
        help_text="Whether action involved offsetting from savings"
    )
    offset_from_contributions = models.BooleanField(
        default=False,
        help_text="Whether action involved offsetting from contributions"
    )

    class Meta:
        indexes = [
            models.Index(fields=["loan", "action_type", "created_at"]),
            models.Index(fields=["action_type", "created_at"]),
            models.Index(fields=["performed_by"]),
            models.Index(fields=["guarantor"]),
            models.Index(fields=["offset_from_savings"]),
            models.Index(fields=["offset_from_contributions"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.loan_id} {self.action_type}"


class Repayment(BaseModel):
    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name="repayments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date_paid = models.DateField()
    method = models.CharField(
        max_length=20,
        choices=MethodChoices.choices,
        default=MethodChoices.MPESA,
    )
    receipt_code = models.CharField(max_length=100, unique=True)
    allocation_breakdown = models.JSONField(default=dict, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_repayments",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="repayment_amount_positive",
            )
        ]
        indexes = [
            models.Index(fields=["loan", "date_paid"]),
            models.Index(fields=["receipt_code"]),
        ]
        ordering = ["-date_paid", "-created_at"]

    def __str__(self) -> str:
        return f"Repayment {self.amount} - {self.loan_id}"


class Penalty(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="penalties",
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="penalties",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    amount_paid = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    reason = models.TextField()
    due_date = models.DateField()
    status = models.CharField(
        max_length=20,
        choices=PenaltyStatus.choices,
        default=PenaltyStatus.UNPAID,
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_penalties",
    )
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_penalties",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="penalty_amount_positive",
            ),
            models.CheckConstraint(
                condition=Q(amount_paid__gte=Decimal("0.00")),
                name="penalty_amount_paid_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(amount_paid__lte=models.F("amount")),
                name="penalty_amount_paid_lte_amount",
            ),
        ]
        indexes = [
            models.Index(fields=["chama", "status"]),
            models.Index(fields=["member", "status"]),
            models.Index(fields=["due_date"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Penalty {self.amount} - {self.member}"

    @property
    def outstanding_amount(self) -> Decimal:
        return max(Decimal(self.amount) - Decimal(self.amount_paid), Decimal("0.00"))


class LedgerEntry(BaseModel):
    """
    LedgerTransaction - immutable record of money movements.
    
    Golden rules:
    - Never update balances directly from UI. Only from successful ledger transactions.
    - Always use idempotency_key to prevent double entries.
    - Use atomic transactions when finalizing.
    """
    # Reference to wallet (new field - replaces chama as primary)
    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.CASCADE,
        related_name="ledger_entries",
        null=True,
        blank=True,
    )
    journal_entry = models.ForeignKey(
        JournalEntry,
        on_delete=models.PROTECT,
        related_name="lines",
        null=True,
        blank=True,
    )
    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="ledger_entries",
        null=True,
        blank=True,
    )
    # Keep chama for backward compatibility
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="ledger_entries",
    )
    
    # Transaction type and direction
    entry_type = models.CharField(max_length=30, choices=LedgerEntryType.choices)
    direction = models.CharField(max_length=10, choices=LedgerDirection.choices)
    
    # Amount
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    debit = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    credit = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    currency = models.CharField(
        max_length=3,
        choices=CurrencyChoices.choices,
        default=CurrencyChoices.KES,
    )
    
    # Status (new field)
    status = models.CharField(
        max_length=20,
        choices=LedgerStatus.choices,
        default=LedgerStatus.SUCCESS,
    )
    
    # Provider info (new field)
    provider = models.CharField(
        max_length=20,
        choices=[("mpesa", "M-Pesa"), ("internal", "Internal")],
        default="internal",
    )
    provider_reference = models.CharField(max_length=100, blank=True)
    
    # Idempotency - prevents double entries
    idempotency_key = models.CharField(max_length=100)
    
    # Related objects (new fields)
    related_payment = models.ForeignKey(
        "payments.PaymentIntent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )
    related_payout = models.ForeignKey(
        "payments.MpesaB2CPayout",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )
    related_loan = models.ForeignKey(
        "Loan",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )
    
    # Metadata
    meta = models.JSONField(default=dict, blank=True)
    
    # Reversal support
    reversal_of = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reversal_entries",
    )
    narration = models.TextField()
    is_immutable = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["chama", "idempotency_key"],
                name="uniq_ledger_idempotency_per_chama",
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="ledger_amount_positive",
            ),
            models.CheckConstraint(
                condition=Q(journal_entry__isnull=True)
                | (
                    (Q(debit__gt=Decimal("0.00")) & Q(credit=Decimal("0.00")))
                    | (Q(credit__gt=Decimal("0.00")) & Q(debit=Decimal("0.00")))
                ),
                name="ledger_exactly_one_side_populated",
            ),
            models.CheckConstraint(
                condition=~Q(id=models.F("reversal_of")),
                name="ledger_reversal_not_self",
            ),
        ]
        indexes = [
            models.Index(fields=["chama", "created_at"]),
            models.Index(fields=["entry_type", "created_at"]),
            models.Index(fields=["reversal_of"]),
            models.Index(fields=["idempotency_key"]),
            models.Index(fields=["provider_reference"]),
            models.Index(fields=["wallet", "status"]),
            models.Index(fields=["journal_entry", "account"]),
            models.Index(fields=["account", "created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.entry_type} {self.direction} {self.amount}"

    def reverse(self, reason: str, actor):
        """Create a reversal entry for this transaction."""
        from core.audit import create_audit_log
        
        reversal = LedgerEntry.objects.create(
            wallet=self.wallet,
            chama=self.chama,
            entry_type=self.entry_type,
            direction=LedgerDirection.CREDIT if self.direction == LedgerDirection.DEBIT else LedgerDirection.DEBIT,
            amount=self.amount,
            currency=self.currency,
            status=LedgerStatus.SUCCESS,
            provider="internal",
            idempotency_key=f"reversal:{self.idempotency_key}",
            reversal_of=self,
            narration=f"Reversal: {reason}",
            meta={"reversed_by": str(actor.id) if actor else None},
        )
        
        # Create audit log
        create_audit_log(
            action="ledger_reversal",
            object_type="LedgerEntry",
            object_id=str(self.id),
            changes={
                "original": str(self.id),
                "reversal": str(reversal.id),
                "reason": reason,
            },
            actor=actor,
        )
        
        return reversal


class ChamaFinancialSnapshot(BaseModel):
    """
    Persisted summary of chama-wide finance metrics for fast reads.

    This is updated on ledger writes and key finance state changes so
    dashboards and AI lookups do not need to rescan ledger rows on every request.
    """

    chama = models.OneToOneField(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="financial_snapshot",
    )
    summary_date = models.DateField(default=timezone.localdate, db_index=True)

    cash_in_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    cash_out_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    contributions_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    withdrawals_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    loan_disbursements_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    loan_repayments_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    penalties_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    fees_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    adjustments_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    outstanding_loans_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    active_loan_count = models.PositiveIntegerField(default=0)
    overdue_loan_count = models.PositiveIntegerField(default=0)
    unpaid_penalties_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    unpaid_penalties_count = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(
                fields=["summary_date"],
                name="finance_cha_summary_1ac492_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"Snapshot {self.chama_id} @ {self.summary_date}"


class FinancialSnapshot(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="financial_snapshots",
    )
    snapshot_date = models.DateField(db_index=True)
    total_balance = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    total_contributions = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    total_loans = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    total_expenses = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["chama", "snapshot_date"],
                name="uniq_financial_snapshot_per_chama_day",
            ),
        ]
        indexes = [
            models.Index(fields=["chama", "snapshot_date"]),
        ]
        ordering = ["-snapshot_date", "-created_at"]

    def __str__(self) -> str:
        return f"{self.chama.name} snapshot {self.snapshot_date}"


class ExpenseCategory(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="expense_categories",
    )
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["chama", "name"],
                name="uniq_expense_category_name_per_chama",
            ),
        ]
        indexes = [
            models.Index(fields=["chama", "is_active"]),
            models.Index(fields=["chama", "name"]),
        ]
        ordering = ["name", "-created_at"]

    def __str__(self) -> str:
        return f"{self.chama.name} - {self.name}"


class Expense(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="expenses",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_expenses",
    )
    category_ref = models.ForeignKey(
        ExpenseCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expenses",
    )
    description = models.CharField(max_length=255)
    category = models.CharField(max_length=80, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    expense_date = models.DateField(default=timezone.localdate)
    status = models.CharField(
        max_length=20,
        choices=ExpenseStatus.choices,
        default=ExpenseStatus.PENDING,
    )
    vendor_name = models.CharField(max_length=120, blank=True)
    receipt_file = models.FileField(
        upload_to="finance/expense-receipts/",
        null=True,
        blank=True,
    )
    receipt_reference = models.CharField(max_length=120, blank=True)
    payment_reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    journal_entry = models.OneToOneField(
        JournalEntry,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expense_record",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_expenses",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rejected_expenses",
    )
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="paid_expenses",
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="expense_amount_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["chama", "expense_date"]),
            models.Index(fields=["chama", "status"]),
            models.Index(fields=["category", "expense_date"]),
        ]
        ordering = ["-expense_date", "-created_at"]

    def __str__(self) -> str:
        return f"{self.chama.name} expense {self.amount}"


class ManualAdjustment(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="manual_adjustments",
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    direction = models.CharField(max_length=10, choices=LedgerDirection.choices)
    reason = models.TextField()
    idempotency_key = models.CharField(  # noqa: DJ001
        max_length=100, null=True, blank=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["chama", "idempotency_key"],
                name="uniq_manual_adjustment_idempotency_per_chama",
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="manual_adjustment_amount_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["chama", "created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.chama.name} {self.direction} {self.amount}"


class MonthClosure(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="month_closures",
    )
    month = models.DateField(help_text="First day of month")
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="closed_finance_months",
    )
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["chama", "month"],
                name="uniq_month_closure_per_chama",
            )
        ]
        indexes = [
            models.Index(fields=["chama", "month"]),
        ]
        ordering = ["-month"]

    def __str__(self) -> str:
        return f"{self.chama.name} {self.month:%Y-%m}"


class DailyAggregate(BaseModel):
    """
    Precomputed daily aggregates for fast AI queries.
    
    Updated daily via scheduled task to avoid SUM() on large tables.
    Stores: cash-in, cash-out, arrears counts, member counts, etc.
    """
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="daily_aggregates",
    )
    date = models.DateField(help_text="Date for this aggregate")
    
    # Cash flow
    total_contributions = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_withdrawals = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_disbursed_loans = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_loan_repayments = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_fines = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    
    # Counts
    contribution_count = models.PositiveIntegerField(default=0)
    withdrawal_count = models.PositiveIntegerField(default=0)
    active_member_count = models.PositiveIntegerField(default=0)
    active_loan_count = models.PositiveIntegerField(default=0)
    overdue_loan_count = models.PositiveIntegerField(default=0)
    unpaid_member_count = models.PositiveIntegerField(default=0)
    
    # Computed
    net_cashflow = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["chama", "date"],
                name="uniq_daily_aggregate_per_chama",
            )
        ]
        indexes = [
            models.Index(
                fields=["chama", "-date"],
                name="finance_dai_chama_i_2bbb7b_idx",
            ),
        ]
        ordering = ["-date"]

    def __str__(self) -> str:
        return f"{self.chama.name} {self.date} (KES {self.net_cashflow})"


def get_cached_daily_aggregate(chama_id, date=None):
    """
    Get cached daily aggregate for a chama.
    Falls back to computing from scratch if not available.
    """
    from django.core.cache import cache
    from django.utils import timezone
    
    if date is None:
        date = timezone.now().date()
    
    cache_key = f"daily_aggregate:{chama_id}:{date}"
    
    # Try cache first
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    
    # Try database
    aggregate = DailyAggregate.objects.filter(
        chama_id=chama_id,
        date=date,
    ).first()
    
    if aggregate:
        result = {
            "total_contributions": float(aggregate.total_contributions),
            "total_withdrawals": float(aggregate.total_withdrawals),
            "net_cashflow": float(aggregate.net_cashflow),
            "active_member_count": aggregate.active_member_count,
            "active_loan_count": aggregate.active_loan_count,
            "unpaid_member_count": aggregate.unpaid_member_count,
        }
        cache.set(cache_key, result, 300)  # Cache for 5 minutes
        return result
    
    return None
