from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

# Import unified models to avoid model conflicts
from apps.payments.unified_models import PaymentIntent, PaymentTransaction
from core.constants import CurrencyChoices
from core.models import BaseModel
from core.utils import normalize_kenyan_phone


class MpesaPurpose(models.TextChoices):
    CONTRIBUTION = "contribution", "Contribution"
    REPAYMENT = "repayment", "Loan Repayment"
    PENALTY = "penalty", "Penalty Payment"
    OTHER = "other", "Other"


class MpesaTransactionStatus(models.TextChoices):
    INITIATED = "initiated", "Initiated"
    PENDING_CALLBACK = "pending_callback", "Pending Callback"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class MpesaTransaction(BaseModel):
    """Legacy STK transaction model retained for backward compatibility."""

    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="mpesa_transactions",
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mpesa_transactions",
    )
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="initiated_mpesa_transactions",
    )

    phone = models.CharField(max_length=16)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    purpose = models.CharField(max_length=30, choices=MpesaPurpose.choices)
    reference = models.CharField(max_length=100, blank=True)

    checkout_request_id = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
    )
    merchant_request_id = models.CharField(max_length=100, blank=True)
    receipt_number = models.CharField(max_length=50, blank=True)
    status = models.CharField(
        max_length=20,
        choices=MpesaTransactionStatus.choices,
        default=MpesaTransactionStatus.INITIATED,
    )

    callback_received_at = models.DateTimeField(null=True, blank=True)
    raw_callback = models.JSONField(default=dict, blank=True)
    failure_reason = models.TextField(blank=True)
    idempotency_key = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["chama", "receipt_number"],
                condition=~Q(receipt_number=""),
                name="uniq_mpesa_receipt_per_chama",
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="legacy_mpesa_amount_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["chama", "status"]),
            models.Index(fields=["purpose", "status"]),
            models.Index(fields=["checkout_request_id"]),
            models.Index(fields=["receipt_number"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"M-Pesa {self.status} {self.amount} ({self.purpose})"

    def save(self, *args, **kwargs):
        self.phone = normalize_kenyan_phone(self.phone)
        super().save(*args, **kwargs)


class MpesaCallbackLog(BaseModel):
    """Legacy callback log for existing STK endpoint."""

    transaction = models.ForeignKey(
        MpesaTransaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="callback_logs",
    )
    merchant_request_id = models.CharField(max_length=100, blank=True)
    checkout_request_id = models.CharField(max_length=100, blank=True)
    callback_data = models.JSONField(default=dict)
    processed = models.BooleanField(default=False)
    processing_error = models.TextField(blank=True)
    source_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["transaction", "created_at"]),
            models.Index(fields=["checkout_request_id"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Callback {self.checkout_request_id or self.id}"


class PaymentIntentType(models.TextChoices):
    DEPOSIT = "DEPOSIT", "Deposit"
    WITHDRAWAL = "WITHDRAWAL", "Withdrawal"
    LOAN_REPAYMENT = "LOAN_REPAYMENT", "Loan Repayment"
    LOAN_DISBURSEMENT = "LOAN_DISBURSEMENT", "Loan Disbursement"


class PaymentPurpose(models.TextChoices):
    CONTRIBUTION = "CONTRIBUTION", "Contribution"
    LOAN_REPAYMENT = "LOAN_REPAYMENT", "Loan Repayment"
    SPLIT_ALLOCATION = "SPLIT_ALLOCATION", "Split Allocation"
    OTHER = "OTHER", "Other"


class PaymentIntentStatus(models.TextChoices):
    INITIATED = "INITIATED", "Initiated"
    PENDING = "PENDING", "Pending"
    SUCCESS = "SUCCESS", "Success"
    FAILED = "FAILED", "Failed"
    EXPIRED = "EXPIRED", "Expired"
    CANCELLED = "CANCELLED", "Cancelled"


class MpesaTransactionType(models.TextChoices):
    PAYBILL = "PayBill", "PayBill"
    BUY_GOODS = "BuyGoods", "BuyGoods"


class MpesaC2BProcessingStatus(models.TextChoices):
    RECEIVED = "RECEIVED", "Received"
    POSTED = "POSTED", "Posted"
    DUPLICATE = "DUPLICATE", "Duplicate"
    REJECTED = "REJECTED", "Rejected"


class MpesaB2CStatus(models.TextChoices):
    INITIATED = "INITIATED", "Initiated"
    PENDING = "PENDING", "Pending"
    SUCCESS = "SUCCESS", "Success"
    FAILED = "FAILED", "Failed"
    TIMEOUT = "TIMEOUT", "Timeout"


class PaymentActivityEvent(models.TextChoices):
    CREATED = "CREATED", "Created"
    CANCELLED = "CANCELLED", "Cancelled"
    STK_SENT = "STK_SENT", "STK Sent"
    C2B_VALIDATED = "C2B_VALIDATED", "C2B Validation"
    C2B_CONFIRMED = "C2B_CONFIRMED", "C2B Confirmation"
    B2C_REQUESTED = "B2C_REQUESTED", "B2C Requested"
    CALLBACK_RECEIVED = "CALLBACK_RECEIVED", "Callback Received"
    POSTED_TO_LEDGER = "POSTED_TO_LEDGER", "Posted To Ledger"
    LOAN_DISBURSEMENT_REQUESTED = (
        "LOAN_DISBURSEMENT_REQUESTED",
        "Loan Disbursement Requested",
    )
    LOAN_DISBURSED = "LOAN_DISBURSED", "Loan Disbursed"
    LOAN_REPAYMENT_POSTED = "LOAN_REPAYMENT_POSTED", "Loan Repayment Posted"
    REFUND_REQUESTED = "REFUND_REQUESTED", "Refund Requested"
    REFUND_APPROVED = "REFUND_APPROVED", "Refund Approved"
    REFUND_REJECTED = "REFUND_REJECTED", "Refund Rejected"
    REFUND_PROCESSED = "REFUND_PROCESSED", "Refund Processed"
    DISPUTE_OPENED = "DISPUTE_OPENED", "Dispute Opened"
    DISPUTE_RESOLVED = "DISPUTE_RESOLVED", "Dispute Resolved"
    NOTIFIED = "NOTIFIED", "Notified"
    FAILED = "FAILED", "Failed"


class ReconciliationRunStatus(models.TextChoices):
    SUCCESS = "SUCCESS", "Success"
    PARTIAL = "PARTIAL", "Partial"
    FAILED = "FAILED", "Failed"


class WithdrawalApprovalStep(models.TextChoices):
    REQUESTED = "REQUESTED", "Requested"
    TREASURER_APPROVED = "TREASURER_APPROVED", "Treasurer Approved"
    ADMIN_APPROVED = "ADMIN_APPROVED", "Admin Approved"
    REJECTED = "REJECTED", "Rejected"


class CallbackKind(models.TextChoices):
    C2B_VALIDATION = "c2b_validation", "C2B Validation"
    C2B_CONFIRMATION = "c2b_confirmation", "C2B Confirmation"
    STK = "stk", "STK Callback"
    B2C_RESULT = "b2c_result", "B2C Result"
    B2C_TIMEOUT = "b2c_timeout", "B2C Timeout"


class PaymentDisputeCategory(models.TextChoices):
    DUPLICATE = "duplicate", "Duplicate Charge"
    INCORRECT_AMOUNT = "incorrect_amount", "Incorrect Amount"
    FAILED_CALLBACK = "failed_callback", "Failed Callback"
    MISSING_REFERENCE = "missing_reference", "Missing Reference"
    FRAUD = "fraud", "Fraud"
    CHARGEBACK = "chargeback", "Chargeback"
    PROVIDER_DISPUTE = "provider_dispute", "Provider Dispute"
    OTHER = "other", "Other"


class PaymentDisputeStatus(models.TextChoices):
    OPEN = "OPEN", "Open"
    IN_REVIEW = "IN_REVIEW", "In Review"
    RESOLVED = "RESOLVED", "Resolved"
    REJECTED = "REJECTED", "Rejected"
    WON = "WON", "Won"
    LOST = "LOST", "Lost"


class PaymentRefundStatus(models.TextChoices):
    REQUESTED = "REQUESTED", "Requested"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    PROCESSED = "PROCESSED", "Processed"
    FAILED = "FAILED", "Failed"


class PaymentAllocationStrategy(models.TextChoices):
    REPAYMENT_FIRST = "repayment_first", "Repayment First"
    WELFARE_FIRST = "welfare_first", "Welfare First"
    RATIO = "ratio", "Ratio Split"








class MpesaC2BTransaction(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="mpesa_c2b_transactions",
    )
    intent = models.ForeignKey(
        PaymentIntent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="c2b_transactions",
    )
    phone = models.CharField(max_length=16)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(
        max_length=3,
        choices=CurrencyChoices.choices,
        default=CurrencyChoices.KES,
    )
    transaction_type = models.CharField(
        max_length=20,
        choices=MpesaTransactionType.choices,
        default=MpesaTransactionType.PAYBILL,
    )
    trans_id = models.CharField(max_length=80, unique=True)
    bill_ref_number = models.CharField(max_length=100, blank=True)
    account_reference = models.CharField(max_length=100, blank=True)
    first_name = models.CharField(max_length=60, blank=True)
    middle_name = models.CharField(max_length=60, blank=True)
    last_name = models.CharField(max_length=60, blank=True)
    trans_time = models.DateTimeField()
    raw_payload = models.JSONField(default=dict, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    processing_status = models.CharField(
        max_length=20,
        choices=MpesaC2BProcessingStatus.choices,
        default=MpesaC2BProcessingStatus.RECEIVED,
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="mpesa_c2b_amount_positive",
            )
        ]
        indexes = [
            models.Index(fields=["chama", "processing_status", "created_at"]),
            models.Index(fields=["account_reference"]),
            models.Index(fields=["bill_ref_number"]),
            models.Index(fields=["phone", "trans_time"]),
        ]
        ordering = ["-trans_time", "-created_at"]

    def save(self, *args, **kwargs):
        self.phone = normalize_kenyan_phone(self.phone)
        super().save(*args, **kwargs)


class MpesaSTKTransaction(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="mpesa_stk_transactions",
    )
    intent = models.ForeignKey(
        PaymentIntent,
        on_delete=models.CASCADE,
        related_name="stk_transactions",
    )
    phone = models.CharField(max_length=16)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    merchant_request_id = models.CharField(max_length=120, blank=True)
    checkout_request_id = models.CharField(max_length=120, unique=True)
    result_code = models.IntegerField(null=True, blank=True)
    result_desc = models.TextField(blank=True)
    mpesa_receipt_number = models.CharField(max_length=80, null=True, blank=True)
    transaction_date = models.DateTimeField(null=True, blank=True)
    raw_callback = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=PaymentIntentStatus.choices,
        default=PaymentIntentStatus.INITIATED,
    )
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="mpesa_stk_amount_positive",
            ),
            models.UniqueConstraint(
                fields=["chama", "mpesa_receipt_number"],
                condition=Q(mpesa_receipt_number__isnull=False)
                & ~Q(mpesa_receipt_number=""),
                name="uniq_mpesa_stk_receipt_per_chama",
            ),
        ]
        indexes = [
            models.Index(fields=["chama", "status", "created_at"]),
            models.Index(fields=["merchant_request_id"]),
            models.Index(fields=["mpesa_receipt_number"]),
        ]
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        self.phone = normalize_kenyan_phone(self.phone)
        super().save(*args, **kwargs)


class MpesaB2CPayout(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="mpesa_b2c_payouts",
    )
    intent = models.ForeignKey(
        PaymentIntent,
        on_delete=models.CASCADE,
        related_name="b2c_payouts",
    )
    phone = models.CharField(max_length=16)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    command_id = models.CharField(max_length=50, default="BusinessPayment")
    remarks = models.TextField(blank=True)
    occasion = models.CharField(max_length=120, blank=True)
    originator_conversation_id = models.CharField(max_length=120, unique=True)
    conversation_id = models.CharField(max_length=120, blank=True)
    response_code = models.CharField(max_length=20, blank=True)
    response_description = models.TextField(blank=True)
    result_code = models.CharField(max_length=20, blank=True)
    result_desc = models.TextField(blank=True)
    transaction_id = models.CharField(max_length=80, blank=True)
    receipt_number = models.CharField(max_length=80, blank=True)
    raw_result = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=MpesaB2CStatus.choices,
        default=MpesaB2CStatus.INITIATED,
    )
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="mpesa_b2c_amount_positive",
            )
        ]
        indexes = [
            models.Index(fields=["chama", "status", "created_at"]),
            models.Index(fields=["conversation_id"]),
            models.Index(fields=["transaction_id"]),
            models.Index(fields=["receipt_number"]),
        ]
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        self.phone = normalize_kenyan_phone(self.phone)
        super().save(*args, **kwargs)


class PaymentActivityLog(BaseModel):
    payment_intent = models.ForeignKey(
        PaymentIntent,
        on_delete=models.CASCADE,
        related_name="activity_logs",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_activity_logs",
    )
    event = models.CharField(max_length=50, choices=PaymentActivityEvent.choices)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["payment_intent", "created_at"]),
            models.Index(fields=["event", "created_at"]),
        ]
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.event} ({self.payment_intent_id})"


class CallbackLog(BaseModel):
    callback_type = models.CharField(max_length=30, choices=CallbackKind.choices)
    source_ip = models.GenericIPAddressField(null=True, blank=True)
    signature_valid = models.BooleanField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    headers = models.JSONField(default=dict, blank=True)
    processing_error = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["callback_type", "created_at"]),
            models.Index(fields=["source_ip", "created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.callback_type} @ {timezone.localtime(self.created_at)}"


class PaymentReconciliationRun(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_reconciliation_runs",
    )
    run_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=20, choices=ReconciliationRunStatus.choices)
    totals = models.JSONField(default=dict, blank=True)
    anomalies = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["chama", "run_at"]),
            models.Index(fields=["status", "run_at"]),
        ]
        ordering = ["-run_at", "-created_at"]

    def __str__(self) -> str:
        scope = self.chama.name if self.chama_id else "global"
        return f"{scope} reconciliation {self.run_at:%Y-%m-%d}"


class WithdrawalApprovalLog(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="withdrawal_approval_logs",
    )
    payment_intent = models.ForeignKey(
        PaymentIntent,
        on_delete=models.CASCADE,
        related_name="approval_logs",
    )
    step = models.CharField(max_length=30, choices=WithdrawalApprovalStep.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="withdrawal_approval_logs",
    )
    notes = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["payment_intent", "created_at"]),
            models.Index(fields=["chama", "step", "created_at"]),
        ]
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.step} ({self.payment_intent_id})"


class PaymentRefund(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="payment_refunds",
    )
    payment_intent = models.ForeignKey(
        PaymentIntent,
        on_delete=models.PROTECT,
        related_name="refunds",
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    reason = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=PaymentRefundStatus.choices,
        default=PaymentRefundStatus.REQUESTED,
    )
    idempotency_key = models.CharField(max_length=100)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_payment_refunds",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_payment_refunds",
    )
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="processed_payment_refunds",
    )
    processed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    ledger_reversal_entry = models.ForeignKey(
        "finance.LedgerEntry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_refunds",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["chama", "idempotency_key"],
                name="uniq_payment_refund_idempotency_per_chama",
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="payment_refund_amount_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["chama", "status", "created_at"]),
            models.Index(fields=["payment_intent", "status", "created_at"]),
            models.Index(fields=["requested_by", "created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Refund {self.payment_intent_id} ({self.status})"


class PaymentDispute(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="payment_disputes",
    )
    payment_intent = models.ForeignKey(
        PaymentIntent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="disputes",
    )
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="opened_payment_disputes",
    )
    category = models.CharField(
        max_length=30,
        choices=PaymentDisputeCategory.choices,
        default=PaymentDisputeCategory.OTHER,
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    reason = models.TextField()
    reference = models.CharField(max_length=120, blank=True)
    provider_case_reference = models.CharField(max_length=150, blank=True)
    status = models.CharField(
        max_length=20,
        choices=PaymentDisputeStatus.choices,
        default=PaymentDisputeStatus.OPEN,
    )
    resolution_notes = models.TextField(blank=True)
    financial_reversal_entry = models.ForeignKey(
        "finance.LedgerEntry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_disputes",
    )
    metadata = models.JSONField(default=dict, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_payment_disputes",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__isnull=True) | Q(amount__gt=Decimal("0.00")),
                name="payment_dispute_amount_positive_or_null",
            )
        ]
        indexes = [
            models.Index(fields=["chama", "status", "created_at"]),
            models.Index(fields=["opened_by", "status", "created_at"]),
            models.Index(fields=["payment_intent", "status", "created_at"]),
            models.Index(fields=["provider_case_reference"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Dispute {self.id} ({self.status})"


class PaymentAllocationRule(BaseModel):
    chama = models.OneToOneField(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="payment_allocation_rule",
    )
    strategy = models.CharField(
        max_length=32,
        choices=PaymentAllocationStrategy.choices,
        default=PaymentAllocationStrategy.REPAYMENT_FIRST,
    )
    repayment_ratio_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("50.00"),
    )
    welfare_contribution_type = models.ForeignKey(
        "finance.ContributionType",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="allocation_rules",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(repayment_ratio_percent__gte=Decimal("0.00"))
                & Q(repayment_ratio_percent__lte=Decimal("100.00")),
                name="payment_allocation_ratio_between_0_100",
            )
        ]
        indexes = [models.Index(fields=["strategy", "is_active"])]

    def __str__(self):
        return f"Allocation rule {self.chama_id}:{self.strategy}"


class UssdSessionLog(BaseModel):
    session_id = models.CharField(max_length=120, db_index=True)
    phone = models.CharField(max_length=16, db_index=True)
    service_code = models.CharField(max_length=40, blank=True)
    text = models.TextField(blank=True)
    response_text = models.TextField(blank=True)
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ussd_session_logs",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ussd_session_logs",
    )
    processed = models.BooleanField(default=False)
    processing_error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["session_id", "created_at"]),
            models.Index(fields=["phone", "created_at"]),
            models.Index(fields=["processed", "created_at"]),
        ]

    def save(self, *args, **kwargs):
        self.phone = normalize_kenyan_phone(self.phone)
        super().save(*args, **kwargs)
