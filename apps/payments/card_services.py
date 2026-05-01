"""
Card payment service layer for MyChama.

Orchestrates card payment operations including intent creation,
webhook processing, and ledger integration.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.chama.models import Chama, ChamaStatus, Membership, MemberStatus
from apps.finance.models import (
    Contribution,
    ContributionType,
    MethodChoices,
)
from apps.finance.services import FinanceService
from apps.notifications.models import (
    NotificationCategory,
    NotificationPriority,
    NotificationType,
)
from apps.notifications.services import create_notification
from apps.payments.card_models import (
    CardPaymentAuditLog,
    CardPaymentIntent,
    CardPaymentPurpose,
    CardPaymentReceipt,
    CardPaymentStatus,
    CardPaymentTransaction,
    CardPaymentWebhook,
)
from apps.payments.providers.base import (
    CardPaymentProvider as CardPaymentProviderBase,
)
from apps.payments.providers.base import (
    CardPaymentProviderError,
)
from apps.payments.providers.factory import CardProviderFactory
from core.audit import create_audit_log
from core.constants import CurrencyChoices
from core.utils import to_decimal

logger = logging.getLogger(__name__)


class CardPaymentServiceError(Exception):
    """Base exception for card payment service errors."""
    pass


class CardPaymentService:
    """
    Service for managing card payments.

    Handles the complete lifecycle of card payments including:
    - Payment intent creation
    - Provider integration
    - Webhook processing
    - Ledger posting
    - Receipt generation
    """

    @staticmethod
    def generate_idempotency_key(
        chama_id: uuid.UUID,
        user_id: uuid.UUID,
        amount: Decimal,
        purpose: str,
    ) -> str:
        """Generate a unique idempotency key for a card payment."""
        base = f"card:{chama_id}:{user_id}:{amount}:{purpose}:{uuid.uuid4().hex}"
        if len(base) <= 100:
            return base
        digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:40]
        return f"card:{purpose}:{digest}"

    @staticmethod
    def _validate_card_payment_request(
        chama: Chama,
        user: Any,
        amount: Decimal,
        currency: str,
        purpose: str,
    ) -> None:
        """Validate card payment request."""
        if amount <= Decimal("0.00"):
            raise CardPaymentServiceError("Amount must be greater than zero")

        if chama.status != ChamaStatus.ACTIVE:
            raise CardPaymentServiceError("Chama is not active")

        membership = Membership.objects.filter(
            chama=chama,
            user=user,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
        ).first()

        if not membership:
            raise CardPaymentServiceError("User is not an active member of this chama")

        if currency not in [c.value for c in CurrencyChoices]:
            raise CardPaymentServiceError(f"Unsupported currency: {currency}")

        if purpose not in {choice for choice, _label in CardPaymentPurpose.choices}:
            raise CardPaymentServiceError("Unsupported payment purpose")

    @staticmethod
    def _resolve_contribution_type(
        *,
        chama: Chama,
        purpose: str,
        contribution_type_id: uuid.UUID | None,
    ) -> ContributionType | None:
        if purpose != CardPaymentPurpose.CONTRIBUTION:
            return None

        if not contribution_type_id:
            raise CardPaymentServiceError(
                "Contribution type is required for contribution payments"
            )

        try:
            return ContributionType.objects.get(
                id=contribution_type_id,
                chama=chama,
                is_active=True,
            )
        except ContributionType.DoesNotExist as exc:
            raise CardPaymentServiceError("Contribution type not found") from exc

    @staticmethod
    def _map_provider_status(provider_status: str) -> str:
        normalized = (provider_status or "").lower()
        if normalized in {"succeeded", "success", "completed", "successful"}:
            return CardPaymentStatus.SUCCESS
        if normalized in {"failed", "canceled", "cancelled"}:
            return CardPaymentStatus.FAILED
        if normalized in {"expired"}:
            return CardPaymentStatus.EXPIRED
        if normalized in {
            "requires_action",
            "requires_source_action",
            "pending_authentication",
        }:
            return CardPaymentStatus.PENDING_AUTHENTICATION
        return CardPaymentStatus.PENDING

    @staticmethod
    def _notification_payload(intent: CardPaymentIntent) -> dict[str, Any]:
        return {
            "payment_intent_id": str(intent.id),
            "reference": intent.reference,
            "amount": str(intent.amount),
            "currency": intent.currency,
            "provider": intent.provider,
            "status": intent.status,
            "purpose": intent.purpose,
        }

    @classmethod
    def _notify(
        cls,
        *,
        intent: CardPaymentIntent,
        event: str,
        receipt: CardPaymentReceipt | None = None,
    ) -> None:
        if not intent.user:
            return

        payload = cls._notification_payload(intent)
        if receipt is not None:
            payload.update(
                {
                    "receipt_number": receipt.receipt_number,
                    "receipt_reference": receipt.reference_number,
                }
            )

        config = {
            "initiated": {
                "title": "Card payment initiated",
                "message": (
                    f"We started your card payment for {intent.currency} "
                    f"{intent.amount:,.2f}. Complete checkout to continue."
                ),
                "priority": NotificationPriority.NORMAL,
                "send_email": False,
            },
            "success": {
                "title": "Card payment successful",
                "message": (
                    f"Your card payment of {intent.currency} {intent.amount:,.2f} "
                    "was confirmed successfully."
                ),
                "priority": NotificationPriority.HIGH,
                "send_email": True,
            },
            "failed": {
                "title": "Card payment failed",
                "message": (
                    f"Your card payment of {intent.currency} {intent.amount:,.2f} "
                    "did not complete."
                ),
                "priority": NotificationPriority.HIGH,
                "send_email": False,
            },
            "receipt": {
                "title": "Receipt available",
                "message": (
                    f"Receipt {receipt.receipt_number if receipt else ''} is available "
                    f"for your {intent.currency} {intent.amount:,.2f} card payment."
                ),
                "priority": NotificationPriority.NORMAL,
                "send_email": True,
            },
        }.get(event)
        if not config:
            return

        try:
            create_notification(
                recipient=intent.user,
                chama=intent.chama,
                notification_type=NotificationType.PAYMENT_CONFIRMATION,
                title=config["title"],
                message=config["message"],
                priority=config["priority"],
                category=NotificationCategory.PAYMENTS,
                action_url=f"/payments/card/{intent.id}",
                metadata=payload,
                send_email=config["send_email"],
                send_sms=False,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to send card payment notification for %s (%s)",
                intent.id,
                event,
            )

    @staticmethod
    def _validate_provider_result(
        *,
        intent: CardPaymentIntent,
        result: Any,
    ) -> None:
        if to_decimal(result.amount) != to_decimal(intent.amount):
            raise CardPaymentServiceError("Provider amount mismatch")

        if (result.currency or "").upper() != intent.currency.upper():
            raise CardPaymentServiceError("Provider currency mismatch")

        provider_metadata = result.provider_metadata or {}
        provider_reference = (
            provider_metadata.get("reference")
            or provider_metadata.get("tx_ref")
            or provider_metadata.get("session_reference")
        )
        if provider_reference and provider_reference != intent.reference:
            raise CardPaymentServiceError("Provider reference mismatch")

    @staticmethod
    def _record_failure(
        *,
        intent: CardPaymentIntent,
        previous_status: str,
        failure_reason: str,
        failure_code: str = "",
        metadata: dict[str, Any] | None = None,
        actor=None,
    ) -> None:
        intent.status = CardPaymentStatus.FAILED
        intent.failure_reason = (failure_reason or "Payment failed")[:500]
        intent.failure_code = (failure_code or "")[:50]
        intent.save(
            update_fields=[
                "status",
                "failure_reason",
                "failure_code",
                "updated_at",
            ]
        )

        CardPaymentAuditLog.objects.create(
            payment_intent=intent,
            actor=actor,
            event="payment_failed",
            previous_status=previous_status,
            new_status=CardPaymentStatus.FAILED,
            metadata=metadata or {},
        )

        create_audit_log(
            actor=actor,
            chama_id=intent.chama_id,
            action="card_payment_failed",
            entity_type="CardPaymentIntent",
            entity_id=intent.id,
            metadata={
                "status": intent.status,
                "failure_reason": intent.failure_reason,
                "failure_code": intent.failure_code,
                **(metadata or {}),
            },
        )

    @staticmethod
    def _get_provider(provider_name: str | None = None) -> CardPaymentProviderBase:
        """Get card payment provider instance."""
        try:
            return CardProviderFactory.get_provider(provider_name)
        except CardPaymentProviderError as e:
            raise CardPaymentServiceError(f"Provider error: {e}")

    @classmethod
    def create_payment_intent(
        cls,
        chama: Chama,
        user: Any,
        amount: Decimal,
        currency: str,
        purpose: str,
        description: str = "",
        contribution_type_id: uuid.UUID | None = None,
        provider_name: str | None = None,
        idempotency_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CardPaymentIntent:
        """
        Create a card payment intent.

        Args:
            chama: Chama instance
            user: User instance
            amount: Payment amount
            currency: Currency code
            purpose: Payment purpose
            description: Payment description
            contribution_type_id: Optional contribution type ID for contribution payments
            provider_name: Optional provider name
            idempotency_key: Optional idempotency key
            metadata: Optional metadata

        Returns:
            CardPaymentIntent instance

        Raises:
            CardPaymentServiceError: If intent creation fails
        """
        cls._validate_card_payment_request(chama, user, amount, currency, purpose)
        contribution_type = cls._resolve_contribution_type(
            chama=chama,
            purpose=purpose,
            contribution_type_id=contribution_type_id,
        )

        if idempotency_key is None:
            idempotency_key = cls.generate_idempotency_key(
                chama.id, user.id, amount, purpose
            )

        provider = cls._get_provider(provider_name)
        existing_intent = CardPaymentIntent.objects.filter(
            chama=chama,
            idempotency_key=idempotency_key,
        ).first()
        if existing_intent:
            return existing_intent

        base_metadata = {
            **(metadata or {}),
            "source": "mobile_app",
        }
        if contribution_type is not None:
            base_metadata.update(
                {
                    "contribution_type_id": str(contribution_type.id),
                    "contribution_type_name": contribution_type.name,
                }
            )

        try:
            with transaction.atomic():
                intent = CardPaymentIntent.objects.create(
                    chama=chama,
                    user=user,
                    contribution_type=contribution_type,
                    amount=amount,
                    currency=currency.upper(),
                    purpose=purpose,
                    description=description or f"{purpose.title()} payment",
                    provider=provider.provider_name,
                    provider_intent_id=f"pending_{uuid.uuid4().hex}",
                    idempotency_key=idempotency_key,
                    reference=f"PAY-{uuid.uuid4().hex[:12].upper()}",
                    metadata=base_metadata,
                    expires_at=timezone.now() + timedelta(hours=24),
                    created_by=user,
                    updated_by=user,
                )

                provider_intent = provider.create_payment_intent(
                    amount=amount,
                    currency=currency,
                    reference=intent.reference,
                    description=description or f"{purpose.title()} payment",
                    customer_email=user.email,
                    customer_phone=getattr(user, "phone", None),
                    metadata={
                        "intent_id": str(intent.id),
                        "chama_id": str(chama.id),
                        "user_id": str(user.id),
                        "reference": intent.reference,
                        **base_metadata,
                    },
                    idempotency_key=idempotency_key,
                )

                intent.provider_intent_id = provider_intent.provider_intent_id
                intent.client_secret = provider_intent.client_secret or ""
                intent.checkout_url = provider_intent.checkout_url or ""
                intent.status = cls._map_provider_status(provider_intent.status)
                intent.save(
                    update_fields=[
                        "provider_intent_id",
                        "client_secret",
                        "checkout_url",
                        "status",
                        "updated_at",
                    ]
                )

                CardPaymentAuditLog.objects.create(
                    payment_intent=intent,
                    actor=user,
                    event="intent_created",
                    new_status=intent.status,
                    metadata={
                        "provider": provider.provider_name,
                        "provider_intent_id": provider_intent.provider_intent_id,
                        "checkout_url_present": bool(intent.checkout_url),
                        "client_secret_present": bool(intent.client_secret),
                    },
                )

                create_audit_log(
                    actor=user,
                    chama_id=chama.id,
                    action="card_payment_intent_created",
                    entity_type="CardPaymentIntent",
                    entity_id=intent.id,
                    metadata={
                        "amount": str(intent.amount),
                        "currency": intent.currency,
                        "purpose": intent.purpose,
                        "provider": intent.provider,
                    },
                )

                cls._notify(intent=intent, event="initiated")

                logger.info(
                    "Card payment intent created: %s for chama %s, user %s",
                    intent.id,
                    chama.id,
                    user.id,
                )

                return intent

        except IntegrityError as e:
            logger.error("Card payment intent creation failed (integrity): %s", e)
            raise CardPaymentServiceError("Payment intent already exists")
        except CardPaymentProviderError as e:
            logger.error("Card payment intent creation failed (provider): %s", e)
            raise CardPaymentServiceError(
                "Unable to start card checkout right now. Please try again."
            )
        except Exception as e:
            logger.error("Card payment intent creation failed: %s", e)
            raise CardPaymentServiceError("Failed to create payment intent")

    @classmethod
    def get_payment_status(cls, intent_id: uuid.UUID) -> CardPaymentIntent:
        """
        Get payment intent status.

        Args:
            intent_id: Payment intent ID

        Returns:
            CardPaymentIntent instance

        Raises:
            CardPaymentServiceError: If intent not found
        """
        try:
            intent = CardPaymentIntent.objects.get(id=intent_id)
            return intent
        except CardPaymentIntent.DoesNotExist:
            raise CardPaymentServiceError("Payment intent not found")

    @classmethod
    def verify_payment(cls, intent_id: uuid.UUID) -> CardPaymentIntent:
        """
        Verify payment status with provider.

        Args:
            intent_id: Payment intent ID

        Returns:
            Updated CardPaymentIntent instance

        Raises:
            CardPaymentServiceError: If verification fails
        """
        try:
            intent = CardPaymentIntent.objects.select_related(
                "chama",
                "user",
                "contribution",
                "contribution_type",
            ).get(id=intent_id)

            if intent.is_terminal:
                return intent

            provider = cls._get_provider(intent.provider)

            result = provider.verify_payment(intent.provider_intent_id)
            cls._validate_provider_result(intent=intent, result=result)

            with transaction.atomic():
                old_status = intent.status
                next_status = cls._map_provider_status(result.status)

                if result.failure_reason:
                    intent.failure_reason = result.failure_reason[:500]

                intent.status = next_status
                intent.save(
                    update_fields=[
                        "status",
                        "failure_reason",
                        "updated_at",
                    ]
                )

                transaction_defaults = {
                    "provider_name": intent.provider,
                    "amount": result.amount,
                    "currency": result.currency,
                    "status": intent.status,
                    "card_brand": result.card_brand or "",
                    "card_last4": result.card_last4 or "",
                    "authorization_code": result.authorization_code or "",
                    "raw_response": result.provider_metadata or {},
                    "paid_at": timezone.now()
                    if intent.status == CardPaymentStatus.SUCCESS
                    else None,
                    "failed_at": timezone.now()
                    if intent.status == CardPaymentStatus.FAILED
                    else None,
                    "created_by": intent.user,
                    "updated_by": intent.user,
                }
                transaction_record, created = CardPaymentTransaction.objects.update_or_create(
                    provider_reference=result.provider_reference,
                    defaults={
                        "payment_intent": intent,
                        **transaction_defaults,
                    },
                )
                if not created:
                    transaction_record.status = intent.status
                    transaction_record.save(update_fields=["status", "updated_at"])

                CardPaymentAuditLog.objects.create(
                    payment_intent=intent,
                    actor=intent.user,
                    event="payment_verified",
                    previous_status=old_status,
                    new_status=intent.status,
                    metadata={
                        "provider_reference": result.provider_reference,
                        "card_brand": result.card_brand,
                        "card_last4": result.card_last4,
                        "verification_result": result.status,
                    },
                )

                create_audit_log(
                    actor=intent.user,
                    chama_id=intent.chama_id,
                    action="card_payment_verified",
                    entity_type="CardPaymentIntent",
                    entity_id=intent.id,
                    metadata={
                        "previous_status": old_status,
                        "new_status": intent.status,
                        "provider_reference": result.provider_reference,
                    },
                )

                if intent.status == CardPaymentStatus.SUCCESS:
                    cls._handle_successful_payment(intent, transaction_record)
                    cls._notify(intent=intent, event="success")
                elif intent.status == CardPaymentStatus.FAILED:
                    cls._notify(intent=intent, event="failed")

                logger.info(
                    "Card payment verified: %s, status: %s",
                    intent.id,
                    intent.status,
                )

                return intent

        except CardPaymentIntent.DoesNotExist:
            raise CardPaymentServiceError("Payment intent not found")
        except CardPaymentProviderError as e:
            logger.error("Card payment verification failed: %s", e)
            raise CardPaymentServiceError(
                "We could not verify the card payment right now. Please try again shortly."
            )
        except Exception as e:
            logger.error("Card payment verification failed: %s", e)
            raise CardPaymentServiceError("Failed to verify payment")

    @classmethod
    def _handle_successful_payment(
        cls,
        intent: CardPaymentIntent,
        transaction_record: CardPaymentTransaction,
    ) -> None:
        """
        Handle successful card payment.

        Posts ledger entries and generates receipt.
        """
        try:
            with transaction.atomic():
                if CardPaymentReceipt.objects.filter(payment_intent=intent).exists():
                    return

                if intent.purpose == CardPaymentPurpose.CONTRIBUTION:
                    cls._post_contribution_ledger(intent, transaction_record)
                    intent.refresh_from_db(fields=["contribution"])

                receipt, _ = CardPaymentReceipt.objects.get_or_create(
                    payment_intent=intent,
                    defaults={
                        "transaction": transaction_record,
                        "amount": intent.amount,
                        "currency": intent.currency,
                        "card_brand": transaction_record.card_brand,
                        "card_last4": transaction_record.card_last4,
                        "issued_by": intent.user,
                        "metadata": {
                            "provider_reference": transaction_record.provider_reference,
                            "authorization_code": transaction_record.authorization_code,
                        },
                        "created_by": intent.user,
                        "updated_by": intent.user,
                    },
                )

                CardPaymentAuditLog.objects.create(
                    payment_intent=intent,
                    actor=intent.user,
                    event="payment_completed",
                    new_status=CardPaymentStatus.SUCCESS,
                    metadata={
                        "receipt_number": receipt.receipt_number,
                        "reference_number": receipt.reference_number,
                    },
                )

                logger.info(
                    "Card payment completed: %s, receipt: %s",
                    intent.id,
                    receipt.receipt_number,
                )

                cls._notify(intent=intent, event="receipt", receipt=receipt)

        except Exception as e:
            logger.error("Failed to handle successful payment %s: %s", intent.id, e)
            raise

    @classmethod
    def _post_contribution_ledger(
        cls,
        intent: CardPaymentIntent,
        transaction_record: CardPaymentTransaction,
    ) -> None:
        """
        Post ledger entries for contribution payment.

        Debit: Cash/Card Clearing Account
        Credit: Contributions Account
        """
        try:
            if intent.contribution_id:
                return

            if not intent.user or not intent.contribution_type_id:
                raise CardPaymentServiceError(
                    "Contribution card payment is missing member or contribution type context"
                )

            idempotency_key = f"card_ledger:{intent.id}"
            receipt_code = intent.reference or f"PAY-{intent.id.hex[:12].upper()}"
            existing_contribution = Contribution.objects.filter(
                receipt_code=receipt_code
            ).first()
            if existing_contribution:
                intent.contribution = existing_contribution
                intent.save(update_fields=["contribution", "updated_at"])
                return

            post_result = FinanceService.post_contribution(
                payload={
                    "chama_id": str(intent.chama_id),
                    "member_id": str(intent.user_id),
                    "contribution_type_id": str(intent.contribution_type_id),
                    "amount": str(intent.amount),
                    "date_paid": timezone.localdate().isoformat(),
                    "method": MethodChoices.CARD,
                    "receipt_code": receipt_code,
                    "idempotency_key": idempotency_key,
                },
                actor=intent.user,
            )

            contribution = post_result.created
            intent.contribution = contribution
            intent.save(update_fields=["contribution", "updated_at"])

            CardPaymentAuditLog.objects.create(
                payment_intent=intent,
                actor=intent.user,
                event="ledger_posted",
                metadata={
                    "contribution_id": str(contribution.id),
                    "ledger_entry_id": str(post_result.ledger_entry.id),
                    "idempotency_key": idempotency_key,
                },
            )

            create_audit_log(
                actor=intent.user,
                chama_id=intent.chama_id,
                action="card_payment_posted_to_ledger",
                entity_type="CardPaymentIntent",
                entity_id=intent.id,
                metadata={
                    "contribution_id": str(contribution.id),
                    "ledger_entry_id": str(post_result.ledger_entry.id),
                },
            )

            logger.info(
                "Ledger posted for card payment %s, contribution %s",
                intent.id,
                contribution.id,
            )

        except Exception as e:
            logger.error("Failed to post ledger for payment %s: %s", intent.id, e)
            raise

    @classmethod
    def process_webhook(
        cls,
        provider_name: str,
        payload: bytes,
        signature: str | None,
        headers: dict[str, str] | None = None,
        source_ip: str | None = None,
    ) -> CardPaymentWebhook:
        """
        Process webhook from payment provider.

        Args:
            provider_name: Provider name
            payload: Raw webhook payload
            signature: Signature header
            headers: All request headers
            source_ip: Source IP address

        Returns:
            CardPaymentWebhook instance

        Raises:
            CardPaymentServiceError: If webhook processing fails
        """
        import json

        try:
            provider = cls._get_provider(provider_name)

            verification_result = provider.verify_webhook_signature(
                payload, signature, headers
            )

            webhook_log = CardPaymentWebhook.objects.create(
                provider=provider_name,
                event_type=verification_result.event_type or "unknown",
                provider_reference=verification_result.provider_reference or "",
                signature_valid=verification_result.is_valid,
                signature=signature or "",
                payload=json.loads(payload) if verification_result.is_valid else {},
                headers=headers or {},
                source_ip=source_ip,
            )

            if not verification_result.is_valid:
                logger.warning(
                    "Webhook signature verification failed for %s: %s",
                    provider_name,
                    verification_result.error,
                )
                webhook_log.processing_error = f"Signature verification failed: {verification_result.error}"
                webhook_log.save()
                return webhook_log

            event_type, provider_reference, metadata = provider.parse_webhook_event(
                verification_result.payload
            )

            webhook_log.event_type = event_type
            webhook_log.provider_reference = provider_reference or ""
            webhook_log.save()

            if event_type in [
                "payment_intent.succeeded",
                "charge.succeeded",
                "payment.successful",
            ]:
                cls._handle_webhook_success(provider_reference, metadata, webhook_log)
            elif event_type in [
                "payment_intent.payment_failed",
                "charge.failed",
                "payment.failed",
            ]:
                cls._handle_webhook_failure(provider_reference, metadata, webhook_log)

            webhook_log.processed = True
            webhook_log.processed_at = timezone.now()
            webhook_log.save()

            logger.info(
                "Webhook processed: %s %s for %s",
                provider_name,
                event_type,
                provider_reference,
            )

            return webhook_log

        except json.JSONDecodeError as e:
            logger.error("Webhook JSON decode error: %s", e)
            raise CardPaymentServiceError(f"Invalid webhook payload: {e}")
        except Exception as e:
            logger.error("Webhook processing failed: %s", e)
            raise CardPaymentServiceError(f"Failed to process webhook: {e}")

    @classmethod
    def _handle_webhook_success(
        cls,
        provider_reference: str | None,
        metadata: dict[str, Any],
        webhook_log: CardPaymentWebhook,
    ) -> None:
        """Handle successful payment webhook."""
        if not provider_reference:
            logger.warning("Webhook success without provider reference")
            return

        try:
            intent = CardPaymentIntent.objects.filter(
                provider_intent_id=provider_reference
            ).first()
            if intent is None and metadata.get("intent_id"):
                intent = CardPaymentIntent.objects.filter(
                    id=metadata["intent_id"]
                ).first()
            if intent is None and metadata.get("reference"):
                intent = CardPaymentIntent.objects.filter(
                    reference=metadata["reference"]
                ).first()
            if intent is None:
                raise CardPaymentIntent.DoesNotExist

            if intent.is_terminal:
                logger.info("Payment %s already in terminal state", intent.id)
                return

            cls.verify_payment(intent.id)

        except CardPaymentIntent.DoesNotExist:
            logger.warning(
                "Payment intent not found for provider reference: %s",
                provider_reference,
            )
        except Exception as e:
            logger.error("Failed to handle webhook success: %s", e)
            webhook_log.processing_error = str(e)
            webhook_log.save()

    @classmethod
    def _handle_webhook_failure(
        cls,
        provider_reference: str | None,
        metadata: dict[str, Any],
        webhook_log: CardPaymentWebhook,
    ) -> None:
        """Handle failed payment webhook."""
        if not provider_reference:
            logger.warning("Webhook failure without provider reference")
            return

        try:
            intent = CardPaymentIntent.objects.filter(
                provider_intent_id=provider_reference
            ).first()
            if intent is None and metadata.get("intent_id"):
                intent = CardPaymentIntent.objects.filter(
                    id=metadata["intent_id"]
                ).first()
            if intent is None and metadata.get("reference"):
                intent = CardPaymentIntent.objects.filter(
                    reference=metadata["reference"]
                ).first()
            if intent is None:
                raise CardPaymentIntent.DoesNotExist

            if intent.is_terminal:
                logger.info("Payment %s already in terminal state", intent.id)
                return

            with transaction.atomic():
                old_status = intent.status
                cls._record_failure(
                    intent=intent,
                    previous_status=old_status,
                    failure_reason=metadata.get("failure_reason", "Payment failed"),
                    failure_code=metadata.get("failure_code", "unknown"),
                    metadata=metadata,
                    actor=intent.user,
                )
                cls._notify(intent=intent, event="failed")

                logger.info("Payment %s marked as failed via webhook", intent.id)

        except CardPaymentIntent.DoesNotExist:
            logger.warning(
                "Payment intent not found for provider reference: %s",
                provider_reference,
            )
        except Exception as e:
            logger.error("Failed to handle webhook failure: %s", e)
            webhook_log.processing_error = str(e)
            webhook_log.save()

    @classmethod
    def get_payment_receipt(cls, intent_id: uuid.UUID) -> CardPaymentReceipt:
        """
        Get payment receipt.

        Args:
            intent_id: Payment intent ID

        Returns:
            CardPaymentReceipt instance

        Raises:
            CardPaymentServiceError: If receipt not found
        """
        try:
            intent = CardPaymentIntent.objects.get(id=intent_id)

            if intent.status != CardPaymentStatus.SUCCESS:
                raise CardPaymentServiceError("Payment not successful")

            receipt = CardPaymentReceipt.objects.get(payment_intent=intent)
            return receipt

        except CardPaymentIntent.DoesNotExist:
            raise CardPaymentServiceError("Payment intent not found")
        except CardPaymentReceipt.DoesNotExist:
            raise CardPaymentServiceError("Receipt not found")

    @classmethod
    def get_user_payments(
        cls,
        user: Any,
        chama_id: uuid.UUID | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[CardPaymentIntent]:
        """
        Get user's card payments.

        Args:
            user: User instance
            chama_id: Optional chama ID filter
            status: Optional status filter
            limit: Maximum number of results

        Returns:
            List of CardPaymentIntent instances
        """
        queryset = CardPaymentIntent.objects.filter(user=user)

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)

        if status:
            queryset = queryset.filter(status=status)

        return list(queryset.order_by("-created_at")[:limit])

    @classmethod
    def get_chama_payments(
        cls,
        chama: Chama,
        status: str | None = None,
        start_date: Any | None = None,
        end_date: Any | None = None,
        limit: int = 100,
    ) -> list[CardPaymentIntent]:
        """
        Get chama's card payments.

        Args:
            chama: Chama instance
            status: Optional status filter
            start_date: Optional start date filter
            end_date: Optional end date filter
            limit: Maximum number of results

        Returns:
            List of CardPaymentIntent instances
        """
        queryset = CardPaymentIntent.objects.filter(chama=chama)

        if status:
            queryset = queryset.filter(status=status)

        if start_date:
            queryset = queryset.filter(created_at__gte=start_date)

        if end_date:
            queryset = queryset.filter(created_at__lte=end_date)

        return list(queryset.order_by("-created_at")[:limit])
