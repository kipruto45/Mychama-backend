"""
Payout Service - Orchestrates the entire payout workflow.

Handles eligibility checks, rotation management, approvals,
payment processing, and notifications.
"""

from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from apps.chama.models import Membership
from apps.finance.ledger_service import LedgerService
from apps.finance.models import (
    LedgerDirection,
    LedgerEntry,
    LedgerEntryType,
    LedgerStatus,
    Penalty,
    PenaltyStatus,
    Wallet,
    WalletOwnerType,
)
from apps.governance.models import ApprovalRequest, ApprovalStatus, ApprovalType
from apps.issues.models import Issue
from apps.finance.models import Loan, LoanStatus
from apps.notifications.services import NotificationService
from apps.payments.models import MpesaB2CPayout, MpesaB2CStatus
from apps.payments.mpesa_client import MpesaClient, MpesaClientError
from apps.payments.unified_models import (
    PaymentAuditLog,
    PaymentIntent,
    PaymentMethod,
    PaymentPurpose,
    PaymentReceipt,
    PaymentStatus,
    PaymentTransaction,
    TransactionStatus,
)

from .models import (
    Payout,
    PayoutAuditLog,
    PayoutEligibilityCheck,
    PayoutRotation,
    PayoutStatus,
    EligibilityStatus,
    PayoutMethod as PayoutPaymentMethod,
)


class PayoutService:
    """Main service for managing payout workflow."""

    @staticmethod
    def _get_chama_wallet_for_update(*, chama, currency: str = "KES") -> Wallet:
        wallet, _ = Wallet.objects.select_for_update().get_or_create(
            owner_type=WalletOwnerType.CHAMA,
            owner_id=str(chama.id),
            defaults={
                "available_balance": Decimal("0.00"),
                "locked_balance": Decimal("0.00"),
                "currency": currency,
            },
        )
        return wallet

    @staticmethod
    def _lock_chama_wallet_for_external_payout(*, payout: Payout) -> None:
        """
        Move funds from `available_balance` to `locked_balance` for external payout methods.

        This prevents double-spend while a provider transfer is pending.
        """
        if payout.payout_method == PayoutPaymentMethod.WALLET:
            return

        with transaction.atomic():
            payout = Payout.objects.select_for_update().select_related("chama").get(id=payout.id)
            meta = dict(payout.metadata or {})
            if meta.get("wallet_lock_applied"):
                return

            wallet = PayoutService._get_chama_wallet_for_update(chama=payout.chama, currency=payout.currency or "KES")
            amount = Decimal(str(payout.amount))
            if amount <= Decimal("0.00"):
                raise ValueError("Payout amount must be greater than zero.")
            if wallet.available_balance < amount:
                raise ValueError("Insufficient chama wallet balance for payout.")

            wallet.available_balance = Decimal(str(wallet.available_balance)) - amount
            wallet.locked_balance = Decimal(str(wallet.locked_balance)) + amount
            wallet.save(update_fields=["available_balance", "locked_balance", "updated_at"])

            meta["wallet_lock_applied"] = True
            payout.metadata = meta
            payout.save(update_fields=["metadata", "updated_at"])

    @staticmethod
    def _release_chama_wallet_lock(*, payout: Payout, restore_available: bool) -> None:
        """
        Release locked funds for a payout.

        When `restore_available` is True, returns funds back to `available_balance` (failure path).
        When False, only decreases `locked_balance` (success path; funds have left the wallet).
        """
        with transaction.atomic():
            payout = Payout.objects.select_for_update().select_related("chama").get(id=payout.id)
            meta = dict(payout.metadata or {})
            if not meta.get("wallet_lock_applied") or meta.get("wallet_lock_released"):
                return

            wallet = PayoutService._get_chama_wallet_for_update(chama=payout.chama, currency=payout.currency or "KES")
            amount = Decimal(str(payout.amount))
            if amount > Decimal("0.00") and Decimal(str(wallet.locked_balance)) >= amount:
                wallet.locked_balance = Decimal(str(wallet.locked_balance)) - amount
                if restore_available:
                    wallet.available_balance = Decimal(str(wallet.available_balance)) + amount
                wallet.save(update_fields=["available_balance", "locked_balance", "updated_at"])

            meta["wallet_lock_released"] = True
            payout.metadata = meta
            payout.save(update_fields=["metadata", "updated_at"])

    @staticmethod
    def _post_external_payout_ledger(
        *,
        payout: Payout,
        provider_reference: str,
        actor=None,
    ) -> LedgerEntry:
        """
        Post an external payout ledger entry (debit chama wallet only).

        External payouts leave the MyChama wallet system (e.g., M-Pesa B2C, bank transfer).
        """
        with transaction.atomic():
            payout = Payout.objects.select_for_update().select_related("chama", "member__user").get(id=payout.id)
            wallet = PayoutService._get_chama_wallet_for_update(chama=payout.chama, currency=payout.currency or "KES")
            amount = Decimal(str(payout.amount))
            idempotency_key = f"payout:{payout.id}:external:debit"
            existing = LedgerEntry.objects.filter(chama=payout.chama, idempotency_key=idempotency_key).first()
            if existing:
                return existing

            zero = Decimal("0.00")
            return LedgerEntry.objects.create(
                wallet=wallet,
                chama=payout.chama,
                entry_type=LedgerEntryType.PAYOUT,
                direction=LedgerDirection.DEBIT,
                amount=amount,
                debit=amount,
                credit=zero,
                currency=payout.currency or wallet.currency or "KES",
                status=LedgerStatus.SUCCESS,
                provider=payout.payout_method,
                provider_reference=str(provider_reference or "")[:120],
                idempotency_key=idempotency_key,
                narration=f"Payout to {getattr(payout.member.user, 'full_name', 'member')}.",
                meta={
                    "payout_id": str(payout.id),
                    "member_id": str(getattr(payout.member.user, "id", "")),
                    "member_phone": str(getattr(payout.member.user, "phone", "") or ""),
                    "payout_method": payout.payout_method,
                },
                created_by=actor,
                updated_by=actor,
            )

    @staticmethod
    def _ensure_payment_receipt(
        *,
        intent: PaymentIntent,
        provider_reference: str,
        provider_name: str,
        actor=None,
        metadata: dict | None = None,
    ) -> PaymentReceipt:
        transaction_record = PaymentTransaction.objects.filter(payment_intent=intent).first()
        if not transaction_record:
            transaction_record = PaymentTransaction.objects.create(
                payment_intent=intent,
                provider=intent.payment_method,
                reference=str(intent.reference or "")[:120],
                provider_reference=str(provider_reference or "")[:120],
                provider_name=str(provider_name or "")[:50],
                payment_method=intent.payment_method,
                amount=intent.amount,
                currency=intent.currency or "KES",
                status=TransactionStatus.VERIFIED,
                payer_reference=getattr(intent.user, "phone", "") if intent.user else "",
                raw_response=metadata or {},
                verified_by=actor,
                verified_at=timezone.now(),
            )

        receipt = getattr(intent, "receipt", None)
        if receipt:
            return receipt
        return PaymentReceipt.objects.create(
            payment_intent=intent,
            transaction=transaction_record,
            amount=intent.amount,
            currency=intent.currency or "KES",
            payment_method=intent.payment_method,
            issued_by=actor,
            metadata=metadata or {},
        )

    @staticmethod
    @transaction.atomic
    def trigger_payout(
        chama_id,
        member_id: Optional = None,
        trigger_type: str = "manual",
        amount: Optional[Decimal] = None,
        triggered_by_id=None,
    ) -> Payout:
        """
        Trigger a new payout.

        Args:
            chama_id: Chama UUID
            member_id: Optional member UUID (if None, use rotation)
            trigger_type: "manual" or "auto"
            amount: Optional payout amount (if None, use member's eligible amount)
            triggered_by_id: User ID who triggered the payout

        Returns:
            Payout instance

        Raises:
            ValueError: If rotation or member invalid
        """
        from apps.chama.models import Chama

        chama = Chama.objects.get(id=chama_id)

        # Get rotation
        rotation, _ = PayoutRotation.objects.get_or_create(chama=chama)

        # Determine member (from rotation if not specified)
        if not member_id:
            member_id = rotation.get_next_member()
            if not member_id:
                raise ValueError(f"No members in rotation for {chama.name}")

        member = Membership.objects.get(id=member_id)

        # Default amount: member's eligible payout (TODO: calculate from rules)
        if not amount:
            amount = Decimal("0.00")  # Will be updated after eligibility check

        # Create payout instance
        payout = Payout.objects.create(
            chama=chama,
            member=member,
            amount=amount or Decimal("0.00"),
            rotation_position=rotation.current_position,
            rotation_cycle=rotation.rotation_cycle,
            status=PayoutStatus.TRIGGERED,
            trigger_type=trigger_type,
        )

        # Log audit
        PayoutAuditLog.objects.create(
            payout=payout,
            action="TRIGGERED",
            actor_id=triggered_by_id,
            new_status=PayoutStatus.TRIGGERED,
            details={
                "trigger_type": trigger_type,
                "rotation_position": rotation.current_position,
                "rotation_cycle": rotation.rotation_cycle,
            },
        )

        return payout

    @staticmethod
    @transaction.atomic
    def check_eligibility(payout_id) -> Tuple[Payout, PayoutEligibilityCheck]:
        """
        Run eligibility checks on a payout.

        Returns:
            (Payout, PayoutEligibilityCheck)

        Sets eligibility status and updates payout status.
        """
        payout = Payout.objects.select_related("member").get(id=payout_id)
        member = payout.member
        issues = []

        # Check 1: Member is active
        member_is_active = member.status == "active"
        if not member_is_active:
            issues.append("Member is not active")

        # Check 2: No outstanding penalties
        penalty_qs = Penalty.objects.filter(
            chama=payout.chama,
            member=member.user,
            status__in=[PenaltyStatus.UNPAID, PenaltyStatus.PARTIAL],
        )
        has_outstanding_penalties = penalty_qs.exists()
        pending_penalties = penalty_qs.values_list("id", flat=True)
        penalty_agg = penalty_qs.aggregate(
            total=models.Sum("amount"),
            paid=models.Sum("amount_paid"),
        )
        penalty_amount = (penalty_agg["total"] or Decimal("0.00")) - (penalty_agg["paid"] or Decimal("0.00"))

        if has_outstanding_penalties:
            issues.append(f"Outstanding penalties: KES {penalty_amount}")

        # Check 3: No active disputes
        active_issue_qs = Issue.objects.filter(
            chama=payout.chama,
            reported_user=member.user,
        ).exclude(status__in=["resolved", "dismissed", "closed"])
        has_active_disputes = active_issue_qs.exists()
        active_dispute_ids = active_issue_qs.values_list("id", flat=True)

        if has_active_disputes:
            issues.append("Active disputes found")

        # Check 4: No overdue loans
        from django.utils.timezone import now

        today = now().date()
        overdue_qs = Loan.objects.filter(
            chama=payout.chama,
            member=member.user,
        ).filter(
            models.Q(status__in=[LoanStatus.OVERDUE, LoanStatus.DEFAULTED, LoanStatus.DEFAULTED_RECOVERING])
            | (
                models.Q(status__in=[LoanStatus.ACTIVE, LoanStatus.DUE_SOON])
                & models.Q(due_date__isnull=False)
                & models.Q(due_date__lt=today)
            )
        )
        has_overdue_loans = overdue_qs.exists()
        overdue_loan_ids = overdue_qs.values_list("id", flat=True)
        overdue_loan_amount = overdue_qs.aggregate(total=models.Sum("total_due"))["total"] or Decimal("0.00")

        if has_overdue_loans:
            issues.append(f"Overdue loans: KES {overdue_loan_amount}")

        # Check 5: Chama wallet has funds (for wallet payouts)
        wallet, _ = Wallet.objects.get_or_create(
            owner_type=WalletOwnerType.CHAMA,
            owner_id=str(payout.chama_id),
            defaults={
                "available_balance": Decimal("0.00"),
                "locked_balance": Decimal("0.00"),
                "currency": payout.currency or "KES",
            },
        )
        wallet_has_funds = wallet.available_balance >= payout.amount
        available_balance = wallet.available_balance

        if payout.payout_method == PayoutPaymentMethod.WALLET and not wallet_has_funds:
            issues.append(
                f"Insufficient wallet balance. Available: KES {available_balance}"
            )

        # Determine eligibility
        if not issues:
            eligibility_result = EligibilityStatus.ELIGIBLE
        elif has_outstanding_penalties:
            eligibility_result = EligibilityStatus.PENDING_PENALTIES
        elif has_active_disputes:
            eligibility_result = EligibilityStatus.ACTIVE_DISPUTES
        elif has_overdue_loans:
            eligibility_result = EligibilityStatus.OVERDUE_LOANS
        elif not member_is_active:
            eligibility_result = EligibilityStatus.INACTIVE_MEMBER
        elif not wallet_has_funds:
            eligibility_result = EligibilityStatus.INSUFFICIENT_FUNDS
        else:
            eligibility_result = EligibilityStatus.MULTIPLE_ISSUES

        # Create eligibility check record
        eligibility_check = PayoutEligibilityCheck.objects.create(
            payout=payout,
            member=member,
            result=eligibility_result,
            has_outstanding_penalties=has_outstanding_penalties,
            penalty_amount=penalty_amount,
            active_penalties=[str(value) for value in pending_penalties],
            has_active_disputes=has_active_disputes,
            active_disputes=[str(value) for value in active_dispute_ids],
            has_overdue_loans=has_overdue_loans,
            overdue_loan_amount=overdue_loan_amount,
            overdue_loans=[str(value) for value in overdue_loan_ids],
            member_is_active=member_is_active,
            wallet_has_funds=wallet_has_funds,
            available_balance=available_balance,
        )

        # Update payout
        payout.eligibility_status = eligibility_result
        payout.eligibility_issues = issues
        payout.eligibility_checked_at = timezone.now()

        if eligibility_result == EligibilityStatus.ELIGIBLE:
            payout.status = PayoutStatus.AWAITING_TREASURER_REVIEW
        else:
            payout.status = PayoutStatus.INELIGIBLE

        payout.save()

        # Log audit
        PayoutAuditLog.objects.create(
            payout=payout,
            action="ELIGIBILITY_CHECK",
            new_status=payout.status,
            details={
                "eligibility": eligibility_result,
                "issues": issues,
            },
        )

        return payout, eligibility_check

    @staticmethod
    @transaction.atomic
    def skip_to_next_member(
        payout_id,
        reason: str = "",
        actor_id=None,
    ) -> Payout:
        """
        Skip current member and defer to next in rotation.

        Args:
            payout_id: Payout UUID
            reason: Reason for skipping
            actor_id: User ID performing the action

        Returns:
            Updated Payout
        """
        payout = Payout.objects.select_related("chama").get(id=payout_id)

        payout.status = PayoutStatus.INELIGIBLE
        payout.skip_reason = reason
        payout.save()

        # Advance rotation
        rotation = PayoutRotation.objects.get(chama=payout.chama)
        rotation.advance_rotation()

        # Log audit
        PayoutAuditLog.objects.create(
            payout=payout,
            action="SKIPPED",
            actor_id=actor_id,
            new_status=PayoutStatus.INELIGIBLE,
            reason=reason,
        )

        return payout

    @staticmethod
    @transaction.atomic
    def defer_to_next_cycle(
        payout_id,
        reason: str = "",
        actor_id=None,
    ) -> Payout:
        """
        Defer payout to next cycle but keep same member.

        Args:
            payout_id: Payout UUID
            reason: Reason for deferral
            actor_id: User ID performing the action

        Returns:
            Updated Payout
        """
        payout = Payout.objects.get(id=payout_id)

        payout.status = PayoutStatus.INELIGIBLE
        payout.defer_reason = reason
        payout.save()

        # Log audit
        PayoutAuditLog.objects.create(
            payout=payout,
            action="DEFERRED",
            actor_id=actor_id,
            new_status=PayoutStatus.INELIGIBLE,
            reason=reason,
        )

        return payout

    @staticmethod
    @transaction.atomic
    def send_to_treasurer_review(payout_id) -> Payout:
        """
        Move payout to treasurer review stage.

        Creates ApprovalRequest for treasurer and chairperson.
        """
        payout = Payout.objects.select_related(
            "chama",
            "member",
        ).get(id=payout_id)

        if payout.status != PayoutStatus.AWAITING_TREASURER_REVIEW:
            raise ValueError(
                f"Payout must be in AWAITING_TREASURER_REVIEW status, "
                f"currently {payout.status}"
            )

        # Create approval request
        approval_request = ApprovalRequest.objects.create(
            chama=payout.chama,
            approval_type=ApprovalType.PAYOUT,
            reference_type="Payout",
            reference_id=payout.id,
            reference_display=f"Payout to {payout.member.user.phone}",
            title=f"Payout Approval - {payout.member.user.get_full_name()}",
            description=(
                f"Payout of KES {payout.amount} to {payout.member.user.phone}"
            ),
            amount=payout.amount,
            first_level_approver_role="treasurer",
            second_level_approver_role="chairperson",
        )

        payout.approval_request = approval_request
        payout.status = PayoutStatus.AWAITING_TREASURER_REVIEW
        payout.save()

        # Log audit
        PayoutAuditLog.objects.create(
            payout=payout,
            action="SENT_TO_TREASURER_REVIEW",
            new_status=PayoutStatus.AWAITING_TREASURER_REVIEW,
        )

        # Send notification to treasurer
        NotificationService.create_notification(
            user=payout.chama.get_treasurer(),
            notification_type="PAYOUT_AWAITING_REVIEW",
            title="Payout Awaiting Review",
            message=f"Payout of KES {payout.amount} to {payout.member.user.get_full_name()} is ready for review.",
            reference_id=payout.id,
            channels=["PUSH", "IN_APP"],
        )

        return payout

    @staticmethod
    @transaction.atomic
    def treasurer_reject(
        payout_id,
        reason: str,
        actor_id,
    ) -> Payout:
        """
        Treasurer rejects payout.

        Args:
            payout_id: Payout UUID
            reason: Rejection reason
            actor_id: Treasurer user ID

        Returns:
            Updated Payout
        """
        payout = Payout.objects.select_related("chama", "member").get(id=payout_id)

        payout.status = PayoutStatus.TREASURY_REJECTED
        payout.treasurer_rejection_reason = reason
        payout.treasurer_reviewed_by_id = actor_id
        payout.treasurer_reviewed_at = timezone.now()
        payout.save()

        # Update approval request
        if payout.approval_request:
            payout.approval_request.status = ApprovalStatus.REJECTED
            payout.approval_request.save()

        # Log audit
        PayoutAuditLog.objects.create(
            payout=payout,
            action="TREASURY_REJECTED",
            actor_id=actor_id,
            new_status=PayoutStatus.TREASURY_REJECTED,
            reason=reason,
        )

        # Notify member and chairs
        NotificationService.create_notification(
            user=payout.member.user,
            notification_type="PAYOUT_REJECTED",
            title="Payout Rejected",
            message=f"Your payout has been rejected. Reason: {reason}",
            reference_id=payout.id,
            channels=["PUSH", "IN_APP"],
        )

        return payout

    @staticmethod
    @transaction.atomic
    def treasurer_approve(
        payout_id,
        actor_id,
    ) -> Payout:
        """
        Treasurer approves payout and sends to chairperson.

        Args:
            payout_id: Payout UUID
            actor_id: Treasurer user ID

        Returns:
            Updated Payout
        """
        payout = Payout.objects.select_related("chama", "member").get(id=payout_id)

        payout.status = PayoutStatus.AWAITING_CHAIR_APPROVAL
        payout.treasurer_reviewed_by_id = actor_id
        payout.treasurer_reviewed_at = timezone.now()
        payout.save()

        # Log audit
        PayoutAuditLog.objects.create(
            payout=payout,
            action="TREASURER_APPROVED",
            actor_id=actor_id,
            new_status=PayoutStatus.AWAITING_CHAIR_APPROVAL,
        )

        # Notify chairperson
        chairperson = payout.chama.get_chairperson()
        if chairperson:
            NotificationService.create_notification(
                user=chairperson,
                notification_type="PAYOUT_AWAITING_CHAIR_APPROVAL",
                title="Payout Awaiting Approval",
                message=(
                    f"Payout of KES {payout.amount} to "
                    f"{payout.member.user.get_full_name()} is ready for final approval."
                ),
                reference_id=payout.id,
                channels=["PUSH", "IN_APP"],
            )

        return payout

    @staticmethod
    @transaction.atomic
    def chairperson_reject(
        payout_id,
        reason: str,
        actor_id,
    ) -> Payout:
        """
        Chairperson rejects payout.

        Args:
            payout_id: Payout UUID
            reason: Rejection reason
            actor_id: Chairperson user ID

        Returns:
            Updated Payout
        """
        payout = Payout.objects.select_related("chama", "member").get(id=payout_id)

        payout.status = PayoutStatus.CHAIR_REJECTED
        payout.chairperson_rejection_reason = reason
        payout.chairperson_approved_by_id = actor_id
        payout.chairperson_approved_at = timezone.now()
        payout.save()

        # Update approval request
        if payout.approval_request:
            payout.approval_request.status = ApprovalStatus.REJECTED
            payout.approval_request.save()

        # Log audit
        PayoutAuditLog.objects.create(
            payout=payout,
            action="CHAIR_REJECTED",
            actor_id=actor_id,
            new_status=PayoutStatus.CHAIR_REJECTED,
            reason=reason,
        )

        # Notify treasurer and member
        NotificationService.create_notification(
            user=payout.member.user,
            notification_type="PAYOUT_REJECTED",
            title="Payout Rejected",
            message=f"Your payout has been rejected. Reason: {reason}",
            reference_id=payout.id,
            channels=["PUSH", "IN_APP"],
        )

        return payout

    @staticmethod
    @transaction.atomic
    def chairperson_approve(
        payout_id,
        actor_id,
    ) -> Payout:
        """
        Chairperson approves payout and initiates payment.

        Args:
            payout_id: Payout UUID
            actor_id: Chairperson user ID

        Returns:
            Updated Payout
        """
        payout = Payout.objects.select_related("chama", "member").get(id=payout_id)

        payout.status = PayoutStatus.APPROVED
        payout.chairperson_approved_by_id = actor_id
        payout.chairperson_approved_at = timezone.now()
        payout.save()

        # Update approval request
        if payout.approval_request:
            payout.approval_request.status = ApprovalStatus.APPROVED
            payout.approval_request.resolved_at = timezone.now()
            payout.approval_request.resolved_by_id = actor_id
            payout.approval_request.save()

        # Log audit
        PayoutAuditLog.objects.create(
            payout=payout,
            action="CHAIR_APPROVED",
            actor_id=actor_id,
            new_status=PayoutStatus.APPROVED,
        )

        # Notify treasurer to process payment
        NotificationService.create_notification(
            user=payout.chama.get_treasurer(),
            notification_type="PAYOUT_APPROVED",
            title="Payout Approved",
            message=(
                f"Payout of KES {payout.amount} to "
                f"{payout.member.user.get_full_name()} has been approved. "
                "Processing payment..."
            ),
            reference_id=payout.id,
            channels=["PUSH", "IN_APP"],
        )

        return payout

    @staticmethod
    @transaction.atomic
    def initiate_payment(payout_id) -> PaymentIntent:
        """
        Initiate payment processing for approved payout.

        Args:
            payout_id: Payout UUID

        Returns:
            PaymentIntent

        Raises:
            ValueError: If payout not approved or payment method invalid
        """
        payout = Payout.objects.select_related(
            "chama",
            "member",
        ).get(id=payout_id)

        if payout.status != PayoutStatus.APPROVED:
            raise ValueError(f"Payout must be approved before payment. Status: {payout.status}")

        if payout.payout_method not in {
            PayoutPaymentMethod.BANK_TRANSFER,
            PayoutPaymentMethod.MPESA,
            PayoutPaymentMethod.WALLET,
        }:
            raise ValueError(f"Unsupported payout method: {payout.payout_method}")

        payment_method = (
            PaymentMethod.WALLET
            if payout.payout_method == PayoutPaymentMethod.WALLET
            else (PaymentMethod.BANK if payout.payout_method == PayoutPaymentMethod.BANK_TRANSFER else PaymentMethod.MPESA)
        )

        intent = PaymentIntent.objects.create(
            chama=payout.chama,
            user=payout.member.user,
            amount=payout.amount,
            currency=payout.currency or "KES",
            purpose=PaymentPurpose.OTHER,
            purpose_id=payout.id,
            description=f"Payout to {payout.member.user.phone}",
            payment_method=payment_method,
            phone=payout.member.user.phone or "",
            provider="payout_disbursement",
            provider_intent_id=f"payout_{timezone.now().timestamp()}_{payout.id}",
            status=PaymentStatus.PENDING if payout.payout_method != PayoutPaymentMethod.WALLET else PaymentStatus.INITIATED,
            idempotency_key=f"payout:{payout.id}:{payout.payout_method}",
            metadata={
                "payout_id": str(payout.id),
                "member_id": str(payout.member.user_id),
                "member_phone": payout.member.user.phone,
                "payout_method": payout.payout_method,
                "source": "payouts_service",
            },
        )
        PaymentAuditLog.objects.create(
            payment_intent=intent,
            actor=None,
            event="payout_initiated",
            new_status=intent.status,
            metadata={"payout_id": str(payout.id), "payout_method": payout.payout_method},
        )

        payout.payment_intent = intent
        payout.status = PayoutStatus.PROCESSING
        payout.payment_started_at = timezone.now()
        payout.save()

        # Log audit
        PayoutAuditLog.objects.create(
            payout=payout,
            action="PAYMENT_INITIATED",
            new_status=PayoutStatus.PROCESSING,
            details={
                "payment_method": payment_method,
                "payment_intent_id": str(intent.id),
            },
        )

        if payout.payout_method != PayoutPaymentMethod.WALLET:
            PayoutService._lock_chama_wallet_for_external_payout(payout=payout)

        # Process payment based on method
        if payout.payout_method == PayoutPaymentMethod.MPESA:
            PayoutService._process_mpesa_payout(intent)
        elif payout.payout_method == PayoutPaymentMethod.BANK_TRANSFER:
            PayoutService._process_bank_payout(intent)
        else:  # Wallet
            PayoutService._process_wallet_payout(intent)

        return intent

    @staticmethod
    def _process_mpesa_payout(payment_intent: PaymentIntent):
        """Process M-Pesa B2C payout."""
        payout = Payout.objects.select_related("chama", "member__user").get(id=payment_intent.purpose_id)
        phone = payout.member.user.phone
        if not phone:
            raise ValueError("Recipient phone number is missing.")

        if getattr(payment_intent, "status", "") == PaymentStatus.SUCCESS:
            return

        if getattr(settings, "MPESA_USE_STUB", True):
            response = {
                "ConversationID": f"AG_{payment_intent.id.hex[:20]}",
                "OriginatorConversationID": f"OC_{payment_intent.id.hex[:20]}",
                "ResponseCode": "0",
                "ResponseDescription": "Accepted for processing",
            }
        else:
            client = MpesaClient()
            try:
                response = client.send_b2c_payment(
                    phone_number=phone,
                    amount=str(payment_intent.amount),
                    command_id="BusinessPayment",
                    remarks=f"Payout {payout.id}"[:100],
                    occasion=str(payout.id)[:100],
                )
            except MpesaClientError as exc:
                raise ValueError("Failed to initiate M-Pesa payout.") from exc

        originator_id = str(response.get("OriginatorConversationID") or "").strip() or f"OC_{payment_intent.id.hex[:20]}"
        b2c = MpesaB2CPayout.objects.create(
            chama=payout.chama,
            intent=payment_intent,
            phone=phone,
            amount=payment_intent.amount,
            originator_conversation_id=originator_id,
            conversation_id=str(response.get("ConversationID") or "")[:120],
            response_code=str(response.get("ResponseCode") or "")[:20],
            response_description=str(response.get("ResponseDescription") or "")[:1000],
            status=MpesaB2CStatus.PENDING,
        )

        payment_intent.provider = "safaricom"
        payment_intent.provider_intent_id = originator_id
        payment_intent.status = PaymentStatus.PENDING
        payment_intent.save(update_fields=["provider", "provider_intent_id", "status", "updated_at"])

        if getattr(settings, "MPESA_USE_STUB", True):
            b2c.status = MpesaB2CStatus.SUCCESS
            b2c.result_code = "0"
            b2c.result_desc = "Completed (stub)"
            b2c.transaction_id = f"STUB-{originator_id}"[:80]
            b2c.processed_at = timezone.now()
            b2c.save(
                update_fields=[
                    "status",
                    "result_code",
                    "result_desc",
                    "transaction_id",
                    "processed_at",
                    "updated_at",
                ]
            )
            PayoutService.handle_payment_success(
                str(payment_intent.id),
                provider_reference=b2c.transaction_id,
                provider_name="safaricom",
            )

    @staticmethod
    def _process_bank_payout(payment_intent: PaymentIntent):
        """Process bank transfer payout."""
        raise ValueError("Bank transfer payouts are not available right now.")

    @staticmethod
    def _process_wallet_payout(payment_intent: PaymentIntent):
        """Process chama wallet payout (instant)."""
        payout = Payout.objects.select_related("chama", "member__user").get(id=payment_intent.purpose_id)

        payment_intent.status = PaymentStatus.SUCCESS
        payment_intent.completed_at = timezone.now()
        payment_intent.provider = "internal"
        payment_intent.provider_intent_id = f"internal_{payment_intent.reference}"
        payment_intent.save(update_fields=["status", "completed_at", "provider", "provider_intent_id", "updated_at"])

        ledger_entry = LedgerService.post_payout(
            chama=payout.chama,
            payout_id=str(payout.id),
            amount=Decimal(str(payment_intent.amount)),
            created_by=None,
        )

        receipt = PayoutService._ensure_payment_receipt(
            intent=payment_intent,
            provider_reference=str(payment_intent.reference or "")[:120],
            provider_name="internal",
            actor=None,
            metadata={"payout_id": str(payout.id), "payout_method": payout.payout_method},
        )

        # Log audit
        PayoutAuditLog.objects.create(
            payout=payout,
            action="PAYMENT_SUCCESS",
            new_status=PayoutStatus.SUCCESS,
            details={
                "payment_method": "wallet",
                "ledger_entry_id": str(ledger_entry.id),
                "receipt_id": str(receipt.id),
            },
        )

        payout.status = PayoutStatus.SUCCESS
        payout.payment_completed_at = timezone.now()
        payout.ledger_entry = ledger_entry
        payout.receipt_generated_at = timezone.now()
        payout.save(update_fields=["status", "payment_completed_at", "ledger_entry", "receipt_generated_at", "updated_at"])

        rotation = PayoutRotation.objects.get(chama=payout.chama)
        rotation.last_completed_payout = payout
        rotation.advance_rotation()

        NotificationService.create_notification(
            user=payout.member.user,
            notification_type="PAYOUT_SUCCESS",
            title="Payout Received",
            message=f"KES {payout.amount} credited to your wallet.",
            reference_id=payout.id,
            channels=["PUSH", "IN_APP", "SMS"],
        )

    @staticmethod
    @transaction.atomic
    def handle_payment_success(
        payment_intent_id,
        *,
        provider_reference: str = "",
        provider_name: str = "",
    ) -> Payout:
        """
        Handle successful payment callback.

        Called by payment provider webhooks (M-Pesa, bank, etc).

        Args:
            payment_intent_id: PaymentIntent UUID

        Returns:
            Updated Payout
        """
        payment_intent = PaymentIntent.objects.select_related("chama", "user").get(id=payment_intent_id)
        payout = Payout.objects.select_related("chama", "member__user").get(id=payment_intent.purpose_id)

        effective_provider_reference = (
            str(provider_reference or "")
            or str(payment_intent.provider_intent_id or "")
            or str(payment_intent.reference or "")
        )[:120]
        effective_provider_name = str(provider_name or payment_intent.provider or "").strip()[:50] or "provider"

        payment_intent.status = PaymentStatus.SUCCESS
        payment_intent.completed_at = timezone.now()
        payment_intent.provider = effective_provider_name
        payment_intent.save(update_fields=["status", "completed_at", "provider", "updated_at"])

        if payout.payout_method == PayoutPaymentMethod.WALLET:
            ledger_entry = LedgerService.post_payout(
                chama=payout.chama,
                payout_id=str(payout.id),
                amount=Decimal(str(payout.amount)),
                created_by=None,
            )
        else:
            PayoutService._release_chama_wallet_lock(payout=payout, restore_available=False)
            ledger_entry = PayoutService._post_external_payout_ledger(
                payout=payout,
                provider_reference=effective_provider_reference,
                actor=None,
            )

        receipt = PayoutService._ensure_payment_receipt(
            intent=payment_intent,
            provider_reference=effective_provider_reference,
            provider_name=effective_provider_name,
            actor=None,
            metadata={"payout_id": str(payout.id), "payout_method": payout.payout_method},
        )

        payout.status = PayoutStatus.SUCCESS
        payout.payment_completed_at = timezone.now()
        payout.ledger_entry = ledger_entry
        payout.receipt_generated_at = payout.receipt_generated_at or timezone.now()
        payout.save(update_fields=["status", "payment_completed_at", "ledger_entry", "receipt_generated_at", "updated_at"])

        # Advance rotation
        rotation = PayoutRotation.objects.get(chama=payout.chama)
        rotation.last_completed_payout = payout
        rotation.advance_rotation()

        # Log audit
        PayoutAuditLog.objects.create(
            payout=payout,
            action="PAYMENT_SUCCESS",
            new_status=PayoutStatus.SUCCESS,
            details={
                "payment_intent_id": str(payment_intent.id),
                "ledger_entry_id": str(ledger_entry.id),
                "receipt_id": str(receipt.id),
            },
        )

        # Send notifications
        NotificationService.create_notification(
            user=payout.member.user,
            notification_type="PAYOUT_SUCCESS",
            title="Payout Received",
            message=f"KES {payout.amount} sent to {payout.member.user.phone}",
            reference_id=payout.id,
            channels=["PUSH", "IN_APP", "SMS"],
        )

        NotificationService.create_notification(
            user=payout.chama.get_treasurer(),
            notification_type="PAYOUT_COMPLETED",
            title="Payout Completed",
            message=f"Payout of KES {payout.amount} to {payout.member.user.get_full_name()} completed successfully.",
            reference_id=payout.id,
            channels=["PUSH", "IN_APP"],
        )

        return payout

    @staticmethod
    @transaction.atomic
    def handle_payment_failure(
        payment_intent_id,
        failure_reason: str = "",
        failure_code: str = "",
    ) -> Payout:
        """
        Handle failed payment.

        Args:
            payment_intent_id: PaymentIntent UUID
            failure_reason: Reason for failure
            failure_code: Error code from provider

        Returns:
            Updated Payout
        """
        payment_intent = PaymentIntent.objects.select_related("chama", "user").get(id=payment_intent_id)
        payout = Payout.objects.select_related("chama", "member__user").get(id=payment_intent.purpose_id)

        # Update payment intent
        payment_intent.status = PaymentStatus.FAILED
        payment_intent.failure_reason = failure_reason
        payment_intent.failure_code = failure_code
        payment_intent.completed_at = timezone.now()
        payment_intent.save(
            update_fields=[
                "status",
                "failure_reason",
                "failure_code",
                "completed_at",
                "updated_at",
            ]
        )

        if payout.payout_method != PayoutPaymentMethod.WALLET:
            PayoutService._release_chama_wallet_lock(payout=payout, restore_available=True)

        # Update payout
        payout.payment_failed_at = timezone.now()
        payout.failure_reason = failure_reason
        payout.failure_code = failure_code
        payout.retry_count += 1

        if payout.can_retry():
            payout.status = PayoutStatus.PROCESSING
            payout.save()
            # Will retry via celery task
        else:
            payout.status = PayoutStatus.FAILED
            payout.save()

        # Log audit
        PayoutAuditLog.objects.create(
            payout=payout,
            action="PAYMENT_FAILED",
            new_status=payout.status,
            details={
                "failure_reason": failure_reason,
                "failure_code": failure_code,
                "retry_count": payout.retry_count,
            },
        )

        # Notify treasurer
        NotificationService.create_notification(
            user=payout.chama.get_treasurer(),
            notification_type="PAYOUT_FAILED",
            title="Payout Failed",
            message=(
                f"Payout of KES {payout.amount} to "
                f"{payout.member.user.get_full_name()} failed. "
                f"Reason: {failure_reason}. Retry count: {payout.retry_count}/{payout.max_retries}"
            ),
            reference_id=payout.id,
            channels=["PUSH", "IN_APP"],
        )

        return payout

    @staticmethod
    @transaction.atomic
    def flag_payout_on_hold(
        payout_id,
        reason: str,
        actor_id,
    ) -> Payout:
        """
        Flag payout to place on hold.

        Args:
            payout_id: Payout UUID
            reason: Reason for hold
            actor_id: User ID flagging the hold

        Returns:
            Updated Payout
        """
        payout = Payout.objects.get(id=payout_id)

        payout.is_on_hold = True
        payout.hold_reason = reason
        payout.hold_flagged_by_id = actor_id
        payout.hold_flagged_at = timezone.now()
        payout.status = PayoutStatus.HOLD
        payout.save()

        # Log audit
        PayoutAuditLog.objects.create(
            payout=payout,
            action="FLAGGED_ON_HOLD",
            actor_id=actor_id,
            new_status=PayoutStatus.HOLD,
            reason=reason,
        )

        return payout

    @staticmethod
    @transaction.atomic
    def release_payout_from_hold(
        payout_id,
        actor_id,
        notes: str = "",
    ) -> Payout:
        """
        Release payout from hold and resume processing.

        Args:
            payout_id: Payout UUID
            actor_id: User ID releasing hold
            notes: Additional notes

        Returns:
            Updated Payout
        """
        payout = Payout.objects.get(id=payout_id)

        payout.is_on_hold = False
        payout.hold_resolved_by_id = actor_id
        payout.hold_resolved_at = timezone.now()
        # Resume previous processing status
        payout.status = PayoutStatus.APPROVED
        payout.save()

        # Log audit
        PayoutAuditLog.objects.create(
            payout=payout,
            action="RELEASED_FROM_HOLD",
            actor_id=actor_id,
            new_status=PayoutStatus.APPROVED,
            reason=notes,
        )

        return payout

    @staticmethod
    def _generate_receipt(payout: Payout):
        """Generate PDF receipt for payout."""
        if payout.status != PayoutStatus.SUCCESS:
            return
        if payout.receipt_generated_at:
            return

        intent = payout.payment_intent
        if not intent:
            payout.receipt_generated_at = timezone.now()
            payout.save(update_fields=["receipt_generated_at", "updated_at"])
            return

        provider_reference = str(intent.provider_intent_id or intent.reference or "")[:120]
        provider_name = str(intent.provider or "provider")[:50]
        PayoutService._ensure_payment_receipt(
            intent=intent,
            provider_reference=provider_reference,
            provider_name=provider_name,
            actor=None,
            metadata={"payout_id": str(payout.id), "payout_method": payout.payout_method},
        )
        payout.receipt_generated_at = timezone.now()
        payout.save(update_fields=["receipt_generated_at", "updated_at"])

    @staticmethod
    @transaction.atomic
    def retry_failed_payout(payout_id) -> Optional[PaymentIntent]:
        """
        Retry a failed payout payment.

        Args:
            payout_id: Payout UUID

        Returns:
            New PaymentIntent if retry initiated, None if max retries exceeded
        """
        payout = Payout.objects.select_related(
            "chama",
            "member",
        ).get(id=payout_id)

        if not payout.can_retry():
            raise ValueError(
                f"Cannot retry. Max retries ({payout.max_retries}) exceeded."
            )

        if payout.status != PayoutStatus.FAILED:
            raise ValueError(f"Payout status must be FAILED. Current: {payout.status}")

        attempt = payout.retry_count + 1
        meta = dict(payout.metadata or {})
        meta.pop("wallet_lock_applied", None)
        meta.pop("wallet_lock_released", None)
        payout.metadata = meta
        payout.save(update_fields=["metadata", "updated_at"])

        payment_intent = PaymentIntent.objects.create(
            chama=payout.chama,
            user=payout.member.user,
            amount=payout.amount,
            currency=payout.currency or "KES",
            purpose=PaymentPurpose.OTHER,
            purpose_id=payout.id,
            description=f"Payout retry to {payout.member.user.phone}",
            payment_method=PaymentMethod.MPESA,
            phone=payout.member.user.phone or "",
            provider="payout_disbursement",
            provider_intent_id=f"payout_retry_{attempt}_{payout.id}",
            status=PaymentStatus.PENDING,
            idempotency_key=f"payout:{payout.id}:retry:{attempt}",
            metadata={
                "payout_id": str(payout.id),
                "member_id": str(payout.member.user_id),
                "member_phone": payout.member.user.phone,
                "payout_method": payout.payout_method,
                "retry_count": attempt,
                "source": "payouts_service_retry",
            },
        )

        payout.payment_intent = payment_intent
        payout.status = PayoutStatus.PROCESSING
        payout.payment_started_at = timezone.now()
        payout.save()

        # Log audit
        PayoutAuditLog.objects.create(
            payout=payout,
            action="RETRY_INITIATED",
            new_status=PayoutStatus.PROCESSING,
            details={"retry_attempt": payout.retry_count + 1},
        )

        # Process payment
        PayoutService._lock_chama_wallet_for_external_payout(payout=payout)
        PayoutService._process_mpesa_payout(payment_intent)

        return payment_intent
