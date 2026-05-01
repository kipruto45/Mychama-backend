"""
Card payment models for MyChama.

Models for handling card payments through various providers
(Stripe, Flutterwave, etc.) with PCI compliance.
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


class CardPaymentProvider(models.TextChoices):
    """Supported card payment providers."""
    STRIPE = "stripe", "Stripe"
    FLUTTERWAVE = "flutterwave", "Flutterwave"
    PAYSTACK = "paystack", "Paystack"


class CardPaymentStatus(models.TextChoices):
    """Card payment status lifecycle."""
    INITIATED = "initiated", "Initiated"
    PENDING_AUTHENTICATION = "pending_authentication", "Pending Authentication"
    PENDING = "pending", "Pending"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"
    EXPIRED = "expired", "Expired"
    REFUNDED = "refunded", "Refunded"
    PARTIALLY_REFUNDED = "partially_refunded", "Partially Refunded"


class CardPaymentPurpose(models.TextChoices):
    """Purpose of card payment."""
    CONTRIBUTION = "contribution", "Contribution"
    LOAN_REPAYMENT = "loan_repayment", "Loan Repayment"
    FEE = "fee", "Fee"
    PENALTY = "penalty", "Penalty"
    OTHER = "other", "Other"


class CardBrand(models.TextChoices):
    """Card brand types."""
    VISA = "visa", "Visa"
    MASTERCARD = "mastercard", "Mastercard"
    AMEX = "amex", "American Express"
    DISCOVER = "discover", "Discover"
    DINERS = "diners", "Diners Club"
    JCB = "jcb", "JCB"
    UNIONPAY = "unionpay", "UnionPay"
    UNKNOWN = "unknown", "Unknown"


class CardPaymentIntent(BaseModel):
    """
    Card payment intent model.

    Represents a card payment intent created with a payment provider.
    This is the primary model for tracking card payment lifecycle.
    """

    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="card_payment_intents",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="card_payment_intents",
    )
    contribution = models.ForeignKey(
        "finance.Contribution",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="card_payment_intents",
    )
    contribution_type = models.ForeignKey(
        "finance.ContributionType",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="card_payment_intents",
    )

    # Payment details
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(
        max_length=3,
        choices=CurrencyChoices.choices,
        default=CurrencyChoices.KES,
    )
    purpose = models.CharField(
        max_length=30,
        choices=CardPaymentPurpose.choices,
        default=CardPaymentPurpose.CONTRIBUTION,
    )
    description = models.TextField(blank=True)

    # Provider details
    provider = models.CharField(
        max_length=20,
        choices=CardPaymentProvider.choices,
        default=CardPaymentProvider.STRIPE,
    )
    provider_intent_id = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
    )
    client_secret = models.CharField(max_length=255, blank=True)
    checkout_url = models.URLField(blank=True)

    # Status tracking
    status = models.CharField(
        max_length=30,
        choices=CardPaymentStatus.choices,
        default=CardPaymentStatus.INITIATED,
    )

    # Idempotency and reference
    idempotency_key = models.CharField(max_length=100, unique=True)
    reference = models.CharField(max_length=100, blank=True)

    # Failure tracking
    failure_reason = models.TextField(blank=True)
    failure_code = models.CharField(max_length=50, blank=True)

    # Metadata
    metadata = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["chama", "idempotency_key"],
                name="uniq_card_payment_intent_idempotency_per_chama",
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="card_payment_intent_amount_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["chama", "status", "created_at"]),
            models.Index(fields=["user", "status", "created_at"]),
            models.Index(fields=["provider", "status"]),
            models.Index(fields=["provider_intent_id"]),
            models.Index(fields=["contribution"]),
            models.Index(fields=["contribution_type"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Card Payment {self.amount} {self.currency} ({self.status})"

    def save(self, *args, **kwargs):
        if self.user_id is None and self.created_by_id is not None:
            self.user_id = self.created_by_id
        super().save(*args, **kwargs)

    @property
    def is_terminal(self) -> bool:
        """Check if payment is in a terminal state."""
        return self.status in {
            CardPaymentStatus.SUCCESS,
            CardPaymentStatus.FAILED,
            CardPaymentStatus.CANCELLED,
            CardPaymentStatus.EXPIRED,
            CardPaymentStatus.REFUNDED,
        }

    @property
    def is_pending(self) -> bool:
        """Check if payment is in a pending state."""
        return self.status in {
            CardPaymentStatus.INITIATED,
            CardPaymentStatus.PENDING_AUTHENTICATION,
            CardPaymentStatus.PENDING,
        }


class CardPaymentTransaction(BaseModel):
    """
    Card payment transaction model.

    Records the actual transaction details from the payment provider.
    """

    payment_intent = models.ForeignKey(
        CardPaymentIntent,
        on_delete=models.CASCADE,
        related_name="transactions",
    )

    # Provider transaction details
    provider_reference = models.CharField(max_length=255, unique=True, db_index=True)
    provider_name = models.CharField(max_length=20, choices=CardPaymentProvider.choices)

    # Transaction details
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, choices=CurrencyChoices.choices)
    status = models.CharField(max_length=30, choices=CardPaymentStatus.choices)

    # Card details (PCI-safe only)
    card_brand = models.CharField(
        max_length=20,
        choices=CardBrand.choices,
        blank=True,
    )
    card_last4 = models.CharField(max_length=4, blank=True)
    card_country = models.CharField(max_length=2, blank=True)

    # Authorization details
    authorization_code = models.CharField(max_length=100, blank=True)
    auth_code = models.CharField(max_length=100, blank=True)

    # Provider response
    raw_response = models.JSONField(default=dict, blank=True)

    # Timestamps
    paid_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="card_payment_transaction_amount_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["payment_intent", "status", "created_at"]),
            models.Index(fields=["provider_name", "status"]),
            models.Index(fields=["provider_reference"]),
            models.Index(fields=["card_brand", "card_last4"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Card Transaction {self.provider_reference} ({self.status})"


class CardPaymentWebhook(BaseModel):
    """
    Card payment webhook log model.

    Stores webhook events received from payment providers.
    """

    provider = models.CharField(
        max_length=20,
        choices=CardPaymentProvider.choices,
    )
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
            models.Index(fields=["provider_reference"]),
            models.Index(fields=["processed", "created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Webhook {self.provider} {self.event_type} @ {self.created_at}"


class CardPaymentReceipt(BaseModel):
    """
    Card payment receipt model.

    Generated after successful card payment.
    """

    payment_intent = models.OneToOneField(
        CardPaymentIntent,
        on_delete=models.CASCADE,
        related_name="receipt",
    )
    transaction = models.OneToOneField(
        CardPaymentTransaction,
        on_delete=models.CASCADE,
        related_name="receipt",
    )

    # Receipt details
    reference_number = models.CharField(max_length=100, unique=True, db_index=True)
    receipt_number = models.CharField(max_length=100, unique=True, db_index=True)

    # Payment details
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, choices=CurrencyChoices.choices)

    # Card details (masked)
    card_brand = models.CharField(max_length=20, blank=True)
    card_last4 = models.CharField(max_length=4, blank=True)

    # Issued details
    issued_at = models.DateTimeField(default=timezone.now)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_card_receipts",
    )

    # Metadata
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["reference_number"]),
            models.Index(fields=["receipt_number"]),
            models.Index(fields=["issued_at"]),
        ]
        ordering = ["-issued_at"]

    def __str__(self) -> str:
        return f"Receipt {self.receipt_number} ({self.amount} {self.currency})"

    def save(self, *args, **kwargs):
        if not self.reference_number:
            self.reference_number = f"CRD-{uuid.uuid4().hex[:12].upper()}"
        if not self.receipt_number:
            self.receipt_number = f"RCP-{uuid.uuid4().hex[:12].upper()}"
        super().save(*args, **kwargs)


class CardPaymentAuditLog(BaseModel):
    """
    Card payment audit log model.

    Tracks all state changes and actions on card payments.
    """

    payment_intent = models.ForeignKey(
        CardPaymentIntent,
        on_delete=models.CASCADE,
        related_name="audit_logs",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="card_payment_audit_logs",
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
