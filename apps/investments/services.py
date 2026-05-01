from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.chama.models import Membership, MembershipRole, MemberStatus
from apps.finance.member_wallet_workflow import _get_member_wallet
from apps.finance.models import WalletOwnerType
from apps.notifications.models import (
    NotificationCategory,
    NotificationPriority,
    NotificationType,
)
from apps.notifications.services import create_notification
from apps.payments.unified_models import (
    PaymentIntent,
    PaymentMethod,
    PaymentPurpose,
    PaymentStatus,
)
from apps.payments.unified_services import PaymentServiceError, UnifiedPaymentService
from core.audit import create_activity_log, create_audit_log
from core.utils import normalize_kenyan_phone

from .models import (
    InvestmentFundingSource,
    InvestmentPayout,
    InvestmentPayoutDestination,
    InvestmentPayoutFrequency,
    InvestmentPayoutKind,
    InvestmentProduct,
    InvestmentProductStatus,
    InvestmentRedemptionRequest,
    InvestmentRedemptionType,
    InvestmentRequestStatus,
    InvestmentReturnLedger,
    InvestmentReturnLedgerStatus,
    InvestmentTransactionRecord,
    InvestmentTransactionRecordType,
    InvestmentUtilizationAction,
    MemberInvestmentPosition,
    MemberInvestmentPositionStatus,
)

ZERO = Decimal("0.00")
HUNDRED = Decimal("100.00")
ADMIN_INVESTMENT_ROLES = {
    MembershipRole.SUPERADMIN,
    MembershipRole.ADMIN,
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.TREASURER,
}


class InvestmentServiceError(Exception):
    """Base exception for investment workflow failures."""


@dataclass
class ProjectionBreakdown:
    gross_returns: Decimal
    management_fee: Decimal
    withholding_tax: Decimal
    net_returns: Decimal
    expected_value: Decimal


class InvestmentService:
    @staticmethod
    def _to_decimal(value: Any) -> Decimal:
        if isinstance(value, Decimal):
            return value
        if value in (None, "", False):
            return ZERO
        return Decimal(str(value))

    @staticmethod
    def _money(value: Any) -> Decimal:
        return InvestmentService._to_decimal(value).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

    @staticmethod
    def _normalize_phone(phone: str | None) -> str:
        if not phone:
            return ""
        try:
            return normalize_kenyan_phone(phone)
        except Exception:  # noqa: BLE001
            return str(phone).strip()

    @classmethod
    def _require_membership(cls, *, user, chama_id) -> Membership:
        membership = Membership.objects.select_related("chama").filter(
            user=user,
            chama_id=chama_id,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
        ).first()
        if not membership:
            raise InvestmentServiceError("You must be an active member of this chama.")
        return membership

    @classmethod
    def _require_admin_membership(cls, *, user, chama_id) -> Membership:
        membership = cls._require_membership(user=user, chama_id=chama_id)
        if membership.role not in ADMIN_INVESTMENT_ROLES and not getattr(user, "is_staff", False):
            raise InvestmentServiceError("You are not allowed to manage investment products.")
        return membership

    @classmethod
    def _notify(
        cls,
        *,
        recipient,
        chama,
        title: str,
        message: str,
        metadata: dict[str, Any] | None = None,
        priority: str = NotificationPriority.NORMAL,
    ) -> None:
        create_notification(
            recipient=recipient,
            chama=chama,
            notification_type=NotificationType.PAYMENT_CONFIRMATION,
            title=title,
            message=message,
            priority=priority,
            category=NotificationCategory.PAYMENTS,
            metadata=metadata or {},
        )

    @classmethod
    def calculate_projection(
        cls,
        *,
        product: InvestmentProduct,
        amount: Decimal,
        elapsed_days: int | None = None,
    ) -> ProjectionBreakdown:
        amount = cls._money(amount)
        period_days = max(int(elapsed_days or product.term_days or 1), 1)
        annual_factor = Decimal(period_days) / Decimal("365")
        gross_returns = amount * (cls._to_decimal(product.expected_return_rate) / HUNDRED) * annual_factor
        gross_returns = cls._money(gross_returns)
        management_fee = cls._money(gross_returns * (cls._to_decimal(product.management_fee_rate) / HUNDRED))
        taxable_amount = max(gross_returns - management_fee, ZERO)
        withholding_tax = cls._money(taxable_amount * (cls._to_decimal(product.withholding_tax_rate) / HUNDRED))
        net_returns = cls._money(max(gross_returns - management_fee - withholding_tax, ZERO))
        expected_value = cls._money(amount + net_returns)
        return ProjectionBreakdown(
            gross_returns=gross_returns,
            management_fee=management_fee,
            withholding_tax=withholding_tax,
            net_returns=net_returns,
            expected_value=expected_value,
        )

    @classmethod
    def validate_product_and_amount(
        cls,
        *,
        product: InvestmentProduct,
        amount: Decimal,
        funding_source: str,
    ) -> None:
        amount = cls._money(amount)
        if product.status != InvestmentProductStatus.ACTIVE:
            raise InvestmentServiceError("This investment product is not available right now.")
        if amount < cls._money(product.minimum_amount):
            raise InvestmentServiceError("Amount is below the minimum investment.")
        if product.maximum_amount and amount > cls._money(product.maximum_amount):
            raise InvestmentServiceError("Amount exceeds the maximum allowed for this product.")
        if funding_source == InvestmentFundingSource.WALLET and not product.wallet_funding_enabled:
            raise InvestmentServiceError("This product cannot be funded from wallet.")
        if funding_source == InvestmentFundingSource.MPESA and not product.mpesa_funding_enabled:
            raise InvestmentServiceError("This product cannot be funded through M-Pesa.")
        if funding_source == InvestmentFundingSource.HYBRID and not product.hybrid_funding_enabled:
            raise InvestmentServiceError("This product does not support combined funding.")

    @classmethod
    def _build_next_payout_date(cls, *, funded_at, product: InvestmentProduct):
        if not funded_at:
            return None
        if product.payout_frequency == InvestmentPayoutFrequency.MONTHLY:
            return funded_at + timezone.timedelta(days=30)
        if product.payout_frequency == InvestmentPayoutFrequency.QUARTERLY:
            return funded_at + timezone.timedelta(days=90)
        if product.payout_frequency == InvestmentPayoutFrequency.ON_MATURITY:
            return funded_at + timezone.timedelta(days=product.term_days)
        return None

    @classmethod
    def refresh_position_metrics(cls, investment: MemberInvestmentPosition) -> MemberInvestmentPosition:
        if investment.funded_at is None:
            return investment

        now = timezone.now()
        elapsed_days = max((now - investment.funded_at).days, 0)
        capped_days = min(elapsed_days, max(investment.product.term_days, 1))
        outstanding_principal = cls._money(investment.principal_amount - investment.redeemed_principal)
        projection = cls.calculate_projection(
            product=investment.product,
            amount=outstanding_principal,
            elapsed_days=capped_days,
        )
        available_returns = cls._money(max(projection.net_returns - investment.realized_returns, ZERO))

        investment.accrued_returns = projection.net_returns
        investment.available_returns = available_returns
        investment.current_value = cls._money(outstanding_principal + available_returns)
        investment.expected_value_at_maturity = cls.calculate_projection(
            product=investment.product,
            amount=outstanding_principal,
        ).expected_value
        investment.next_payout_date = cls._build_next_payout_date(
            funded_at=investment.funded_at,
            product=investment.product,
        )
        investment.last_accrual_at = now
        if investment.maturity_date and now >= investment.maturity_date and investment.status in {
            MemberInvestmentPositionStatus.ACTIVE,
            MemberInvestmentPositionStatus.PARTIALLY_REDEEMED,
        }:
            investment.status = MemberInvestmentPositionStatus.MATURED
        investment.save(
            update_fields=[
                "accrued_returns",
                "available_returns",
                "current_value",
                "expected_value_at_maturity",
                "next_payout_date",
                "last_accrual_at",
                "status",
                "updated_at",
            ]
        )

        ledger, created = InvestmentReturnLedger.objects.get_or_create(
            investment=investment,
            period_start=investment.funded_at.date(),
            period_end=now.date(),
            defaults={
                "gross_returns": projection.gross_returns,
                "management_fee": projection.management_fee,
                "withholding_tax": projection.withholding_tax,
                "net_returns": projection.net_returns,
                "available_amount": available_returns,
                "utilized_amount": investment.realized_returns,
                "status": (
                    InvestmentReturnLedgerStatus.AVAILABLE
                    if available_returns > ZERO
                    else InvestmentReturnLedgerStatus.ACCRUED
                ),
                "available_at": now if available_returns > ZERO else None,
                "created_by": investment.member,
                "updated_by": investment.member,
            },
        )
        if not created:
            ledger.gross_returns = projection.gross_returns
            ledger.management_fee = projection.management_fee
            ledger.withholding_tax = projection.withholding_tax
            ledger.net_returns = projection.net_returns
            ledger.available_amount = available_returns
            ledger.utilized_amount = investment.realized_returns
            ledger.status = (
                InvestmentReturnLedgerStatus.AVAILABLE
                if available_returns > ZERO
                else InvestmentReturnLedgerStatus.ACCRUED
            )
            if available_returns > ZERO and ledger.available_at is None:
                ledger.available_at = now
            ledger.save(
                update_fields=[
                    "gross_returns",
                    "management_fee",
                    "withholding_tax",
                    "net_returns",
                    "available_amount",
                    "utilized_amount",
                    "status",
                    "available_at",
                    "updated_at",
                ]
            )
        return investment

    @classmethod
    def create_product(cls, *, actor, chama_id, payload: dict[str, Any]) -> InvestmentProduct:
        membership = cls._require_admin_membership(user=actor, chama_id=chama_id)
        product = InvestmentProduct.objects.create(
            chama=membership.chama,
            created_by=actor,
            updated_by=actor,
            **payload,
        )
        create_audit_log(
            actor=actor,
            chama_id=membership.chama_id,
            action="investment_product_created",
            entity_type="InvestmentProduct",
            entity_id=product.id,
            metadata={"product_code": product.code, "product_name": product.name},
        )
        return product

    @classmethod
    def start_investment(
        cls,
        *,
        actor,
        chama_id,
        product: InvestmentProduct,
        amount: Decimal,
        funding_source: str,
        wallet_amount: Decimal = ZERO,
        mpesa_amount: Decimal = ZERO,
        phone: str = "",
        auto_reinvest: bool = False,
        idempotency_key: str | None = None,
    ) -> MemberInvestmentPosition:
        membership = cls._require_membership(user=actor, chama_id=chama_id)
        amount = cls._money(amount)
        wallet_amount = cls._money(wallet_amount)
        mpesa_amount = cls._money(mpesa_amount)
        cls.validate_product_and_amount(product=product, amount=amount, funding_source=funding_source)

        if funding_source == InvestmentFundingSource.WALLET:
            wallet_amount = amount
            mpesa_amount = ZERO
        elif funding_source == InvestmentFundingSource.MPESA:
            wallet_amount = ZERO
            mpesa_amount = amount
        else:
            if wallet_amount <= ZERO or mpesa_amount <= ZERO or cls._money(wallet_amount + mpesa_amount) != amount:
                raise InvestmentServiceError("Split the wallet and M-Pesa amounts correctly.")

        projection = cls.calculate_projection(product=product, amount=amount)

        with transaction.atomic():
            investment = MemberInvestmentPosition.objects.create(
                chama=membership.chama,
                product=product,
                member=actor,
                funding_source=funding_source,
                currency=product.currency,
                principal_amount=amount,
                wallet_funded_amount=wallet_amount,
                mpesa_funded_amount=mpesa_amount,
                current_value=amount if mpesa_amount == ZERO else ZERO,
                accrued_returns=ZERO,
                available_returns=ZERO,
                expected_value_at_maturity=projection.expected_value,
                auto_reinvest=bool(auto_reinvest and product.auto_reinvest_available),
                beneficiary_phone=cls._normalize_phone(phone) or getattr(actor, "phone", ""),
                maturity_date=timezone.now() + timezone.timedelta(days=product.term_days),
                started_at=timezone.now(),
                status=(
                    MemberInvestmentPositionStatus.ACTIVE
                    if mpesa_amount == ZERO
                    else MemberInvestmentPositionStatus.PENDING_FUNDING
                ),
                funded_at=timezone.now() if mpesa_amount == ZERO else None,
                created_by=actor,
                updated_by=actor,
                metadata={
                    "funding_source": funding_source,
                    "requested_wallet_amount": str(wallet_amount),
                    "requested_mpesa_amount": str(mpesa_amount),
                },
            )

            if wallet_amount > ZERO:
                wallet = _get_member_wallet(actor, currency=product.currency, lock_for_update=True)
                if cls._money(wallet.available_balance) < wallet_amount:
                    raise InvestmentServiceError("You do not have enough wallet balance for this investment.")
                wallet.available_balance = cls._money(wallet.available_balance - wallet_amount)
                wallet.save(update_fields=["available_balance", "updated_at"])
                InvestmentTransactionRecord.objects.create(
                    investment=investment,
                    product=product,
                    chama=membership.chama,
                    member=actor,
                    transaction_type=InvestmentTransactionRecordType.FUNDED,
                    status=InvestmentRequestStatus.COMPLETED,
                    amount=wallet_amount,
                    net_amount=wallet_amount,
                    destination=InvestmentPayoutDestination.WALLET,
                    wallet=wallet,
                    notes="Funded from member wallet",
                    created_by=actor,
                    updated_by=actor,
                )

            payment_intent = None
            if mpesa_amount > ZERO:
                payment_intent = UnifiedPaymentService.create_payment_intent(
                    chama=membership.chama,
                    user=actor,
                    amount=mpesa_amount,
                    currency=product.currency,
                    payment_method=PaymentMethod.MPESA,
                    purpose=PaymentPurpose.OTHER,
                    description=f"Funding investment {product.name}",
                    idempotency_key=idempotency_key,
                    phone=cls._normalize_phone(phone) or getattr(actor, "phone", ""),
                    metadata={
                        "investment_position_id": str(investment.id),
                        "investment_reference": investment.reference,
                        "product_id": str(product.id),
                        "funding_source": funding_source,
                        "wallet_component": str(wallet_amount),
                        "mpesa_component": str(mpesa_amount),
                        "source": "member_investment_funding",
                    },
                )
                InvestmentTransactionRecord.objects.create(
                    investment=investment,
                    product=product,
                    chama=membership.chama,
                    member=actor,
                    transaction_type=InvestmentTransactionRecordType.CREATED,
                    status=InvestmentRequestStatus.PENDING,
                    amount=mpesa_amount,
                    net_amount=mpesa_amount,
                    destination=InvestmentPayoutDestination.MPESA,
                    payment_intent=payment_intent,
                    notes="Awaiting M-Pesa funding confirmation",
                    created_by=actor,
                    updated_by=actor,
                )

            create_activity_log(
                actor=actor,
                chama_id=membership.chama_id,
                action="investment_started",
                entity_type="MemberInvestmentPosition",
                entity_id=investment.id,
                metadata={
                    "reference": investment.reference,
                    "product_name": product.name,
                    "amount": str(amount),
                    "funding_source": funding_source,
                    "pending_funding": bool(mpesa_amount > ZERO),
                },
            )

        if mpesa_amount == ZERO:
            cls.refresh_position_metrics(investment)
            cls._notify(
                recipient=actor,
                chama=membership.chama,
                title="Investment created",
                message=f"Your {product.name} investment is now active.",
                metadata={"investment_id": str(investment.id), "reference": investment.reference},
                priority=NotificationPriority.HIGH,
            )

        return investment

    @classmethod
    def refresh_investment_funding(cls, *, actor, investment: MemberInvestmentPosition) -> MemberInvestmentPosition:
        if investment.member_id != actor.id and not getattr(actor, "is_staff", False):
            raise InvestmentServiceError("You cannot access this investment.")
        cls._require_membership(user=actor, chama_id=investment.chama_id)

        payment_record = investment.transactions_v2.filter(
            payment_intent__isnull=False,
            transaction_type=InvestmentTransactionRecordType.CREATED,
        ).select_related("payment_intent").first()
        if not payment_record or not payment_record.payment_intent:
            return cls.refresh_position_metrics(investment)

        intent = payment_record.payment_intent
        if intent.status in {
            PaymentStatus.PENDING,
            PaymentStatus.INITIATED,
            PaymentStatus.PENDING_AUTHENTICATION,
            PaymentStatus.PENDING_VERIFICATION,
        }:
            intent = UnifiedPaymentService.verify_payment(intent.id)

        if (
            intent.status == PaymentStatus.SUCCESS
            and investment.status == MemberInvestmentPositionStatus.PENDING_FUNDING
        ):
            investment.status = MemberInvestmentPositionStatus.ACTIVE
            investment.funded_at = investment.funded_at or timezone.now()
            investment.current_value = investment.principal_amount
            investment.next_payout_date = cls._build_next_payout_date(
                funded_at=investment.funded_at,
                product=investment.product,
            )
            investment.save(
                update_fields=[
                    "status",
                    "funded_at",
                    "current_value",
                    "next_payout_date",
                    "updated_at",
                ]
            )
            payment_record.status = InvestmentRequestStatus.COMPLETED
            payment_record.save(update_fields=["status", "updated_at"])
            InvestmentTransactionRecord.objects.create(
                investment=investment,
                product=investment.product,
                chama=investment.chama,
                member=investment.member,
                transaction_type=InvestmentTransactionRecordType.FUNDED,
                status=InvestmentRequestStatus.COMPLETED,
                amount=investment.mpesa_funded_amount,
                net_amount=investment.mpesa_funded_amount,
                destination=InvestmentPayoutDestination.MPESA,
                payment_intent=intent,
                notes="M-Pesa funding confirmed",
                created_by=investment.member,
                updated_by=investment.member,
            )
            cls._notify(
                recipient=investment.member,
                chama=investment.chama,
                title="Investment funded",
                message=f"{investment.product.name} has been funded successfully.",
                metadata={"investment_id": str(investment.id), "reference": investment.reference},
                priority=NotificationPriority.HIGH,
            )
        elif intent.status in {PaymentStatus.FAILED, PaymentStatus.CANCELLED, PaymentStatus.EXPIRED}:
            investment.status = MemberInvestmentPositionStatus.FAILED
            investment.latest_status_note = intent.failure_reason or "Funding was not completed."
            investment.save(update_fields=["status", "latest_status_note", "updated_at"])
            payment_record.status = InvestmentRequestStatus.FAILED
            payment_record.notes = intent.failure_reason or payment_record.notes
            payment_record.save(update_fields=["status", "notes", "updated_at"])

        return cls.refresh_position_metrics(investment)

    @classmethod
    def _credit_wallet(cls, *, member, amount: Decimal, currency: str) -> None:
        wallet = _get_member_wallet(member, currency=currency, lock_for_update=True)
        wallet.available_balance = cls._money(wallet.available_balance + amount)
        wallet.save(update_fields=["available_balance", "updated_at"])

    @classmethod
    def utilize_returns(
        cls,
        *,
        actor,
        investment: MemberInvestmentPosition,
        action_type: str,
        amount: Decimal,
        beneficiary_phone: str = "",
    ) -> InvestmentUtilizationAction:
        if investment.member_id != actor.id:
            raise InvestmentServiceError("You cannot use returns from this investment.")
        cls.refresh_position_metrics(investment)
        amount = cls._money(amount)
        if amount <= ZERO:
            raise InvestmentServiceError("Enter a valid returns amount.")
        if not investment.product.returns_utilization_allowed:
            raise InvestmentServiceError("Returns cannot be utilized for this product.")
        if amount > investment.available_returns:
            raise InvestmentServiceError("The selected amount exceeds available returns.")

        fee_amount = ZERO
        tax_amount = ZERO
        net_amount = amount
        with transaction.atomic():
            investment = MemberInvestmentPosition.objects.select_for_update().get(id=investment.id)
            cls.refresh_position_metrics(investment)
            if amount > investment.available_returns:
                raise InvestmentServiceError("Available returns changed. Refresh and try again.")

            payout = None
            status = InvestmentRequestStatus.COMPLETED
            if action_type == InvestmentPayoutDestination.WALLET:
                cls._credit_wallet(member=investment.member, amount=net_amount, currency=investment.currency)
                kind = InvestmentPayoutKind.RETURNS_PAYOUT
                payout = InvestmentPayout.objects.create(
                    investment=investment,
                    kind=kind,
                    destination=InvestmentPayoutDestination.WALLET,
                    status=InvestmentRequestStatus.COMPLETED,
                    gross_amount=amount,
                    net_amount=net_amount,
                    processed_at=timezone.now(),
                    completed_at=timezone.now(),
                    created_by=actor,
                    updated_by=actor,
                )
            elif action_type == InvestmentPayoutDestination.REINVEST:
                investment.principal_amount = cls._money(investment.principal_amount + net_amount)
                investment.wallet_funded_amount = cls._money(investment.wallet_funded_amount + net_amount)
                investment.save(update_fields=["principal_amount", "wallet_funded_amount", "updated_at"])
            elif action_type == InvestmentPayoutDestination.MPESA:
                payout = InvestmentPayout.objects.create(
                    investment=investment,
                    kind=InvestmentPayoutKind.RETURNS_PAYOUT,
                    destination=InvestmentPayoutDestination.MPESA,
                    status=InvestmentRequestStatus.PENDING,
                    gross_amount=amount,
                    net_amount=net_amount,
                    destination_phone=cls._normalize_phone(beneficiary_phone) or investment.beneficiary_phone,
                    created_by=actor,
                    updated_by=actor,
                )
                status = InvestmentRequestStatus.PENDING
            else:
                raise InvestmentServiceError("Unsupported returns destination.")

            action = InvestmentUtilizationAction.objects.create(
                investment=investment,
                action_type=action_type,
                status=status,
                amount=amount,
                fee_amount=fee_amount,
                tax_amount=tax_amount,
                net_amount=net_amount,
                beneficiary_phone=cls._normalize_phone(beneficiary_phone),
                payout=payout,
                processed_at=timezone.now(),
                completed_at=timezone.now() if status == InvestmentRequestStatus.COMPLETED else None,
                created_by=actor,
                updated_by=actor,
            )

            investment.realized_returns = cls._money(investment.realized_returns + amount)
            investment.available_returns = cls._money(max(investment.available_returns - amount, ZERO))
            investment.current_value = cls._money(
                investment.principal_amount - investment.redeemed_principal + investment.available_returns
            )
            investment.save(
                update_fields=[
                    "realized_returns",
                    "available_returns",
                    "current_value",
                    "updated_at",
                ]
            )

            InvestmentTransactionRecord.objects.create(
                investment=investment,
                product=investment.product,
                chama=investment.chama,
                member=investment.member,
                transaction_type=(
                    InvestmentTransactionRecordType.REINVESTMENT
                    if action_type == InvestmentPayoutDestination.REINVEST
                    else InvestmentTransactionRecordType.RETURN_UTILIZATION
                ),
                status=status,
                amount=amount,
                fee_amount=fee_amount,
                tax_amount=tax_amount,
                net_amount=net_amount,
                destination=action_type,
                notes="Returns utilized",
                created_by=actor,
                updated_by=actor,
            )

        cls._notify(
            recipient=investment.member,
            chama=investment.chama,
            title="Returns utilized",
            message=f"You utilized KES {net_amount:,.2f} from {investment.product.name}.",
            metadata={"investment_id": str(investment.id), "action_type": action_type},
        )
        return action

    @classmethod
    def redeem_investment(
        cls,
        *,
        actor,
        investment: MemberInvestmentPosition,
        redemption_type: str,
        amount: Decimal | None,
        destination: str,
        beneficiary_phone: str = "",
        reason: str = "",
    ) -> InvestmentRedemptionRequest:
        if investment.member_id != actor.id:
            raise InvestmentServiceError("You cannot redeem this investment.")
        cls.refresh_position_metrics(investment)
        if destination == InvestmentPayoutDestination.REINVEST:
            raise InvestmentServiceError("Reinvest is only available from returns utilization.")
        if destination == InvestmentPayoutDestination.WALLET and not investment.product.wallet_payout_enabled:
            raise InvestmentServiceError("This product cannot pay out to wallet.")
        if destination == InvestmentPayoutDestination.MPESA and not investment.product.mpesa_payout_enabled:
            raise InvestmentServiceError("This product cannot pay out to M-Pesa.")
        if not investment.redemption_eligible and redemption_type != InvestmentRedemptionType.RETURNS_ONLY:
            raise InvestmentServiceError("This investment is still within the lock period.")

        outstanding_principal = cls._money(investment.principal_amount - investment.redeemed_principal)
        if redemption_type == InvestmentRedemptionType.RETURNS_ONLY:
            requested_amount = cls._money(amount or investment.available_returns)
            principal_amount = ZERO
            profit_amount = requested_amount
        elif redemption_type == InvestmentRedemptionType.FULL:
            principal_amount = outstanding_principal
            profit_amount = investment.available_returns
            requested_amount = cls._money(principal_amount + profit_amount)
        else:
            requested_amount = cls._money(amount)
            if requested_amount <= ZERO:
                raise InvestmentServiceError("Enter a valid redemption amount.")
            if not investment.product.partial_redemption_allowed:
                raise InvestmentServiceError("This product does not support partial redemption.")
            if requested_amount < cls._money(investment.product.partial_redemption_min_amount):
                raise InvestmentServiceError("This amount is below the minimum partial redemption threshold.")
            if requested_amount > outstanding_principal:
                raise InvestmentServiceError("The requested principal amount exceeds what is available.")
            principal_amount = requested_amount
            profit_amount = ZERO

        if requested_amount <= ZERO:
            raise InvestmentServiceError("There is no amount available to redeem.")

        early_penalty = ZERO
        if not investment.is_matured and redemption_type != InvestmentRedemptionType.RETURNS_ONLY:
            early_penalty = cls._money(principal_amount * (cls._to_decimal(investment.product.early_redemption_penalty_rate) / HUNDRED))

        tax_amount = ZERO
        net_amount = cls._money(max(requested_amount - early_penalty - tax_amount, ZERO))

        with transaction.atomic():
            investment = MemberInvestmentPosition.objects.select_for_update().get(id=investment.id)
            cls.refresh_position_metrics(investment)

            status = InvestmentRequestStatus.COMPLETED if destination == InvestmentPayoutDestination.WALLET else InvestmentRequestStatus.PENDING
            payout = None
            if destination == InvestmentPayoutDestination.WALLET:
                cls._credit_wallet(member=investment.member, amount=net_amount, currency=investment.currency)
                payout = InvestmentPayout.objects.create(
                    investment=investment,
                    kind=InvestmentPayoutKind.REDEMPTION_PAYOUT,
                    destination=destination,
                    status=InvestmentRequestStatus.COMPLETED,
                    gross_amount=requested_amount,
                    penalty_amount=early_penalty,
                    tax_amount=tax_amount,
                    net_amount=net_amount,
                    processed_at=timezone.now(),
                    completed_at=timezone.now(),
                    created_by=actor,
                    updated_by=actor,
                )
            else:
                payout = InvestmentPayout.objects.create(
                    investment=investment,
                    kind=InvestmentPayoutKind.REDEMPTION_PAYOUT,
                    destination=destination,
                    status=InvestmentRequestStatus.PENDING,
                    gross_amount=requested_amount,
                    penalty_amount=early_penalty,
                    tax_amount=tax_amount,
                    net_amount=net_amount,
                    destination_phone=cls._normalize_phone(beneficiary_phone) or investment.beneficiary_phone,
                    created_by=actor,
                    updated_by=actor,
                )

            request_record = InvestmentRedemptionRequest.objects.create(
                investment=investment,
                redemption_type=redemption_type,
                destination=destination,
                status=status,
                requested_amount=requested_amount,
                principal_amount=principal_amount,
                profit_amount=profit_amount,
                fee_amount=ZERO,
                tax_amount=tax_amount,
                penalty_amount=early_penalty,
                net_amount=net_amount,
                beneficiary_phone=cls._normalize_phone(beneficiary_phone),
                reason=reason,
                payout=payout,
                processed_at=timezone.now() if status == InvestmentRequestStatus.COMPLETED else None,
                completed_at=timezone.now() if status == InvestmentRequestStatus.COMPLETED else None,
                created_by=actor,
                updated_by=actor,
            )

            if redemption_type == InvestmentRedemptionType.RETURNS_ONLY:
                investment.realized_returns = cls._money(investment.realized_returns + profit_amount)
                investment.available_returns = cls._money(max(investment.available_returns - profit_amount, ZERO))
            else:
                investment.redeemed_principal = cls._money(investment.redeemed_principal + principal_amount)
                investment.available_returns = cls._money(max(investment.available_returns - profit_amount, ZERO))
                investment.realized_returns = cls._money(investment.realized_returns + profit_amount)
                remaining_principal = cls._money(investment.principal_amount - investment.redeemed_principal)
                if remaining_principal <= ZERO:
                    investment.status = MemberInvestmentPositionStatus.REDEEMED
                    investment.closed_at = timezone.now()
                else:
                    investment.status = MemberInvestmentPositionStatus.PARTIALLY_REDEEMED
            investment.total_penalties_charged = cls._money(investment.total_penalties_charged + early_penalty)
            investment.current_value = cls._money(
                max(investment.principal_amount - investment.redeemed_principal, ZERO) + investment.available_returns
            )
            investment.save(
                update_fields=[
                    "redeemed_principal",
                    "available_returns",
                    "realized_returns",
                    "status",
                    "closed_at",
                    "total_penalties_charged",
                    "current_value",
                    "updated_at",
                ]
            )

            InvestmentTransactionRecord.objects.create(
                investment=investment,
                product=investment.product,
                chama=investment.chama,
                member=investment.member,
                transaction_type=(
                    InvestmentTransactionRecordType.FULL_REDEMPTION
                    if redemption_type == InvestmentRedemptionType.FULL
                    else InvestmentTransactionRecordType.PARTIAL_REDEMPTION
                ),
                status=status,
                amount=requested_amount,
                penalty_amount=early_penalty,
                net_amount=net_amount,
                destination=destination,
                notes=f"Redemption request {request_record.reference}",
                created_by=actor,
                updated_by=actor,
            )

        cls._notify(
            recipient=investment.member,
            chama=investment.chama,
            title="Redemption requested" if status != InvestmentRequestStatus.COMPLETED else "Redemption completed",
            message=f"{investment.product.name} redemption of KES {net_amount:,.2f} has been recorded.",
            metadata={"investment_id": str(investment.id), "redemption_id": str(request_record.id)},
            priority=NotificationPriority.HIGH,
        )
        return request_record

    @classmethod
    def process_redemption_request(
        cls,
        *,
        actor,
        redemption: InvestmentRedemptionRequest,
        action: str,
        failure_reason: str = "",
    ) -> InvestmentRedemptionRequest:
        cls._require_admin_membership(user=actor, chama_id=redemption.investment.chama_id)
        action = str(action or "").strip().lower()
        now = timezone.now()
        with transaction.atomic():
            redemption = InvestmentRedemptionRequest.objects.select_for_update().select_related(
                "investment",
                "investment__product",
                "payout",
            ).get(id=redemption.id)
            payout = redemption.payout
            if action == "approve":
                redemption.status = InvestmentRequestStatus.PROCESSING
                if payout:
                    payout.status = InvestmentRequestStatus.PROCESSING
                    payout.processed_at = now
                    payout.save(update_fields=["status", "processed_at", "updated_at"])
            elif action == "complete":
                redemption.status = InvestmentRequestStatus.COMPLETED
                redemption.completed_at = now
                if payout:
                    payout.status = InvestmentRequestStatus.COMPLETED
                    payout.completed_at = now
                    payout.processed_at = payout.processed_at or now
                    payout.save(
                        update_fields=["status", "processed_at", "completed_at", "updated_at"]
                    )
            elif action == "reject":
                redemption.status = InvestmentRequestStatus.REJECTED
                redemption.failure_reason = failure_reason or "This redemption request was rejected."
                if payout:
                    payout.status = InvestmentRequestStatus.REJECTED
                    payout.failure_reason = redemption.failure_reason
                    payout.save(update_fields=["status", "failure_reason", "updated_at"])
            else:
                raise InvestmentServiceError("Unsupported redemption action.")

            redemption.processed_by = actor
            redemption.processed_at = now
            redemption.save(
                update_fields=[
                    "status",
                    "failure_reason",
                    "processed_by",
                    "processed_at",
                    "completed_at",
                    "updated_at",
                ]
            )

        create_audit_log(
            actor=actor,
            chama_id=redemption.investment.chama_id,
            action=f"investment_redemption_{action}",
            entity_type="InvestmentRedemptionRequest",
            entity_id=redemption.id,
            metadata={"reference": redemption.reference, "status": redemption.status},
        )
        return redemption

    @classmethod
    def portfolio_summary(cls, *, actor, chama_id) -> dict[str, Any]:
        membership = cls._require_membership(user=actor, chama_id=chama_id)
        investments = list(
            MemberInvestmentPosition.objects.select_related("product")
            .filter(chama=membership.chama, member=actor)
            .order_by("-created_at")
        )
        for investment in investments:
            cls.refresh_position_metrics(investment)

        queryset = MemberInvestmentPosition.objects.filter(chama=membership.chama, member=actor)
        aggregates = queryset.aggregate(
            total_invested=Sum("principal_amount"),
            current_value=Sum("current_value"),
            total_returns=Sum("accrued_returns"),
            available_returns=Sum("available_returns"),
        )
        next_maturity = queryset.filter(
            maturity_date__isnull=False,
            status__in=[
                MemberInvestmentPositionStatus.ACTIVE,
                MemberInvestmentPositionStatus.MATURED,
                MemberInvestmentPositionStatus.PARTIALLY_REDEEMED,
            ],
        ).order_by("maturity_date").first()
        best_performer = queryset.order_by("-available_returns", "-accrued_returns").first()
        return {
            "currency": membership.chama.currency,
            "total_invested": str(cls._money(aggregates["total_invested"])),
            "current_value": str(cls._money(aggregates["current_value"])),
            "total_returns": str(cls._money(aggregates["total_returns"])),
            "available_returns": str(cls._money(aggregates["available_returns"])),
            "active_count": queryset.filter(status=MemberInvestmentPositionStatus.ACTIVE).count(),
            "matured_count": queryset.filter(status=MemberInvestmentPositionStatus.MATURED).count(),
            "next_maturity": {
                "investment_id": str(next_maturity.id),
                "reference": next_maturity.reference,
                "product_name": next_maturity.product.name,
                "maturity_date": next_maturity.maturity_date.isoformat() if next_maturity and next_maturity.maturity_date else None,
            }
            if next_maturity
            else None,
            "best_performing_investment": {
                "investment_id": str(best_performer.id),
                "reference": best_performer.reference,
                "product_name": best_performer.product.name,
                "returns": str(cls._money(best_performer.available_returns)),
            }
            if best_performer
            else None,
            "alerts": [
                {"kind": "returns_available", "message": "You have returns available for withdrawal."}
                if cls._money(aggregates["available_returns"]) > ZERO
                else None,
                {
                    "kind": "maturity_due",
                    "message": "An investment matures soon."
                }
                if next_maturity and next_maturity.maturity_date and next_maturity.maturity_date <= timezone.now() + timezone.timedelta(days=7)
                else None,
            ],
        }

    @classmethod
    def portfolio_analytics(cls, *, actor, chama_id) -> dict[str, Any]:
        membership = cls._require_membership(user=actor, chama_id=chama_id)
        queryset = MemberInvestmentPosition.objects.filter(chama=membership.chama, member=actor).select_related("product")
        investments = list(queryset)
        for investment in investments:
            cls.refresh_position_metrics(investment)
        allocation = (
            queryset.values("product__name")
            .annotate(total=Sum("principal_amount"), count=Count("id"))
            .order_by("-total")
        )
        timeline = [
            {
                "reference": position.reference,
                "product_name": position.product.name,
                "principal": str(cls._money(position.principal_amount)),
                "current_value": str(cls._money(position.current_value)),
                "returns": str(cls._money(position.accrued_returns)),
            }
            for position in investments[:8]
        ]
        return {
            "allocation": [
                {
                    "label": row["product__name"],
                    "value": str(cls._money(row["total"])),
                    "count": row["count"],
                }
                for row in allocation
            ],
            "growth_series": timeline,
            "realized_returns": str(cls._money(queryset.aggregate(total=Sum("realized_returns"))["total"])),
            "unrealized_returns": str(cls._money(queryset.aggregate(total=Sum("available_returns"))["total"])),
            "wallet_withdrawals": str(
                cls._money(
                    InvestmentTransactionRecord.objects.filter(
                        member=actor,
                        chama=membership.chama,
                        destination=InvestmentPayoutDestination.WALLET,
                    ).aggregate(total=Sum("net_amount"))["total"]
                )
            ),
            "reinvestment_total": str(
                cls._money(
                    InvestmentTransactionRecord.objects.filter(
                        member=actor,
                        chama=membership.chama,
                        transaction_type=InvestmentTransactionRecordType.REINVESTMENT,
                    ).aggregate(total=Sum("net_amount"))["total"]
                )
            ),
        }

    @classmethod
    def admin_analytics(cls, *, actor, chama_id) -> dict[str, Any]:
        membership = cls._require_admin_membership(user=actor, chama_id=chama_id)
        products = InvestmentProduct.objects.filter(chama=membership.chama)
        positions = MemberInvestmentPosition.objects.filter(chama=membership.chama)
        return {
            "product_count": products.count(),
            "active_products": products.filter(status=InvestmentProductStatus.ACTIVE).count(),
            "total_invested": str(cls._money(positions.aggregate(total=Sum("principal_amount"))["total"])),
            "current_value": str(cls._money(positions.aggregate(total=Sum("current_value"))["total"])),
            "pending_redemptions": InvestmentRedemptionRequest.objects.filter(
                investment__chama=membership.chama,
                status__in=[InvestmentRequestStatus.PENDING, InvestmentRequestStatus.PROCESSING],
            ).count(),
            "redemption_trends": [
                {
                    "status": row["status"],
                    "count": row["count"],
                }
                for row in InvestmentRedemptionRequest.objects.filter(investment__chama=membership.chama)
                .values("status")
                .annotate(count=Count("id"))
                .order_by("-count")
            ],
            "adoption_by_product": [
                {
                    "product_name": row["product__name"],
                    "members": row["members"],
                    "invested": str(cls._money(row["invested"])),
                }
                for row in positions.values("product__name")
                .annotate(members=Count("member", distinct=True), invested=Sum("principal_amount"))
                .order_by("-invested")
            ],
        }
