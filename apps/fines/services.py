from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama
from apps.finance.models import JournalEntrySource, LedgerEntryType
from apps.finance.services import (
    FinanceService,
    FinanceServiceError,
    IdempotencyConflictError,
)
from apps.fines.models import Fine, FineAdjustment, FinePayment, FineStatus
from core.audit import create_activity_log, create_audit_log
from core.utils import to_decimal


class FinesServiceError(Exception):
    pass


class FinesService:
    @staticmethod
    def _notify_member(*, fine: Fine, subject: str, message: str, suffix: str, actor: User | None = None) -> None:
        try:
            from apps.notifications.models import NotificationType
            from apps.notifications.services import NotificationService

            NotificationService.send_notification(
                user=fine.member,
                chama=fine.chama,
                channels=["in_app", "email"],
                message=message,
                subject=subject,
                notification_type=NotificationType.FINE_UPDATE,
                idempotency_key=f"fine:{fine.id}:{suffix}",
                actor=actor,
            )
        except Exception:  # noqa: BLE001
            return

    @staticmethod
    @transaction.atomic
    def issue_fine(*, chama: Chama, member: User, payload: dict, actor: User) -> Fine:
        amount = to_decimal(payload["amount"])
        if amount <= Decimal("0.00"):
            raise FinesServiceError("Fine amount must be greater than zero.")

        fine = Fine.objects.create(
            chama=chama,
            member=member,
            category=payload["category"],
            rule=payload.get("rule"),
            amount=amount,
            due_date=payload["due_date"],
            status=FineStatus.PENDING,
            issued_by=actor,
            issued_reason=payload["reason"],
            attachments=payload.get("attachments", []),
        )

        create_activity_log(
            actor=actor,
            chama_id=chama.id,
            action="fine_issued",
            entity_type="Fine",
            entity_id=fine.id,
            metadata={"member_id": str(member.id), "amount": str(fine.amount), "category": fine.category},
        )
        create_audit_log(
            actor=actor,
            chama_id=chama.id,
            action="fine_created",
            entity_type="Fine",
            entity_id=fine.id,
            metadata={"member_id": str(member.id), "amount": str(fine.amount), "reason": fine.issued_reason},
        )
        FinesService._notify_member(
            fine=fine,
            subject="Fine issued",
            message=(
                f"A fine of KES {fine.amount:,.2f} was issued to your MyChama account. "
                f"Reason: {fine.issued_reason}"
            ),
            suffix="issued",
            actor=actor,
        )
        return fine

    @staticmethod
    @transaction.atomic
    def pay_fine(*, fine_id, payload: dict, actor: User) -> tuple[Fine, FinePayment]:
        fine = get_object_or_404(Fine.objects.select_related("chama", "member"), id=fine_id)
        if fine.status == FineStatus.WAIVED:
            raise FinesServiceError("Waived fines cannot be paid.")

        amount = to_decimal(payload["amount"])
        if amount <= Decimal("0.00"):
            raise FinesServiceError("Payment amount must be greater than zero.")
        outstanding = Decimal(str(fine.outstanding_amount))
        if outstanding <= Decimal("0.00"):
            raise FinesServiceError("Fine is already fully settled.")
        if amount > outstanding:
            raise FinesServiceError("Payment amount exceeds the outstanding fine balance.")

        payment = FinePayment.objects.create(
            fine=fine,
            amount=amount,
            method=payload["method"],
            transaction_reference=str(payload.get("transaction_reference", "")).strip(),
            recorded_by=actor,
            notes=str(payload.get("notes", "")).strip(),
        )

        idempotency_key = (
            str(payload.get("idempotency_key") or "").strip()
            or f"fine:{fine.id}:payment:{payment.id}"
        )
        try:
            journal, debit_line, _credit_line = FinanceService._create_balanced_journal(
                chama=fine.chama,
                actor=actor,
                reference=payment.transaction_reference or f"fine:{fine.id}:payment:{payment.id}",
                description=f"Fine payment for {fine.member.get_full_name()}",
                source_type=JournalEntrySource.PENALTY,
                source_id=fine.id,
                idempotency_key=idempotency_key,
                entry_type=LedgerEntryType.PENALTY,
                debit_account=FinanceService._get_or_create_account(fine.chama, "cash"),
                credit_account=FinanceService._get_or_create_account(fine.chama, "penalty_income"),
                amount=amount,
                metadata={
                    "fine_id": str(fine.id),
                    "member_id": str(fine.member_id),
                    "payment_id": str(payment.id),
                    "method": payment.method,
                },
            )
        except (FinanceServiceError, IdempotencyConflictError) as exc:
            raise FinesServiceError(str(exc)) from exc

        remaining = Decimal(str(fine.outstanding_amount))
        fine.status = FineStatus.PAID if remaining <= Decimal("0.00") else (
            FineStatus.OVERDUE if fine.due_date < timezone.localdate() else FineStatus.DUE
        )
        if fine.status == FineStatus.PAID:
            fine.paid_at = timezone.now()
        fine.save(update_fields=["status", "paid_at", "updated_at"])

        create_activity_log(
            actor=actor,
            chama_id=fine.chama_id,
            action="fine_payment_recorded",
            entity_type="Fine",
            entity_id=fine.id,
            metadata={
                "payment_id": str(payment.id),
                "amount": str(payment.amount),
                "ledger_entry_id": str(debit_line.id),
                "journal_entry_id": str(journal.id),
            },
        )
        create_audit_log(
            actor=actor,
            chama_id=fine.chama_id,
            action="fine_payment_posted",
            entity_type="Fine",
            entity_id=fine.id,
            metadata={
                "payment_id": str(payment.id),
                "amount": str(payment.amount),
                "remaining_balance": str(fine.outstanding_amount),
                "idempotency_key": idempotency_key,
            },
        )
        FinesService._notify_member(
            fine=fine,
            subject="Fine payment received",
            message=(
                f"We received KES {payment.amount:,.2f} for your fine. "
                f"Outstanding balance is KES {Decimal(str(fine.outstanding_amount)):,.2f}."
            ),
            suffix=f"paid:{payment.id}",
            actor=actor,
        )
        return fine, payment

    @staticmethod
    @transaction.atomic
    def waive_fine(*, fine_id, reason: str, actor: User) -> Fine:
        fine = get_object_or_404(Fine.objects.select_related("chama", "member"), id=fine_id)
        if fine.status == FineStatus.PAID:
            raise FinesServiceError("Paid fines cannot be waived.")
        if fine.status == FineStatus.WAIVED:
            return fine

        FineAdjustment.objects.create(
            fine=fine,
            before_amount=fine.amount,
            after_amount=Decimal("0.00"),
            reason=reason,
            adjusted_by=actor,
        )
        fine.status = FineStatus.WAIVED
        fine.waived_at = timezone.now()
        fine.save(update_fields=["status", "waived_at", "updated_at"])

        create_activity_log(
            actor=actor,
            chama_id=fine.chama_id,
            action="fine_waived",
            entity_type="Fine",
            entity_id=fine.id,
            metadata={"reason": reason},
        )
        create_audit_log(
            actor=actor,
            chama_id=fine.chama_id,
            action="fine_waived",
            entity_type="Fine",
            entity_id=fine.id,
            metadata={"reason": reason, "outstanding_balance": str(fine.outstanding_amount)},
        )
        FinesService._notify_member(
            fine=fine,
            subject="Fine waived",
            message=f"Your fine was waived. Reason: {reason}",
            suffix="waived",
            actor=actor,
        )
        return fine
