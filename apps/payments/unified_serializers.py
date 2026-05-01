"""
Unified Payment Serializers for MyChama.

Serializers for unified payment API endpoints.
"""

from __future__ import annotations

from decimal import Decimal

from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers

from apps.payments.models import (
    PaymentDispute,
    PaymentDisputeCategory,
    PaymentDisputeStatus,
    PaymentRefund,
)
from apps.payments.unified_models import (
    BankPaymentDetails,
    CashPaymentDetails,
    ManualPaymentApprovalPolicy,
    MpesaPaymentDetails,
    PaymentAuditLog,
    PaymentIntent,
    PaymentMethod,
    PaymentPurpose,
    PaymentReceipt,
    PaymentReconciliationCase,
    PaymentSettlement,
    PaymentSettlementAllocation,
    PaymentStatementImport,
    PaymentStatementLine,
    PaymentStatus,
    PaymentTransaction,
)


class PaymentIntentCreateSerializer(serializers.Serializer):
    """Serializer for creating payment intent."""

    chama_id = serializers.UUIDField(required=True)
    amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=True,
    )
    currency = serializers.ChoiceField(
        choices=[("KES", "KES"), ("USD", "USD"), ("EUR", "EUR"), ("GBP", "GBP")],
        default="KES",
    )
    payment_method = serializers.ChoiceField(
        choices=[
            (PaymentMethod.MPESA, "M-Pesa"),
            (PaymentMethod.CASH, "Cash"),
            (PaymentMethod.BANK, "Bank Transfer"),
            (PaymentMethod.WALLET, "Wallet"),
        ],
        required=True,
    )
    purpose = serializers.ChoiceField(
        choices=PaymentPurpose.choices,
        default=PaymentPurpose.CONTRIBUTION,
    )
    purpose_id = serializers.UUIDField(required=False, allow_null=True)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    contribution_id = serializers.UUIDField(required=False, allow_null=True)
    contribution_type_id = serializers.UUIDField(required=False, allow_null=True)
    provider = serializers.CharField(required=False, allow_blank=True, default="")
    idempotency_key = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=100,
    )
    metadata = serializers.DictField(required=False, default=dict)

    # M-Pesa specific
    phone = serializers.CharField(required=False, allow_blank=True, max_length=16)

    # Cash specific
    received_by = serializers.UUIDField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    # Bank specific (optional; canonical details come from chama config)
    bank_name = serializers.CharField(required=False, allow_blank=True, default="")
    account_number = serializers.CharField(required=False, allow_blank=True, default="")
    account_name = serializers.CharField(required=False, allow_blank=True, default="")
    transfer_reference = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_amount(self, value: Decimal) -> Decimal:
        """Validate amount is positive."""
        if value <= Decimal("0.00"):
            raise serializers.ValidationError("Amount must be greater than zero")
        return value

    def validate(self, data):
        """Validate method-specific fields."""
        payment_method = data.get("payment_method")

        if payment_method == PaymentMethod.MPESA:
            if not data.get("phone"):
                raise serializers.ValidationError({"phone": "Phone number is required for M-Pesa payments"})

        # Notes are helpful for manual methods but should not block intent creation.
        if payment_method in {PaymentMethod.CASH, PaymentMethod.BANK}:
            data["notes"] = str(data.get("notes") or "").strip()

        if data.get("purpose") in {PaymentPurpose.FINE, PaymentPurpose.LOAN_REPAYMENT} and not data.get("purpose_id"):
            raise serializers.ValidationError({"purpose_id": "purpose_id is required for this payment purpose"})

        return data


class MpesaPaymentDetailsSerializer(serializers.ModelSerializer):
    """Serializer for M-Pesa payment details."""

    class Meta:
        model = MpesaPaymentDetails
        fields = [
            "id",
            "phone",
            "checkout_request_id",
            "merchant_request_id",
            "mpesa_receipt_number",
            "callback_received_at",
            "created_at",
        ]
        read_only_fields = fields


class CashPaymentDetailsSerializer(serializers.ModelSerializer):
    """Serializer for cash payment details."""

    first_verified_by_name = serializers.CharField(source="first_verified_by.get_full_name", read_only=True, default=None)
    received_by_name = serializers.CharField(source="received_by.get_full_name", read_only=True, default=None)
    verified_by_name = serializers.CharField(source="verified_by.get_full_name", read_only=True, default=None)

    class Meta:
        model = CashPaymentDetails
        fields = [
            "id",
            "received_by",
            "received_by_name",
            "receipt_number",
            "proof_photo",
            "notes",
            "first_verified_by",
            "first_verified_by_name",
            "first_verified_at",
            "verified_by",
            "verified_by_name",
            "verified_at",
            "created_at",
        ]
        read_only_fields = fields


class BankPaymentDetailsSerializer(serializers.ModelSerializer):
    """Serializer for bank transfer payment details."""

    first_verified_by_name = serializers.CharField(source="first_verified_by.get_full_name", read_only=True, default=None)
    verified_by_name = serializers.CharField(source="verified_by.get_full_name", read_only=True, default=None)

    class Meta:
        model = BankPaymentDetails
        fields = [
            "id",
            "bank_name",
            "account_number",
            "account_name",
            "transfer_reference",
            "proof_document",
            "notes",
            "first_verified_by",
            "first_verified_by_name",
            "first_verified_at",
            "verified_by",
            "verified_by_name",
            "verified_at",
            "created_at",
        ]
        read_only_fields = fields


class PaymentIntentResponseSerializer(serializers.ModelSerializer):
    """Serializer for payment intent response."""

    user_name = serializers.CharField(source="user.get_full_name", read_only=True)
    chama_name = serializers.CharField(source="chama.name", read_only=True)
    contribution_type = serializers.CharField(
        source="contribution.contribution_type.name",
        read_only=True,
        default=None,
    )

    # Method-specific details
    mpesa_details = MpesaPaymentDetailsSerializer(read_only=True, default=None)
    cash_details = CashPaymentDetailsSerializer(read_only=True, default=None)
    bank_details = BankPaymentDetailsSerializer(read_only=True, default=None)

    class Meta:
        model = PaymentIntent
        fields = [
            "id",
            "chama",
            "chama_name",
            "user",
            "user_name",
            "contribution",
            "contribution_type",
            "amount",
            "currency",
            "purpose",
            "purpose_id",
            "description",
            "payment_method",
            "provider",
            "provider_intent_id",
            "status",
            "idempotency_key",
            "reference",
            "failure_reason",
            "failure_code",
            "metadata",
            "expires_at",
            "initiated_at",
            "completed_at",
            "created_at",
            "updated_at",
            "mpesa_details",
            "cash_details",
            "bank_details",
        ]
        read_only_fields = fields


class PaymentTransactionSerializer(serializers.ModelSerializer):
    """Serializer for payment transaction."""

    class Meta:
        model = PaymentTransaction
        fields = [
            "id",
            "payment_intent",
            "provider_reference",
            "provider_name",
            "payment_method",
            "amount",
            "currency",
            "status",
            "payer_reference",
            "verified_at",
            "verified_by",
            "failed_at",
            "created_at",
        ]
        read_only_fields = fields


class PaymentReceiptSerializer(serializers.ModelSerializer):
    """Serializer for payment receipt."""

    payment_intent_id = serializers.UUIDField(source="payment_intent.id", read_only=True)
    transaction_id = serializers.UUIDField(source="transaction.id", read_only=True)

    class Meta:
        model = PaymentReceipt
        fields = [
            "id",
            "payment_intent_id",
            "transaction_id",
            "receipt_number",
            "reference_number",
            "amount",
            "currency",
            "payment_method",
            "issued_at",
            "issued_by",
            "metadata",
            "created_at",
        ]
        read_only_fields = fields


class PaymentAuditLogSerializer(serializers.ModelSerializer):
    """Serializer for payment audit log."""

    actor_name = serializers.CharField(
        source="actor.get_full_name",
        read_only=True,
        default=None,
    )

    class Meta:
        model = PaymentAuditLog
        fields = [
            "id",
            "payment_intent",
            "actor",
            "actor_name",
            "event",
            "previous_status",
            "new_status",
            "metadata",
            "ip_address",
            "created_at",
        ]
        read_only_fields = fields


class PaymentStatusResponseSerializer(serializers.Serializer):
    """Serializer for payment status response."""

    intent = PaymentIntentResponseSerializer()
    transactions = PaymentTransactionSerializer(many=True)
    receipt = PaymentReceiptSerializer(required=False, allow_null=True)
    audit_logs = PaymentAuditLogSerializer(many=True)


class PaymentWebhookSerializer(serializers.Serializer):
    """Serializer for webhook endpoint."""

    payment_method = serializers.ChoiceField(choices=[(PaymentMethod.MPESA, "M-Pesa")])
    provider = serializers.CharField(required=True)
    payload = serializers.JSONField(required=False)
    signature = serializers.CharField(required=False, allow_blank=True)


class PaymentListSerializer(serializers.ModelSerializer):
    """Serializer for listing payments."""

    user_name = serializers.CharField(source="user.get_full_name", read_only=True)
    chama_name = serializers.CharField(source="chama.name", read_only=True)
    payer_info = serializers.SerializerMethodField()

    class Meta:
        model = PaymentIntent
        fields = [
            "id",
            "chama",
            "chama_name",
            "user",
            "user_name",
            "amount",
            "currency",
            "purpose",
            "payment_method",
            "status",
            "provider",
            "reference",
            "payer_info",
            "failure_reason",
            "initiated_at",
            "completed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_payer_info(self, obj: PaymentIntent) -> dict[str, str] | None:
        """Get payer information based on payment method."""
        if obj.payment_method == PaymentMethod.MPESA:
            mpesa_details = getattr(obj, "mpesa_details", None)
            if mpesa_details:
                return {"phone": mpesa_details.phone}
        return None


class PaymentFilterSerializer(serializers.Serializer):
    """Serializer for filtering payments."""

    payment_method = serializers.ChoiceField(
        choices=[(PaymentMethod.MPESA, "M-Pesa"), (PaymentMethod.CASH, "Cash")],
        required=False,
    )
    status = serializers.ChoiceField(
        choices=PaymentStatus.choices,
        required=False,
    )
    purpose = serializers.ChoiceField(
        choices=PaymentPurpose.choices,
        required=False,
    )
    start_date = serializers.DateTimeField(required=False)
    end_date = serializers.DateTimeField(required=False)
    min_amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        required=False,
    )
    max_amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        required=False,
    )


class PaymentRefundSerializer(serializers.Serializer):
    """Serializer for refunding payment."""

    intent_id = serializers.UUIDField(required=True)
    amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=False,
        allow_null=True,
    )
    reason = serializers.CharField(required=False, allow_blank=True)
    idempotency_key = serializers.CharField(required=False, allow_blank=True, max_length=100)


class PaymentRefundDecisionSerializer(serializers.Serializer):
    """Serializer for approving or rejecting a refund."""

    approve = serializers.BooleanField(required=False, default=True)
    note = serializers.CharField(required=False, allow_blank=True)


class PaymentRefundRecordSerializer(serializers.ModelSerializer):
    """Serialized refund record."""

    requested_by_name = serializers.CharField(source="requested_by.get_full_name", read_only=True, default=None)
    approved_by_name = serializers.CharField(source="approved_by.get_full_name", read_only=True, default=None)
    processed_by_name = serializers.CharField(source="processed_by.get_full_name", read_only=True, default=None)

    class Meta:
        model = PaymentRefund
        fields = [
            "id",
            "chama",
            "payment_intent",
            "amount",
            "reason",
            "status",
            "idempotency_key",
            "requested_by",
            "requested_by_name",
            "approved_by",
            "approved_by_name",
            "processed_by",
            "processed_by_name",
            "processed_at",
            "notes",
            "ledger_reversal_entry",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


@extend_schema_serializer(component_name="UnifiedPaymentDisputeCreateRequest")
class PaymentDisputeCreateSerializer(serializers.Serializer):
    """Serializer for opening unified payment disputes."""

    chama_id = serializers.UUIDField(required=True)
    intent_id = serializers.UUIDField(required=False, allow_null=True)
    category = serializers.ChoiceField(
        choices=PaymentDisputeCategory.choices,
        required=False,
        default=PaymentDisputeCategory.OTHER,
    )
    amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=False,
        allow_null=True,
    )
    reason = serializers.CharField(required=True)
    reference = serializers.CharField(required=False, allow_blank=True, max_length=120)
    provider_case_reference = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=150,
    )

    def validate(self, attrs):
        category = attrs.get("category")
        if category in {PaymentDisputeCategory.CHARGEBACK, PaymentDisputeCategory.PROVIDER_DISPUTE} and not attrs.get("intent_id"):
            raise serializers.ValidationError({"intent_id": "intent_id is required for provider-linked disputes"})
        return attrs


class PaymentDisputeDecisionSerializer(serializers.Serializer):
    """Serializer for resolving disputes and chargebacks."""

    status = serializers.ChoiceField(
        choices=[
            PaymentDisputeStatus.IN_REVIEW,
            PaymentDisputeStatus.RESOLVED,
            PaymentDisputeStatus.REJECTED,
            PaymentDisputeStatus.WON,
            PaymentDisputeStatus.LOST,
        ]
    )
    resolution_notes = serializers.CharField(required=False, allow_blank=True)
    amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=False,
        allow_null=True,
    )
    provider_case_reference = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=150,
    )


class PaymentDisputeRecordSerializer(serializers.ModelSerializer):
    """Serialized dispute record."""

    opened_by_name = serializers.CharField(source="opened_by.get_full_name", read_only=True, default=None)
    resolved_by_name = serializers.CharField(source="resolved_by.get_full_name", read_only=True, default=None)

    class Meta:
        model = PaymentDispute
        fields = [
            "id",
            "chama",
            "payment_intent",
            "opened_by",
            "opened_by_name",
            "category",
            "amount",
            "reason",
            "reference",
            "provider_case_reference",
            "status",
            "resolution_notes",
            "financial_reversal_entry",
            "metadata",
            "resolved_by",
            "resolved_by_name",
            "resolved_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class PaymentConfirmSerializer(serializers.Serializer):
    """Serializer for confirming client return."""

    intent_id = serializers.UUIDField(required=True)
    status = serializers.CharField(required=False, allow_blank=True)


class CashPaymentVerifySerializer(serializers.Serializer):
    """Serializer for verifying cash payment."""

    intent_id = serializers.UUIDField(required=True)
    receipt_number = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)


class BankPaymentProofUploadSerializer(serializers.Serializer):
    """Serializer for submitting bank transfer proof."""

    intent_id = serializers.UUIDField(required=True)
    transfer_reference = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)


class BankPaymentVerifySerializer(serializers.Serializer):
    """Serializer for verifying bank transfer payment."""

    intent_id = serializers.UUIDField(required=True)
    transfer_reference = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)


class ManualPaymentApprovalPolicySerializer(serializers.ModelSerializer):
    """Serializer for manual payment approval policy."""

    class Meta:
        model = ManualPaymentApprovalPolicy
        fields = [
            "id",
            "chama",
            "cash_maker_checker_enabled",
            "bank_maker_checker_enabled",
            "block_payer_self_approval",
            "require_cash_receipt_number",
            "require_cash_proof",
            "require_bank_proof_document",
            "require_bank_transfer_reference",
            "dual_approval_threshold",
            "allowed_cash_recorder_roles",
            "allowed_cash_verifier_roles",
            "allowed_bank_verifier_roles",
            "allowed_reconciliation_roles",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class PaymentStatementImportRequestSerializer(serializers.Serializer):
    """Serializer for importing statement rows for reconciliation."""

    chama_id = serializers.UUIDField(required=True)
    payment_method = serializers.ChoiceField(
        choices=[
            (PaymentMethod.MPESA, "M-Pesa"),
            (PaymentMethod.BANK, "Bank Transfer"),
        ]
    )
    provider_name = serializers.CharField(required=False, allow_blank=True, default="")
    source_name = serializers.CharField(required=False, allow_blank=True, default="")
    statement_date = serializers.DateField(required=False, allow_null=True)
    csv_text = serializers.CharField(required=False, allow_blank=True, default="")
    rows = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        allow_empty=False,
    )

    def validate(self, attrs):
        if not attrs.get("csv_text") and not attrs.get("rows"):
            raise serializers.ValidationError("Provide csv_text or rows for statement import")
        return attrs


class PaymentStatementLineSerializer(serializers.ModelSerializer):
    """Serializer for imported statement lines."""

    class Meta:
        model = PaymentStatementLine
        fields = [
            "id",
            "line_number",
            "external_reference",
            "payer_reference",
            "amount",
            "currency",
            "transaction_date",
            "match_status",
            "matched_payment_intent",
            "matched_transaction",
            "reconciliation_case",
            "raw_payload",
            "created_at",
        ]
        read_only_fields = fields


class PaymentStatementImportSerializer(serializers.ModelSerializer):
    """Serializer for statement imports."""

    imported_by_name = serializers.CharField(source="imported_by.get_full_name", read_only=True, default=None)
    lines = PaymentStatementLineSerializer(many=True, read_only=True)

    class Meta:
        model = PaymentStatementImport
        fields = [
            "id",
            "chama",
            "imported_by",
            "imported_by_name",
            "payment_method",
            "provider_name",
            "source_name",
            "statement_date",
            "status",
            "total_rows",
            "matched_rows",
            "mismatch_rows",
            "unmatched_rows",
            "metadata",
            "lines",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class PaymentSettlementCreateSerializer(serializers.Serializer):
    """Serializer for posting provider settlement into finance."""

    chama_id = serializers.UUIDField(required=True)
    payment_method = serializers.ChoiceField(
        choices=[(PaymentMethod.MPESA, "M-Pesa")]
    )
    provider_name = serializers.CharField(required=False, allow_blank=True, default="")
    settlement_reference = serializers.CharField(required=True, max_length=150)
    settlement_date = serializers.DateField(required=False, allow_null=True)
    currency = serializers.ChoiceField(
        choices=[("KES", "KES"), ("USD", "USD"), ("EUR", "EUR"), ("GBP", "GBP")],
        default="KES",
    )
    gross_amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=True,
    )
    fee_amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.00"),
        required=False,
        default=Decimal("0.00"),
    )
    statement_import_id = serializers.UUIDField(required=False, allow_null=True)
    transaction_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=False,
    )
    metadata = serializers.DictField(required=False, default=dict)

    def validate(self, attrs):
        gross_amount = attrs["gross_amount"]
        fee_amount = attrs.get("fee_amount") or Decimal("0.00")
        if fee_amount > gross_amount:
            raise serializers.ValidationError({"fee_amount": "Fee amount cannot exceed gross amount"})
        if not attrs.get("statement_import_id") and not attrs.get("transaction_ids"):
            raise serializers.ValidationError(
                "Provide statement_import_id or transaction_ids to support settlement matching"
            )
        return attrs


class PaymentSettlementAllocationSerializer(serializers.ModelSerializer):
    """Serializer for settlement allocations."""

    provider_reference = serializers.CharField(
        source="payment_transaction.provider_reference",
        read_only=True,
        default="",
    )

    class Meta:
        model = PaymentSettlementAllocation
        fields = [
            "id",
            "payment_transaction",
            "provider_reference",
            "settled_amount",
            "metadata",
            "created_at",
        ]
        read_only_fields = fields


class PaymentSettlementSerializer(serializers.ModelSerializer):
    """Serializer for settlement records."""

    posted_by_name = serializers.CharField(
        source="posted_by.get_full_name",
        read_only=True,
        default=None,
    )
    allocations = PaymentSettlementAllocationSerializer(many=True, read_only=True)

    class Meta:
        model = PaymentSettlement
        fields = [
            "id",
            "chama",
            "statement_import",
            "payment_method",
            "provider_name",
            "settlement_reference",
            "settlement_date",
            "currency",
            "status",
            "gross_amount",
            "fee_amount",
            "net_amount",
            "clearing_account_key",
            "destination_account_key",
            "fee_account_key",
            "journal_entry",
            "posted_by",
            "posted_by_name",
            "posted_at",
            "metadata",
            "allocations",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class PaymentReconciliationListSerializer(serializers.Serializer):
    """Serializer for reconciliation queue items."""

    id = serializers.CharField(required=False, allow_blank=True)
    issue_type = serializers.CharField()
    severity = serializers.CharField()
    summary = serializers.CharField()
    payment_intent_id = serializers.UUIDField(required=False, allow_null=True)
    provider_reference = serializers.CharField(required=False, allow_blank=True)
    payment_method = serializers.CharField(required=False, allow_blank=True)
    status = serializers.CharField(required=False, allow_blank=True)
    amount = serializers.CharField(required=False, allow_blank=True)
    currency = serializers.CharField(required=False, allow_blank=True)
    reference = serializers.CharField(required=False, allow_blank=True)
    metadata = serializers.DictField(required=False)
    created_at = serializers.DateTimeField(required=False)


class PaymentReconciliationResolveSerializer(serializers.Serializer):
    """Serializer for resolving reconciliation issues."""

    action = serializers.ChoiceField(
        choices=[
            ("retry_verification", "Retry Verification"),
            ("confirm_payment", "Confirm Payment"),
            ("mark_reconciled", "Mark Reconciled"),
            ("mark_failed", "Mark Failed"),
        ]
    )
    notes = serializers.CharField(required=False, allow_blank=True)


class ManualPaymentRejectSerializer(serializers.Serializer):
    """Serializer for rejecting manual payment verification."""

    intent_id = serializers.UUIDField(required=True)
    notes = serializers.CharField(required=False, allow_blank=True)


class PaymentReconciliationCaseSerializer(serializers.ModelSerializer):
    """Serializer for reconciliation cases."""

    assigned_to_name = serializers.CharField(
        source="assigned_to.get_full_name",
        read_only=True,
        default=None,
    )

    class Meta:
        model = PaymentReconciliationCase
        fields = [
            "id",
            "chama",
            "payment_intent",
            "payment_transaction",
            "webhook",
            "mismatch_type",
            "case_status",
            "expected_amount",
            "received_amount",
            "expected_reference",
            "received_reference",
            "assigned_to",
            "assigned_to_name",
            "resolution_notes",
            "resolved_at",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
