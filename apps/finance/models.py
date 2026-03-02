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
    PAID = "paid", "Paid"
    CLOSED = "closed", "Closed"
    CLEARED = "cleared", "Cleared"
    DEFAULTED = "defaulted", "Defaulted"
    REJECTED = "rejected", "Rejected"


class LoanPenaltyType(models.TextChoices):
    FIXED = "fixed", "Fixed Amount"
    PERCENTAGE = "percentage", "Percentage"


class LoanEligibilityStatus(models.TextChoices):
    ELIGIBLE = "eligible", "Eligible"
    INELIGIBLE = "ineligible", "Ineligible"


class LoanApprovalStage(models.TextChoices):
    TREASURER_REVIEW = "treasurer_review", "Treasurer Review"
    ADMIN_APPROVAL = "admin_approval", "Admin Approval"


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
    DUE = "due", "Due"
    PAID = "paid", "Paid"
    OVERDUE = "overdue", "Overdue"


class PenaltyStatus(models.TextChoices):
    UNPAID = "unpaid", "Unpaid"
    PAID = "paid", "Paid"
    WAIVED = "waived", "Waived"


class LedgerEntryType(models.TextChoices):
    CONTRIBUTION = "contribution", "Contribution"
    WALLET_TOPUP = "wallet_topup", "Wallet Top-up"
    LOAN_DISBURSEMENT = "loan_disbursement", "Loan Disbursement"
    LOAN_REPAYMENT = "loan_repayment", "Loan Repayment"
    WITHDRAWAL = "withdrawal", "Withdrawal"
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
    owner_id = models.PositiveIntegerField()
    
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

    @property
    def total_balance(self) -> Decimal:
        return self.available_balance + self.locked_balance


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

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="contribution_amount_positive",
            )
        ]
        indexes = [
            models.Index(fields=["chama", "date_paid"]),
            models.Index(fields=["member", "date_paid"]),
            models.Index(fields=["receipt_code"]),
        ]
        ordering = ["-date_paid", "-created_at"]

    def __str__(self) -> str:
        return f"{self.member} - {self.amount}"


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
    principal = models.DecimalField(max_digits=12, decimal_places=2)
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
        max_length=20,
        choices=LoanStatus.choices,
        default=LoanStatus.REQUESTED,
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_loans",
    )
    disbursed_at = models.DateTimeField(null=True, blank=True)
    disbursement_reference = models.CharField(max_length=120, blank=True)
    disbursed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="disbursed_loans",
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
        ]
        indexes = [
            models.Index(fields=["chama", "status"]),
            models.Index(fields=["member", "status"]),
            models.Index(fields=["loan_product", "status"]),
            models.Index(fields=["requested_at"]),
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
    accepted_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)

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
        ]
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.loan_id}:{self.guarantor_id}:{self.status}"


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
            )
        ]
        indexes = [
            models.Index(fields=["chama", "status"]),
            models.Index(fields=["member", "status"]),
            models.Index(fields=["due_date"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Penalty {self.amount} - {self.member}"


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
