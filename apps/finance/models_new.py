"""
Additional finance models for wallet transfers, chama payments, and loan updates.
These are appended to the main finance/models.py
"""

from decimal import Decimal
from django.db import models
from django.conf import settings
from django.utils import timezone
from django.db.models import Q
from core.models import BaseModel, CurrencyChoices


class WalletTransferStatus(models.TextChoices):
    """Status for wallet transfers between members."""
    INITIATED = "initiated", "Initiated"
    PENDING = "pending", "Pending"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class WalletTransfer(BaseModel):
    """
    Peer-to-peer wallet transfers between members of the same chama.
    
    Transfers move funds from one member wallet to another within the same chama.
    All transfers create ledger entries for auditability.
    """
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="wallet_transfers",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_wallet_transfers",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="received_wallet_transfers",
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(
        max_length=3,
        choices=CurrencyChoices.choices,
        default=CurrencyChoices.KES,
    )
    reference = models.CharField(max_length=100, unique=True, db_index=True)
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=WalletTransferStatus.choices,
        default=WalletTransferStatus.INITIATED,
    )
    
    # Ledger linkage
    ledger_entry = models.ForeignKey(
        "LedgerEntry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="wallet_transfers",
    )
    
    # Timestamps
    requested_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    # Failure info
    failure_reason = models.TextField(blank=True)
    
    # Metadata
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="wallet_transfer_amount_positive",
            ),
            models.CheckConstraint(
                condition=~Q(sender=models.F("recipient")),
                name="wallet_transfer_sender_not_recipient",
            ),
        ]
        indexes = [
            models.Index(fields=["chama", "status", "requested_at"]),
            models.Index(fields=["sender", "status", "requested_at"]),
            models.Index(fields=["recipient", "status", "requested_at"]),
            models.Index(fields=["reference"]),
            models.Index(fields=["status"]),
        ]
        ordering = ["-requested_at", "-created_at"]

    def __str__(self) -> str:
        return f"Transfer {self.amount} from {self.sender} to {self.recipient}"


class ChamaPaymentStatus(models.TextChoices):
    """Status for member-to-chama wallet payments/contributions."""
    INITIATED = "initiated", "Initiated"
    PENDING = "pending", "Pending"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class ChamaPayment(BaseModel):
    """
    Member wallet contribution to chama collective pool.
    
    Transfers funds from member wallet to chama wallet.
    Creates a Contribution record and ledger entries.
    """
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="wallet_payments",
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chama_wallet_payments",
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(
        max_length=3,
        choices=CurrencyChoices.choices,
        default=CurrencyChoices.KES,
    )
    reference = models.CharField(max_length=100, unique=True, db_index=True)
    
    # Optional contribution type
    contribution_type = models.ForeignKey(
        "ContributionType",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="wallet_payments",
    )
    
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=ChamaPaymentStatus.choices,
        default=ChamaPaymentStatus.INITIATED,
    )
    
    # Ledger linkage
    ledger_entry = models.ForeignKey(
        "LedgerEntry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chama_payments",
    )
    
    # Contribution record linkage
    contribution = models.ForeignKey(
        "Contribution",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="wallet_payment_source",
    )
    
    # Timestamps
    requested_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    # Failure info
    failure_reason = models.TextField(blank=True)
    
    # Metadata
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="chama_payment_amount_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["chama", "status", "requested_at"]),
            models.Index(fields=["member", "status", "requested_at"]),
            models.Index(fields=["reference"]),
            models.Index(fields=["status"]),
            models.Index(fields=["contribution_type", "requested_at"]),
        ]
        ordering = ["-requested_at", "-created_at"]

    def __str__(self) -> str:
        return f"Payment {self.amount} from {self.member} to {self.chama}"


class LoanUpdateRequest(BaseModel):
    """
    Request to update loan terms (amount, duration, interest rate).
    
    Only allowed for loans that haven't been disbursed yet.
    Requires treasurer/admin approval.
    """
    loan = models.ForeignKey(
        "Loan",
        on_delete=models.CASCADE,
        related_name="update_requests",
    )
    
    # Requested changes
    requested_principal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="New principal amount (if changing)"
    )
    requested_duration_months = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="New duration in months (if changing)"
    )
    requested_interest_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="New interest rate (if changing)"
    )
    
    reason = models.TextField(help_text="Reason for update request")
    
    # Old values snapshot
    old_principal = models.DecimalField(max_digits=12, decimal_places=2)
    old_duration_months = models.PositiveIntegerField()
    old_interest_rate = models.DecimalField(max_digits=5, decimal_places=2)
    
    # Review
    status = models.CharField(
        max_length=20,
        choices=[
            ("requested", "Requested"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
            ("applied", "Applied"),
        ],
        default="requested",
    )
    
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_loan_updates",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)
    
    # Applied changes tracking
    applied_at = models.DateTimeField(null=True, blank=True)
    applied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="applied_loan_updates",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(requested_principal__isnull=False) |
                    Q(requested_duration_months__isnull=False) |
                    Q(requested_interest_rate__isnull=False)
                ),
                name="loan_update_at_least_one_field",
            ),
            models.CheckConstraint(
                condition=Q(old_principal__gt=Decimal("0.00")),
                name="loan_update_old_principal_positive",
            ),
            models.CheckConstraint(
                condition=Q(old_duration_months__gt=0),
                name="loan_update_old_duration_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["loan", "status", "created_at"]),
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["reviewed_by"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"LoanUpdate {self.loan_id} ({self.status})"

    def is_editable(self) -> bool:
        """Check if this request can still be edited."""
        return self.status == "requested"
