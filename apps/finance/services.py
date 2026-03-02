from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import IntegrityError, transaction
from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth
from django.shortcuts import get_object_or_404
from django.utils import timezone

logger = logging.getLogger(__name__)

from apps.accounts.models import User
from apps.chama.models import Chama, MemberStatus, Membership
from apps.finance.models import (
    Contribution,
    ContributionGoal,
    ContributionGoalStatus,
    ContributionType,
    InstallmentSchedule,
    InstallmentStatus,
    LedgerDirection,
    LedgerEntry,
    LedgerEntryType,
    LedgerStatus,
    Loan,
    LoanApprovalDecision,
    LoanApprovalLog,
    LoanApprovalStage,
    LoanEligibilityCheck,
    LoanEligibilityStatus,
    LoanGuarantor,
    LoanGuarantorStatus,
    LoanInterestType,
    LoanProduct,
    LoanRestructureRequest,
    LoanRestructureStatus,
    LoanStatus,
    LoanTopUpRequest,
    LoanTopUpStatus,
    ManualAdjustment,
    MethodChoices,
    MonthClosure,
    Penalty,
    PenaltyStatus,
    Repayment,
)
from core.audit import create_activity_log, create_audit_log
from core.constants import CurrencyChoices
from core.utils import parse_iso_date, to_decimal


class FinanceServiceError(Exception):
    pass


class IdempotencyConflictError(FinanceServiceError):
    pass


class MonthClosedError(FinanceServiceError):
    pass


def _first_day_of_month(value: date) -> date:
    return value.replace(day=1)


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1

    day = min(
        value.day,
        [
            31,
            29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
            31,
            30,
            31,
            30,
            31,
            31,
            30,
            31,
            30,
            31,
        ][month - 1],
    )
    return date(year, month, day)


def _to_date(value) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()

    parsed = parse_iso_date(str(value))
    if not parsed:
        raise FinanceServiceError("Invalid date format.")
    return parsed


def _ensure_member_active(chama: Chama, member: User):
    is_active_member = Membership.objects.filter(
        chama=chama,
        user=member,
        is_active=True,
        is_approved=True,
        status=MemberStatus.ACTIVE,
        exited_at__isnull=True,
    ).exists()
    if not is_active_member:
        raise FinanceServiceError("Member is not active and approved in this chama.")


def _loan_total_payable(loan: Loan, early_repayment_date: date | None = None) -> Decimal:
    """
    Calculate total payable amount for a loan.
    Applies early repayment discount if loan has early repayment date before original due date.
    
    Args:
        loan: Loan object
        early_repayment_date: Optional early repayment date to calculate discount
    
    Returns:
        Total payable amount in Decimal
    """
    principal = to_decimal(loan.principal)
    rate = to_decimal(loan.interest_rate, precision="0.0001")
    duration = Decimal(loan.duration_months)

    if loan.interest_type == LoanInterestType.FLAT:
        total_interest = (
            principal * (rate / Decimal("100")) * (duration / Decimal("12"))
        )
        total_payable = principal + total_interest
    else:
        monthly_rate = rate / Decimal("100") / Decimal("12")
        if monthly_rate <= Decimal("0"):
            total_payable = principal
        else:
            factor = (Decimal("1") + monthly_rate) ** loan.duration_months
            installment = principal * (monthly_rate * factor) / (factor - Decimal("1"))
            installment = installment.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total_payable = installment * duration

    # Apply early repayment discount if applicable
    if early_repayment_date and loan.due_date and early_repayment_date < loan.due_date:
        loan_product = loan.loan_product
        discount_percent = to_decimal(
            getattr(loan_product, "early_repayment_discount_percent", Decimal("0"))
        )
        if discount_percent > Decimal("0"):
            discount_amount = total_payable * (discount_percent / Decimal("100"))
            total_payable = total_payable - discount_amount

    return to_decimal(total_payable)



@dataclass
class LedgerPostResult:
    ledger_entry: LedgerEntry
    created: object


@dataclass
class LoanEligibilityResult:
    eligible: bool
    recommended_max_amount: Decimal
    reasons: list[str]
    loan_product: LoanProduct


class FinanceService:
    @staticmethod
    def _ensure_month_open(chama: Chama, value_date: date):
        month = _first_day_of_month(value_date)
        if MonthClosure.objects.filter(chama=chama, month=month).exists():
            raise MonthClosedError(f"Month {month:%Y-%m} is closed for this chama.")

    @staticmethod
    def _post_ledger(
        *,
        chama: Chama,
        entry_type: str,
        direction: str,
        amount: Decimal,
        idempotency_key: str,
        reference_type: str,
        reference_id,
        narration: str,
        actor: User,
        reversal_of: LedgerEntry | None = None,
    ) -> LedgerEntry:
        reference_type_value = str(reference_type or "").strip()
        reference_id_value = str(reference_id) if reference_id else ""
        meta = {
            "reference_type": reference_type_value,
            "reference_id": reference_id_value,
        }
        related_loan_id = None
        if reference_type_value.lower() == "loan" and reference_id:
            related_loan_id = reference_id

        create_kwargs = {
            "chama": chama,
            "entry_type": entry_type,
            "direction": direction,
            "amount": to_decimal(amount),
            "currency": CurrencyChoices.KES,
            "idempotency_key": idempotency_key,
            "reversal_of": reversal_of,
            "narration": narration,
            "meta": meta,
            "created_by": actor,
            "updated_by": actor,
        }
        if related_loan_id:
            create_kwargs["related_loan_id"] = related_loan_id

        try:
            ledger_entry = LedgerEntry.objects.create(**create_kwargs)
            create_audit_log(
                actor=actor,
                chama_id=chama.id,
                action="finance_ledger_posted",
                entity_type="LedgerEntry",
                entity_id=ledger_entry.id,
                metadata={
                    "entry_type": entry_type,
                    "direction": direction,
                    "amount": str(amount),
                    "idempotency_key": idempotency_key,
                    "reference_type": reference_type,
                    "reference_id": str(reference_id) if reference_id else "",
                    "reversal_of": str(reversal_of.id) if reversal_of else "",
                },
            )
            return ledger_entry
        except IntegrityError as exc:
            if "uniq_ledger_idempotency_per_chama" in str(exc) or "idempotency" in str(
                exc
            ):
                raise IdempotencyConflictError(
                    "Duplicate idempotency_key for chama."
                ) from exc
            raise

    @staticmethod
    def _resolve_loan_product(chama: Chama, payload: dict) -> LoanProduct:
        product_id = payload.get("loan_product_id")
        if product_id:
            return get_object_or_404(
                LoanProduct,
                id=product_id,
                chama=chama,
                is_active=True,
            )

        product = (
            LoanProduct.objects.filter(chama=chama, is_active=True, is_default=True)
            .order_by("created_at")
            .first()
        )
        if product:
            return product

        fallback = (
            LoanProduct.objects.filter(chama=chama, is_active=True)
            .order_by("created_at")
            .first()
        )
        if fallback:
            return fallback

        raise FinanceServiceError("No active loan policy configured for this chama.")

    @staticmethod
    def evaluate_loan_eligibility(
        *,
        chama: Chama,
        member: User,
        principal: Decimal,
        duration_months: int,
        loan_product: LoanProduct,
    ) -> LoanEligibilityResult:
        reasons: list[str] = []
        principal_amount = to_decimal(principal)
        recommended_max = to_decimal(loan_product.max_loan_amount)

        membership = Membership.objects.filter(
            chama=chama,
            user=member,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        ).first()
        if not membership or not membership.is_active:
            reasons.append("Membership is not approved and active.")
        else:
            min_joined_at = timezone.now() - timedelta(
                days=30 * loan_product.minimum_membership_months
            )
            if (
                loan_product.minimum_membership_months > 0
                and membership.joined_at
                and membership.joined_at > min_joined_at
            ):
                reasons.append(
                    "Minimum membership duration requirement has not been met."
                )

        if not (
            loan_product.min_duration_months
            <= duration_months
            <= loan_product.max_duration_months
        ):
            reasons.append(
                f"Loan duration must be between {loan_product.min_duration_months} and "
                f"{loan_product.max_duration_months} months."
            )

        contributions_total = Contribution.objects.filter(
            chama=chama,
            member=member,
        ).aggregate(
            total=Coalesce(
                Sum("amount"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )[
            "total"
        ]
        contributions_total = to_decimal(contributions_total)
        if loan_product.contribution_multiple > Decimal("0.00"):
            recommended_by_savings = to_decimal(
                contributions_total * loan_product.contribution_multiple
            )
            recommended_max = min(recommended_max, recommended_by_savings)

        if loan_product.minimum_contribution_months > 0:
            since_date = timezone.localdate() - timedelta(
                days=31 * loan_product.minimum_contribution_months
            )
            contributed_months = (
                Contribution.objects.filter(
                    chama=chama,
                    member=member,
                    date_paid__gte=since_date,
                )
                .annotate(month=TruncMonth("date_paid"))
                .values("month")
                .distinct()
                .count()
            )
            if contributed_months < loan_product.minimum_contribution_months:
                reasons.append("Contribution consistency requirement has not been met.")

        if (
            loan_product.block_if_unpaid_penalties
            and Penalty.objects.filter(
                chama=chama,
                member=member,
                status=PenaltyStatus.UNPAID,
            ).exists()
        ):
            reasons.append("Member has unpaid penalties.")

        if (
            loan_product.block_if_overdue_loans
            and Loan.objects.filter(
                chama=chama,
                member=member,
                status__in=[
                    LoanStatus.APPROVED,
                    LoanStatus.DISBURSING,
                    LoanStatus.DISBURSED,
                    LoanStatus.ACTIVE,
                ],
                installments__status=InstallmentStatus.OVERDUE,
            ).exists()
        ):
            reasons.append("Member has overdue loan installments.")

        has_active_loan = Loan.objects.filter(
            chama=chama,
            member=member,
            status__in=[
                LoanStatus.APPROVED,
                LoanStatus.DISBURSING,
                LoanStatus.DISBURSED,
                LoanStatus.ACTIVE,
            ],
        ).exists()
        if has_active_loan:
            reasons.append("Member already has an active loan.")

        if principal_amount > recommended_max:
            reasons.append(
                f"Requested amount exceeds recommended maximum of KES {recommended_max}."
            )

        return LoanEligibilityResult(
            eligible=not reasons,
            recommended_max_amount=max(recommended_max, Decimal("0.00")),
            reasons=reasons,
            loan_product=loan_product,
        )

    @staticmethod
    @transaction.atomic
    def post_contribution(payload: dict, actor: User) -> LedgerPostResult:
        chama = get_object_or_404(Chama, id=payload["chama_id"])
        member = get_object_or_404(User, id=payload["member_id"])
        _ensure_member_active(chama, member)
        contribution_type = get_object_or_404(
            ContributionType,
            id=payload["contribution_type_id"],
            chama=chama,
            is_active=True,
        )

        date_paid = _to_date(payload["date_paid"])
        FinanceService._ensure_month_open(chama, date_paid)

        contribution = Contribution.objects.create(
            chama=chama,
            member=member,
            contribution_type=contribution_type,
            amount=to_decimal(payload["amount"]),
            date_paid=date_paid,
            method=payload.get("method", MethodChoices.MPESA),
            receipt_code=payload["receipt_code"],
            recorded_by=actor,
            created_by=actor,
            updated_by=actor,
        )

        active_goals = ContributionGoal.objects.filter(
            chama=chama,
            member=member,
            is_active=True,
            status=ContributionGoalStatus.ACTIVE,
        )
        for goal in active_goals:
            goal.current_amount = to_decimal(goal.current_amount + contribution.amount)
            if goal.current_amount >= goal.target_amount:
                goal.status = ContributionGoalStatus.COMPLETED
                goal.is_active = False
            goal.updated_by = actor
            goal.save(update_fields=["current_amount", "status", "is_active", "updated_by", "updated_at"])

        ledger = FinanceService._post_ledger(
            chama=chama,
            entry_type=LedgerEntryType.CONTRIBUTION,
            direction=LedgerDirection.CREDIT,
            amount=contribution.amount,
            idempotency_key=payload["idempotency_key"],
            reference_type="Contribution",
            reference_id=contribution.id,
            narration=f"Contribution posted for {member.full_name}",
            actor=actor,
        )

        create_activity_log(
            actor=actor,
            chama_id=chama.id,
            action="contribution_recorded",
            entity_type="Contribution",
            entity_id=contribution.id,
            metadata={
                "member_id": str(member.id),
                "amount": str(contribution.amount),
                "receipt_code": contribution.receipt_code,
                "ledger_entry_id": str(ledger.id),
            },
        )

        return LedgerPostResult(ledger_entry=ledger, created=contribution)

    @staticmethod
    @transaction.atomic
    def check_loan_eligibility(payload: dict, actor: User) -> dict:
        chama = get_object_or_404(Chama, id=payload["chama_id"])
        member = get_object_or_404(User, id=payload["member_id"])
        loan_product = FinanceService._resolve_loan_product(chama, payload)
        result = FinanceService.evaluate_loan_eligibility(
            chama=chama,
            member=member,
            principal=to_decimal(payload["principal"]),
            duration_months=int(payload["duration_months"]),
            loan_product=loan_product,
        )
        return {
            "chama_id": str(chama.id),
            "member_id": str(member.id),
            "loan_product_id": str(loan_product.id),
            "eligible": result.eligible,
            "status": (
                LoanEligibilityStatus.ELIGIBLE
                if result.eligible
                else LoanEligibilityStatus.INELIGIBLE
            ),
            "recommended_max_amount": str(to_decimal(result.recommended_max_amount)),
            "reasons": result.reasons,
        }

    @staticmethod
    @transaction.atomic
    def upsert_contribution_goal(payload: dict, actor: User) -> ContributionGoal:
        chama = get_object_or_404(Chama, id=payload["chama_id"])
        member = get_object_or_404(User, id=payload["member_id"])
        _ensure_member_active(chama, member)

        goal = (
            ContributionGoal.objects.filter(
                chama=chama,
                member=member,
                title=payload["title"],
                is_active=True,
            )
            .order_by("-created_at")
            .first()
        )
        if goal:
            goal.target_amount = to_decimal(payload["target_amount"])
            goal.due_date = payload.get("due_date")
            goal.status = payload.get("status", goal.status)
            goal.is_active = goal.status == ContributionGoalStatus.ACTIVE
            goal.updated_by = actor
            goal.save(
                update_fields=[
                    "target_amount",
                    "due_date",
                    "status",
                    "is_active",
                    "updated_by",
                    "updated_at",
                ]
            )
            return goal

        return ContributionGoal.objects.create(
            chama=chama,
            member=member,
            title=payload["title"],
            target_amount=to_decimal(payload["target_amount"]),
            due_date=payload.get("due_date"),
            status=payload.get("status", ContributionGoalStatus.ACTIVE),
            is_active=payload.get("status", ContributionGoalStatus.ACTIVE)
            == ContributionGoalStatus.ACTIVE,
            created_by=actor,
            updated_by=actor,
        )

    @staticmethod
    @transaction.atomic
    def add_loan_guarantor(payload: dict, actor: User) -> LoanGuarantor:
        loan = get_object_or_404(Loan, id=payload["loan_id"])
        guarantor = get_object_or_404(User, id=payload["guarantor_id"])
        _ensure_member_active(loan.chama, guarantor)
        if guarantor.id == loan.member_id:
            raise FinanceServiceError("Borrower cannot guarantee their own loan.")

        guarantor_obj, created = LoanGuarantor.objects.get_or_create(
            loan=loan,
            guarantor=guarantor,
            defaults={
                "guaranteed_amount": to_decimal(payload["guaranteed_amount"]),
                "status": LoanGuarantorStatus.ACCEPTED,
                "accepted_at": timezone.now(),
                "created_by": actor,
                "updated_by": actor,
            },
        )
        if not created:
            guarantor_obj.guaranteed_amount = to_decimal(payload["guaranteed_amount"])
            guarantor_obj.status = LoanGuarantorStatus.ACCEPTED
            guarantor_obj.accepted_at = timezone.now()
            guarantor_obj.rejected_at = None
            guarantor_obj.updated_by = actor
            guarantor_obj.save(
                update_fields=[
                    "guaranteed_amount",
                    "status",
                    "accepted_at",
                    "rejected_at",
                    "updated_by",
                    "updated_at",
                ]
            )
        return guarantor_obj

    @staticmethod
    def compute_wallet_balance(chama_id, member_id):
        statement = FinanceService.compute_member_statement(chama_id, member_id)
        totals = statement.get("totals", {})
        return {
            "chama_id": str(chama_id),
            "member_id": str(member_id),
            "currency": CurrencyChoices.KES,
            "wallet_balance": totals.get("closing_balance", "0.00"),
            "components": {
                "contributions": totals.get("contributions", "0.00"),
                "loan_disbursements": totals.get("loan_disbursements", "0.00"),
                "repayments": totals.get("repayments", "0.00"),
                "penalties_debited": totals.get("penalties_debited", "0.00"),
                "penalties_credited": totals.get("penalties_credited", "0.00"),
            },
        }

    @staticmethod
    def compute_credit_score(chama_id, member_id):
        chama = get_object_or_404(Chama, id=chama_id)
        member = get_object_or_404(User, id=member_id)

        contributions_months = (
            Contribution.objects.filter(chama=chama, member=member)
            .annotate(month=TruncMonth("date_paid"))
            .values("month")
            .distinct()
            .count()
        )
        penalties_unpaid = Penalty.objects.filter(
            chama=chama,
            member=member,
            status=PenaltyStatus.UNPAID,
        ).count()
        loans = Loan.objects.filter(chama=chama, member=member).exclude(
            status=LoanStatus.REJECTED
        )
        overdue_installments = InstallmentSchedule.objects.filter(
            loan__in=loans,
            status=InstallmentStatus.OVERDUE,
        ).count()

        total_due = InstallmentSchedule.objects.filter(loan__in=loans).aggregate(
            total=Coalesce(
                Sum("expected_amount"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        total_paid = Repayment.objects.filter(loan__in=loans).aggregate(
            total=Coalesce(
                Sum("amount"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        repayment_rate = Decimal("0.00")
        if Decimal(total_due) > Decimal("0.00"):
            repayment_rate = Decimal(total_paid) / Decimal(total_due)

        score = Decimal("550")
        score += min(Decimal(contributions_months) * Decimal("15"), Decimal("120"))
        score += min(repayment_rate * Decimal("180"), Decimal("180"))
        score -= Decimal(overdue_installments) * Decimal("35")
        score -= Decimal(penalties_unpaid) * Decimal("20")
        score = max(Decimal("300"), min(score, Decimal("850")))

        return {
            "chama_id": str(chama.id),
            "member_id": str(member.id),
            "score": int(score),
            "factors": {
                "contribution_months": contributions_months,
                "repayment_rate_percent": str(to_decimal(repayment_rate * Decimal("100"))),
                "overdue_installments": overdue_installments,
                "unpaid_penalties": penalties_unpaid,
            },
        }

    @staticmethod
    @transaction.atomic
    def request_loan_topup(loan_id, payload: dict, actor: User) -> LoanTopUpRequest:
        loan = get_object_or_404(Loan, id=loan_id)
        if loan.status not in {
            LoanStatus.APPROVED,
            LoanStatus.DISBURSING,
            LoanStatus.DISBURSED,
            LoanStatus.ACTIVE,
        }:
            raise FinanceServiceError("Loan is not eligible for top-up.")

        request = LoanTopUpRequest.objects.create(
            loan=loan,
            requested_amount=to_decimal(payload["requested_amount"]),
            reason=payload.get("reason", ""),
            status=LoanTopUpStatus.REQUESTED,
            created_by=actor,
            updated_by=actor,
        )
        return request

    @staticmethod
    @transaction.atomic
    def review_loan_topup(
        request_id,
        *,
        actor: User,
        decision: str,
        note: str = "",
    ) -> LoanTopUpRequest:
        topup = get_object_or_404(LoanTopUpRequest.objects.select_related("loan"), id=request_id)
        if topup.status != LoanTopUpStatus.REQUESTED:
            raise FinanceServiceError("Top-up request is already processed.")

        if decision == LoanTopUpStatus.REJECTED:
            topup.status = LoanTopUpStatus.REJECTED
            topup.reviewed_by = actor
            topup.reviewed_at = timezone.now()
            topup.review_note = note
            topup.updated_by = actor
            topup.save(
                update_fields=[
                    "status",
                    "reviewed_by",
                    "reviewed_at",
                    "review_note",
                    "updated_by",
                    "updated_at",
                ]
            )
            return topup

        base_loan = topup.loan
        new_loan = Loan.objects.create(
            chama=base_loan.chama,
            member=base_loan.member,
            loan_product=base_loan.loan_product,
            principal=to_decimal(topup.requested_amount),
            interest_type=base_loan.interest_type,
            interest_rate=base_loan.interest_rate,
            duration_months=base_loan.duration_months,
            grace_period_days=base_loan.grace_period_days,
            late_penalty_type=base_loan.late_penalty_type,
            late_penalty_value=base_loan.late_penalty_value,
            early_repayment_discount_percent=base_loan.early_repayment_discount_percent,
            eligibility_status=LoanEligibilityStatus.ELIGIBLE,
            eligibility_reason="Top-up approved",
            recommended_max_amount=to_decimal(topup.requested_amount),
            status=LoanStatus.APPROVED,
            approved_by=actor,
            approved_at=timezone.now(),
            created_by=actor,
            updated_by=actor,
        )
        FinanceService.generate_schedule(new_loan)
        from apps.payments.services import PaymentWorkflowService

        PaymentWorkflowService.ensure_loan_disbursement_intent(loan=new_loan, actor=actor)

        topup.status = LoanTopUpStatus.APPROVED
        topup.reviewed_by = actor
        topup.reviewed_at = timezone.now()
        topup.review_note = note
        topup.created_loan = new_loan
        topup.updated_by = actor
        topup.save(
            update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "review_note",
                "created_loan",
                "updated_by",
                "updated_at",
            ]
        )
        return topup

    @staticmethod
    @transaction.atomic
    def request_loan_restructure(loan_id, payload: dict, actor: User) -> LoanRestructureRequest:
        loan = get_object_or_404(Loan, id=loan_id)
        if loan.status not in {
            LoanStatus.DISBURSED,
            LoanStatus.ACTIVE,
            LoanStatus.DISBURSING,
        }:
            raise FinanceServiceError("Loan is not eligible for restructuring.")

        return LoanRestructureRequest.objects.create(
            loan=loan,
            requested_duration_months=int(payload["requested_duration_months"]),
            requested_interest_rate=payload.get("requested_interest_rate"),
            reason=payload.get("reason", ""),
            status=LoanRestructureStatus.REQUESTED,
            created_by=actor,
            updated_by=actor,
        )

    @staticmethod
    @transaction.atomic
    def review_loan_restructure(
        request_id,
        *,
        actor: User,
        decision: str,
        note: str = "",
    ) -> LoanRestructureRequest:
        restructure = get_object_or_404(
            LoanRestructureRequest.objects.select_related("loan"),
            id=request_id,
        )
        if restructure.status != LoanRestructureStatus.REQUESTED:
            raise FinanceServiceError("Restructure request is already processed.")

        loan = restructure.loan
        has_repayments = Repayment.objects.filter(loan=loan).exists()
        if has_repayments and decision == LoanRestructureStatus.APPROVED:
            raise FinanceServiceError(
                "Cannot apply restructure after repayments have already started."
            )

        if decision == LoanRestructureStatus.REJECTED:
            restructure.status = LoanRestructureStatus.REJECTED
        else:
            restructure.status = LoanRestructureStatus.APPLIED
            loan.duration_months = restructure.requested_duration_months
            if restructure.requested_interest_rate is not None:
                loan.interest_rate = to_decimal(
                    restructure.requested_interest_rate,
                    precision="0.01",
                )
            loan.updated_by = actor
            loan.save(update_fields=["duration_months", "interest_rate", "updated_by", "updated_at"])
            FinanceService.generate_schedule(loan)

        restructure.reviewed_by = actor
        restructure.reviewed_at = timezone.now()
        restructure.review_note = note
        restructure.updated_by = actor
        restructure.save(
            update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "review_note",
                "updated_by",
                "updated_at",
            ]
        )
        return restructure

    @staticmethod
    @transaction.atomic
    def request_loan(payload: dict, actor: User) -> Loan:
        chama = get_object_or_404(Chama, id=payload["chama_id"])
        member = get_object_or_404(User, id=payload["member_id"])
        _ensure_member_active(chama, member)
        loan_product = FinanceService._resolve_loan_product(chama, payload)

        eligibility = FinanceService.evaluate_loan_eligibility(
            chama=chama,
            member=member,
            principal=to_decimal(payload["principal"]),
            duration_months=int(payload["duration_months"]),
            loan_product=loan_product,
        )

        if not eligibility.eligible:
            raise FinanceServiceError(
                "Loan not eligible: " + "; ".join(eligibility.reasons)
            )

        loan = Loan.objects.create(
            chama=chama,
            member=member,
            loan_product=loan_product,
            principal=to_decimal(payload["principal"]),
            interest_type=loan_product.interest_type,
            interest_rate=to_decimal(loan_product.interest_rate, precision="0.01"),
            duration_months=int(payload["duration_months"]),
            grace_period_days=loan_product.grace_period_days,
            late_penalty_type=loan_product.late_penalty_type,
            late_penalty_value=to_decimal(loan_product.late_penalty_value),
            early_repayment_discount_percent=to_decimal(
                loan_product.early_repayment_discount_percent
            ),
            eligibility_status=LoanEligibilityStatus.ELIGIBLE,
            eligibility_reason="",
            recommended_max_amount=to_decimal(eligibility.recommended_max_amount),
            status=LoanStatus.REQUESTED,
            created_by=actor,
            updated_by=actor,
        )

        LoanEligibilityCheck.objects.create(
            loan=loan,
            chama=chama,
            member=member,
            requested_amount=loan.principal,
            recommended_max_amount=to_decimal(eligibility.recommended_max_amount),
            duration_months=loan.duration_months,
            status=LoanEligibilityStatus.ELIGIBLE,
            reasons=[],
            created_by=actor,
            updated_by=actor,
        )

        if loan_product.require_treasurer_review:
            LoanApprovalLog.objects.create(
                loan=loan,
                stage=LoanApprovalStage.TREASURER_REVIEW,
                decision=LoanApprovalDecision.PENDING,
                note="Awaiting treasurer review.",
                actor=None,
                created_by=actor,
                updated_by=actor,
            )

        create_audit_log(
            actor=actor,
            chama_id=chama.id,
            action="loan_requested",
            entity_type="Loan",
            entity_id=loan.id,
            metadata={
                "principal": str(loan.principal),
                "duration_months": loan.duration_months,
                "loan_product_id": str(loan_product.id),
            },
        )
        create_activity_log(
            actor=actor,
            chama_id=chama.id,
            action="loan_requested",
            entity_type="Loan",
            entity_id=loan.id,
            metadata={
                "principal": str(loan.principal),
                "duration_months": loan.duration_months,
            },
        )
        return loan

    @staticmethod
    @transaction.atomic
    def review_loan(loan_id, actor: User, decision: str, note: str = "") -> Loan:
        loan = get_object_or_404(Loan, id=loan_id)
        if loan.status != LoanStatus.REQUESTED:
            raise FinanceServiceError("Only requested loans can be reviewed.")
        if decision not in {
            LoanApprovalDecision.APPROVED,
            LoanApprovalDecision.REJECTED,
        }:
            raise FinanceServiceError("Invalid review decision.")

        LoanApprovalLog.objects.create(
            loan=loan,
            stage=LoanApprovalStage.TREASURER_REVIEW,
            decision=decision,
            note=note,
            actor=actor,
            created_by=actor,
            updated_by=actor,
        )

        if decision == LoanApprovalDecision.REJECTED:
            loan.status = LoanStatus.REJECTED
            loan.updated_by = actor
            loan.save(update_fields=["status", "updated_by", "updated_at"])
            create_audit_log(
                actor=actor,
                chama_id=loan.chama_id,
                action="loan_rejected",
                entity_type="Loan",
                entity_id=loan.id,
                metadata={"stage": LoanApprovalStage.TREASURER_REVIEW, "note": note},
            )
        else:
            loan.status = LoanStatus.REVIEW
            loan.updated_by = actor
            loan.save(update_fields=["status", "updated_by", "updated_at"])

        return loan

    @staticmethod
    @transaction.atomic
    def approve_loan(loan_id, actor: User, note: str = "") -> Loan:
        loan = get_object_or_404(Loan, id=loan_id)
        if loan.status not in {LoanStatus.REQUESTED, LoanStatus.REVIEW}:
            raise FinanceServiceError("Only requested loans can be approved.")

        requires_review = bool(
            loan.loan_product and loan.loan_product.require_treasurer_review
        )
        if requires_review:
            review_log = (
                LoanApprovalLog.objects.filter(
                    loan=loan,
                    stage=LoanApprovalStage.TREASURER_REVIEW,
                    decision=LoanApprovalDecision.APPROVED,
                )
                .order_by("-acted_at")
                .first()
            )
            if not review_log:
                raise FinanceServiceError(
                    "Loan requires treasurer review before admin approval."
                )
            if review_log.actor_id == actor.id:
                raise FinanceServiceError(
                    "Maker-checker enforcement: reviewer and approver must differ."
                )

        loan.status = LoanStatus.APPROVED
        loan.approved_by = actor
        loan.approved_at = timezone.now()
        loan.updated_by = actor
        loan.save(
            update_fields=[
                "status",
                "approved_by",
                "approved_at",
                "updated_by",
                "updated_at",
            ]
        )

        # Send notification to member
        try:
            from apps.notifications.services import notify_loan_approved
            notify_loan_approved(loan)
        except Exception as e:
            logger.warning(f"Failed to send loan approval notification: {e}")

        LoanApprovalLog.objects.create(
            loan=loan,
            stage=LoanApprovalStage.ADMIN_APPROVAL,
            decision=LoanApprovalDecision.APPROVED,
            note=note,
            actor=actor,
            created_by=actor,
            updated_by=actor,
        )

        FinanceService.generate_schedule(loan)
        # Approval now creates a pending payment intent to enforce maker-checker
        # disbursement through the payments workflow.
        from apps.payments.services import PaymentWorkflowService

        disbursement_intent = PaymentWorkflowService.ensure_loan_disbursement_intent(
            loan=loan,
            actor=actor,
        )
        create_audit_log(
            actor=actor,
            chama_id=loan.chama_id,
            action="loan_approved",
            entity_type="Loan",
            entity_id=loan.id,
            metadata={
                "note": note,
                "disbursement_intent_id": str(disbursement_intent.id),
            },
        )
        return loan

    @staticmethod
    @transaction.atomic
    def reject_loan(loan_id, actor: User, note: str = "") -> Loan:
        loan = get_object_or_404(Loan, id=loan_id)
        if loan.status != LoanStatus.REQUESTED:
            raise FinanceServiceError("Only requested loans can be rejected.")

        loan.status = LoanStatus.REJECTED
        loan.updated_by = actor
        loan.save(update_fields=["status", "updated_by", "updated_at"])

        # Send notification to member
        try:
            from apps.notifications.services import notify_loan_rejected
            notify_loan_rejected(loan, note)
        except Exception as e:
            logger.warning(f"Failed to send loan rejection notification: {e}")

        LoanApprovalLog.objects.create(
            loan=loan,
            stage=LoanApprovalStage.ADMIN_APPROVAL,
            decision=LoanApprovalDecision.REJECTED,
            note=note,
            actor=actor,
            created_by=actor,
            updated_by=actor,
        )
        create_audit_log(
            actor=actor,
            chama_id=loan.chama_id,
            action="loan_rejected",
            entity_type="Loan",
            entity_id=loan.id,
            metadata={"note": note},
        )
        return loan

    @staticmethod
    @transaction.atomic
    def disburse_loan(
        loan_id,
        actor: User,
        *,
        idempotency_key: str | None = None,
        disbursement_reference: str = "",
    ) -> LedgerPostResult:
        loan = get_object_or_404(Loan, id=loan_id)
        if loan.status not in {LoanStatus.APPROVED, LoanStatus.DISBURSING}:
            raise FinanceServiceError(
                "Only approved/disbursing loans can be disbursed."
            )
        _ensure_member_active(loan.chama, loan.member)

        if (
            loan.loan_product
            and loan.loan_product.require_separate_disburser
            and loan.approved_by_id == actor.id
        ):
            raise FinanceServiceError(
                "Maker-checker enforcement: approver cannot disburse the same loan."
            )

        disburse_date = timezone.localdate()
        FinanceService._ensure_month_open(loan.chama, disburse_date)

        loan.status = LoanStatus.DISBURSED
        loan.disbursed_by = actor
        loan.disbursed_at = timezone.now()
        loan.disbursement_reference = disbursement_reference or loan.disbursement_reference
        loan.updated_by = actor
        loan.save(
            update_fields=[
                "status",
                "disbursed_by",
                "disbursed_at",
                "disbursement_reference",
                "updated_by",
                "updated_at",
            ]
        )

        # Send notification to member
        try:
            from apps.notifications.services import notify_loan_disbursed
            notify_loan_disbursed(loan)
        except Exception as e:
            logger.warning(f"Failed to send loan disbursement notification: {e}")

        ledger = FinanceService._post_ledger(
            chama=loan.chama,
            entry_type=LedgerEntryType.LOAN_DISBURSEMENT,
            direction=LedgerDirection.DEBIT,
            amount=loan.principal,
            idempotency_key=idempotency_key or f"loan_disburse:{loan.id}",
            reference_type="Loan",
            reference_id=loan.id,
            narration=f"Loan disbursement to {loan.member.full_name}",
            actor=actor,
        )

        create_audit_log(
            actor=actor,
            chama_id=loan.chama_id,
            action="loan_disbursed",
            entity_type="Loan",
            entity_id=loan.id,
            metadata={
                "principal": str(loan.principal),
                "idempotency_key": ledger.idempotency_key,
                "disbursement_reference": loan.disbursement_reference,
            },
        )
        return LedgerPostResult(ledger_entry=ledger, created=loan)

    @staticmethod
    @transaction.atomic
    def generate_schedule(loan: Loan):
        InstallmentSchedule.objects.filter(loan=loan).delete()

        principal = to_decimal(loan.principal)
        rate = to_decimal(loan.interest_rate, precision="0.0001")
        n = int(loan.duration_months)
        if n <= 0:
            return

        due_anchor = timezone.localdate() + timedelta(days=loan.grace_period_days)
        created_by = loan.updated_by or loan.created_by

        if loan.interest_type == LoanInterestType.FLAT:
            total_interest = (
                principal * (rate / Decimal("100")) * (Decimal(n) / Decimal("12"))
            )
            base_principal = (principal / Decimal(n)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            base_interest = (total_interest / Decimal(n)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

            principal_acc = Decimal("0.00")
            interest_acc = Decimal("0.00")
            for idx in range(n):
                due_date = _add_months(due_anchor, idx + 1)
                if idx == n - 1:
                    expected_principal = to_decimal(principal - principal_acc)
                    expected_interest = to_decimal(total_interest - interest_acc)
                else:
                    expected_principal = to_decimal(base_principal)
                    expected_interest = to_decimal(base_interest)
                    principal_acc += expected_principal
                    interest_acc += expected_interest

                expected_amount = to_decimal(expected_principal + expected_interest)
                InstallmentSchedule.objects.create(
                    loan=loan,
                    due_date=due_date,
                    expected_amount=expected_amount,
                    expected_principal=expected_principal,
                    expected_interest=expected_interest,
                    expected_penalty=Decimal("0.00"),
                    status=InstallmentStatus.DUE,
                    created_by=created_by,
                    updated_by=created_by,
                )
            return

        monthly_rate = rate / Decimal("100") / Decimal("12")
        if monthly_rate <= Decimal("0"):
            equal_principal = (principal / Decimal(n)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            remaining = principal
            for idx in range(n):
                due_date = _add_months(due_anchor, idx + 1)
                if idx == n - 1:
                    expected_principal = to_decimal(remaining)
                else:
                    expected_principal = to_decimal(equal_principal)
                expected_interest = Decimal("0.00")
                expected_amount = expected_principal
                remaining = to_decimal(remaining - expected_principal)
                InstallmentSchedule.objects.create(
                    loan=loan,
                    due_date=due_date,
                    expected_amount=expected_amount,
                    expected_principal=expected_principal,
                    expected_interest=expected_interest,
                    expected_penalty=Decimal("0.00"),
                    status=InstallmentStatus.DUE,
                    created_by=created_by,
                    updated_by=created_by,
                )
            return

        factor = (Decimal("1") + monthly_rate) ** n
        payment = principal * (monthly_rate * factor) / (factor - Decimal("1"))
        payment = payment.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        remaining = principal
        for idx in range(n):
            due_date = _add_months(due_anchor, idx + 1)
            interest_component = to_decimal(remaining * monthly_rate)
            if idx == n - 1:
                principal_component = to_decimal(remaining)
            else:
                principal_component = to_decimal(payment - interest_component)
            expected_amount = to_decimal(principal_component + interest_component)
            remaining = to_decimal(remaining - principal_component)

            InstallmentSchedule.objects.create(
                loan=loan,
                due_date=due_date,
                expected_amount=expected_amount,
                expected_principal=principal_component,
                expected_interest=interest_component,
                expected_penalty=Decimal("0.00"),
                status=InstallmentStatus.DUE,
                created_by=created_by,
                updated_by=created_by,
            )

    @staticmethod
    @transaction.atomic
    def post_repayment(loan_id, payload: dict, actor: User) -> LedgerPostResult:
        loan = get_object_or_404(Loan, id=loan_id)
        if loan.status in {
            LoanStatus.REJECTED,
            LoanStatus.DEFAULTED,
            LoanStatus.CLEARED,
            LoanStatus.PAID,
            LoanStatus.CLOSED,
        }:
            raise FinanceServiceError("Loan is not repayable in current status.")
        _ensure_member_active(loan.chama, loan.member)

        date_paid = _to_date(payload["date_paid"])
        FinanceService._ensure_month_open(loan.chama, date_paid)

        repayment = Repayment.objects.create(
            loan=loan,
            amount=to_decimal(payload["amount"]),
            date_paid=date_paid,
            method=payload.get("method", MethodChoices.MPESA),
            receipt_code=payload["receipt_code"],
            recorded_by=actor,
            created_by=actor,
            updated_by=actor,
        )

        ledger = FinanceService._post_ledger(
            chama=loan.chama,
            entry_type=LedgerEntryType.REPAYMENT,
            direction=LedgerDirection.CREDIT,
            amount=repayment.amount,
            idempotency_key=payload["idempotency_key"],
            reference_type="Repayment",
            reference_id=repayment.id,
            narration=f"Loan repayment from {loan.member.full_name}",
            actor=actor,
        )

        total_paid = Repayment.objects.filter(loan=loan).aggregate(
            total=Coalesce(
                Sum("amount"), Value(Decimal("0.00"), output_field=DecimalField())
            )
        )["total"]
        total_paid = to_decimal(total_paid)

        remaining = total_paid
        installments = InstallmentSchedule.objects.filter(loan=loan).order_by(
            "due_date", "created_at"
        )
        today = timezone.localdate()

        total_due = installments.aggregate(
            total=Coalesce(
                Sum("expected_amount"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        total_due = to_decimal(total_due)

        discount_rate = to_decimal(
            loan.early_repayment_discount_percent,
            precision="0.0001",
        )
        discount_amount = Decimal("0.00")
        if discount_rate > Decimal("0.00"):
            future_interest_total = installments.filter(due_date__gt=date_paid).aggregate(
                total=Coalesce(
                    Sum("expected_interest"),
                    Value(Decimal("0.00"), output_field=DecimalField()),
                )
            )["total"]
            future_interest_total = to_decimal(future_interest_total)
            if future_interest_total > Decimal("0.00"):
                discount_amount = to_decimal(
                    future_interest_total * (discount_rate / Decimal("100"))
                )

        effective_total_due = to_decimal(max(total_due - discount_amount, Decimal("0.00")))
        if total_paid >= effective_total_due:
            remaining = to_decimal(total_paid + discount_amount)

        for installment in installments:
            if remaining >= installment.expected_amount:
                installment.status = InstallmentStatus.PAID
                remaining -= installment.expected_amount
            else:
                installment.status = (
                    InstallmentStatus.OVERDUE
                    if installment.due_date < today
                    else InstallmentStatus.DUE
                )
            installment.updated_by = actor
            installment.save(update_fields=["status", "updated_by", "updated_at"])

        if discount_amount > Decimal("0.00") and total_paid >= effective_total_due:
            FinanceService._post_ledger(
                chama=loan.chama,
                entry_type=LedgerEntryType.ADJUSTMENT,
                direction=LedgerDirection.DEBIT,
                amount=discount_amount,
                idempotency_key=f"loan_early_discount:{loan.id}:{repayment.id}",
                reference_type="Loan",
                reference_id=loan.id,
                narration=f"Early repayment discount applied at {discount_rate}%.",
                actor=actor,
            )

        if effective_total_due > Decimal("0.00") and total_paid >= effective_total_due:
            loan.status = LoanStatus.PAID
        elif loan.status in {LoanStatus.DISBURSED, LoanStatus.DISBURSING}:
            loan.status = LoanStatus.ACTIVE

        loan.updated_by = actor
        loan.save(update_fields=["status", "updated_by", "updated_at"])

        create_activity_log(
            actor=actor,
            chama_id=loan.chama_id,
            action="loan_repayment_recorded",
            entity_type="Repayment",
            entity_id=repayment.id,
            metadata={
                "loan_id": str(loan.id),
                "amount": str(repayment.amount),
                "receipt_code": repayment.receipt_code,
                "ledger_entry_id": str(ledger.id),
            },
        )

        return LedgerPostResult(ledger_entry=ledger, created=repayment)

    @staticmethod
    @transaction.atomic
    def issue_penalty(payload: dict, actor: User) -> LedgerPostResult:
        chama = get_object_or_404(Chama, id=payload["chama_id"])
        member = get_object_or_404(User, id=payload["member_id"])
        due_date = _to_date(payload["due_date"])

        FinanceService._ensure_month_open(chama, timezone.localdate())
        _ensure_member_active(chama, member)

        penalty = Penalty.objects.create(
            chama=chama,
            member=member,
            amount=to_decimal(payload["amount"]),
            reason=payload["reason"],
            due_date=due_date,
            status=PenaltyStatus.UNPAID,
            issued_by=actor,
            created_by=actor,
            updated_by=actor,
        )

        ledger = FinanceService._post_ledger(
            chama=chama,
            entry_type=LedgerEntryType.PENALTY,
            direction=LedgerDirection.DEBIT,
            amount=penalty.amount,
            idempotency_key=payload["idempotency_key"],
            reference_type="Penalty",
            reference_id=penalty.id,
            narration=f"Penalty issued to {member.full_name}: {penalty.reason[:80]}",
            actor=actor,
        )

        return LedgerPostResult(ledger_entry=ledger, created=penalty)

    @staticmethod
    @transaction.atomic
    def mark_penalty_paid(penalty_id, payload: dict, actor: User) -> LedgerPostResult:
        penalty = get_object_or_404(Penalty, id=penalty_id)
        if penalty.status != PenaltyStatus.UNPAID:
            raise FinanceServiceError("Only unpaid penalties can be marked paid.")

        penalty.status = PenaltyStatus.PAID
        penalty.resolved_by = actor
        penalty.resolved_at = timezone.now()
        penalty.updated_by = actor
        penalty.save(
            update_fields=[
                "status",
                "resolved_by",
                "resolved_at",
                "updated_by",
                "updated_at",
            ]
        )

        ledger = FinanceService._post_ledger(
            chama=penalty.chama,
            entry_type=LedgerEntryType.PENALTY,
            direction=LedgerDirection.CREDIT,
            amount=penalty.amount,
            idempotency_key=payload["idempotency_key"],
            reference_type="Penalty",
            reference_id=penalty.id,
            narration=f"Penalty paid by {penalty.member.full_name}",
            actor=actor,
        )

        return LedgerPostResult(ledger_entry=ledger, created=penalty)

    @staticmethod
    @transaction.atomic
    def waive_penalty(penalty_id, actor: User) -> LedgerPostResult:
        penalty = get_object_or_404(Penalty, id=penalty_id)
        if penalty.status != PenaltyStatus.UNPAID:
            raise FinanceServiceError("Only unpaid penalties can be waived.")

        penalty.status = PenaltyStatus.WAIVED
        penalty.resolved_by = actor
        penalty.resolved_at = timezone.now()
        penalty.updated_by = actor
        penalty.save(
            update_fields=[
                "status",
                "resolved_by",
                "resolved_at",
                "updated_by",
                "updated_at",
            ]
        )

        ledger = FinanceService._post_ledger(
            chama=penalty.chama,
            entry_type=LedgerEntryType.ADJUSTMENT,
            direction=LedgerDirection.CREDIT,
            amount=penalty.amount,
            idempotency_key=f"penalty_waive:{penalty.id}",
            reference_type="Penalty",
            reference_id=penalty.id,
            narration=f"Penalty waived for {penalty.member.full_name}",
            actor=actor,
        )

        return LedgerPostResult(ledger_entry=ledger, created=penalty)

    @staticmethod
    @transaction.atomic
    def post_manual_adjustment(payload: dict, actor: User) -> LedgerPostResult:
        chama = get_object_or_404(Chama, id=payload["chama_id"])

        try:
            adjustment = ManualAdjustment.objects.create(
                chama=chama,
                amount=to_decimal(payload["amount"]),
                direction=payload["direction"],
                reason=payload["reason"],
                idempotency_key=payload["idempotency_key"],
                created_by=actor,
                updated_by=actor,
            )
        except IntegrityError as exc:
            if "idempotency" in str(exc):
                raise IdempotencyConflictError(
                    "Duplicate idempotency_key for manual adjustment."
                ) from exc
            raise

        ledger = FinanceService._post_ledger(
            chama=chama,
            entry_type=LedgerEntryType.ADJUSTMENT,
            direction=adjustment.direction,
            amount=adjustment.amount,
            idempotency_key=payload["idempotency_key"],
            reference_type="ManualAdjustment",
            reference_id=adjustment.id,
            narration=adjustment.reason,
            actor=actor,
        )

        create_audit_log(
            actor=actor,
            chama_id=chama.id,
            action="finance_manual_adjustment",
            entity_type="ManualAdjustment",
            entity_id=adjustment.id,
            metadata={
                "direction": adjustment.direction,
                "amount": str(adjustment.amount),
                "idempotency_key": adjustment.idempotency_key,
            },
        )

        return LedgerPostResult(ledger_entry=ledger, created=adjustment)

    @staticmethod
    @transaction.atomic
    def reverse_ledger_entry(entry_id, payload: dict, actor: User) -> LedgerPostResult:
        original = get_object_or_404(LedgerEntry, id=entry_id)
        if original.reversal_of_id:
            raise FinanceServiceError("Cannot reverse a reversal entry.")
        if original.reversal_entries.exists():
            raise FinanceServiceError("Ledger entry has already been reversed.")

        FinanceService._ensure_month_open(original.chama, timezone.localdate())
        reverse_direction = (
            LedgerDirection.DEBIT
            if original.direction == LedgerDirection.CREDIT
            else LedgerDirection.CREDIT
        )
        reason = payload.get("reason", "").strip()
        narration = f"Reversal of ledger {original.id}"
        if reason:
            narration = f"{narration}. Reason: {reason}"

        reversal = FinanceService._post_ledger(
            chama=original.chama,
            entry_type=LedgerEntryType.ADJUSTMENT,
            direction=reverse_direction,
            amount=original.amount,
            idempotency_key=payload["idempotency_key"],
            reference_type="LedgerEntry",
            reference_id=original.id,
            narration=narration,
            actor=actor,
            reversal_of=original,
        )
        return LedgerPostResult(ledger_entry=reversal, created=original)

    @staticmethod
    @transaction.atomic
    def close_month(payload: dict, actor: User) -> MonthClosure:
        chama = get_object_or_404(Chama, id=payload["chama_id"])
        month = _first_day_of_month(_to_date(payload["month"]))
        closure, _ = MonthClosure.objects.get_or_create(
            chama=chama,
            month=month,
            defaults={
                "closed_by": actor,
                "notes": payload.get("notes", ""),
                "created_by": actor,
                "updated_by": actor,
            },
        )
        return closure

    @staticmethod
    def get_next_due_installment(loan_id):
        loan = get_object_or_404(Loan, id=loan_id)
        installment = (
            InstallmentSchedule.objects.filter(
                loan=loan,
                status__in=[InstallmentStatus.DUE, InstallmentStatus.OVERDUE],
            )
            .order_by("due_date", "created_at")
            .first()
        )
        if not installment:
            return {"loan_id": str(loan.id), "next_due": None}
        return {
            "loan_id": str(loan.id),
            "next_due": {
                "installment_id": str(installment.id),
                "due_date": installment.due_date.isoformat(),
                "expected_amount": str(installment.expected_amount),
                "status": installment.status,
            },
        }

    @staticmethod
    def compute_member_statement(chama_id, member_id, from_date=None, to_date=None):
        chama = get_object_or_404(Chama, id=chama_id)
        member = get_object_or_404(User, id=member_id)

        from_date = _to_date(from_date) if from_date else None
        to_date = _to_date(to_date) if to_date else None

        contribution_refs = Contribution.objects.filter(chama=chama, member=member)
        repayment_refs = Repayment.objects.filter(
            loan__chama=chama, loan__member=member
        )
        penalty_refs = Penalty.objects.filter(chama=chama, member=member)
        loan_refs = Loan.objects.filter(chama=chama, member=member)

        contributions_qs = contribution_refs
        repayments_qs = repayment_refs
        penalties_qs = penalty_refs
        loans_qs = loan_refs

        if from_date:
            contributions_qs = contributions_qs.filter(date_paid__gte=from_date)
            repayments_qs = repayments_qs.filter(date_paid__gte=from_date)
            penalties_qs = penalties_qs.filter(created_at__date__gte=from_date)
            loans_qs = loans_qs.filter(requested_at__date__gte=from_date)
        if to_date:
            contributions_qs = contributions_qs.filter(date_paid__lte=to_date)
            repayments_qs = repayments_qs.filter(date_paid__lte=to_date)
            penalties_qs = penalties_qs.filter(created_at__date__lte=to_date)
            loans_qs = loans_qs.filter(requested_at__date__lte=to_date)

        contribution_ids = {
            str(value)
            for value in contribution_refs.values_list("id", flat=True)
        }
        repayment_ids = {
            str(value)
            for value in repayment_refs.values_list("id", flat=True)
        }
        penalty_ids = {str(value) for value in penalty_refs.values_list("id", flat=True)}
        loan_ids = {str(value) for value in loan_refs.values_list("id", flat=True)}

        candidate_ledger_qs = LedgerEntry.objects.filter(
            chama=chama,
            status=LedgerStatus.SUCCESS,
        ).select_related("related_loan")

        if from_date:
            candidate_ledger_qs = candidate_ledger_qs.filter(created_at__date__gte=from_date)
        if to_date:
            candidate_ledger_qs = candidate_ledger_qs.filter(created_at__date__lte=to_date)

        ledger_entries = []
        member_id_text = str(member.id)
        for entry in candidate_ledger_qs.order_by("created_at", "id"):
            meta = entry.meta if isinstance(entry.meta, dict) else {}
            ref_type = str(meta.get("reference_type") or "").strip().lower()
            ref_id = str(meta.get("reference_id") or "").strip()
            meta_member_id = str(meta.get("member_id") or "").strip()

            matches_member = (
                (entry.related_loan_id is not None and str(entry.related_loan_id) in loan_ids)
                or entry.created_by_id == member.id
                or (meta_member_id and meta_member_id == member_id_text)
                or (ref_type == "contribution" and ref_id in contribution_ids)
                or (ref_type == "repayment" and ref_id in repayment_ids)
                or (ref_type == "penalty" and ref_id in penalty_ids)
                or (ref_type == "loan" and ref_id in loan_ids)
            )
            if matches_member:
                ledger_entries.append(entry)

        total_contributions = contributions_qs.aggregate(
            total=Coalesce(
                Sum("amount"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        total_repayments = repayments_qs.aggregate(
            total=Coalesce(
                Sum("amount"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        total_loan_disbursements = loans_qs.filter(
            status__in=[
                LoanStatus.DISBURSED,
                LoanStatus.ACTIVE,
                LoanStatus.PAID,
                LoanStatus.CLOSED,
                LoanStatus.DEFAULTED,
            ]
        ).aggregate(
            total=Coalesce(
                Sum("principal"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        total_penalties_debited = penalties_qs.aggregate(
            total=Coalesce(
                Sum("amount"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        total_penalties_credited = penalties_qs.filter(
            status__in=[PenaltyStatus.PAID, PenaltyStatus.WAIVED]
        ).aggregate(
            total=Coalesce(
                Sum("amount"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]

        contributions = [
            {
                "id": str(obj.id),
                "amount": str(obj.amount),
                "date_paid": obj.date_paid.isoformat(),
                "method": obj.method,
                "receipt_code": obj.receipt_code,
                "contribution_type": obj.contribution_type.name,
            }
            for obj in contributions_qs.select_related("contribution_type").order_by(
                "date_paid", "created_at"
            )
        ]
        loans = [
            {
                "id": str(obj.id),
                "principal": str(obj.principal),
                "interest_type": obj.interest_type,
                "interest_rate": str(obj.interest_rate),
                "duration_months": obj.duration_months,
                "status": obj.status,
                "requested_at": obj.requested_at.isoformat(),
                "approved_at": obj.approved_at.isoformat() if obj.approved_at else None,
                "disbursed_at": (
                    obj.disbursed_at.isoformat() if obj.disbursed_at else None
                ),
            }
            for obj in loans_qs.order_by("requested_at", "created_at")
        ]
        repayments = [
            {
                "id": str(obj.id),
                "loan_id": str(obj.loan_id),
                "amount": str(obj.amount),
                "date_paid": obj.date_paid.isoformat(),
                "method": obj.method,
                "receipt_code": obj.receipt_code,
            }
            for obj in repayments_qs.order_by("date_paid", "created_at")
        ]
        penalties = [
            {
                "id": str(obj.id),
                "amount": str(obj.amount),
                "reason": obj.reason,
                "status": obj.status,
                "due_date": obj.due_date.isoformat(),
                "issued_at": obj.created_at.isoformat(),
                "resolved_at": obj.resolved_at.isoformat() if obj.resolved_at else None,
            }
            for obj in penalties_qs.order_by("created_at")
        ]

        running_balance = Decimal("0.00")
        ledger_lines = []
        for entry in ledger_entries:
            meta = entry.meta if isinstance(entry.meta, dict) else {}
            reference_type = (
                "Loan"
                if entry.related_loan_id
                else "PaymentIntent"
                if entry.related_payment_id
                else "B2CPayout"
                if entry.related_payout_id
                else str(meta.get("reference_type") or "")
            )
            reference_id = (
                str(entry.related_loan_id)
                if entry.related_loan_id
                else str(entry.related_payment_id)
                if entry.related_payment_id
                else str(entry.related_payout_id)
                if entry.related_payout_id
                else str(meta.get("reference_id") or "") or None
            )
            signed = (
                entry.amount
                if entry.direction == LedgerDirection.CREDIT
                else (entry.amount * Decimal("-1"))
            )
            running_balance += signed
            ledger_lines.append(
                {
                    "id": str(entry.id),
                    "date": entry.created_at.isoformat(),
                    "entry_type": entry.entry_type,
                    "direction": entry.direction,
                    "amount": str(entry.amount),
                    "reference_type": reference_type,
                    "reference_id": reference_id,
                    "narration": entry.narration,
                    "running_balance": str(to_decimal(running_balance)),
                }
            )

        total_penalties = total_penalties_debited - total_penalties_credited
        closing_balance = to_decimal(
            to_decimal(total_contributions)
            + to_decimal(total_repayments)
            + to_decimal(total_penalties_credited)
            - to_decimal(total_loan_disbursements)
            - to_decimal(total_penalties_debited)
        )

        return {
            "member_id": str(member.id),
            "member_name": member.full_name,
            "chama_id": str(chama.id),
            "currency": CurrencyChoices.KES,
            "from": str(from_date) if from_date else None,
            "to": str(to_date) if to_date else None,
            "total_contributions": str(to_decimal(total_contributions)),
            "total_repayments": str(to_decimal(total_repayments)),
            "total_penalties": str(to_decimal(total_penalties)),
            "ledger_entries": len(ledger_entries),
            "open_loans": Loan.objects.filter(
                chama=chama,
                member=member,
                status__in=[
                    LoanStatus.APPROVED,
                    LoanStatus.DISBURSING,
                    LoanStatus.DISBURSED,
                    LoanStatus.ACTIVE,
                ],
            ).count(),
            "totals": {
                "contributions": str(to_decimal(total_contributions)),
                "loan_disbursements": str(to_decimal(total_loan_disbursements)),
                "repayments": str(to_decimal(total_repayments)),
                "penalties_debited": str(to_decimal(total_penalties_debited)),
                "penalties_credited": str(to_decimal(total_penalties_credited)),
                "closing_balance": str(closing_balance),
            },
            "contributions": contributions,
            "loans": loans,
            "repayments": repayments,
            "penalties": penalties,
            "ledger": ledger_lines,
        }

    @staticmethod
    def compute_loan_portfolio(chama_id, *, mask_members: bool = False):
        chama = get_object_or_404(Chama, id=chama_id)
        loans = (
            Loan.objects.select_related("member")
            .filter(chama=chama)
            .exclude(status=LoanStatus.REJECTED)
        )

        loans_disbursed = loans.filter(
            status__in=[
                LoanStatus.DISBURSED,
                LoanStatus.ACTIVE,
                LoanStatus.CLEARED,
                LoanStatus.PAID,
                LoanStatus.CLOSED,
                LoanStatus.DEFAULTED,
            ]
        )
        total_loans_out = loans_disbursed.aggregate(
            total=Coalesce(
                Sum("principal"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        total_repayments = Repayment.objects.filter(loan__in=loans_disbursed).aggregate(
            total=Coalesce(
                Sum("amount"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        total_loans_out = to_decimal(total_loans_out)
        total_repayments = to_decimal(total_repayments)
        outstanding = to_decimal(
            max(total_loans_out - total_repayments, Decimal("0.00"))
        )

        overdue_loans = loans.filter(
            installments__status=InstallmentStatus.OVERDUE
        ).distinct()
        defaulted_loans = loans.filter(status=LoanStatus.DEFAULTED)
        defaulters_by_loan = {}
        for loan in list(overdue_loans) + list(defaulted_loans):
            loan_repaid = loan.repayments.aggregate(
                total=Coalesce(
                    Sum("amount"),
                    Value(Decimal("0.00"), output_field=DecimalField()),
                )
            )["total"]
            loan_repaid = to_decimal(loan_repaid)
            outstanding_balance = to_decimal(
                max(loan.principal - loan_repaid, Decimal("0.00"))
            )
            member_name = loan.member.full_name
            member_phone = loan.member.phone
            if mask_members:
                member_name = f"Member #{str(loan.member_id)[:8]}"
                member_phone = f"***{member_phone[-4:]}"

            defaulters_by_loan[str(loan.id)] = {
                "loan_id": str(loan.id),
                "member_id": str(loan.member_id),
                "member_name": member_name,
                "member_phone": member_phone,
                "status": loan.status,
                "principal": str(loan.principal),
                "outstanding_balance": str(outstanding_balance),
                "overdue_installments": loan.installments.filter(
                    status=InstallmentStatus.OVERDUE
                ).count(),
            }

        repayment_rate = Decimal("0.00")
        if total_loans_out > Decimal("0.00"):
            repayment_rate = (total_repayments / total_loans_out) * Decimal("100")

        return {
            "chama_id": str(chama.id),
            "total_loans_out": str(total_loans_out),
            "total_repayments": str(total_repayments),
            "outstanding": str(outstanding),
            "overdue_count": overdue_loans.count(),
            "defaulters_count": len(defaulters_by_loan),
            "repayment_rate_percent": str(to_decimal(repayment_rate)),
            "defaulters": list(defaulters_by_loan.values()),
        }

    @staticmethod
    def compute_chama_dashboard(chama_id):
        chama = get_object_or_404(Chama, id=chama_id)

        ledger = LedgerEntry.objects.filter(chama=chama)
        credits = ledger.filter(direction=LedgerDirection.CREDIT).aggregate(
            total=Coalesce(
                Sum("amount"), Value(Decimal("0.00"), output_field=DecimalField())
            )
        )["total"]
        debits = ledger.filter(direction=LedgerDirection.DEBIT).aggregate(
            total=Coalesce(
                Sum("amount"), Value(Decimal("0.00"), output_field=DecimalField())
            )
        )["total"]

        outstanding_penalties = Penalty.objects.filter(
            chama=chama,
            status=PenaltyStatus.UNPAID,
        ).aggregate(
            total=Coalesce(
                Sum("amount"), Value(Decimal("0.00"), output_field=DecimalField())
            )
        )[
            "total"
        ]

        active_loans = Loan.objects.filter(
            chama=chama,
            status__in=[
                LoanStatus.APPROVED,
                LoanStatus.DISBURSING,
                LoanStatus.DISBURSED,
                LoanStatus.ACTIVE,
            ],
        )

        loans_outstanding = active_loans.aggregate(
            total=Coalesce(
                Sum("principal"), Value(Decimal("0.00"), output_field=DecimalField())
            )
        )["total"]
        portfolio = FinanceService.compute_loan_portfolio(chama_id, mask_members=False)

        return {
            "chama_id": str(chama.id),
            "total_credits": str(to_decimal(credits)),
            "total_debits": str(to_decimal(debits)),
            "net_position": str(to_decimal(credits - debits)),
            "active_loan_count": active_loans.count(),
            "loans_outstanding": str(to_decimal(loans_outstanding)),
            "unpaid_penalties": str(to_decimal(outstanding_penalties)),
            "closed_months": MonthClosure.objects.filter(chama=chama).count(),
            "loan_portfolio": portfolio,
        }

    @staticmethod
    def compute_monthly_aggregates(chama_id, months: int = 12):
        chama = get_object_or_404(Chama, id=chama_id)
        since = timezone.now() - timedelta(days=31 * months)

        data = (
            LedgerEntry.objects.filter(chama=chama, created_at__gte=since)
            .annotate(month=TruncMonth("created_at"))
            .values("month", "direction")
            .annotate(
                total=Coalesce(
                    Sum("amount"), Value(Decimal("0.00"), output_field=DecimalField())
                )
            )
            .order_by("month", "direction")
        )

        return [
            {
                "month": item["month"].date().isoformat() if item["month"] else None,
                "direction": item["direction"],
                "total": str(item["total"]),
            }
            for item in data
        ]
