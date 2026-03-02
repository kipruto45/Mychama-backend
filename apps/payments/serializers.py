from decimal import Decimal
from uuid import UUID

from rest_framework import serializers

from apps.accounts.models import User
from apps.accounts.serializers import UserSerializer
from apps.chama.models import Chama, MemberStatus, Membership, MembershipRole
from apps.chama.services import get_effective_role
from apps.finance.models import ContributionType, Loan
from apps.payments.models import (
    MpesaB2CPayout,
    MpesaC2BTransaction,
    MpesaCallbackLog,
    MpesaPurpose,
    MpesaSTKTransaction,
    MpesaTransaction,
    MpesaTransactionStatus,
    PaymentActivityLog,
    PaymentAllocationRule,
    PaymentAllocationStrategy,
    PaymentDispute,
    PaymentDisputeCategory,
    PaymentDisputeStatus,
    PaymentIntent,
    PaymentIntentStatus,
    PaymentIntentType,
    PaymentPurpose,
    PaymentRefund,
    PaymentRefundStatus,
    PaymentReconciliationRun,
    UssdSessionLog,
    WithdrawalApprovalLog,
)
from core.utils import normalize_kenyan_phone


class MpesaTransactionSerializer(serializers.ModelSerializer):
    member = UserSerializer(read_only=True)
    initiated_by = UserSerializer(read_only=True)

    class Meta:
        model = MpesaTransaction
        fields = [
            "id",
            "chama",
            "member",
            "initiated_by",
            "phone",
            "amount",
            "purpose",
            "reference",
            "checkout_request_id",
            "merchant_request_id",
            "receipt_number",
            "status",
            "callback_received_at",
            "raw_callback",
            "failure_reason",
            "idempotency_key",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "chama",
            "member",
            "initiated_by",
            "checkout_request_id",
            "merchant_request_id",
            "receipt_number",
            "status",
            "callback_received_at",
            "raw_callback",
            "failure_reason",
            "created_at",
            "updated_at",
        ]


class InitiateMpesaSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField(required=False)
    member_id = serializers.UUIDField(required=False)
    phone = serializers.CharField(max_length=16)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    purpose = serializers.ChoiceField(
        choices=[
            (MpesaPurpose.CONTRIBUTION, "Contribution"),
            (MpesaPurpose.REPAYMENT, "Loan Repayment"),
        ]
    )
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True)
    idempotency_key = serializers.CharField(
        max_length=100, required=False, allow_blank=True
    )

    def validate_phone(self, value):
        try:
            return normalize_kenyan_phone(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate_amount(self, value):
        if value <= Decimal("0"):
            raise serializers.ValidationError("Amount must be greater than zero.")
        return value

    def validate(self, attrs):
        chama = self.context.get("chama")
        chama_id = attrs.get("chama_id")

        if chama_id and chama and str(chama.id) != str(chama_id):
            raise serializers.ValidationError(
                {"chama_id": "chama_id does not match scoped chama."}
            )

        if not chama:
            if not chama_id:
                raise serializers.ValidationError(
                    {"chama_id": "chama_id is required."}
                )
            chama = Chama.objects.filter(id=chama_id).first()
            if not chama:
                raise serializers.ValidationError({"chama_id": "Chama not found."})

        attrs["chama"] = chama
        request_user = self.context["request"].user
        member_id = attrs.get("member_id")
        requester_membership = Membership.objects.filter(
            user=request_user,
            chama=chama,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
        ).first()

        if member_id:
            try:
                member = User.objects.get(id=member_id)
            except User.DoesNotExist as exc:
                raise serializers.ValidationError(
                    {"member_id": "Member not found."}
                ) from exc

            is_member = Membership.objects.filter(
                user=member,
                chama=chama,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
            ).exists()
            if not is_member:
                raise serializers.ValidationError(
                    {"member_id": "Selected member is not approved in this chama."}
                )

            if (
                member.id != request_user.id
                and (
                    not requester_membership
                    or get_effective_role(
                        request_user,
                        chama.id,
                        requester_membership,
                    )
                    not in {MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER}
                )
            ):
                raise serializers.ValidationError(
                    {
                        "member_id": (
                            "Only chama admin or treasurer can initiate payment "
                            "for another member."
                        )
                    }
                )
            attrs["member"] = member
        else:
            attrs["member"] = request_user

        reference = attrs.get("reference", "").strip()
        if not reference:
            raise serializers.ValidationError(
                {"reference": "Reference is required for this payment purpose."}
            )

        try:
            reference_uuid = UUID(reference)
        except ValueError as exc:
            raise serializers.ValidationError(
                {"reference": "Reference must be a valid UUID."}
            ) from exc

        if attrs["purpose"] == MpesaPurpose.CONTRIBUTION:
            contribution_type_exists = ContributionType.objects.filter(
                id=reference_uuid,
                chama=chama,
                is_active=True,
            ).exists()
            if not contribution_type_exists:
                raise serializers.ValidationError(
                    {"reference": "Contribution type was not found for this chama."}
                )

        if attrs["purpose"] == MpesaPurpose.REPAYMENT:
            loan = Loan.objects.filter(id=reference_uuid, chama=chama).first()
            if not loan:
                raise serializers.ValidationError(
                    {"reference": "Loan was not found for this chama."}
                )
            if attrs["member"] and loan.member_id != attrs["member"].id:
                raise serializers.ValidationError(
                    {"reference": "Loan does not belong to the selected member."}
                )

        attrs["reference"] = str(reference_uuid)

        idempotency_key = attrs.get("idempotency_key", "").strip()
        attrs["idempotency_key"] = idempotency_key or None

        return attrs

    def create(self, validated_data):
        chama = validated_data.pop("chama")
        validated_data.pop("chama_id", None)
        request = self.context["request"]

        member = validated_data.pop("member")
        transaction = MpesaTransaction.objects.create(
            chama=chama,
            member=member,
            initiated_by=request.user,
            status=MpesaTransactionStatus.INITIATED,
            created_by=request.user,
            updated_by=request.user,
            **validated_data,
        )
        return transaction


class MpesaCallbackLogSerializer(serializers.ModelSerializer):
    transaction = MpesaTransactionSerializer(read_only=True)

    class Meta:
        model = MpesaCallbackLog
        fields = [
            "id",
            "transaction",
            "merchant_request_id",
            "checkout_request_id",
            "callback_data",
            "processed",
            "processing_error",
            "source_ip",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class MpesaCallbackSerializer(serializers.Serializer):
    Body = serializers.DictField()

    def validate(self, attrs):
        body = attrs.get("Body", {})
        stk_callback = body.get("stkCallback", {})

        checkout_request_id = stk_callback.get("CheckoutRequestID")
        merchant_request_id = stk_callback.get("MerchantRequestID")
        if not checkout_request_id:
            raise serializers.ValidationError(
                {"Body": "Missing CheckoutRequestID in callback."}
            )

        attrs["checkout_request_id"] = checkout_request_id
        attrs["merchant_request_id"] = merchant_request_id or ""
        attrs["result_code"] = stk_callback.get("ResultCode")
        attrs["result_desc"] = stk_callback.get("ResultDesc", "")

        callback_items = (
            stk_callback.get("CallbackMetadata", {}).get("Item", [])
            if isinstance(stk_callback.get("CallbackMetadata"), dict)
            else []
        )

        receipt_number = ""
        for item in callback_items:
            if item.get("Name") == "MpesaReceiptNumber":
                receipt_number = str(item.get("Value", ""))
                break

        attrs["receipt_number"] = receipt_number
        return attrs


class PaymentIntentSerializer(serializers.ModelSerializer):
    masked_phone = serializers.SerializerMethodField()

    class Meta:
        model = PaymentIntent
        fields = [
            "id",
            "chama",
            "intent_type",
            "purpose",
            "reference_type",
            "reference_id",
            "amount",
            "currency",
            "phone",
            "masked_phone",
            "status",
            "idempotency_key",
            "expires_at",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "metadata",
            "created_at",
            "updated_at",
        ]

    def get_masked_phone(self, obj):
        value = str(obj.phone or "")
        if len(value) < 6:
            return value
        return f"{value[:5]}****{value[-3:]}"


class MpesaC2BTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = MpesaC2BTransaction
        fields = [
            "id",
            "chama",
            "intent",
            "phone",
            "amount",
            "currency",
            "transaction_type",
            "trans_id",
            "bill_ref_number",
            "account_reference",
            "trans_time",
            "processing_status",
            "processed_at",
            "created_at",
        ]
        read_only_fields = fields


class MpesaSTKTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = MpesaSTKTransaction
        fields = [
            "id",
            "chama",
            "intent",
            "phone",
            "amount",
            "merchant_request_id",
            "checkout_request_id",
            "result_code",
            "result_desc",
            "mpesa_receipt_number",
            "transaction_date",
            "status",
            "processed_at",
            "created_at",
        ]
        read_only_fields = fields


class MpesaB2CPayoutSerializer(serializers.ModelSerializer):
    class Meta:
        model = MpesaB2CPayout
        fields = [
            "id",
            "chama",
            "intent",
            "phone",
            "amount",
            "command_id",
            "remarks",
            "occasion",
            "originator_conversation_id",
            "conversation_id",
            "response_code",
            "response_description",
            "result_code",
            "result_desc",
            "transaction_id",
            "receipt_number",
            "status",
            "processed_at",
            "created_at",
        ]
        read_only_fields = fields


class WithdrawalApprovalLogSerializer(serializers.ModelSerializer):
    actor = UserSerializer(read_only=True)

    class Meta:
        model = WithdrawalApprovalLog
        fields = [
            "id",
            "chama",
            "payment_intent",
            "step",
            "actor",
            "notes",
            "created_at",
        ]
        read_only_fields = fields


class PaymentReconciliationRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentReconciliationRun
        fields = [
            "id",
            "chama",
            "run_at",
            "status",
            "totals",
            "anomalies",
            "created_at",
        ]
        read_only_fields = fields


class PaymentRefundSerializer(serializers.ModelSerializer):
    requested_by = UserSerializer(read_only=True)
    approved_by = UserSerializer(read_only=True)
    processed_by = UserSerializer(read_only=True)

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
            "approved_by",
            "processed_by",
            "processed_at",
            "notes",
            "ledger_reversal_entry",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class PaymentDisputeSerializer(serializers.ModelSerializer):
    opened_by = UserSerializer(read_only=True)
    resolved_by = UserSerializer(read_only=True)

    class Meta:
        model = PaymentDispute
        fields = [
            "id",
            "chama",
            "payment_intent",
            "opened_by",
            "category",
            "reason",
            "reference",
            "status",
            "resolution_notes",
            "resolved_by",
            "resolved_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class _PurposeChoiceMixin:
    @staticmethod
    def _normalize_choice(value: str, allowed: set[str], field: str) -> str:
        raw = str(value or "").strip()
        if raw in allowed:
            return raw
        upper = raw.upper()
        if upper in allowed:
            return upper
        lower_to_upper = {item.lower(): item for item in allowed}
        mapped = lower_to_upper.get(raw.lower())
        if mapped:
            return mapped
        raise serializers.ValidationError(f"Invalid {field}.")


class DepositSTKInitiateSerializer(serializers.Serializer, _PurposeChoiceMixin):
    chama_id = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    purpose = serializers.CharField(required=False, default=PaymentPurpose.CONTRIBUTION)
    reference_id = serializers.UUIDField(required=True)
    phone = serializers.CharField(max_length=16, required=False, allow_blank=True)
    idempotency_key = serializers.CharField(max_length=100, required=False)

    def validate_amount(self, value):
        if value <= Decimal("0"):
            raise serializers.ValidationError("Amount must be greater than zero.")
        return value

    def validate_purpose(self, value):
        return self._normalize_choice(value, set(PaymentPurpose.values), "purpose")

    def validate_phone(self, value):
        if not value:
            return value
        try:
            return normalize_kenyan_phone(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class DepositC2BIntentSerializer(DepositSTKInitiateSerializer):
    pass


class LoanRepaymentSTKInitiateSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        required=False,
    )
    phone = serializers.CharField(max_length=16, required=False, allow_blank=True)
    idempotency_key = serializers.CharField(max_length=100, required=False)

    def validate_amount(self, value):
        if value <= Decimal("0"):
            raise serializers.ValidationError("Amount must be greater than zero.")
        return value

    def validate_phone(self, value):
        if not value:
            return value
        try:
            return normalize_kenyan_phone(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class LoanRepaymentC2BIntentSerializer(LoanRepaymentSTKInitiateSerializer):
    pass


class SplitPaymentSTKInitiateSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    loan_id = serializers.UUIDField()
    contribution_type_id = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    phone = serializers.CharField(max_length=16, required=False, allow_blank=True)
    strategy = serializers.ChoiceField(
        choices=["repayment_first", "welfare_first", "ratio", "auto", "custom"],
        required=False,
        default="repayment_first",
    )
    repayment_amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        required=False,
    )
    idempotency_key = serializers.CharField(max_length=100, required=False)

    def validate_amount(self, value):
        if value <= Decimal("0"):
            raise serializers.ValidationError("Amount must be greater than zero.")
        return value

    def validate_repayment_amount(self, value):
        if value <= Decimal("0"):
            raise serializers.ValidationError("repayment_amount must be greater than zero.")
        return value

    def validate_phone(self, value):
        if not value:
            return value
        try:
            return normalize_kenyan_phone(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate(self, attrs):
        strategy = attrs.get("strategy", "repayment_first")
        repayment_amount = attrs.get("repayment_amount")
        if strategy == "custom" and repayment_amount is None:
            raise serializers.ValidationError(
                {"repayment_amount": "repayment_amount is required for custom strategy."}
            )
        if strategy == "custom" and repayment_amount and repayment_amount > attrs["amount"]:
            raise serializers.ValidationError(
                {"repayment_amount": "repayment_amount cannot exceed total amount."}
            )
        return attrs


class SplitPaymentC2BIntentSerializer(SplitPaymentSTKInitiateSerializer):
    pass


class WithdrawalRequestSerializer(serializers.Serializer, _PurposeChoiceMixin):
    chama_id = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    phone = serializers.CharField(max_length=16)
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True)
    purpose = serializers.CharField(required=False, default=PaymentPurpose.OTHER)
    reference_type = serializers.CharField(max_length=40, required=False, default="OTHER")
    reference_id = serializers.UUIDField(required=False)
    idempotency_key = serializers.CharField(max_length=100, required=False)

    def validate_amount(self, value):
        if value <= Decimal("0"):
            raise serializers.ValidationError("Amount must be greater than zero.")
        return value

    def validate_phone(self, value):
        try:
            return normalize_kenyan_phone(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate_purpose(self, value):
        return self._normalize_choice(value, set(PaymentPurpose.values), "purpose")


class IntentApprovalSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, max_length=300)


class RefundRequestSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    intent_id = serializers.UUIDField()
    amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        required=False,
        min_value=Decimal("0.01"),
    )
    reason = serializers.CharField()
    idempotency_key = serializers.CharField(max_length=100, required=False)


class RefundDecisionSerializer(serializers.Serializer):
    approve = serializers.BooleanField(required=False, default=True)
    note = serializers.CharField(required=False, allow_blank=True, max_length=300)


class PaymentDisputeCreateSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    intent_id = serializers.UUIDField(required=False)
    category = serializers.ChoiceField(
        choices=PaymentDisputeCategory.choices,
        required=False,
        default=PaymentDisputeCategory.OTHER,
    )
    reason = serializers.CharField()
    reference = serializers.CharField(required=False, allow_blank=True, max_length=120)


class PaymentDisputeResolveSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[
            PaymentDisputeStatus.IN_REVIEW,
            PaymentDisputeStatus.RESOLVED,
            PaymentDisputeStatus.REJECTED,
        ]
    )
    resolution_notes = serializers.CharField(required=False, allow_blank=True)


class LoanRepaymentStatusQuerySerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()


class TransactionsQuerySerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()


class AdminTransactionsQuerySerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=PaymentIntentStatus.choices, required=False)
    intent_type = serializers.ChoiceField(
        choices=PaymentIntentType.choices,
        required=False,
    )
    purpose = serializers.ChoiceField(choices=PaymentPurpose.choices, required=False)
    phone = serializers.CharField(required=False)
    receipt = serializers.CharField(required=False)
    search = serializers.CharField(required=False)
    member_id = serializers.UUIDField(required=False)
    loan_id = serializers.UUIDField(required=False)
    from_date = serializers.DateField(required=False)
    to_date = serializers.DateField(required=False)


class ReconciliationRunsQuerySerializer(serializers.Serializer):
    chama_id = serializers.UUIDField(required=False)


class PaymentAllocationRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentAllocationRule
        fields = [
            "id",
            "chama",
            "strategy",
            "repayment_ratio_percent",
            "welfare_contribution_type",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "chama", "created_at", "updated_at"]


class PaymentAllocationRuleUpsertSerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    strategy = serializers.ChoiceField(
        choices=PaymentAllocationStrategy.choices,
        required=False,
        default=PaymentAllocationStrategy.REPAYMENT_FIRST,
    )
    repayment_ratio_percent = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        required=False,
        default=Decimal("50.00"),
    )
    welfare_contribution_type_id = serializers.UUIDField(required=False, allow_null=True)
    is_active = serializers.BooleanField(required=False, default=True)

    def validate_repayment_ratio_percent(self, value):
        if value < Decimal("0.00") or value > Decimal("100.00"):
            raise serializers.ValidationError("repayment_ratio_percent must be between 0 and 100.")
        return value


class UssdCallbackSerializer(serializers.Serializer):
    sessionId = serializers.CharField()
    serviceCode = serializers.CharField(required=False, allow_blank=True)
    phoneNumber = serializers.CharField()
    text = serializers.CharField(required=False, allow_blank=True)
    chama_id = serializers.UUIDField(required=False)


class CallbackEnvelopeSerializer(serializers.Serializer):
    payload = serializers.JSONField(required=False)


class C2BValidationPayloadSerializer(serializers.Serializer):
    TransAmount = serializers.CharField(required=False)
    BillRefNumber = serializers.CharField(required=False)


class C2BConfirmationPayloadSerializer(serializers.Serializer):
    TransID = serializers.CharField(required=False)
    TransAmount = serializers.CharField(required=False)
    BillRefNumber = serializers.CharField(required=False)
    MSISDN = serializers.CharField(required=False)


class B2CTimeoutPayloadSerializer(serializers.Serializer):
    OriginatorConversationID = serializers.CharField(required=False)


class PaymentActivityLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.full_name", read_only=True)
    actor_id = serializers.UUIDField(source="actor.id", read_only=True)

    class Meta:
        model = PaymentActivityLog
        fields = [
            "id",
            "payment_intent",
            "event",
            "meta",
            "actor_id",
            "actor_name",
            "created_at",
        ]
        read_only_fields = fields


class PaymentIntentHistorySerializer(serializers.ModelSerializer):
    stk_transactions = MpesaSTKTransactionSerializer(many=True, read_only=True)
    c2b_transactions = MpesaC2BTransactionSerializer(many=True, read_only=True)
    b2c_payouts = MpesaB2CPayoutSerializer(many=True, read_only=True)
    approvals = serializers.SerializerMethodField()
    phone = serializers.SerializerMethodField()
    refund_count = serializers.SerializerMethodField()
    dispute_count = serializers.SerializerMethodField()

    class Meta:
        model = PaymentIntent
        fields = [
            "id",
            "intent_type",
            "purpose",
            "reference_type",
            "reference_id",
            "amount",
            "currency",
            "status",
            "phone",
            "metadata",
            "created_at",
            "updated_at",
            "stk_transactions",
            "c2b_transactions",
            "b2c_payouts",
            "approvals",
            "refund_count",
            "dispute_count",
        ]

    def get_approvals(self, obj):
        logs = obj.approval_logs.select_related("actor").order_by("created_at")
        return WithdrawalApprovalLogSerializer(logs, many=True).data

    def get_phone(self, obj):
        phone = obj.phone or ""
        if len(phone) < 7:
            return phone
        return f"{phone[:5]}****{phone[-3:]}"

    def get_refund_count(self, obj):
        return obj.refunds.count()

    def get_dispute_count(self, obj):
        return obj.disputes.count()
