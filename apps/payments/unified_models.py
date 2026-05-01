"""
Unified Payment Models for MyChama.

One payment architecture to rule them all: M-Pesa, Card, Cash, Bank.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.constants import CurrencyChoices
from core.models import BaseModel


class PaymentMethod(models.TextChoices):
    """Supported payment methods."""
    MPESA = "mpesa", "M-Pesa"
    CARD = "card", "Card"
    CASH = "cash", "Cash"
    BANK = "bank", "Bank Transfer"
    WALLET = "wallet", "Wallet"


class PaymentPurpose(models.TextChoices):
    """Payment purpose types."""
    CONTRIBUTION = "contribution", "Contribution"
    FINE = "fine", "Fine"
    LOAN_REPAYMENT = "loan_repayment", "Loan Repayment"
    MEETING_FEE = "meeting_fee", "Meeting Fee"
    SPECIAL_CONTRIBUTION = "special_contribution", "Special Contribution"
    OTHER = "other", "Other"


class PaymentStatus(models.TextChoices):
    """Unified payment status lifecycle."""
    INITIATED = "initiated", "Initiated"
    PENDING = "pending", "Pending"
    PENDING_AUTHENTICATION = "pending_authentication", "Pending Authentication"
    PENDING_VERIFICATION = "pending_verification", "Pending Verification"
    SUCCESS = "success", "Success"
    PARTIALLY_REFUNDED = "partially_refunded", "Partially Refunded"
    REFUNDED = "refunded", "Refunded"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"
    EXPIRED = "expired", "Expired"
    RECONCILED = "reconciled", "Reconciled"


class TransactionStatus(models.TextChoices):
    """Transaction status lifecycle."""
    RECEIVED = "received", "Received"
    VERIFIED = "verified", "Verified"
    PARTIALLY_REFUNDED = "partially_refunded", "Partially Refunded"
    FAILED = "failed", "Failed"
    REVERSED = "reversed", "Reversed"
    REFUNDED = "refunded", "Refunded"


class ReconciliationMismatchType(models.TextChoices):
    """Reconciliation mismatch categories."""

    PROVIDER_VERIFICATION_MISMATCH = "provider_verification_mismatch", "Provider Verification Mismatch"
    DUPLICATE_PROVIDER_REFERENCE = "duplicate_provider_reference", "Duplicate Provider Reference"
    ORPHAN_WEBHOOK = "orphan_webhook", "Orphan Webhook"
    CALLBACK_MISSING = "callback_missing", "Callback Missing"
    WEBHOOK_PROCESSING_ERROR = "webhook_processing_error", "Webhook Processing Error"
    MANUAL_REVIEW = "manual_review", "Manual Review"


class ReconciliationCaseStatus(models.TextChoices):
    """Reconciliation case lifecycle."""

    OPEN = "open", "Open"
    IN_REVIEW = "in_review", "In Review"
    RESOLVED = "resolved", "Resolved"
    DISMISSED = "dismissed", "Dismissed"


class StatementImportStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSED = "processed", "Processed"
    FAILED = "failed", "Failed"


class StatementLineMatchStatus(models.TextChoices):
    MATCHED = "matched", "Matched"
    PENDING_REVIEW = "pending_review", "Pending Review"
    UNMATCHED = "unmatched", "Unmatched"
    MISMATCH = "mismatch", "Mismatch"
    DUPLICATE = "duplicate", "Duplicate"


class SettlementStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    POSTED = "posted", "Posted"
    RECONCILED = "reconciled", "Reconciled"
    CANCELLED = "cancelled", "Cancelled"


def generate_payment_reference() -> str:
    return f"PAY-{uuid.uuid4().hex[:12].upper()}"


def generate_transaction_reference() -> str:
    return f"TXN-{uuid.uuid4().hex[:12].upper()}"


class PaymentIntent(BaseModel):
    """
    Unified Payment Intent Model.
    
    Created before any payment happens, regardless of payment method.
    This is the single source of truth for all payment operations.
    """

    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="payment_intents",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_intents",
    )
    contribution = models.ForeignKey(
        "finance.Contribution",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_intents",
    )

    # Legacy fields retained so historical migrations and older selectors keep working.
    intent_type = models.CharField(max_length=30, blank=True, default="")
    reference_type = models.CharField(max_length=40, blank=True, default="")
    reference_id = models.UUIDField(null=True, blank=True)
    phone = models.CharField(max_length=16, blank=True, default="")
    checkout_request_id = models.CharField(max_length=120, blank=True, default="", db_index=True)
    merchant_request_id = models.CharField(max_length=120, blank=True, default="")
    mpesa_receipt_number = models.CharField(max_length=80, blank=True, default="", db_index=True)
    raw_response = models.JSONField(default=dict, blank=True)

    # Payment details
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(
        max_length=3,
        choices=CurrencyChoices.choices,
        default=CurrencyChoices.KES,
    )
    purpose = models.CharField(
        max_length=30,
        choices=PaymentPurpose.choices,
        default=PaymentPurpose.CONTRIBUTION,
    )
    purpose_id = models.UUIDField(null=True, blank=True, help_text="ID of the purpose object (contribution, fine, loan, etc.)")
    description = models.TextField(blank=True, default="")

    # Payment method
    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
        default=PaymentMethod.MPESA,
    )

    # Provider details
    provider = models.CharField(max_length=50, blank=True, default="", help_text="Payment provider name (stripe, flutterwave, safaricom, etc.)")
    provider_intent_id = models.CharField(max_length=255, blank=True, default="", help_text="Provider's payment intent/session ID")

    # Status tracking
    status = models.CharField(
        max_length=30,
        choices=PaymentStatus.choices,
        default=PaymentStatus.INITIATED,
    )

    # Reference and idempotency
    reference = models.CharField(max_length=100, unique=True, default=generate_payment_reference, help_text="Unique payment reference")
    idempotency_key = models.CharField(max_length=100, unique=True, help_text="Idempotency key to prevent duplicates")

    # Failure tracking
    failure_reason = models.TextField(blank=True, default="")
    failure_code = models.CharField(max_length=50, blank=True, default="")

    # Metadata
    metadata = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    # Timestamps
    initiated_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["chama", "idempotency_key"],
                name="uniq_payment_intent_idempotency_per_chama",
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="payment_intent_amount_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["chama", "status", "payment_method"]),
            models.Index(fields=["user", "status", "created_at"]),
            models.Index(fields=["payment_method", "status"]),
            models.Index(fields=["provider", "status"]),
            models.Index(fields=["reference"]),
            models.Index(fields=["idempotency_key"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.payment_method} {self.amount} {self.currency} ({self.status})"

    def save(self, *args, **kwargs):
        if self.user_id is None and self.created_by_id is not None:
            self.user_id = self.created_by_id
        super().save(*args, **kwargs)

    @property
    def is_terminal(self) -> bool:
        """Check if payment is in a terminal state."""
        return self.status in {
            PaymentStatus.SUCCESS,
            PaymentStatus.PARTIALLY_REFUNDED,
            PaymentStatus.REFUNDED,
            PaymentStatus.FAILED,
            PaymentStatus.CANCELLED,
            PaymentStatus.EXPIRED,
            PaymentStatus.RECONCILED,
        }

    @property
    def is_pending(self) -> bool:
        """Check if payment is in a pending state."""
        return self.status in {
            PaymentStatus.INITIATED,
            PaymentStatus.PENDING,
            PaymentStatus.PENDING_AUTHENTICATION,
            PaymentStatus.PENDING_VERIFICATION,
        }


class PaymentTransaction(BaseModel):
    """
    Unified Payment Transaction Model.
    
    Represents the actual transaction event from the payment provider.
    """

    payment_intent = models.ForeignKey(
        PaymentIntent,
        on_delete=models.CASCADE,
        related_name="transactions",
    )

    # Legacy fields retained for existing services and migrations.
    provider = models.CharField(max_length=20, default="mpesa")
    reference = models.CharField(max_length=120, unique=True, default=generate_transaction_reference)
    provider_response = models.JSONField(default=dict, blank=True)

    # Provider transaction details
    provider_reference = models.CharField(max_length=255, unique=True, db_index=True, default=generate_transaction_reference, help_text="Provider's transaction reference")
    provider_name = models.CharField(max_length=50, default="manual", help_text="Provider name (stripe, flutterwave, safaricom, etc.)")
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.MPESA)

    # Transaction details
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, choices=CurrencyChoices.choices, default=CurrencyChoices.KES)
    status = models.CharField(max_length=30, choices=TransactionStatus.choices, default=TransactionStatus.RECEIVED)

    # Payer reference (phone, card last4, bank ref, etc.)
    payer_reference = models.CharField(max_length=100, blank=True, default="", help_text="Payer reference (phone, card last4, bank ref)")

    # Provider response
    raw_response = models.JSONField(default=dict, blank=True)

    # Verification
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_transactions",
    )

    # Timestamps
    failed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="payment_transaction_amount_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["payment_intent", "status", "created_at"]),
            models.Index(fields=["provider_name", "status"]),
            models.Index(fields=["payment_method", "status"]),
            models.Index(fields=["provider_reference"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.provider_name} {self.provider_reference} ({self.status})"


class PaymentReceipt(BaseModel):
    """
    Unified Payment Receipt Model.
    
    Generated after successful payment, regardless of payment method.
    """

    payment_intent = models.OneToOneField(
        PaymentIntent,
        on_delete=models.CASCADE,
        related_name="receipt",
    )
    transaction = models.OneToOneField(
        PaymentTransaction,
        on_delete=models.CASCADE,
        related_name="receipt",
    )

    # Receipt details
    receipt_number = models.CharField(max_length=100, unique=True, db_index=True)
    reference_number = models.CharField(max_length=100, unique=True, db_index=True)

    # Payment details
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, choices=CurrencyChoices.choices)
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices)

    # Issued details
    issued_at = models.DateTimeField(default=timezone.now)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_receipts",
    )

    # Metadata
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["receipt_number"]),
            models.Index(fields=["reference_number"]),
            models.Index(fields=["issued_at"]),
        ]
        ordering = ["-issued_at"]

    def __str__(self) -> str:
        return f"Receipt {self.receipt_number} ({self.amount} {self.currency})"

    def save(self, *args, **kwargs):
        if not self.receipt_number:
            self.receipt_number = f"RCP-{uuid.uuid4().hex[:12].upper()}"
        if not self.reference_number:
            self.reference_number = f"PAY-{uuid.uuid4().hex[:12].upper()}"
        super().save(*args, **kwargs)


def generate_receipt_download_token() -> str:
    # 64 hex chars; short-lived and single-use.
    return uuid.uuid4().hex + uuid.uuid4().hex


class PaymentReceiptDownloadToken(BaseModel):
    """
    Short-lived, single-use token for downloading a receipt PDF.

    This is used for mobile clients that need a URL they can open in a browser
    (e.g., via expo-web-browser) without passing Authorization headers.
    """

    token = models.CharField(
        max_length=64,
        unique=True,
        default=generate_receipt_download_token,
        db_index=True,
    )
    payment_intent = models.ForeignKey(
        PaymentIntent,
        on_delete=models.CASCADE,
        related_name="receipt_download_tokens",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="receipt_download_tokens",
    )
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["payment_intent", "expires_at"]),
            models.Index(fields=["requested_by", "expires_at"]),
            models.Index(fields=["consumed_at"]),
        ]

    def __str__(self) -> str:
        return f"ReceiptDownloadToken({self.payment_intent_id})"


class PaymentWebhook(BaseModel):
    """
    Unified Payment Webhook Model.
    
    Stores webhook events received from all payment providers.
    """

    provider = models.CharField(max_length=50, help_text="Provider name (stripe, flutterwave, safaricom, etc.)")
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices)
    event_type = models.CharField(max_length=100, db_index=True)
    provider_reference = models.CharField(max_length=255, blank=True, db_index=True)

    # Webhook verification
    signature_valid = models.BooleanField(null=True, blank=True)
    signature = models.CharField(max_length=255, blank=True)

    # Payload
    payload = models.JSONField(default=dict, blank=True)
    headers = models.JSONField(default=dict, blank=True)

    # Processing
    processed = models.BooleanField(default=False)
    processed_at = models.DateTimeField(null=True, blank=True)
    processing_error = models.TextField(blank=True)

    # Source
    source_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["provider", "event_type", "created_at"]),
            models.Index(fields=["payment_method", "event_type"]),
            models.Index(fields=["provider_reference"]),
            models.Index(fields=["processed", "created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Webhook {self.provider} {self.event_type} @ {self.created_at}"


class ManualPaymentApprovalPolicy(BaseModel):
    """Chama-level policy for cash and bank verification workflows."""

    chama = models.OneToOneField(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="manual_payment_policy",
    )
    cash_maker_checker_enabled = models.BooleanField(default=True)
    bank_maker_checker_enabled = models.BooleanField(default=True)
    block_payer_self_approval = models.BooleanField(default=True)
    require_cash_receipt_number = models.BooleanField(default=False)
    require_cash_proof = models.BooleanField(default=False)
    require_bank_proof_document = models.BooleanField(default=True)
    require_bank_transfer_reference = models.BooleanField(default=True)
    dual_approval_threshold = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    allowed_cash_recorder_roles = models.JSONField(default=list, blank=True)
    allowed_cash_verifier_roles = models.JSONField(default=list, blank=True)
    allowed_bank_verifier_roles = models.JSONField(default=list, blank=True)
    allowed_reconciliation_roles = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Manual Payment Approval Policy"
        verbose_name_plural = "Manual Payment Approval Policies"

    def __str__(self) -> str:
        return f"Manual payment policy for {self.chama_id}"


class PaymentAuditLog(BaseModel):
    """
    Unified Payment Audit Log Model.
    
    Tracks all state changes on all payment intents.
    """

    payment_intent = models.ForeignKey(
        PaymentIntent,
        on_delete=models.CASCADE,
        related_name="audit_logs",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_audit_logs",
    )

    # Event details
    event = models.CharField(max_length=100)
    previous_status = models.CharField(max_length=30, blank=True)
    new_status = models.CharField(max_length=30, blank=True)

    # Metadata
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["payment_intent", "created_at"]),
            models.Index(fields=["event", "created_at"]),
            models.Index(fields=["actor", "created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Audit {self.event} for {self.payment_intent_id}"


class PaymentReconciliationCase(BaseModel):
    """Tracks payment mismatches and reconciliation work items."""

    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="payment_reconciliation_cases",
    )
    payment_intent = models.ForeignKey(
        PaymentIntent,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reconciliation_cases",
    )
    payment_transaction = models.ForeignKey(
        PaymentTransaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reconciliation_cases",
    )
    webhook = models.ForeignKey(
        PaymentWebhook,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reconciliation_cases",
    )

    mismatch_type = models.CharField(
        max_length=50,
        choices=ReconciliationMismatchType.choices,
    )
    case_status = models.CharField(
        max_length=20,
        choices=ReconciliationCaseStatus.choices,
        default=ReconciliationCaseStatus.OPEN,
    )

    expected_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    received_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    expected_reference = models.CharField(max_length=255, blank=True)
    received_reference = models.CharField(max_length=255, blank=True)

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_payment_reconciliation_cases",
    )
    resolution_notes = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["chama", "case_status", "mismatch_type"]),
            models.Index(fields=["payment_intent", "case_status"]),
            models.Index(fields=["payment_transaction", "case_status"]),
            models.Index(fields=["assigned_to", "case_status"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.mismatch_type} ({self.case_status})"


class PaymentStatementImport(BaseModel):
    """Imported provider/bank statements used for reconciliation."""

    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="payment_statement_imports",
    )
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="imported_payment_statements",
    )
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices)
    provider_name = models.CharField(max_length=50, blank=True)
    source_name = models.CharField(max_length=150, blank=True)
    statement_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=StatementImportStatus.choices,
        default=StatementImportStatus.PENDING,
    )
    total_rows = models.PositiveIntegerField(default=0)
    matched_rows = models.PositiveIntegerField(default=0)
    mismatch_rows = models.PositiveIntegerField(default=0)
    unmatched_rows = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["chama", "payment_method", "created_at"]),
            models.Index(fields=["status", "created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.payment_method} statement import {self.id}"


class PaymentStatementLine(BaseModel):
    """Parsed statement line used to match external settlement records."""

    statement_import = models.ForeignKey(
        PaymentStatementImport,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    line_number = models.PositiveIntegerField()
    external_reference = models.CharField(max_length=255, blank=True)
    payer_reference = models.CharField(max_length=120, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(
        max_length=3,
        choices=CurrencyChoices.choices,
        default=CurrencyChoices.KES,
    )
    transaction_date = models.DateTimeField(null=True, blank=True)
    match_status = models.CharField(
        max_length=20,
        choices=StatementLineMatchStatus.choices,
        default=StatementLineMatchStatus.PENDING_REVIEW,
    )
    matched_payment_intent = models.ForeignKey(
        PaymentIntent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="statement_lines",
    )
    matched_transaction = models.ForeignKey(
        PaymentTransaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="statement_lines",
    )
    reconciliation_case = models.ForeignKey(
        PaymentReconciliationCase,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="statement_lines",
    )
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["statement_import", "match_status"]),
            models.Index(fields=["external_reference"]),
            models.Index(fields=["amount", "currency"]),
        ]
        ordering = ["line_number", "created_at"]

    def __str__(self) -> str:
        return f"Statement line {self.line_number} ({self.match_status})"


class PaymentSettlement(BaseModel):
    """Moves funds from payment-method clearing into the real settlement account."""

    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="payment_settlements",
    )
    statement_import = models.ForeignKey(
        PaymentStatementImport,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="settlements",
    )
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices)
    provider_name = models.CharField(max_length=50, blank=True)
    settlement_reference = models.CharField(max_length=150, unique=True, db_index=True)
    settlement_date = models.DateField(default=timezone.localdate)
    currency = models.CharField(
        max_length=3,
        choices=CurrencyChoices.choices,
        default=CurrencyChoices.KES,
    )
    status = models.CharField(
        max_length=20,
        choices=SettlementStatus.choices,
        default=SettlementStatus.PENDING,
    )
    gross_amount = models.DecimalField(max_digits=14, decimal_places=2)
    fee_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    net_amount = models.DecimalField(max_digits=14, decimal_places=2)
    clearing_account_key = models.CharField(max_length=50, default="mpesa_clearing")
    destination_account_key = models.CharField(max_length=50, default="bank_account")
    fee_account_key = models.CharField(max_length=50, default="payment_processing_fees")
    journal_entry = models.ForeignKey(
        "finance.JournalEntry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_settlements",
    )
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="posted_payment_settlements",
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(gross_amount__gt=Decimal("0.00")),
                name="payment_settlement_gross_positive",
            ),
            models.CheckConstraint(
                condition=Q(fee_amount__gte=Decimal("0.00")),
                name="payment_settlement_fee_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(net_amount__gte=Decimal("0.00")),
                name="payment_settlement_net_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["chama", "payment_method", "status"]),
            models.Index(fields=["provider_name", "status"]),
            models.Index(fields=["settlement_date", "status"]),
        ]
        ordering = ["-settlement_date", "-created_at"]

    def __str__(self) -> str:
        return f"{self.payment_method} settlement {self.settlement_reference} ({self.status})"


class PaymentSettlementAllocation(BaseModel):
    """Links settled gross amounts back to individual verified transactions."""

    settlement = models.ForeignKey(
        PaymentSettlement,
        on_delete=models.CASCADE,
        related_name="allocations",
    )
    payment_transaction = models.ForeignKey(
        PaymentTransaction,
        on_delete=models.PROTECT,
        related_name="settlement_allocations",
    )
    settled_amount = models.DecimalField(max_digits=14, decimal_places=2)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["settlement", "payment_transaction"],
                name="uniq_settlement_transaction_allocation",
            ),
            models.CheckConstraint(
                condition=Q(settled_amount__gt=Decimal("0.00")),
                name="payment_settlement_allocation_amount_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["settlement", "created_at"]),
            models.Index(fields=["payment_transaction", "created_at"]),
        ]
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.settlement_id}:{self.payment_transaction_id}"


# ============================================================================
# PAYMENT METHOD SPECIFIC MODELS
# ============================================================================

class MpesaPaymentDetails(BaseModel):
    """M-Pesa specific payment details."""
    
    payment_intent = models.OneToOneField(
        PaymentIntent,
        on_delete=models.CASCADE,
        related_name="mpesa_details",
    )
    
    phone = models.CharField(max_length=16, help_text="M-Pesa phone number")
    checkout_request_id = models.CharField(max_length=120, blank=True, unique=True)
    merchant_request_id = models.CharField(max_length=120, blank=True)
    mpesa_receipt_number = models.CharField(max_length=80, blank=True, db_index=True)
    
    # Callback data
    callback_received_at = models.DateTimeField(null=True, blank=True)
    raw_callback = models.JSONField(default=dict, blank=True)
    
    class Meta:
        indexes = [
            models.Index(fields=["checkout_request_id"]),
            models.Index(fields=["mpesa_receipt_number"]),
        ]


class CardPaymentDetails(BaseModel):
    """Card specific payment details."""
    
    payment_intent = models.OneToOneField(
        PaymentIntent,
        on_delete=models.CASCADE,
        related_name="card_details",
    )
    
    # Provider details
    provider_intent_id = models.CharField(max_length=255, unique=True, help_text="Provider's payment intent ID")
    client_secret = models.CharField(max_length=255, blank=True)
    checkout_url = models.URLField(blank=True)
    
    # Card details (PCI-safe only)
    card_brand = models.CharField(max_length=20, blank=True)
    card_last4 = models.CharField(max_length=4, blank=True)
    card_country = models.CharField(max_length=2, blank=True)
    
    # Authorization
    authorization_code = models.CharField(max_length=100, blank=True)
    
    class Meta:
        indexes = [
            models.Index(fields=["provider_intent_id"]),
        ]


class CashPaymentDetails(BaseModel):
    """Cash specific payment details."""
    
    payment_intent = models.OneToOneField(
        PaymentIntent,
        on_delete=models.CASCADE,
        related_name="cash_details",
    )
    
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="received_cash_payments",
    )
    
    receipt_number = models.CharField(max_length=100, blank=True)
    proof_photo = models.ImageField(upload_to='cash_proofs/', blank=True)
    notes = models.TextField(blank=True)
    
    # Verification
    first_verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="first_verified_cash_payments",
    )
    first_verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_cash_payments",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        indexes = [
            models.Index(fields=["received_by"]),
            models.Index(fields=["receipt_number"]),
        ]


class BankPaymentDetails(BaseModel):
    """Bank transfer specific payment details."""
    
    payment_intent = models.OneToOneField(
        PaymentIntent,
        on_delete=models.CASCADE,
        related_name="bank_details",
    )
    
    bank_name = models.CharField(max_length=100)
    account_number = models.CharField(max_length=50)
    account_name = models.CharField(max_length=100, blank=True)
    
    transfer_reference = models.CharField(max_length=100, blank=True)
    proof_document = models.FileField(upload_to='bank_proofs/', blank=True)
    notes = models.TextField(blank=True)
    
    # Verification
    first_verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="first_verified_bank_payments",
    )
    first_verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_bank_payments",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        indexes = [
            models.Index(fields=["bank_name"]),
            models.Index(fields=["transfer_reference"]),
        ]
