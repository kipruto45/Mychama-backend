"""
Card payment serializers for MyChama.

Serializers for card payment API endpoints.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from rest_framework import serializers

from apps.payments.card_models import (
    CardPaymentAuditLog,
    CardPaymentIntent,
    CardPaymentProvider,
    CardPaymentPurpose,
    CardPaymentReceipt,
    CardPaymentStatus,
    CardPaymentTransaction,
)


class CardPaymentIntentCreateSerializer(serializers.Serializer):
    """Serializer for creating card payment intent."""

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
    purpose = serializers.ChoiceField(
        choices=CardPaymentPurpose.choices,
        default=CardPaymentPurpose.CONTRIBUTION,
    )
    description = serializers.CharField(required=False, allow_blank=True, default="")
    contribution_type_id = serializers.UUIDField(required=False, allow_null=True)
    contribution_id = serializers.UUIDField(required=False, allow_null=True, write_only=True)
    provider = serializers.ChoiceField(
        choices=CardPaymentProvider.choices,
        required=False,
        default=CardPaymentProvider.STRIPE,
    )
    idempotency_key = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=100,
    )
    metadata = serializers.DictField(required=False, default=dict)

    def validate_amount(self, value: Decimal) -> Decimal:
        """Validate amount is positive."""
        if value <= Decimal("0.00"):
            raise serializers.ValidationError("Amount must be greater than zero")
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        legacy_contribution_id = attrs.pop("contribution_id", None)
        if not attrs.get("contribution_type_id") and legacy_contribution_id:
            attrs["contribution_type_id"] = legacy_contribution_id
        return attrs


class CardPaymentIntentResponseSerializer(serializers.ModelSerializer):
    """Serializer for card payment intent response."""

    user_name = serializers.CharField(source="user.get_full_name", read_only=True)
    chama_name = serializers.CharField(source="chama.name", read_only=True)
    contribution_type = serializers.CharField(
        source="contribution_type.name",
        read_only=True,
        default=None,
    )
    contribution_type_id = serializers.UUIDField(
        source="contribution_type.id",
        read_only=True,
        default=None,
    )

    class Meta:
        model = CardPaymentIntent
        fields = [
            "id",
            "chama",
            "chama_name",
            "user",
            "user_name",
            "contribution",
            "contribution_type",
            "contribution_type_id",
            "amount",
            "currency",
            "purpose",
            "description",
            "provider",
            "provider_intent_id",
            "client_secret",
            "checkout_url",
            "status",
            "idempotency_key",
            "reference",
            "failure_reason",
            "failure_code",
            "metadata",
            "expires_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class CardPaymentTransactionSerializer(serializers.ModelSerializer):
    """Serializer for card payment transaction."""

    class Meta:
        model = CardPaymentTransaction
        fields = [
            "id",
            "payment_intent",
            "provider_reference",
            "provider_name",
            "amount",
            "currency",
            "status",
            "card_brand",
            "card_last4",
            "card_country",
            "authorization_code",
            "paid_at",
            "failed_at",
            "created_at",
        ]
        read_only_fields = fields


class CardPaymentReceiptSerializer(serializers.ModelSerializer):
    """Serializer for card payment receipt."""

    payment_intent_id = serializers.UUIDField(source="payment_intent.id", read_only=True)
    transaction_id = serializers.UUIDField(source="transaction.id", read_only=True)

    class Meta:
        model = CardPaymentReceipt
        fields = [
            "id",
            "payment_intent_id",
            "transaction_id",
            "reference_number",
            "receipt_number",
            "amount",
            "currency",
            "card_brand",
            "card_last4",
            "issued_at",
            "metadata",
            "created_at",
        ]
        read_only_fields = fields


class CardPaymentAuditLogSerializer(serializers.ModelSerializer):
    """Serializer for card payment audit log."""

    actor_name = serializers.CharField(
        source="actor.get_full_name",
        read_only=True,
        default=None,
    )

    class Meta:
        model = CardPaymentAuditLog
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


class CardPaymentStatusResponseSerializer(serializers.Serializer):
    """Serializer for payment status response."""

    intent = CardPaymentIntentResponseSerializer()
    transactions = CardPaymentTransactionSerializer(many=True)
    receipt = CardPaymentReceiptSerializer(required=False, allow_null=True)
    audit_logs = CardPaymentAuditLogSerializer(many=True)


class CardPaymentWebhookSerializer(serializers.Serializer):
    """Serializer for webhook endpoint."""

    provider = serializers.ChoiceField(choices=CardPaymentProvider.choices)
    payload = serializers.JSONField(required=False)
    signature = serializers.CharField(required=False, allow_blank=True)


class CardPaymentListSerializer(serializers.ModelSerializer):
    """Serializer for listing card payments."""

    user_name = serializers.CharField(source="user.get_full_name", read_only=True)
    chama_name = serializers.CharField(source="chama.name", read_only=True)
    card_info = serializers.SerializerMethodField()

    class Meta:
        model = CardPaymentIntent
        fields = [
            "id",
            "chama",
            "chama_name",
            "user",
            "user_name",
            "amount",
            "currency",
            "purpose",
            "status",
            "provider",
            "reference",
            "card_info",
            "failure_reason",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_card_info(self, obj: CardPaymentIntent) -> dict[str, str] | None:
        """Get masked card information from latest transaction."""
        transaction = obj.transactions.order_by("-created_at").first()
        if transaction and transaction.card_last4:
            return {
                "brand": transaction.card_brand,
                "last4": transaction.card_last4,
            }
        return None


class CardPaymentFilterSerializer(serializers.Serializer):
    """Serializer for filtering card payments."""

    status = serializers.ChoiceField(
        choices=CardPaymentStatus.choices,
        required=False,
    )
    provider = serializers.ChoiceField(
        choices=CardPaymentProvider.choices,
        required=False,
    )
    purpose = serializers.ChoiceField(
        choices=CardPaymentPurpose.choices,
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


class CardPaymentRefundSerializer(serializers.Serializer):
    """Serializer for refunding card payment."""

    amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=False,
        allow_null=True,
    )
    reason = serializers.CharField(required=False, allow_blank=True)


class CardPaymentConfirmSerializer(serializers.Serializer):
    """Serializer for confirming client return."""

    intent_id = serializers.UUIDField(required=True)
    status = serializers.CharField(required=False, allow_blank=True)
