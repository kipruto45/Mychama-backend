from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import IntegrityError, transaction
from django.db.models import Count, DecimalField, F, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth
from django.shortcuts import get_object_or_404
from django.utils import timezone

logger = logging.getLogger(__name__)

from apps.accounts.models import MemberKYC, MemberKYCStatus, User
from apps.chama.models import (
    Chama,
    Membership,
    MembershipRole,
    MemberStatus,
)
from apps.chama.models import (
    LoanPolicy as ChamaLoanPolicy,
)
from apps.finance.models import (
    Account,
    AccountType,
    Contribution,
    ContributionGoal,
    ContributionGoalStatus,
    ContributionSchedule,
    ContributionScheduleStatus,
    ContributionType,
    Expense,
    ExpenseCategory,
    ExpenseStatus,
    FinancialSnapshot,
    InstallmentSchedule,
    InstallmentStatus,
    JournalEntry,
    JournalEntrySource,
    LedgerDirection,
    LedgerEntry,
    LedgerEntryType,
    LedgerStatus,
    Loan,
    LoanApplication,
    LoanApplicationApproval,
    LoanApplicationGuarantor,
    LoanApplicationStatus,
    LoanApprovalDecision,
    LoanApprovalLog,
    LoanApprovalStage,
    LoanAuditLog,
    LoanEligibilityCheck,
    LoanEligibilityStatus,
    LoanGuarantor,
    LoanGuarantorStatus,
    LoanInterestType,
    LoanProduct,
    LoanRecoveryAction,
    LoanRecoveryActionType,
    LoanRestructure,
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
from apps.finance.calculators import LoanCalculator
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
    metrics: dict


class FinanceService:
    SYSTEM_ACCOUNT_DEFINITIONS = {
        "cash": ("CASH", "Cash Account", AccountType.ASSET),
        "cash_on_hand": ("CASH", "Cash Account", AccountType.ASSET),
        "mpesa_clearing": ("MPESA_CLEARING", "M-Pesa Clearing", AccountType.ASSET),
        "card_clearing": ("CARD_CLEARING", "Card Clearing", AccountType.ASSET),
        "bank_account": ("BANK_MAIN", "Bank Account", AccountType.ASSET),
        "wallet_clearing": ("WALLET", "Member Wallet Clearing", AccountType.ASSET),
        "member_contributions": (
            "CONTRIB_INCOME",
            "Member Contributions",
            AccountType.INCOME,
        ),
        "contributions_account": (
            "CONTRIB_INCOME",
            "Member Contributions",
            AccountType.INCOME,
        ),
        "loan_receivable": ("LOAN_RECEIVABLE", "Loan Receivable", AccountType.ASSET),
        "loan_interest_income": ("LOAN_INTEREST", "Loan Interest Income", AccountType.INCOME),
        "penalty_receivable": ("PENALTY_RECEIVABLE", "Penalty Receivable", AccountType.ASSET),
        "penalty_income": ("PENALTY_INCOME", "Penalty Income", AccountType.INCOME),
        "fine_income": ("PENALTY_INCOME", "Penalty Income", AccountType.INCOME),
        "meeting_fee_income": ("MEETING_FEES", "Meeting Fee Income", AccountType.INCOME),
        "special_contributions": (
            "SPECIAL_CONTRIB",
            "Special Contributions",
            AccountType.INCOME,
        ),
        "payment_processing_fees": (
            "PAYMENT_FEES",
            "Payment Processing Fees",
            AccountType.EXPENSE,
        ),
        "expense_control": ("EXPENSE_CTRL", "Expense Control", AccountType.EXPENSE),
        "adjustments": ("ADJUSTMENTS", "Accounting Adjustments", AccountType.EQUITY),
    }

    @staticmethod
    def _payment_method_account_key(method: str | None) -> str:
        normalized = str(method or "").lower()
        return {
            MethodChoices.MPESA: "mpesa_clearing",
            MethodChoices.CARD: "card_clearing",
            MethodChoices.CASH: "cash_on_hand",
            MethodChoices.BANK_TRANSFER: "bank_account",
            MethodChoices.WALLET: "wallet_clearing",
        }.get(normalized, "cash")

    @staticmethod
    def _ensure_month_open(chama: Chama, value_date: date):
        month = _first_day_of_month(value_date)
        if MonthClosure.objects.filter(chama=chama, month=month).exists():
            raise MonthClosedError(f"Month {month:%Y-%m} is closed for this chama.")

    @staticmethod
    def _get_or_create_account(chama: Chama, key: str) -> Account:
        code, name, account_type = FinanceService.SYSTEM_ACCOUNT_DEFINITIONS[key]
        account, _ = Account.objects.get_or_create(
            chama=chama,
            code=code,
            defaults={
                "name": name,
                "type": account_type,
                "system_managed": True,
            },
        )
        return account

    @staticmethod
    def _create_balanced_journal(
        *,
        chama: Chama,
        actor: User,
        reference: str,
        description: str,
        source_type: str,
        source_id,
        idempotency_key: str,
        entry_type: str,
        debit_account: Account,
        credit_account: Account,
        amount: Decimal,
        metadata: dict | None = None,
    ) -> tuple[JournalEntry, LedgerEntry, LedgerEntry]:
        amount = to_decimal(amount)
        if amount <= Decimal("0.00"):
            raise FinanceServiceError("Journal amount must be positive.")

        try:
            journal = JournalEntry.objects.create(
                chama=chama,
                reference=reference,
                description=description,
                source_type=source_type,
                source_id=source_id,
                posted_at=timezone.now(),
                idempotency_key=idempotency_key,
                metadata=metadata or {},
                created_by=actor,
                updated_by=actor,
            )
        except IntegrityError as exc:
            if "uniq_journal_idempotency_per_chama" in str(exc) or "idempotency" in str(exc):
                raise IdempotencyConflictError(
                    "Duplicate idempotency_key for journal entry."
                ) from exc
            raise

        debit_line = LedgerEntry.objects.create(
            journal_entry=journal,
            account=debit_account,
            chama=chama,
            entry_type=entry_type,
            direction=LedgerDirection.DEBIT,
            amount=amount,
            debit=amount,
            credit=Decimal("0.00"),
            currency=CurrencyChoices.KES,
            status=LedgerStatus.SUCCESS,
            provider="internal",
            idempotency_key=f"{idempotency_key}:dr:{debit_account.code.lower()}",
            narration=description,
            meta={"reference_type": source_type, "reference_id": str(source_id or ""), **(metadata or {})},
            created_by=actor,
            updated_by=actor,
        )
        credit_line = LedgerEntry.objects.create(
            journal_entry=journal,
            account=credit_account,
            chama=chama,
            entry_type=entry_type,
            direction=LedgerDirection.CREDIT,
            amount=amount,
            debit=Decimal("0.00"),
            credit=amount,
            currency=CurrencyChoices.KES,
            status=LedgerStatus.SUCCESS,
            provider="internal",
            idempotency_key=f"{idempotency_key}:cr:{credit_account.code.lower()}",
            narration=description,
            meta={"reference_type": source_type, "reference_id": str(source_id or ""), **(metadata or {})},
            created_by=actor,
            updated_by=actor,
        )

        create_audit_log(
            actor=actor,
            chama_id=chama.id,
            action="finance_journal_posted",
            entity_type="JournalEntry",
            entity_id=journal.id,
            metadata={
                "reference": reference,
                "source_type": source_type,
                "source_id": str(source_id or ""),
                "amount": str(amount),
                "debit_account": debit_account.code,
                "credit_account": credit_account.code,
                "idempotency_key": idempotency_key,
            },
        )
        return journal, debit_line, credit_line

    @staticmethod
    def _ledger_balance(chama: Chama) -> Decimal:
        credits = LedgerEntry.objects.filter(
            chama=chama,
            direction=LedgerDirection.CREDIT,
        ).aggregate(
            total=Coalesce(
                Sum("amount"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        debits = LedgerEntry.objects.filter(
            chama=chama,
            direction=LedgerDirection.DEBIT,
        ).aggregate(
            total=Coalesce(
                Sum("amount"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        return (credits or Decimal("0.00")) - (debits or Decimal("0.00"))

    @staticmethod
    def _refresh_financial_snapshot(chama: Chama, snapshot_date: date | None = None) -> FinancialSnapshot:
        snapshot_date = snapshot_date or timezone.localdate()
        total_contributions = FinanceService._net_contribution_sum(
            Contribution.objects.filter(chama=chama)
        )
        total_loans = Loan.objects.filter(
            chama=chama,
            status__in=[LoanStatus.DISBURSED, LoanStatus.ACTIVE, LoanStatus.PAID, LoanStatus.CLOSED],
        ).aggregate(
            total=Coalesce(Sum("principal"), Value(Decimal("0.00"), output_field=DecimalField()))
        )["total"]
        total_expenses = Expense.objects.filter(
            chama=chama,
            status=ExpenseStatus.PAID,
        ).aggregate(
            total=Coalesce(Sum("amount"), Value(Decimal("0.00"), output_field=DecimalField()))
        )["total"]
        total_balance = FinanceService._ledger_balance(chama)
        snapshot, _ = FinancialSnapshot.objects.update_or_create(
            chama=chama,
            snapshot_date=snapshot_date,
            defaults={
                "total_balance": to_decimal(total_balance),
                "total_contributions": to_decimal(total_contributions),
                "total_loans": to_decimal(total_loans),
                "total_expenses": to_decimal(total_expenses),
                "metadata": {"refreshed_at": timezone.now().isoformat()},
            },
        )
        return snapshot

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
    def _get_loan_policy(chama: Chama) -> ChamaLoanPolicy:
        policy = getattr(chama, "loan_policy", None)
        if policy:
            return policy
        policy, _ = ChamaLoanPolicy.objects.get_or_create(
            chama=chama,
            defaults={
                "created_by": None,
                "updated_by": None,
            },
        )
        return policy

    @staticmethod
    def _member_savings_total(*, chama: Chama, member: User) -> Decimal:
        total = FinanceService._net_contribution_sum(
            Contribution.objects.filter(chama=chama, member=member)
        )
        return to_decimal(total)

    @staticmethod
    def _net_contribution_sum(queryset) -> Decimal:
        return to_decimal(
            queryset.aggregate(
                total=Coalesce(
                    Sum(F("amount") - F("refunded_amount")),
                    Value(Decimal("0.00"), output_field=DecimalField()),
                )
            )["total"]
            or Decimal("0.00")
        )

    @staticmethod
    def _contribution_compliance_metrics(*, chama: Chama, member: User) -> tuple[Decimal, int, int]:
        due_qs = ContributionSchedule.objects.filter(
            chama=chama,
            member=member,
            is_active=True,
            scheduled_date__lte=timezone.localdate(),
        )
        completed = due_qs.filter(status=ContributionScheduleStatus.PAID).count()
        required = due_qs.exclude(status=ContributionScheduleStatus.SKIPPED).count()
        if required <= 0:
            return Decimal("100.00"), completed, required
        compliance = (Decimal(completed) / Decimal(required)) * Decimal("100")
        return to_decimal(compliance), completed, required

    @staticmethod
    def _has_overdue_contributions(*, chama: Chama, member: User) -> bool:
        today = timezone.localdate()
        return ContributionSchedule.objects.filter(
            chama=chama,
            member=member,
            is_active=True,
            scheduled_date__lt=today,
            status__in=[
                ContributionScheduleStatus.PENDING,
                ContributionScheduleStatus.MISSED,
            ],
        ).exists()

    @staticmethod
    def _chama_available_liquidity(chama: Chama) -> Decimal:
        cash_account = FinanceService._get_or_create_account(chama, "cash")
        cash_totals = LedgerEntry.objects.filter(
            chama=chama,
            account=cash_account,
            status=LedgerStatus.SUCCESS,
        ).aggregate(
            debit_total=Coalesce(
                Sum("debit"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            ),
            credit_total=Coalesce(
                Sum("credit"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            ),
        )
        return to_decimal(
            to_decimal(cash_totals["debit_total"]) - to_decimal(cash_totals["credit_total"])
        )

    @staticmethod
    def _guarantor_exposure(guarantor: User, chama: Chama) -> Decimal:
        active_statuses = [
            LoanStatus.REQUESTED,
            LoanStatus.REVIEW,
            LoanStatus.APPROVED,
            LoanStatus.DISBURSING,
            LoanStatus.DISBURSED,
            LoanStatus.ACTIVE,
            LoanStatus.DUE_SOON,
            LoanStatus.OVERDUE,
            LoanStatus.RESTRUCTURED,
            LoanStatus.DEFAULTED,
            LoanStatus.DEFAULTED_RECOVERING,
        ]
        exposure = LoanGuarantor.objects.filter(
            guarantor=guarantor,
            loan__chama=chama,
            loan__status__in=active_statuses,
            status=LoanGuarantorStatus.ACCEPTED,
        ).aggregate(
            total=Coalesce(
                Sum("guaranteed_amount"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        return to_decimal(exposure)

    @staticmethod
    def _guarantor_capacity(*, policy: ChamaLoanPolicy, chama: Chama, guarantor: User) -> Decimal:
        savings = FinanceService._member_savings_total(chama=chama, member=guarantor)
        gross_capacity = to_decimal(savings * to_decimal(policy.guarantor_capacity_multiplier))
        return to_decimal(max(gross_capacity - FinanceService._guarantor_exposure(guarantor, chama), Decimal("0.00")))

    @staticmethod
    def _record_loan_recovery_action(
        *,
        loan: Loan,
        action_type: str,
        actor: User | None,
        amount: Decimal = Decimal("0.00"),
        notes: str = "",
        metadata: dict | None = None,
    ) -> LoanRecoveryAction:
        payload = metadata or {}
        guarantor = None
        guarantor_id = payload.get("guarantor_id")
        if guarantor_id:
            guarantor = LoanGuarantor.objects.filter(
                loan=loan,
                id=guarantor_id,
            ).first()
        action = LoanRecoveryAction.objects.create(
            loan=loan,
            action_type=action_type,
            amount=to_decimal(amount),
            notes=notes,
            metadata=payload,
            performed_by=actor,
            guarantor=guarantor,
            offset_from_savings=bool(payload.get("offset_from_savings", False)),
            offset_from_contributions=bool(
                payload.get("offset_from_contributions", False)
            ),
            created_by=actor,
            updated_by=actor,
        )
        create_audit_log(
            actor=actor,
            chama_id=loan.chama_id,
            action=f"loan_recovery_{action_type}",
            entity_type="LoanRecoveryAction",
            entity_id=action.id,
            metadata={
                "loan_id": str(loan.id),
                "amount": str(action.amount),
                "notes": notes,
                **payload,
            },
        )
        return action

    @staticmethod
    def _set_loan_final_status(
        loan: Loan,
        *,
        final_status: str,
        actor: User | None = None,
    ) -> Loan:
        if loan.final_status == final_status:
            return loan
        loan.final_status = final_status
        loan.final_status_date = timezone.now()
        loan.final_status_by = actor
        loan.updated_by = actor
        loan.save(
            update_fields=[
                "final_status",
                "final_status_date",
                "final_status_by",
                "updated_by",
                "updated_at",
            ]
        )
        return loan

    @staticmethod
    def _sync_member_loan_restrictions(
        loan: Loan,
        *,
        actor: User | None = None,
    ) -> Membership | None:
        membership = Membership.objects.filter(
            chama=loan.chama,
            user=loan.member,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        ).first()
        if not membership:
            return None

        policy = FinanceService._get_loan_policy(loan.chama)
        managed_prefix = "loan_policy:"
        delinquent_statuses = [
            LoanStatus.OVERDUE,
            LoanStatus.DEFAULTED,
            LoanStatus.DEFAULTED_RECOVERING,
            LoanStatus.WRITTEN_OFF,
        ]
        has_delinquent_loan = Loan.objects.filter(
            chama=loan.chama,
            member=loan.member,
            status__in=delinquent_statuses,
        ).exists()
        has_defaulted_loan = Loan.objects.filter(
            chama=loan.chama,
            member=loan.member,
            status__in=[
                LoanStatus.DEFAULTED,
                LoanStatus.DEFAULTED_RECOVERING,
                LoanStatus.WRITTEN_OFF,
            ],
        ).exists()

        reason_codes: list[str] = []
        membership.loan_default_risk = has_delinquent_loan
        membership.can_request_loan = not (
            has_delinquent_loan and policy.restrict_new_loans_on_overdue
        )
        if not membership.can_request_loan:
            reason_codes.append("loan_request_blocked")

        if has_defaulted_loan and policy.restrict_member_privileges_on_default:
            if policy.restrict_withdrawals_on_default:
                membership.can_withdraw_savings = False
                reason_codes.append("withdrawals_blocked")
            if policy.restrict_voting_on_default:
                membership.can_vote = False
                reason_codes.append("voting_blocked")
            if policy.restrict_invites_on_default:
                membership.can_invite_members = False
                reason_codes.append("invites_blocked")
        elif str(membership.restriction_reason or "").startswith(managed_prefix):
            membership.can_withdraw_savings = True
            membership.can_vote = True
            membership.can_invite_members = True

        if reason_codes:
            membership.restriction_reason = managed_prefix + ",".join(reason_codes)
            membership.restrictions_applied_at = timezone.now()
            membership.restrictions_applied_by = actor
        elif str(membership.restriction_reason or "").startswith(managed_prefix):
            membership.restriction_reason = ""
            membership.restrictions_applied_at = None
            membership.restrictions_applied_by = None

        membership.updated_by = actor
        membership.save(
            update_fields=[
                "loan_default_risk",
                "can_request_loan",
                "can_withdraw_savings",
                "can_vote",
                "can_invite_members",
                "restriction_reason",
                "restrictions_applied_at",
                "restrictions_applied_by",
                "updated_by",
                "updated_at",
            ]
        )
        return membership

    @staticmethod
    def _sync_guarantor_state(
        loan: Loan,
        *,
        notify_guarantors: bool = False,
        trigger_recovery: bool = False,
    ) -> list[LoanGuarantor]:
        guarantors = list(
            LoanGuarantor.objects.select_related("guarantor")
            .filter(loan=loan, status=LoanGuarantorStatus.ACCEPTED)
            .order_by("created_at", "id")
        )
        if not guarantors:
            return []

        total_guaranteed = sum(
            (to_decimal(record.guaranteed_amount) for record in guarantors),
            Decimal("0.00"),
        )
        outstanding = to_decimal(loan.total_due)
        remaining_exposure = outstanding
        for index, record in enumerate(guarantors):
            if total_guaranteed > Decimal("0.00"):
                if index == len(guarantors) - 1:
                    exposure = remaining_exposure
                else:
                    ratio = to_decimal(record.guaranteed_amount) / total_guaranteed
                    exposure = to_decimal(outstanding * ratio)
                    remaining_exposure = to_decimal(max(remaining_exposure - exposure, Decimal("0.00")))
            else:
                exposure = Decimal("0.00")
            exposure = to_decimal(min(exposure, to_decimal(record.guaranteed_amount)))

            update_fields = ["exposure_amount", "updated_at"]
            record.exposure_amount = exposure
            if trigger_recovery or loan.status in {
                LoanStatus.OVERDUE,
                LoanStatus.DEFAULTED,
                LoanStatus.DEFAULTED_RECOVERING,
            }:
                record.status = LoanGuarantorStatus.AT_RISK
                update_fields.append("status")
            if notify_guarantors:
                record.notified_at = timezone.now()
                update_fields.append("notified_at")
            if trigger_recovery:
                record.recovery_triggered = True
                record.recovery_triggered_at = timezone.now()
                update_fields.extend(["recovery_triggered", "recovery_triggered_at"])
            record.save(update_fields=update_fields)
        return guarantors

    @staticmethod
    def _notify_loan_admins(
        loan: Loan,
        *,
        subject: str,
        message: str,
        idempotency_suffix: str,
    ) -> int:
        from apps.notifications.models import NotificationType
        from apps.notifications.services import NotificationService

        admins = Membership.objects.select_related("user").filter(
            chama=loan.chama,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            role__in=[
                MembershipRole.CHAMA_ADMIN,
                MembershipRole.TREASURER,
                MembershipRole.SECRETARY,
            ],
        )
        sent = 0
        for admin in admins:
            NotificationService.send_notification(
                user=admin.user,
                chama=loan.chama,
                channels=["in_app", "email"],
                message=message,
                subject=subject,
                notification_type=NotificationType.LOAN_UPDATE,
                idempotency_key=f"loan-admin:{loan.id}:{admin.user_id}:{idempotency_suffix}",
            )
            sent += 1
        return sent

    @staticmethod
    def _notify_guarantors(
        loan: Loan,
        *,
        guarantors: list[LoanGuarantor],
        defaulted: bool = False,
    ) -> int:
        from apps.notifications.models import NotificationType
        from apps.notifications.services import NotificationService

        sent = 0
        for record in guarantors:
            message = (
                f"Loan {loan.id} for {loan.member.full_name} has defaulted. "
                f"Current guaranteed exposure is KES {record.exposure_amount:,.2f}."
                if defaulted
                else f"Loan {loan.id} for {loan.member.full_name} is overdue. "
                f"Current guaranteed exposure is KES {record.exposure_amount:,.2f}."
            )
            NotificationService.send_notification(
                user=record.guarantor,
                chama=loan.chama,
                channels=["in_app", "email", "sms"],
                message=message,
                subject="Guarantor loan alert",
                notification_type=NotificationType.LOAN_UPDATE,
                idempotency_key=(
                    f"loan-guarantor:{loan.id}:{record.guarantor_id}:{loan.status}"
                ),
            )
            sent += 1
        return sent

    @staticmethod
    def _recalculate_loan_balances(loan: Loan, *, actor: User | None = None) -> Loan:
        installments = InstallmentSchedule.objects.filter(loan=loan)
        principal_due = installments.aggregate(
            total=Coalesce(
                Sum("expected_principal"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        interest_due = installments.aggregate(
            total=Coalesce(
                Sum("expected_interest"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        penalty_due = installments.aggregate(
            total=Coalesce(
                Sum("expected_penalty"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        principal_paid = installments.aggregate(
            total=Coalesce(
                Sum("paid_principal"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        interest_paid = installments.aggregate(
            total=Coalesce(
                Sum("paid_interest"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        penalty_paid = installments.aggregate(
            total=Coalesce(
                Sum("paid_penalty"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]

        loan.outstanding_principal = to_decimal(max(to_decimal(principal_due) - to_decimal(principal_paid), Decimal("0.00")))
        loan.outstanding_interest = to_decimal(max(to_decimal(interest_due) - to_decimal(interest_paid), Decimal("0.00")))
        loan.outstanding_penalty = to_decimal(max(to_decimal(penalty_due) - to_decimal(penalty_paid), Decimal("0.00")))
        loan.total_due = to_decimal(
            loan.outstanding_principal + loan.outstanding_interest + loan.outstanding_penalty
        )
        if actor:
            loan.updated_by = actor
        loan.save(
            update_fields=[
                "outstanding_principal",
                "outstanding_interest",
                "outstanding_penalty",
                "total_due",
                "updated_by",
                "updated_at",
            ]
        )
        return loan

    @staticmethod
    def _calculate_membership_months(membership: Membership | None) -> int:
        if not membership or not membership.joined_at:
            return 0
        membership_days = max((timezone.now() - membership.joined_at).days, 0)
        return membership_days // 30

    @staticmethod
    def _estimate_periodic_payment(
        *,
        principal: Decimal,
        duration_months: int,
        interest_rate: Decimal,
        interest_type: str,
    ) -> Decimal:
        principal_amount = to_decimal(principal)
        rate = to_decimal(interest_rate, precision="0.01")
        if principal_amount <= Decimal("0.00") or duration_months <= 0:
            return Decimal("0.00")
        if str(interest_type or LoanInterestType.FLAT).lower() == LoanInterestType.REDUCING:
            return to_decimal(
                LoanCalculator.calculate_monthly_payment(
                    principal_amount,
                    rate,
                    duration_months,
                )
            )
        total_interest = principal_amount * (rate / Decimal("100")) * (
            Decimal(duration_months) / Decimal("12")
        )
        total_repayment = principal_amount + total_interest
        return to_decimal(total_repayment / Decimal(duration_months))

    @staticmethod
    def _estimate_total_repayment(
        *,
        principal: Decimal,
        duration_months: int,
        interest_rate: Decimal,
        interest_type: str,
    ) -> Decimal:
        installment = FinanceService._estimate_periodic_payment(
            principal=principal,
            duration_months=duration_months,
            interest_rate=interest_rate,
            interest_type=interest_type,
        )
        return to_decimal(installment * Decimal(max(duration_months, 0)))

    @staticmethod
    def _calculate_repayment_history_score(
        *,
        chama: Chama,
        member: User,
    ) -> dict:
        loan_qs = Loan.objects.filter(chama=chama, member=member)
        defaulted_count = loan_qs.filter(
            status__in=[
                LoanStatus.DEFAULTED,
                LoanStatus.DEFAULTED_RECOVERING,
                LoanStatus.WRITTEN_OFF,
            ]
        ).count()
        overdue_count = loan_qs.filter(status=LoanStatus.OVERDUE).count()
        unpaid_loan_count = loan_qs.filter(
            status__in=[
                LoanStatus.APPROVED,
                LoanStatus.DISBURSING,
                LoanStatus.DISBURSED,
                LoanStatus.ACTIVE,
                LoanStatus.DUE_SOON,
                LoanStatus.OVERDUE,
                LoanStatus.RESTRUCTURED,
                LoanStatus.DEFAULTED_RECOVERING,
            ]
        ).count()
        late_installment_count = InstallmentSchedule.objects.filter(
            loan__chama=chama,
            loan__member=member,
            paid_at__isnull=False,
            paid_at__date__gt=F("due_date"),
        ).count()
        overdue_installment_count = InstallmentSchedule.objects.filter(
            loan__chama=chama,
            loan__member=member,
            status=InstallmentStatus.OVERDUE,
        ).count()
        completed_loans = loan_qs.filter(
            status__in=[
                LoanStatus.PAID,
                LoanStatus.CLEARED,
                LoanStatus.CLOSED,
                LoanStatus.RECOVERED_FROM_OFFSET,
                LoanStatus.RECOVERED_FROM_GUARANTOR,
            ]
        ).count()

        score = Decimal("100.00")
        score -= Decimal(defaulted_count * 35)
        score -= Decimal(overdue_count * 20)
        score -= Decimal(overdue_installment_count * 10)
        score -= Decimal(late_installment_count * 6)
        if completed_loans > 0:
            score += Decimal(min(completed_loans * 2, 10))
        score = max(Decimal("0.00"), min(score, Decimal("100.00")))

        return {
            "score": to_decimal(score),
            "defaulted_loans_count": defaulted_count,
            "overdue_loans_count": overdue_count,
            "late_installments_count": late_installment_count,
            "overdue_installments_count": overdue_installment_count,
            "completed_loans_count": completed_loans,
            "unpaid_loans_count": unpaid_loan_count,
        }

    @staticmethod
    def _calculate_contribution_consistency_score(
        *,
        chama: Chama,
        member: User,
    ) -> dict:
        compliance_percent, completed_cycles, required_cycles = (
            FinanceService._contribution_compliance_metrics(
                chama=chama,
                member=member,
            )
        )
        recent_window_start = timezone.localdate() - timedelta(days=120)
        recent_contribution_months = (
            Contribution.objects.filter(
                chama=chama,
                member=member,
                refunded_amount__lt=F("amount"),
                date_paid__gte=recent_window_start,
            )
            .annotate(month=TruncMonth("date_paid"))
            .values("month")
            .distinct()
            .count()
        )
        recent_score = min((Decimal(recent_contribution_months) / Decimal("4")) * Decimal("100"), Decimal("100.00"))
        score = to_decimal((to_decimal(compliance_percent) * Decimal("0.7")) + (recent_score * Decimal("0.3")))
        return {
            "score": to_decimal(max(Decimal("0.00"), min(score, Decimal("100.00")))),
            "compliance_percent": to_decimal(compliance_percent),
            "completed_cycles": completed_cycles,
            "required_cycles": required_cycles,
            "recent_contribution_months": recent_contribution_months,
        }

    @staticmethod
    def _record_loan_audit_log(
        *,
        chama: Chama,
        member: User,
        action: str,
        actor: User | None,
        loan_application: LoanApplication | None = None,
        loan: Loan | None = None,
        status_from: str = "",
        status_to: str = "",
        notes: str = "",
        metadata: dict | None = None,
    ) -> LoanAuditLog:
        return LoanAuditLog.objects.create(
            chama=chama,
            member=member,
            loan_application=loan_application,
            loan=loan,
            actor=actor,
            action=action,
            status_from=status_from,
            status_to=status_to,
            notes=notes,
            metadata=metadata or {},
            created_by=actor,
            updated_by=actor,
        )

    @staticmethod
    def evaluate_loan_eligibility(
        *,
        chama: Chama,
        member: User,
        principal: Decimal,
        duration_months: int,
        loan_product: LoanProduct,
    ) -> LoanEligibilityResult:
        principal_amount = to_decimal(principal)
        policy = FinanceService._get_loan_policy(chama)
        reasons: list[str] = []
        next_steps: list[str] = []
        risk_notes: list[str] = []
        policy_checks: list[dict] = []
        metrics: dict = {}

        def register_check(
            *,
            key: str,
            label: str,
            passed: bool,
            actual,
            required,
            message: str,
            next_step: str | None = None,
            severity: str = "blocking",
        ) -> None:
            policy_checks.append(
                {
                    "key": key,
                    "label": label,
                    "passed": passed,
                    "severity": severity,
                    "actual": actual,
                    "required": required,
                    "message": message,
                }
            )
            if not passed and message not in reasons:
                reasons.append(message)
            if not passed and next_step and next_step not in next_steps:
                next_steps.append(next_step)

        membership = Membership.objects.filter(
            chama=chama,
            user=member,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        ).first()
        membership_days = max((timezone.now() - membership.joined_at).days, 0) if membership and membership.joined_at else 0
        membership_months = FinanceService._calculate_membership_months(membership)
        metrics["membership_days"] = membership_days
        metrics["membership_months"] = membership_months
        metrics["is_member_active"] = bool(membership and membership.is_active)
        metrics["account_active"] = bool(member.is_active)

        register_check(
            key="active_account",
            label="Active account",
            passed=bool(member.is_active),
            actual=bool(member.is_active),
            required=True,
            message="Your account is not active right now, so you cannot apply for a loan.",
        )
        register_check(
            key="active_membership",
            label="Active chama membership",
            passed=bool(membership and membership.is_active),
            actual=bool(membership and membership.is_active),
            required=True,
            message="You must be an active member of this chama before applying for a loan.",
        )
        register_check(
            key="membership_duration",
            label="Minimum membership period",
            passed=membership_days >= int(getattr(policy, "min_membership_days", 0) or 0),
            actual=membership_days,
            required=int(getattr(policy, "min_membership_days", 0) or 0),
            message=f"You need at least {int(getattr(policy, 'min_membership_days', 0) or 0) // 30 or 1} months of active membership before applying.",
            next_step="Remain an active member until you meet the minimum membership period.",
        )
        register_check(
            key="loan_permission",
            label="Loan access",
            passed=bool(membership and membership.can_request_loan),
            actual=bool(membership.can_request_loan) if membership else False,
            required=True,
            message="Member is currently restricted from requesting loans.",
            next_step="Clear any current loan restrictions with your chama administrators.",
        )

        contributions_qs = Contribution.objects.filter(chama=chama, member=member)
        contributions_total = FinanceService._net_contribution_sum(contributions_qs)
        contributions_count = contributions_qs.exclude(refunded_amount__gte=F("amount")).count()
        metrics["contributions_total"] = str(contributions_total)
        metrics["successful_contributions"] = contributions_count

        consistency = FinanceService._calculate_contribution_consistency_score(
            chama=chama,
            member=member,
        )
        compliance_percent = consistency["compliance_percent"]
        metrics["contribution_compliance_percent"] = str(compliance_percent)
        metrics["contribution_cycles_completed"] = consistency["completed_cycles"]
        metrics["contribution_cycles_required"] = consistency["required_cycles"]
        metrics["contribution_consistency_score"] = str(consistency["score"])
        metrics["recent_contribution_months"] = consistency["recent_contribution_months"]

        register_check(
            key="minimum_contributions",
            label="Successful contributions",
            passed=contributions_count >= int(policy.min_contribution_cycles or 0),
            actual=contributions_count,
            required=int(policy.min_contribution_cycles or 0),
            message=f"You need at least {int(policy.min_contribution_cycles or 0)} successful contributions to qualify.",
            next_step="Continue making successful contributions until you reach the minimum required count.",
        )
        register_check(
            key="minimum_savings",
            label="Minimum savings threshold",
            passed=contributions_total >= to_decimal(policy.min_savings_threshold),
            actual=str(contributions_total),
            required=str(to_decimal(policy.min_savings_threshold)),
            message="Your savings are below the minimum required amount for borrowing.",
            next_step=f"Increase your savings to at least KES {to_decimal(policy.min_savings_threshold):,.2f}.",
        )
        register_check(
            key="contribution_consistency",
            label="Contribution consistency",
            passed=(
                to_decimal(getattr(policy, "min_contribution_compliance_percent", Decimal("0.00")))
                <= Decimal("0.00")
                or compliance_percent >= to_decimal(policy.min_contribution_compliance_percent)
            ),
            actual=str(compliance_percent),
            required=str(to_decimal(getattr(policy, "min_contribution_compliance_percent", Decimal("0.00")))),
            message="Your recent contribution pattern is not yet consistent enough for borrowing.",
            next_step="Maintain consistent contributions in the upcoming cycles to improve your eligibility score.",
        )

        if loan_product.minimum_contribution_months > 0:
            since_date = timezone.localdate() - timedelta(days=31 * loan_product.minimum_contribution_months)
            contributed_months = (
                Contribution.objects.filter(
                    chama=chama,
                    member=member,
                    refunded_amount__lt=F("amount"),
                    date_paid__gte=since_date,
                )
                .annotate(month=TruncMonth("date_paid"))
                .values("month")
                .distinct()
                .count()
            )
            register_check(
                key="recent_contribution_months",
                label="Recent contribution months",
                passed=contributed_months >= loan_product.minimum_contribution_months,
                actual=contributed_months,
                required=int(loan_product.minimum_contribution_months),
                message="You need contributions across more recent cycles before this loan can be considered.",
                next_step="Keep contributing in the next cycles to build a recent contribution track record.",
            )

        overdue_contributions = (
            policy.require_no_overdue_contributions
            and FinanceService._has_overdue_contributions(chama=chama, member=member)
        )
        register_check(
            key="overdue_contributions",
            label="Overdue contributions",
            passed=not overdue_contributions,
            actual=bool(overdue_contributions),
            required=False,
            message="You have overdue contributions that must be cleared before you can borrow.",
            next_step="Clear any overdue contributions first.",
        )

        repayment_history = FinanceService._calculate_repayment_history_score(
            chama=chama,
            member=member,
        )
        repayment_score = to_decimal(repayment_history["score"])
        metrics["repayment_history_score"] = str(repayment_score)
        metrics.update({key: value for key, value in repayment_history.items() if key != "score"})

        unresolved_penalties = Penalty.objects.filter(
            chama=chama,
            member=member,
            status__in=[PenaltyStatus.UNPAID, PenaltyStatus.PARTIAL],
        ).count()
        metrics["unpaid_penalties_count"] = unresolved_penalties
        register_check(
            key="unpaid_penalties",
            label="Unpaid penalties",
            passed=not ((policy.block_unpaid_penalties or loan_product.block_if_unpaid_penalties) and unresolved_penalties > 0),
            actual=unresolved_penalties,
            required=0,
            message="You need to clear unpaid penalties before applying for a new loan.",
            next_step="Clear unpaid penalties and try again.",
        )

        active_loan_qs = Loan.objects.filter(
            chama=chama,
            member=member,
            status__in=[
                LoanStatus.APPROVED,
                LoanStatus.DISBURSING,
                LoanStatus.DISBURSED,
                LoanStatus.ACTIVE,
                LoanStatus.DUE_SOON,
                LoanStatus.OVERDUE,
                LoanStatus.RESTRUCTURED,
                LoanStatus.DEFAULTED_RECOVERING,
            ],
        )
        active_loans_count = active_loan_qs.count()
        outstanding_active_total = active_loan_qs.aggregate(
            total=Coalesce(
                Sum("total_due"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        metrics["active_loans_count"] = active_loans_count
        metrics["outstanding_active_loans_total"] = str(to_decimal(outstanding_active_total))

        overdue_or_defaulted_loans = Loan.objects.filter(
            chama=chama,
            member=member,
            status__in=[
                LoanStatus.OVERDUE,
                LoanStatus.DEFAULTED,
                LoanStatus.DEFAULTED_RECOVERING,
                LoanStatus.WRITTEN_OFF,
            ],
        )
        overdue_loan_count = overdue_or_defaulted_loans.count()
        metrics["overdue_or_defaulted_loans_count"] = overdue_loan_count

        max_active_loans = max(int(policy.max_active_loans or 1), 1)
        register_check(
            key="active_loans_limit",
            label="Active loan limit",
            passed=active_loans_count < max_active_loans,
            actual=active_loans_count,
            required=max_active_loans,
            message="You already have an active loan and must repay it before taking another one.",
            next_step="Repay or complete your current active loan first.",
        )
        register_check(
            key="overdue_loan_block",
            label="Overdue or defaulted loans",
            passed=not (
                policy.restrict_new_loans_on_overdue
                and overdue_loan_count > 0
            ),
            actual=overdue_loan_count,
            required=0,
            message="Your current loan is overdue or unresolved, so you cannot apply for a new one.",
            next_step="Clear the overdue balance and any related penalties before applying again.",
        )
        register_check(
            key="default_history",
            label="Default history",
            passed=not (
                policy.block_defaulted_loans
                and repayment_history["defaulted_loans_count"] > 0
            ),
            actual=repayment_history["defaulted_loans_count"],
            required=0,
            message="Your repayment history does not currently meet the loan requirements.",
            next_step="Resolve any defaulted loan history with the chama before reapplying.",
        )

        pending_application_count = LoanApplication.objects.filter(
            chama=chama,
            member=member,
            status__in=[
                LoanApplicationStatus.SUBMITTED,
                LoanApplicationStatus.IN_REVIEW,
                LoanApplicationStatus.TREASURER_APPROVED,
                LoanApplicationStatus.COMMITTEE_APPROVED,
            ],
        ).count()
        metrics["pending_loan_applications_count"] = pending_application_count
        register_check(
            key="pending_application_block",
            label="Pending loan applications",
            passed=not (
                policy.block_pending_loan_applications
                and pending_application_count > 0
            ),
            actual=pending_application_count,
            required=0,
            message="You already have a loan application under review.",
            next_step="Wait for the current application decision before submitting another request.",
        )

        phone_required = bool(policy.require_phone_verification)
        email_required = bool(policy.require_email_verification)
        kyc_required = bool(policy.require_kyc)
        has_approved_kyc = MemberKYC.objects.filter(
            user=member,
            chama=chama,
            status=MemberKYCStatus.APPROVED,
        ).exists()
        metrics["kyc_approved"] = has_approved_kyc
        register_check(
            key="phone_verified",
            label="Phone verification",
            passed=(not phone_required) or bool(member.phone_verified),
            actual=bool(member.phone_verified),
            required=phone_required,
            message="Verify your phone number before applying for a loan.",
            next_step="Complete phone verification in your account settings.",
            severity="supporting",
        )
        register_check(
            key="email_verified",
            label="Email verification",
            passed=(not email_required) or bool(getattr(member, "email_verified", False)),
            actual=bool(getattr(member, "email_verified", False)),
            required=email_required,
            message="Verify your email address before applying for a loan.",
            next_step="Complete email verification in your account settings.",
            severity="supporting",
        )
        register_check(
            key="kyc_status",
            label="KYC status",
            passed=(not kyc_required) or has_approved_kyc,
            actual=has_approved_kyc,
            required=kyc_required,
            message="Complete your chama KYC review before applying for a loan.",
            next_step="Finish the required KYC steps and wait for approval.",
            severity="supporting",
        )

        if policy.minimum_credit_score:
            credit_score = FinanceService.compute_credit_score(chama.id, member.id)["score"]
            metrics["credit_score"] = credit_score
            register_check(
                key="credit_score",
                label="Minimum credit score",
                passed=int(credit_score) >= int(policy.minimum_credit_score),
                actual=credit_score,
                required=int(policy.minimum_credit_score),
                message="Your current credit score is below the loan threshold for this chama.",
                next_step="Improve your repayment discipline and contribution consistency to raise your score.",
                severity="supporting",
            )

        duration_valid = (
            loan_product.min_duration_months <= duration_months <= loan_product.max_duration_months
        )
        register_check(
            key="repayment_term",
            label="Repayment term",
            passed=duration_valid,
            actual=duration_months,
            required=f"{loan_product.min_duration_months}-{loan_product.max_duration_months} months",
            message=(
                f"Choose a repayment term between {loan_product.min_duration_months} and "
                f"{loan_product.max_duration_months} months."
            ),
            next_step="Select a repayment term within the allowed range.",
        )

        recommended_max = to_decimal(loan_product.max_loan_amount)
        savings_based_cap = recommended_max
        product_savings_cap = None
        if loan_product.contribution_multiple > Decimal("0.00"):
            product_savings_cap = to_decimal(contributions_total * loan_product.contribution_multiple)
            savings_based_cap = min(savings_based_cap, product_savings_cap)
        if policy.loan_cap_multiplier > Decimal("0.00"):
            policy_cap = to_decimal(contributions_total * to_decimal(policy.loan_cap_multiplier))
            savings_based_cap = min(savings_based_cap, policy_cap)
            metrics["policy_savings_multiple_cap"] = str(policy_cap)
        else:
            policy_cap = None
        if policy.max_member_loan_amount and to_decimal(policy.max_member_loan_amount) > Decimal("0.00"):
            savings_based_cap = min(savings_based_cap, to_decimal(policy.max_member_loan_amount))
            metrics["policy_member_cap"] = str(to_decimal(policy.max_member_loan_amount))

        available_liquidity = FinanceService._chama_available_liquidity(chama)
        effective_liquidity = to_decimal(
            available_liquidity - to_decimal(policy.reserve_liquidity_amount)
        )
        metrics["available_liquidity"] = str(available_liquidity)
        metrics["effective_lendable_liquidity"] = str(effective_liquidity)
        recommended_max = max(Decimal("0.00"), min(savings_based_cap, effective_liquidity))

        installment_estimate = FinanceService._estimate_periodic_payment(
            principal=principal_amount,
            duration_months=duration_months,
            interest_rate=to_decimal(loan_product.interest_rate),
            interest_type=loan_product.interest_type,
        )
        total_repayment_estimate = FinanceService._estimate_total_repayment(
            principal=principal_amount,
            duration_months=duration_months,
            interest_rate=to_decimal(loan_product.interest_rate),
            interest_type=loan_product.interest_type,
        )
        average_monthly_savings = to_decimal(
            contributions_total / Decimal(max(membership_months or 1, 1))
        ) if contributions_total > Decimal("0.00") else Decimal("0.00")
        repayment_capacity_ratio = (
            to_decimal(installment_estimate / average_monthly_savings)
            if average_monthly_savings > Decimal("0.00")
            else Decimal("999.00")
            if installment_estimate > Decimal("0.00")
            else Decimal("0.00")
        )
        metrics["installment_estimate"] = str(installment_estimate)
        metrics["total_repayment_estimate"] = str(total_repayment_estimate)
        metrics["average_monthly_savings"] = str(average_monthly_savings)
        metrics["repayment_capacity_ratio"] = str(repayment_capacity_ratio)

        register_check(
            key="minimum_loan_amount",
            label="Minimum loan amount",
            passed=principal_amount >= to_decimal(policy.minimum_loan_amount),
            actual=str(principal_amount),
            required=str(to_decimal(policy.minimum_loan_amount)),
            message="The requested amount is below the minimum loan amount for this chama.",
            next_step=f"Request at least KES {to_decimal(policy.minimum_loan_amount):,.2f}.",
        )
        register_check(
            key="maximum_loan_amount",
            label="Maximum eligible amount",
            passed=principal_amount <= recommended_max,
            actual=str(principal_amount),
            required=str(recommended_max),
            message="Your requested amount is above your current eligible limit.",
            next_step=f"Reduce the request to KES {recommended_max:,.2f} or less.",
        )
        register_check(
            key="liquidity_policy",
            label="Chama liquidity policy",
            passed=principal_amount <= effective_liquidity,
            actual=str(principal_amount),
            required=str(effective_liquidity),
            message="This request is above the chama’s current lendable liquidity.",
            next_step="Reduce the amount or wait until the chama has more available liquidity.",
        )
        register_check(
            key="repayment_capacity",
            label="Repayment capacity",
            passed=repayment_capacity_ratio <= to_decimal(policy.repayment_capacity_ratio_limit),
            actual=str(repayment_capacity_ratio),
            required=str(to_decimal(policy.repayment_capacity_ratio_limit)),
            message="The repayment plan is too heavy compared with your recent savings behaviour.",
            next_step="Choose a longer repayment term or lower the requested amount.",
        )

        if repayment_score < Decimal("70.00"):
            risk_notes.append("Repayment history is below the preferred borrowing threshold.")
        if to_decimal(consistency["score"]) < Decimal("70.00"):
            risk_notes.append("Contribution consistency is below the preferred borrowing threshold.")
        if overdue_loan_count > 0:
            risk_notes.append("There are unresolved overdue or defaulted loan obligations on your profile.")
        if unresolved_penalties > 0:
            risk_notes.append("Outstanding penalties increase the risk of new borrowing.")

        required_guarantors = FinanceService._required_guarantor_count(
            policy=policy,
            principal_amount=principal_amount,
        )
        approval_path: list[str] = []
        if policy.require_treasurer_approval:
            approval_path.append("treasurer_review")
        if (
            policy.require_committee_vote
            and policy.committee_threshold_amount
            and principal_amount >= to_decimal(policy.committee_threshold_amount)
        ):
            approval_path.append("committee_approval")
        if policy.require_admin_approval:
            approval_path.append("admin_approval")
        approval_requirements = {
            "requires_guarantors": required_guarantors > 0,
            "required_guarantors": required_guarantors,
            "guarantor_threshold_amount": str(
                to_decimal(getattr(policy, "guarantor_requirement_threshold", Decimal("0.00")))
            ),
            "requires_treasurer_approval": bool(policy.require_treasurer_approval),
            "requires_admin_approval": bool(policy.require_admin_approval),
            "requires_committee_approval": "committee_approval" in approval_path,
            "approval_path": approval_path,
        }
        metrics["approval_requirements"] = approval_requirements

        savings_summary = {
            "eligible_personal_savings": str(contributions_total),
            "minimum_required_savings": str(to_decimal(policy.min_savings_threshold)),
            "savings_shortfall": str(
                max(to_decimal(policy.min_savings_threshold) - contributions_total, Decimal("0.00"))
            ),
            "loan_multiplier": str(to_decimal(policy.loan_cap_multiplier)),
            "product_contribution_multiple": str(to_decimal(loan_product.contribution_multiple)),
            "max_based_on_savings": str(max(savings_based_cap, Decimal("0.00"))),
        }
        metrics["savings_summary"] = savings_summary
        metrics["policy_checks"] = policy_checks
        metrics["next_steps"] = next_steps
        metrics["risk_notes"] = risk_notes
        metrics["requested_amount_validation"] = {
            "requested_amount": str(principal_amount),
            "minimum_amount": str(to_decimal(policy.minimum_loan_amount)),
            "maximum_amount": str(recommended_max),
            "within_limit": principal_amount >= to_decimal(policy.minimum_loan_amount)
            and principal_amount <= recommended_max,
        }

        return LoanEligibilityResult(
            eligible=not reasons,
            recommended_max_amount=max(recommended_max, Decimal("0.00")),
            reasons=reasons,
            loan_product=loan_product,
            metrics=metrics,
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

        journal, _debit_line, credit_line = FinanceService._create_balanced_journal(
            chama=chama,
            actor=actor,
            reference=contribution.receipt_code,
            description=f"Contribution posted for {member.full_name}",
            source_type=JournalEntrySource.CONTRIBUTION,
            source_id=contribution.id,
            idempotency_key=payload["idempotency_key"],
            entry_type=LedgerEntryType.CONTRIBUTION,
            debit_account=FinanceService._get_or_create_account(
                chama,
                FinanceService._payment_method_account_key(contribution.method),
            ),
            credit_account=FinanceService._get_or_create_account(
                chama, "member_contributions"
            ),
            amount=contribution.amount,
            metadata={
                "member_id": str(member.id),
                "contribution_type_id": str(contribution_type.id),
                "receipt_code": contribution.receipt_code,
            },
        )
        FinanceService._refresh_financial_snapshot(chama, date_paid)

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
                "journal_entry_id": str(journal.id),
                "ledger_entry_id": str(credit_line.id),
            },
        )

        return LedgerPostResult(ledger_entry=credit_line, created=contribution)

    @staticmethod
    @transaction.atomic
    def reverse_contribution(contribution_id, payload: dict, actor: User) -> LedgerPostResult:
        contribution = get_object_or_404(
            Contribution.objects.select_for_update().select_related(
                "chama",
                "member",
                "contribution_type",
            ),
            id=contribution_id,
        )
        refund_amount = to_decimal(payload.get("amount") or contribution.net_amount)
        if refund_amount <= Decimal("0.00"):
            raise FinanceServiceError("Contribution reversal amount must be greater than zero.")
        if refund_amount > contribution.net_amount:
            raise FinanceServiceError("Contribution reversal amount exceeds refundable contribution balance.")

        FinanceService._ensure_month_open(contribution.chama, timezone.localdate())

        contribution.refunded_amount = to_decimal(contribution.refunded_amount + refund_amount)
        contribution.refunded_by = actor
        contribution.refunded_at = timezone.now()
        contribution.updated_by = actor
        contribution.save(
            update_fields=[
                "refunded_amount",
                "refunded_by",
                "refunded_at",
                "updated_by",
                "updated_at",
            ]
        )

        impacted_goals = ContributionGoal.objects.filter(
            chama=contribution.chama,
            member=contribution.member,
            created_at__lte=contribution.created_at,
            current_amount__gt=Decimal("0.00"),
        )
        for goal in impacted_goals:
            goal.current_amount = to_decimal(max(goal.current_amount - refund_amount, Decimal("0.00")))
            if goal.current_amount < goal.target_amount and goal.status == ContributionGoalStatus.COMPLETED:
                goal.status = ContributionGoalStatus.ACTIVE
                goal.is_active = True
            goal.updated_by = actor
            goal.save(update_fields=["current_amount", "status", "is_active", "updated_by", "updated_at"])

        journal, debit_line, _credit_line = FinanceService._create_balanced_journal(
            chama=contribution.chama,
            actor=actor,
            reference=f"contribution-reversal:{contribution.receipt_code}",
            description=f"Contribution refund for {contribution.member.full_name}",
            source_type=JournalEntrySource.ADJUSTMENT,
            source_id=contribution.id,
            idempotency_key=payload["idempotency_key"],
            entry_type=LedgerEntryType.ADJUSTMENT,
            debit_account=FinanceService._get_or_create_account(
                contribution.chama,
                "member_contributions",
            ),
            credit_account=FinanceService._get_or_create_account(
                contribution.chama,
                FinanceService._payment_method_account_key(contribution.method),
            ),
            amount=refund_amount,
            metadata={
                "member_id": str(contribution.member_id),
                "contribution_id": str(contribution.id),
                "receipt_code": contribution.receipt_code,
                "refund_amount": str(refund_amount),
                "adjustment_reason": "contribution_refund",
            },
        )
        FinanceService._refresh_financial_snapshot(contribution.chama, timezone.localdate())

        create_activity_log(
            actor=actor,
            chama_id=contribution.chama_id,
            action="contribution_reversed",
            entity_type="Contribution",
            entity_id=contribution.id,
            metadata={
                "member_id": str(contribution.member_id),
                "amount": str(refund_amount),
                "receipt_code": contribution.receipt_code,
                "journal_entry_id": str(journal.id),
                "ledger_entry_id": str(debit_line.id),
            },
        )
        return LedgerPostResult(ledger_entry=debit_line, created=contribution)

    @staticmethod
    @transaction.atomic
    def check_loan_eligibility(payload: dict, actor: User) -> dict:
        chama = get_object_or_404(Chama, id=payload["chama_id"])
        member = get_object_or_404(User, id=payload["member_id"])
        loan_product = FinanceService._resolve_loan_product(chama, payload)
        policy = FinanceService._get_loan_policy(chama)
        principal_amount = to_decimal(payload["principal"])
        result = FinanceService.evaluate_loan_eligibility(
            chama=chama,
            member=member,
            principal=principal_amount,
            duration_months=int(payload["duration_months"]),
            loan_product=loan_product,
        )
        required_guarantors = FinanceService._required_guarantor_count(
            policy=policy,
            principal_amount=principal_amount,
        )
        result.metrics["required_guarantors"] = required_guarantors
        result.metrics["guarantors_required"] = required_guarantors > 0
        result.metrics["selected_product_name"] = loan_product.name
        requested_amount_validation = result.metrics.get("requested_amount_validation", {})
        savings_summary = result.metrics.get("savings_summary", {})
        approval_requirements = result.metrics.get("approval_requirements", {})
        policy_checks = result.metrics.get("policy_checks", [])
        next_steps = result.metrics.get("next_steps", [])
        risk_notes = result.metrics.get("risk_notes", [])
        policy_summary = {
            "min_membership_months": max(int(policy.min_membership_days or 0) // 30, 0),
            "min_membership_days": int(policy.min_membership_days or 0),
            "minimum_contributions": int(policy.min_contribution_cycles or 0),
            "minimum_savings_threshold": str(to_decimal(policy.min_savings_threshold)),
            "minimum_loan_amount": str(to_decimal(policy.minimum_loan_amount)),
            "loan_cap_multiplier": str(to_decimal(policy.loan_cap_multiplier)),
            "maximum_member_loan_amount": str(to_decimal(policy.max_member_loan_amount)),
            "max_active_loans": int(policy.max_active_loans or 1),
            "block_unpaid_penalties": bool(policy.block_unpaid_penalties),
            "block_pending_loan_applications": bool(policy.block_pending_loan_applications),
            "repayment_capacity_ratio_limit": str(to_decimal(policy.repayment_capacity_ratio_limit)),
            "require_phone_verification": bool(policy.require_phone_verification),
            "require_kyc": bool(policy.require_kyc),
        }
        calculated_metrics = {
            "membership_days": result.metrics.get("membership_days", 0),
            "membership_months": result.metrics.get("membership_months", 0),
            "successful_contributions": result.metrics.get("successful_contributions", 0),
            "contribution_compliance_percent": result.metrics.get("contribution_compliance_percent", "0.00"),
            "contribution_consistency_score": result.metrics.get("contribution_consistency_score", "0.00"),
            "repayment_history_score": result.metrics.get("repayment_history_score", "0.00"),
            "active_loans_count": result.metrics.get("active_loans_count", 0),
            "pending_loan_applications_count": result.metrics.get("pending_loan_applications_count", 0),
            "available_liquidity": result.metrics.get("available_liquidity", "0.00"),
            "effective_lendable_liquidity": result.metrics.get("effective_lendable_liquidity", "0.00"),
            "installment_estimate": result.metrics.get("installment_estimate", "0.00"),
            "total_repayment_estimate": result.metrics.get("total_repayment_estimate", "0.00"),
            "average_monthly_savings": result.metrics.get("average_monthly_savings", "0.00"),
            "repayment_capacity_ratio": result.metrics.get("repayment_capacity_ratio", "0.00"),
        }
        return {
            "chama_id": str(chama.id),
            "member_id": str(member.id),
            "loan_product_id": str(loan_product.id),
            "currency": getattr(chama, "currency", "") or CurrencyChoices.KES,
            "eligible": result.eligible,
            "status": (
                LoanEligibilityStatus.ELIGIBLE
                if result.eligible
                else LoanEligibilityStatus.INELIGIBLE
            ),
            "requested_amount_valid": bool(requested_amount_validation.get("within_limit", False)),
            "requested_amount_validation": requested_amount_validation,
            "recommended_max_amount": str(to_decimal(result.recommended_max_amount)),
            "minimum_loan_amount": str(to_decimal(policy.minimum_loan_amount)),
            "reasons": result.reasons,
            "next_steps": next_steps,
            "risk_notes": risk_notes,
            "policy_summary": policy_summary,
            "policy_checks": policy_checks,
            "calculated_metrics": calculated_metrics,
            "repayment_history_score": result.metrics.get("repayment_history_score", "0.00"),
            "contribution_consistency_score": result.metrics.get("contribution_consistency_score", "0.00"),
            "savings_summary": savings_summary,
            "approval_requirements": approval_requirements,
            "metrics": result.metrics,
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
    def _validate_guarantor_assignment(
        *,
        loan: Loan,
        guarantor: User,
        guaranteed_amount: Decimal,
    ) -> Decimal:
        _ensure_member_active(loan.chama, guarantor)
        if guarantor.id == loan.member_id:
            raise FinanceServiceError("Borrower cannot guarantee their own loan.")

        policy = FinanceService._get_loan_policy(loan.chama)
        available_capacity = FinanceService._guarantor_capacity(
            policy=policy,
            chama=loan.chama,
            guarantor=guarantor,
        )
        requested_amount = to_decimal(guaranteed_amount)
        if requested_amount > available_capacity:
            raise FinanceServiceError(
                f"Guarantor has insufficient guarantee capacity. Available: KES {available_capacity}."
            )
        return requested_amount

    @staticmethod
    def _validate_application_guarantor_assignment(
        *,
        chama: Chama,
        borrower: User,
        guarantor: User,
        guaranteed_amount: Decimal,
    ) -> Decimal:
        _ensure_member_active(chama, guarantor)
        if guarantor.id == borrower.id:
            raise FinanceServiceError("Borrower cannot guarantee their own application.")

        policy = FinanceService._get_loan_policy(chama)
        available_capacity = FinanceService._guarantor_capacity(
            policy=policy,
            chama=chama,
            guarantor=guarantor,
        )
        requested_amount = to_decimal(guaranteed_amount)
        if requested_amount > available_capacity:
            raise FinanceServiceError(
                f"Guarantor has insufficient guarantee capacity. Available: KES {available_capacity}."
            )
        return requested_amount

    @staticmethod
    @transaction.atomic
    def add_loan_guarantor(payload: dict, actor: User) -> LoanGuarantor:
        loan = get_object_or_404(Loan, id=payload["loan_id"])
        guarantor = get_object_or_404(User, id=payload["guarantor_id"])
        guaranteed_amount = FinanceService._validate_guarantor_assignment(
            loan=loan,
            guarantor=guarantor,
            guaranteed_amount=payload["guaranteed_amount"],
        )

        guarantor_obj, created = LoanGuarantor.objects.get_or_create(
            loan=loan,
            guarantor=guarantor,
            defaults={
                "guaranteed_amount": guaranteed_amount,
                "status": (
                    LoanGuarantorStatus.ACCEPTED
                    if actor.id == guarantor.id
                    else LoanGuarantorStatus.PROPOSED
                ),
                "accepted_at": timezone.now() if actor.id == guarantor.id else None,
                "created_by": actor,
                "updated_by": actor,
            },
        )
        if not created:
            guarantor_obj.guaranteed_amount = guaranteed_amount
            guarantor_obj.status = (
                LoanGuarantorStatus.ACCEPTED
                if actor.id == guarantor.id
                else LoanGuarantorStatus.PROPOSED
            )
            guarantor_obj.accepted_at = timezone.now() if actor.id == guarantor.id else None
            guarantor_obj.rejected_at = None if actor.id == guarantor.id else guarantor_obj.rejected_at
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
        create_audit_log(
            actor=actor,
            chama_id=loan.chama_id,
            action="loan_guarantor_assigned",
            entity_type="LoanGuarantor",
            entity_id=guarantor_obj.id,
            metadata={
                "loan_id": str(loan.id),
                "guarantor_id": str(guarantor.id),
                "guaranteed_amount": str(guarantor_obj.guaranteed_amount),
                "status": guarantor_obj.status,
            },
        )
        if guarantor_obj.status == LoanGuarantorStatus.PROPOSED:
            try:
                from apps.notifications.models import NotificationType
                from apps.notifications.services import NotificationService

                NotificationService.send_notification(
                    user=guarantor,
                    chama=loan.chama,
                    channels=["in_app", "email"],
                    message=(
                        f"You have been requested to guarantee loan {loan.id} for "
                        f"KES {guarantor_obj.guaranteed_amount}."
                    ),
                    subject="Guarantor action required",
                    notification_type=NotificationType.LOAN_UPDATE,
                    idempotency_key=f"loan-guarantor-request:{guarantor_obj.id}",
                )
            except Exception:  # noqa: BLE001
                logger.exception("Failed to send guarantor request notification")
        return guarantor_obj

    @staticmethod
    @transaction.atomic
    def add_loan_application_guarantor(payload: dict, actor: User) -> LoanApplicationGuarantor:
        loan_application = get_object_or_404(
            LoanApplication,
            id=payload["loan_application_id"],
        )
        guarantor = get_object_or_404(User, id=payload["guarantor_id"])
        guaranteed_amount = FinanceService._validate_application_guarantor_assignment(
            chama=loan_application.chama,
            borrower=loan_application.member,
            guarantor=guarantor,
            guaranteed_amount=payload["guaranteed_amount"],
        )

        guarantor_obj, created = LoanApplicationGuarantor.objects.get_or_create(
            loan_application=loan_application,
            guarantor=guarantor,
            defaults={
                "guaranteed_amount": guaranteed_amount,
                "status": (
                    LoanGuarantorStatus.ACCEPTED
                    if actor.id == guarantor.id
                    else LoanGuarantorStatus.PROPOSED
                ),
                "accepted_at": timezone.now() if actor.id == guarantor.id else None,
                "created_by": actor,
                "updated_by": actor,
            },
        )
        if not created:
            guarantor_obj.guaranteed_amount = guaranteed_amount
            guarantor_obj.status = (
                LoanGuarantorStatus.ACCEPTED
                if actor.id == guarantor.id
                else LoanGuarantorStatus.PROPOSED
            )
            guarantor_obj.accepted_at = (
                timezone.now() if actor.id == guarantor.id else None
            )
            guarantor_obj.rejected_at = (
                None if actor.id == guarantor.id else guarantor_obj.rejected_at
            )
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
        create_audit_log(
            actor=actor,
            chama_id=loan_application.chama_id,
            action="loan_application_guarantor_assigned",
            entity_type="LoanApplicationGuarantor",
            entity_id=guarantor_obj.id,
            metadata={
                "loan_application_id": str(loan_application.id),
                "guarantor_id": str(guarantor.id),
                "guaranteed_amount": str(guarantor_obj.guaranteed_amount),
                "status": guarantor_obj.status,
            },
        )
        if guarantor_obj.status == LoanGuarantorStatus.PROPOSED:
            try:
                from apps.notifications.models import NotificationType
                from apps.notifications.services import NotificationService

                NotificationService.send_notification(
                    user=guarantor,
                    chama=loan_application.chama,
                    channels=["in_app", "email"],
                    message=(
                        f"You have been requested to guarantee loan application "
                        f"{loan_application.id} for KES {guarantor_obj.guaranteed_amount}."
                    ),
                    subject="Guarantor action required",
                    notification_type=NotificationType.LOAN_UPDATE,
                    idempotency_key=f"loan-application-guarantor-request:{guarantor_obj.id}",
                )
            except Exception:  # noqa: BLE001
                logger.exception("Failed to send application guarantor request notification")
        return guarantor_obj

    @staticmethod
    @transaction.atomic
    def respond_to_loan_guarantor(
        guarantor_record_id,
        *,
        actor: User,
        decision: str,
        note: str = "",
    ) -> LoanGuarantor:
        guarantor_record = get_object_or_404(
            LoanGuarantor.objects.select_related("loan", "loan__chama", "loan__member", "guarantor"),
            id=guarantor_record_id,
        )
        if guarantor_record.guarantor_id != actor.id:
            raise FinanceServiceError("Only the selected guarantor can respond.")
        if guarantor_record.status not in {
            LoanGuarantorStatus.PROPOSED,
            LoanGuarantorStatus.REJECTED,
        }:
            raise FinanceServiceError("Guarantor request is already finalised.")

        FinanceService._validate_guarantor_assignment(
            loan=guarantor_record.loan,
            guarantor=actor,
            guaranteed_amount=guarantor_record.guaranteed_amount,
        )
        if decision == LoanGuarantorStatus.ACCEPTED:
            guarantor_record.status = LoanGuarantorStatus.ACCEPTED
            guarantor_record.accepted_at = timezone.now()
            guarantor_record.rejected_at = None
        else:
            guarantor_record.status = LoanGuarantorStatus.REJECTED
            guarantor_record.rejected_at = timezone.now()
        guarantor_record.review_note = note
        guarantor_record.updated_by = actor
        guarantor_record.save(
            update_fields=[
                "status",
                "accepted_at",
                "rejected_at",
                "review_note",
                "updated_by",
                "updated_at",
            ]
        )
        create_audit_log(
            actor=actor,
            chama_id=guarantor_record.loan.chama_id,
            action="loan_guarantor_responded",
            entity_type="LoanGuarantor",
            entity_id=guarantor_record.id,
            metadata={
                "loan_id": str(guarantor_record.loan_id),
                "decision": decision,
                "note": note,
            },
        )
        return guarantor_record

    @staticmethod
    @transaction.atomic
    def respond_to_loan_application_guarantor(
        guarantor_record_id,
        *,
        actor: User,
        decision: str,
        note: str = "",
    ) -> LoanApplicationGuarantor:
        guarantor_record = get_object_or_404(
            LoanApplicationGuarantor.objects.select_related(
                "loan_application",
                "loan_application__chama",
                "loan_application__member",
                "guarantor",
            ),
            id=guarantor_record_id,
        )
        if guarantor_record.guarantor_id != actor.id:
            raise FinanceServiceError("Only the selected guarantor can respond.")
        if guarantor_record.status not in {
            LoanGuarantorStatus.PROPOSED,
            LoanGuarantorStatus.REJECTED,
        }:
            raise FinanceServiceError("Guarantor request is already finalised.")

        FinanceService._validate_application_guarantor_assignment(
            chama=guarantor_record.loan_application.chama,
            borrower=guarantor_record.loan_application.member,
            guarantor=actor,
            guaranteed_amount=guarantor_record.guaranteed_amount,
        )
        if decision == LoanGuarantorStatus.ACCEPTED:
            guarantor_record.status = LoanGuarantorStatus.ACCEPTED
            guarantor_record.accepted_at = timezone.now()
            guarantor_record.rejected_at = None
        else:
            guarantor_record.status = LoanGuarantorStatus.REJECTED
            guarantor_record.rejected_at = timezone.now()
        guarantor_record.review_note = note
        guarantor_record.updated_by = actor
        guarantor_record.save(
            update_fields=[
                "status",
                "accepted_at",
                "rejected_at",
                "review_note",
                "updated_by",
                "updated_at",
            ]
        )
        create_audit_log(
            actor=actor,
            chama_id=guarantor_record.loan_application.chama_id,
            action="loan_application_guarantor_responded",
            entity_type="LoanApplicationGuarantor",
            entity_id=guarantor_record.id,
            metadata={
                "loan_application_id": str(guarantor_record.loan_application_id),
                "decision": decision,
                "note": note,
            },
        )
        return guarantor_record

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
            Contribution.objects.filter(
                chama=chama,
                member=member,
                refunded_amount__lt=F("amount"),
            )
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
        policy = FinanceService._get_loan_policy(loan.chama)
        if not policy.allow_restructure:
            raise FinanceServiceError("Loan restructuring is disabled by chama policy.")
        if loan.status not in {
            LoanStatus.DISBURSED,
            LoanStatus.ACTIVE,
            LoanStatus.DISBURSING,
            LoanStatus.DUE_SOON,
            LoanStatus.OVERDUE,
            LoanStatus.DEFAULTED,
            LoanStatus.DEFAULTED_RECOVERING,
        }:
            raise FinanceServiceError("Loan is not eligible for restructuring.")

        request_obj = LoanRestructureRequest.objects.create(
            loan=loan,
            requested_duration_months=int(payload["requested_duration_months"]),
            requested_interest_rate=payload.get("requested_interest_rate"),
            reason=payload.get("reason", ""),
            status=LoanRestructureStatus.REQUESTED,
            created_by=actor,
            updated_by=actor,
        )
        FinanceService._record_loan_recovery_action(
            loan=loan,
            action_type=LoanRecoveryActionType.RESTRUCTURE_REQUESTED,
            actor=actor,
            notes=request_obj.reason,
            metadata={"restructure_request_id": str(request_obj.id)},
        )
        return request_obj

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
        policy = FinanceService._get_loan_policy(loan.chama)
        if not policy.allow_restructure:
            raise FinanceServiceError("Loan restructuring is disabled by chama policy.")
        has_repayments = Repayment.objects.filter(loan=loan).exists()
        if has_repayments and decision == LoanRestructureStatus.APPROVED:
            raise FinanceServiceError(
                "Cannot apply restructure after repayments have already started."
            )

        if decision == LoanRestructureStatus.REJECTED:
            restructure.status = LoanRestructureStatus.REJECTED
        else:
            restructure.status = LoanRestructureStatus.APPLIED
            old_terms = {
                "duration_months": loan.duration_months,
                "interest_rate": str(loan.interest_rate),
                "due_date": loan.due_date.isoformat() if loan.due_date else None,
                "total_due": str(loan.total_due),
            }
            loan.duration_months = restructure.requested_duration_months
            if restructure.requested_interest_rate is not None:
                loan.interest_rate = to_decimal(
                    restructure.requested_interest_rate,
                    precision="0.01",
                )
            loan.status = LoanStatus.RESTRUCTURED
            loan.updated_by = actor
            loan.save(update_fields=["duration_months", "interest_rate", "status", "updated_by", "updated_at"])
            FinanceService.generate_schedule(loan)
            LoanRestructure.objects.create(
                loan=loan,
                source_request=restructure,
                old_terms_snapshot=old_terms,
                new_terms_snapshot={
                    "duration_months": loan.duration_months,
                    "interest_rate": str(loan.interest_rate),
                    "due_date": loan.due_date.isoformat() if loan.due_date else None,
                    "total_due": str(loan.total_due),
                },
                approved_by=actor,
                created_by=actor,
                updated_by=actor,
            )
            FinanceService._record_loan_recovery_action(
                loan=loan,
                action_type=LoanRecoveryActionType.RESTRUCTURE_APPROVED,
                actor=actor,
                notes=note,
                metadata={"restructure_request_id": str(restructure.id)},
            )
            FinanceService._set_loan_final_status(
                loan,
                final_status="restructured",
                actor=actor,
            )

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
        try:
            from apps.notifications.services import notify_loan_restructure_reviewed

            notify_loan_restructure_reviewed(loan, decision != LoanRestructureStatus.REJECTED)
        except Exception:  # noqa: BLE001
            logger.exception("Failed restructure notification for loan=%s", loan.id)
        return restructure

    @staticmethod
    def _required_guarantor_count(
        *,
        policy: ChamaLoanPolicy,
        principal_amount: Decimal,
    ) -> int:
        required_guarantors = 0
        threshold = to_decimal(
            getattr(policy, "guarantor_requirement_threshold", Decimal("0.00"))
        )
        guarantor_threshold_met = threshold <= Decimal("0.00") or principal_amount >= threshold
        if policy.require_guarantors and guarantor_threshold_met:
            required_guarantors = max(required_guarantors, int(policy.min_guarantors or 0))
        if (
            policy.medium_loan_threshold
            and principal_amount >= to_decimal(policy.medium_loan_threshold)
        ):
            required_guarantors = max(
                required_guarantors,
                int(policy.medium_loan_guarantors_count or 0),
            )
        return required_guarantors

    @staticmethod
    def _assert_application_guarantor_coverage(
        application: LoanApplication,
    ) -> tuple[int, Decimal]:
        policy = FinanceService._get_loan_policy(application.chama)
        required_guarantors = FinanceService._required_guarantor_count(
            policy=policy,
            principal_amount=to_decimal(application.requested_amount),
        )
        accepted_guarantors = LoanApplicationGuarantor.objects.filter(
            loan_application=application,
            status=LoanGuarantorStatus.ACCEPTED,
        )
        guaranteed_total = accepted_guarantors.aggregate(
            total=Coalesce(
                Sum("guaranteed_amount"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        guaranteed_total = to_decimal(guaranteed_total)
        if required_guarantors > 0:
            if accepted_guarantors.count() < required_guarantors:
                raise FinanceServiceError(
                    f"Application requires at least {required_guarantors} approved guarantor(s)."
                )
            if guaranteed_total < to_decimal(application.requested_amount):
                raise FinanceServiceError(
                    "Approved guarantor coverage must fully cover the requested amount."
                )
        return required_guarantors, guaranteed_total

    @staticmethod
    def _create_loan_from_application(
        application: LoanApplication,
        *,
        actor: User,
    ) -> Loan:
        if application.created_loan_id:
            return application.created_loan

        policy = FinanceService._get_loan_policy(application.chama)
        loan_product = application.loan_product or FinanceService._resolve_loan_product(
            application.chama,
            {"loan_product_id": application.loan_product_id},
        )
        loan = Loan.objects.create(
            chama=application.chama,
            member=application.member,
            loan_product=loan_product,
            purpose=application.purpose,
            principal=to_decimal(application.requested_amount),
            outstanding_principal=to_decimal(application.requested_amount),
            outstanding_interest=Decimal("0.00"),
            outstanding_penalty=Decimal("0.00"),
            total_due=to_decimal(application.requested_amount),
            interest_type=loan_product.interest_type,
            interest_rate=to_decimal(loan_product.interest_rate, precision="0.01"),
            duration_months=int(application.requested_term_months),
            grace_period_days=max(
                int(loan_product.grace_period_days or 0),
                int(policy.grace_period_days or 0),
            ),
            late_penalty_type=loan_product.late_penalty_type,
            late_penalty_value=to_decimal(
                loan_product.late_penalty_value or policy.late_fee_value or Decimal("0.00")
            ),
            early_repayment_discount_percent=to_decimal(
                loan_product.early_repayment_discount_percent
            ),
            eligibility_status=application.eligibility_status,
            eligibility_reason="",
            recommended_max_amount=to_decimal(application.recommended_max_amount),
            status=LoanStatus.APPROVED,
            approved_by=actor,
            approved_at=timezone.now(),
            created_by=actor,
            updated_by=actor,
        )
        FinanceService.generate_schedule(loan)

        for guarantor in LoanApplicationGuarantor.objects.filter(
            loan_application=application,
            status=LoanGuarantorStatus.ACCEPTED,
        ):
            LoanGuarantor.objects.create(
                loan=loan,
                guarantor=guarantor.guarantor,
                guaranteed_amount=guarantor.guaranteed_amount,
                status=LoanGuarantorStatus.ACCEPTED,
                review_note=guarantor.review_note,
                accepted_at=guarantor.accepted_at,
                created_by=actor,
                updated_by=actor,
            )

        LoanApprovalLog.objects.create(
            loan=loan,
            stage=LoanApprovalStage.ADMIN_APPROVAL,
            decision=LoanApprovalDecision.APPROVED,
            note=f"Created from loan application {application.id}.",
            actor=actor,
            created_by=actor,
            updated_by=actor,
        )
        application.created_loan = loan
        application.updated_by = actor
        application.save(update_fields=["created_loan", "updated_by", "updated_at"])
        return loan

    @staticmethod
    @transaction.atomic
    def request_loan_application(payload: dict, actor: User) -> LoanApplication:
        chama = get_object_or_404(Chama, id=payload["chama_id"])
        member = get_object_or_404(User, id=payload["member_id"])
        _ensure_member_active(chama, member)
        loan_product = FinanceService._resolve_loan_product(chama, payload)
        policy = FinanceService._get_loan_policy(chama)

        if not policy.loans_enabled:
            raise FinanceServiceError("Loans are disabled for this chama.")
        if policy.require_loan_purpose and not str(payload.get("purpose", "")).strip():
            raise FinanceServiceError("Loan purpose is required by chama policy.")
        if (
            policy.block_pending_loan_applications
            and LoanApplication.objects.filter(
                chama=chama,
                member=member,
                status__in=[
                    LoanApplicationStatus.SUBMITTED,
                    LoanApplicationStatus.IN_REVIEW,
                    LoanApplicationStatus.TREASURER_APPROVED,
                    LoanApplicationStatus.COMMITTEE_APPROVED,
                ],
            ).exists()
        ):
            raise FinanceServiceError(
                "You already have a loan application under review."
            )

        eligibility = FinanceService.evaluate_loan_eligibility(
            chama=chama,
            member=member,
            principal=to_decimal(payload["requested_amount"]),
            duration_months=int(payload["requested_term_months"]),
            loan_product=loan_product,
        )
        if not eligibility.eligible:
            raise FinanceServiceError(
                "Loan application not eligible: " + "; ".join(eligibility.reasons)
            )

        savings_summary = eligibility.metrics.get("savings_summary", {})
        approval_requirements = eligibility.metrics.get("approval_requirements", {})
        next_steps = list(eligibility.metrics.get("next_steps", []))
        risk_notes = list(eligibility.metrics.get("risk_notes", []))
        installment_estimate = to_decimal(
            eligibility.metrics.get("installment_estimate", Decimal("0.00"))
        )
        total_repayment_estimate = to_decimal(
            eligibility.metrics.get("total_repayment_estimate", Decimal("0.00"))
        )
        contribution_count = int(eligibility.metrics.get("successful_contributions", 0) or 0)
        savings_balance = to_decimal(
            savings_summary.get("eligible_personal_savings", Decimal("0.00"))
        )
        repayment_history_score = to_decimal(
            eligibility.metrics.get("repayment_history_score", Decimal("0.00"))
        )
        contribution_consistency_score = to_decimal(
            eligibility.metrics.get("contribution_consistency_score", Decimal("0.00"))
        )

        application = LoanApplication.objects.create(
            chama=chama,
            member=member,
            loan_product=loan_product,
            requested_amount=to_decimal(payload["requested_amount"]),
            requested_term_months=int(payload["requested_term_months"]),
            purpose=str(payload.get("purpose", "")).strip(),
            status=LoanApplicationStatus.SUBMITTED,
            eligibility_status=LoanEligibilityStatus.ELIGIBLE,
            recommended_max_amount=to_decimal(eligibility.recommended_max_amount),
            eligible_amount_at_application=to_decimal(eligibility.recommended_max_amount),
            savings_balance_at_application=savings_balance,
            contribution_count_at_application=contribution_count,
            repayment_history_score=repayment_history_score,
            contribution_consistency_score=contribution_consistency_score,
            installment_estimate=installment_estimate,
            total_repayment_estimate=total_repayment_estimate,
            loan_multiplier_at_application=to_decimal(policy.loan_cap_multiplier),
            risk_notes=risk_notes,
            next_steps=next_steps,
            approval_requirements=approval_requirements,
            eligibility_snapshot={
                "reasons": eligibility.reasons,
                "metrics": eligibility.metrics,
                "loan_product_id": str(loan_product.id),
                "policy_checks": eligibility.metrics.get("policy_checks", []),
                "savings_summary": savings_summary,
                "approval_requirements": approval_requirements,
                "next_steps": next_steps,
                "risk_notes": risk_notes,
            },
            created_by=actor,
            updated_by=actor,
        )

        for guarantor_payload in payload.get("guarantors") or []:
            FinanceService.add_loan_application_guarantor(
                {
                    "loan_application_id": str(application.id),
                    "guarantor_id": guarantor_payload["guarantor_id"],
                    "guaranteed_amount": guarantor_payload["guaranteed_amount"],
                },
                actor,
            )

        LoanApplicationApproval.objects.create(
            loan_application=application,
            stage=LoanApprovalStage.TREASURER_REVIEW,
            decision=LoanApprovalDecision.PENDING,
            note="Awaiting treasurer review.",
            actor=None,
            created_by=actor,
            updated_by=actor,
        )
        if (
            policy.require_committee_vote
            and policy.committee_threshold_amount
            and application.requested_amount >= policy.committee_threshold_amount
        ):
            LoanApplicationApproval.objects.create(
                loan_application=application,
                stage=LoanApprovalStage.COMMITTEE_APPROVAL,
                decision=LoanApprovalDecision.PENDING,
                note="Awaiting committee approval due to amount threshold.",
                actor=None,
                created_by=actor,
                updated_by=actor,
            )
        LoanApplicationApproval.objects.create(
            loan_application=application,
            stage=LoanApprovalStage.ADMIN_APPROVAL,
            decision=LoanApprovalDecision.PENDING,
            note="Awaiting final approval.",
            actor=None,
            created_by=actor,
            updated_by=actor,
        )

        create_audit_log(
            actor=actor,
            chama_id=chama.id,
            action="loan_application_submitted",
            entity_type="LoanApplication",
            entity_id=application.id,
            metadata={
                "requested_amount": str(application.requested_amount),
                "requested_term_months": application.requested_term_months,
                "loan_product_id": str(loan_product.id),
                "purpose": application.purpose,
            },
        )
        create_activity_log(
            actor=actor,
            chama_id=chama.id,
            action="loan_application_submitted",
            entity_type="LoanApplication",
            entity_id=application.id,
            metadata={
                "requested_amount": str(application.requested_amount),
                "requested_term_months": application.requested_term_months,
            },
        )
        FinanceService._record_loan_audit_log(
            chama=chama,
            member=member,
            loan_application=application,
            actor=actor,
            action="application_submitted",
            status_to=application.status,
            notes=application.purpose,
            metadata={
                "requested_amount": str(application.requested_amount),
                "requested_term_months": application.requested_term_months,
                "eligible_amount_at_application": str(application.eligible_amount_at_application),
            },
        )
        try:
            from apps.notifications.models import NotificationType
            from apps.notifications.services import NotificationService

            approvers = Membership.objects.select_related("user").filter(
                chama=chama,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
                role__in=[MembershipRole.TREASURER, MembershipRole.CHAMA_ADMIN],
            )
            for approver in approvers:
                NotificationService.send_notification(
                    user=approver.user,
                    chama=chama,
                    channels=["in_app", "email"],
                    message=(
                        f"Loan application review required for {member.full_name}: "
                        f"KES {application.requested_amount} over "
                        f"{application.requested_term_months} month(s)."
                    ),
                    subject="Loan application submitted",
                    notification_type=NotificationType.LOAN_UPDATE,
                    idempotency_key=f"loan-application-submitted:{application.id}:{approver.user_id}",
                )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to notify approvers for loan application=%s", application.id)
        return application

    @staticmethod
    @transaction.atomic
    def review_loan_application(
        application_id,
        *,
        actor: User,
        decision: str,
        note: str = "",
    ) -> LoanApplication:
        application = get_object_or_404(
            LoanApplication.objects.select_related("chama", "member", "loan_product"),
            id=application_id,
        )
        if application.status in {
            LoanApplicationStatus.REJECTED,
            LoanApplicationStatus.APPROVED,
            LoanApplicationStatus.DISBURSED,
            LoanApplicationStatus.CANCELLED,
        }:
            raise FinanceServiceError("Loan application is already finalised.")
        previous_status = application.status

        LoanApplicationApproval.objects.create(
            loan_application=application,
            stage=LoanApprovalStage.TREASURER_REVIEW,
            decision=decision,
            note=note,
            actor=actor,
            created_by=actor,
            updated_by=actor,
        )
        application.reviewed_at = timezone.now()
        application.reviewed_by = actor
        application.updated_by = actor
        if decision == LoanApprovalDecision.REJECTED:
            application.status = LoanApplicationStatus.REJECTED
            application.rejection_reason = note
        else:
            policy = FinanceService._get_loan_policy(application.chama)
            if (
                policy.require_committee_vote
                and policy.committee_threshold_amount
                and application.requested_amount >= policy.committee_threshold_amount
            ):
                application.status = LoanApplicationStatus.TREASURER_APPROVED
            else:
                application.status = LoanApplicationStatus.IN_REVIEW
        application.save(
            update_fields=[
                "status",
                "rejection_reason",
                "reviewed_at",
                "reviewed_by",
                "updated_by",
                "updated_at",
            ]
        )
        FinanceService._record_loan_audit_log(
            chama=application.chama,
            member=application.member,
            loan_application=application,
            actor=actor,
            action="application_reviewed",
            status_from=previous_status,
            status_to=application.status,
            notes=note,
            metadata={"decision": decision},
        )
        return application

    @staticmethod
    @transaction.atomic
    def committee_approve_loan_application(
        application_id,
        *,
        actor: User,
        decision: str,
        note: str = "",
    ) -> LoanApplication:
        application = get_object_or_404(LoanApplication, id=application_id)
        if application.status not in {
            LoanApplicationStatus.TREASURER_APPROVED,
            LoanApplicationStatus.IN_REVIEW,
        }:
            raise FinanceServiceError("Loan application is not awaiting committee action.")
        previous_status = application.status
        LoanApplicationApproval.objects.create(
            loan_application=application,
            stage=LoanApprovalStage.COMMITTEE_APPROVAL,
            decision=decision,
            note=note,
            actor=actor,
            created_by=actor,
            updated_by=actor,
        )
        application.reviewed_at = timezone.now()
        application.reviewed_by = actor
        application.updated_by = actor
        if decision == LoanApprovalDecision.REJECTED:
            application.status = LoanApplicationStatus.REJECTED
            application.rejection_reason = note
        else:
            application.status = LoanApplicationStatus.COMMITTEE_APPROVED
        application.save(
            update_fields=[
                "status",
                "rejection_reason",
                "reviewed_at",
                "reviewed_by",
                "updated_by",
                "updated_at",
            ]
        )
        FinanceService._record_loan_audit_log(
            chama=application.chama,
            member=application.member,
            loan_application=application,
            actor=actor,
            action="application_committee_reviewed",
            status_from=previous_status,
            status_to=application.status,
            notes=note,
            metadata={"decision": decision},
        )
        return application

    @staticmethod
    @transaction.atomic
    def approve_loan_application(
        application_id,
        *,
        actor: User,
        note: str = "",
    ) -> LoanApplication:
        application = get_object_or_404(
            LoanApplication.objects.select_related("chama", "member", "loan_product"),
            id=application_id,
        )
        if application.status in {
            LoanApplicationStatus.REJECTED,
            LoanApplicationStatus.APPROVED,
            LoanApplicationStatus.DISBURSED,
            LoanApplicationStatus.CANCELLED,
        }:
            raise FinanceServiceError("Loan application is already finalised.")
        previous_status = application.status

        policy = FinanceService._get_loan_policy(application.chama)
        treasurer_review = (
            LoanApplicationApproval.objects.filter(
                loan_application=application,
                stage=LoanApprovalStage.TREASURER_REVIEW,
                decision=LoanApprovalDecision.APPROVED,
            )
            .order_by("-acted_at")
            .first()
        )
        if policy.require_treasurer_approval and not treasurer_review:
            raise FinanceServiceError(
                "Loan application requires treasurer review before final approval."
            )
        if treasurer_review and treasurer_review.actor_id == actor.id:
            raise FinanceServiceError(
                "Maker-checker enforcement: reviewer and approver must differ."
            )
        if (
            policy.require_committee_vote
            and policy.committee_threshold_amount
            and application.requested_amount >= policy.committee_threshold_amount
        ):
            committee_review = LoanApplicationApproval.objects.filter(
                loan_application=application,
                stage=LoanApprovalStage.COMMITTEE_APPROVAL,
                decision=LoanApprovalDecision.APPROVED,
            ).exists()
            if not committee_review:
                raise FinanceServiceError(
                    "Loan application requires committee approval before final approval."
                )

        FinanceService._assert_application_guarantor_coverage(application)
        loan = FinanceService._create_loan_from_application(application, actor=actor)
        application.status = LoanApplicationStatus.APPROVED
        application.approved_at = timezone.now()
        application.approved_by = actor
        application.reviewed_at = timezone.now()
        application.reviewed_by = actor
        application.updated_by = actor
        application.save(
            update_fields=[
                "status",
                "approved_at",
                "approved_by",
                "reviewed_at",
                "reviewed_by",
                "updated_by",
                "updated_at",
            ]
        )
        LoanApplicationApproval.objects.create(
            loan_application=application,
            stage=LoanApprovalStage.ADMIN_APPROVAL,
            decision=LoanApprovalDecision.APPROVED,
            note=note,
            actor=actor,
            created_by=actor,
            updated_by=actor,
        )
        FinanceService._record_loan_audit_log(
            chama=application.chama,
            member=application.member,
            loan_application=application,
            loan=loan,
            actor=actor,
            action="application_approved",
            status_from=previous_status,
            status_to=application.status,
            notes=note,
            metadata={"loan_id": str(loan.id)},
        )

        try:
            from apps.notifications.services import notify_loan_approved

            notify_loan_approved(loan)
        except Exception:  # noqa: BLE001
            logger.exception("Failed approval notification for loan application=%s", application.id)
        return application

    @staticmethod
    @transaction.atomic
    def reject_loan_application(
        application_id,
        *,
        actor: User,
        note: str = "",
    ) -> LoanApplication:
        application = get_object_or_404(LoanApplication, id=application_id)
        if application.status in {
            LoanApplicationStatus.REJECTED,
            LoanApplicationStatus.APPROVED,
            LoanApplicationStatus.DISBURSED,
            LoanApplicationStatus.CANCELLED,
        }:
            raise FinanceServiceError("Loan application is already finalised.")
        previous_status = application.status
        application.status = LoanApplicationStatus.REJECTED
        application.rejection_reason = note
        application.reviewed_at = timezone.now()
        application.reviewed_by = actor
        application.updated_by = actor
        application.save(
            update_fields=[
                "status",
                "rejection_reason",
                "reviewed_at",
                "reviewed_by",
                "updated_by",
                "updated_at",
            ]
        )
        LoanApplicationApproval.objects.create(
            loan_application=application,
            stage=LoanApprovalStage.ADMIN_APPROVAL,
            decision=LoanApprovalDecision.REJECTED,
            note=note,
            actor=actor,
            created_by=actor,
            updated_by=actor,
        )
        FinanceService._record_loan_audit_log(
            chama=application.chama,
            member=application.member,
            loan_application=application,
            actor=actor,
            action="application_rejected",
            status_from=previous_status,
            status_to=application.status,
            notes=note,
        )
        try:
            from apps.notifications.services import notify_loan_rejected

            notify_loan_rejected(application.created_loan or application, note)
        except Exception:  # noqa: BLE001
            logger.exception("Failed rejection notification for loan application=%s", application.id)
        return application

    @staticmethod
    @transaction.atomic
    def disburse_loan_application(
        application_id,
        *,
        actor: User,
        idempotency_key: str | None = None,
        disbursement_reference: str = "",
    ) -> tuple[LoanApplication, LedgerPostResult]:
        application = get_object_or_404(
            LoanApplication.objects.select_related("created_loan"),
            id=application_id,
        )
        if application.status not in {
            LoanApplicationStatus.APPROVED,
            LoanApplicationStatus.DISBURSED,
        }:
            raise FinanceServiceError("Only approved loan applications can be disbursed.")
        previous_status = application.status
        loan = application.created_loan or FinanceService._create_loan_from_application(
            application,
            actor=actor,
        )
        result = FinanceService.disburse_loan(
            loan.id,
            actor,
            idempotency_key=idempotency_key,
            disbursement_reference=disbursement_reference,
        )
        application.status = LoanApplicationStatus.DISBURSED
        application.disbursed_at = timezone.now()
        application.updated_by = actor
        application.save(update_fields=["status", "disbursed_at", "updated_by", "updated_at"])
        LoanApplicationApproval.objects.create(
            loan_application=application,
            stage=LoanApprovalStage.DISBURSEMENT,
            decision=LoanApprovalDecision.APPROVED,
            note=disbursement_reference,
            actor=actor,
            created_by=actor,
            updated_by=actor,
        )
        FinanceService._record_loan_audit_log(
            chama=application.chama,
            member=application.member,
            loan_application=application,
            loan=loan,
            actor=actor,
            action="application_disbursed",
            status_from=previous_status,
            status_to=application.status,
            notes=disbursement_reference,
            metadata={"loan_id": str(loan.id)},
        )
        return application, result

    @staticmethod
    @transaction.atomic
    def request_loan(payload: dict, actor: User) -> Loan:
        chama = get_object_or_404(Chama, id=payload["chama_id"])
        member = get_object_or_404(User, id=payload["member_id"])
        _ensure_member_active(chama, member)
        loan_product = FinanceService._resolve_loan_product(chama, payload)
        policy = FinanceService._get_loan_policy(chama)

        if not policy.loans_enabled:
            raise FinanceServiceError("Loans are disabled for this chama.")
        if policy.require_loan_purpose and not str(payload.get("purpose", "")).strip():
            raise FinanceServiceError("Loan purpose is required by chama policy.")

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
            purpose=str(payload.get("purpose", "")).strip(),
            principal=to_decimal(payload["principal"]),
            outstanding_principal=to_decimal(payload["principal"]),
            outstanding_interest=Decimal("0.00"),
            outstanding_penalty=Decimal("0.00"),
            total_due=to_decimal(payload["principal"]),
            interest_type=loan_product.interest_type,
            interest_rate=to_decimal(loan_product.interest_rate, precision="0.01"),
            duration_months=int(payload["duration_months"]),
            grace_period_days=max(
                int(loan_product.grace_period_days or 0),
                int(policy.grace_period_days or 0),
            ),
            late_penalty_type=loan_product.late_penalty_type,
            late_penalty_value=to_decimal(
                loan_product.late_penalty_value or policy.late_fee_value or Decimal("0.00")
            ),
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
            reasons=eligibility.reasons,
            created_by=actor,
            updated_by=actor,
        )

        for guarantor_payload in payload.get("guarantors") or []:
            FinanceService.add_loan_guarantor(
                {
                    "loan_id": str(loan.id),
                    "guarantor_id": guarantor_payload["guarantor_id"],
                    "guaranteed_amount": guarantor_payload["guaranteed_amount"],
                },
                actor,
            )

        if loan_product.require_treasurer_review or policy.require_treasurer_approval:
            LoanApprovalLog.objects.create(
                loan=loan,
                stage=LoanApprovalStage.TREASURER_REVIEW,
                decision=LoanApprovalDecision.PENDING,
                note="Awaiting treasurer review.",
                actor=None,
                created_by=actor,
                updated_by=actor,
            )
        if policy.require_committee_vote and policy.committee_threshold_amount and loan.principal >= policy.committee_threshold_amount:
            LoanApprovalLog.objects.create(
                loan=loan,
                stage=LoanApprovalStage.COMMITTEE_APPROVAL,
                decision=LoanApprovalDecision.PENDING,
                note="Awaiting committee approval due to amount threshold.",
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
                "purpose": loan.purpose,
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
        try:
            from apps.notifications.models import NotificationType
            from apps.notifications.services import NotificationService

            approvers = Membership.objects.select_related("user").filter(
                chama=chama,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
                role__in=[MembershipRole.TREASURER, MembershipRole.CHAMA_ADMIN],
            )
            for approver in approvers:
                NotificationService.send_notification(
                    user=approver.user,
                    chama=chama,
                    channels=["in_app", "email"],
                    message=(
                        f"Loan review required for {member.full_name}: "
                        f"KES {loan.principal} over {loan.duration_months} month(s)."
                    ),
                    subject="Loan application submitted",
                    notification_type=NotificationType.LOAN_UPDATE,
                    idempotency_key=f"loan-requested:{loan.id}:{approver.user_id}",
                )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to notify approvers for loan request=%s", loan.id)
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
            loan.rejection_reason = note
            loan.updated_by = actor
            loan.save(update_fields=["status", "rejection_reason", "updated_by", "updated_at"])
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
        policy = FinanceService._get_loan_policy(loan.chama)

        requires_review = bool(
            (loan.loan_product and loan.loan_product.require_treasurer_review)
            or policy.require_treasurer_approval
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

        if policy.require_committee_vote and policy.committee_threshold_amount and loan.principal >= policy.committee_threshold_amount:
            committee_log = (
                LoanApprovalLog.objects.filter(
                    loan=loan,
                    stage=LoanApprovalStage.COMMITTEE_APPROVAL,
                    decision=LoanApprovalDecision.APPROVED,
                )
                .order_by("-acted_at")
                .first()
            )
            if not committee_log:
                raise FinanceServiceError("Loan requires committee approval before final approval.")

        required_guarantors = 0
        if policy.require_guarantors:
            required_guarantors = max(required_guarantors, int(policy.min_guarantors or 0))
        if policy.medium_loan_threshold and loan.principal >= policy.medium_loan_threshold:
            required_guarantors = max(required_guarantors, int(policy.medium_loan_guarantors_count or 0))
        accepted_guarantors = LoanGuarantor.objects.filter(
            loan=loan,
            status=LoanGuarantorStatus.ACCEPTED,
        )
        guaranteed_total = accepted_guarantors.aggregate(
            total=Coalesce(
                Sum("guaranteed_amount"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        guaranteed_total = to_decimal(guaranteed_total)
        if required_guarantors > 0:
            if accepted_guarantors.count() < required_guarantors:
                raise FinanceServiceError(
                    f"Loan requires at least {required_guarantors} approved guarantor(s)."
                )
            if guaranteed_total < loan.principal:
                raise FinanceServiceError(
                    "Approved guarantor coverage must fully cover the requested amount."
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
        loan.rejection_reason = note
        loan.updated_by = actor
        loan.save(update_fields=["status", "rejection_reason", "updated_by", "updated_at"])

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
        policy = FinanceService._get_loan_policy(loan.chama)
        available_liquidity = FinanceService._chama_available_liquidity(loan.chama)
        effective_lendable = to_decimal(
            available_liquidity - to_decimal(policy.reserve_liquidity_amount)
        )
        if loan.principal > effective_lendable:
            raise FinanceServiceError(
                "Chama liquidity reserve policy blocks this disbursement."
            )

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

        journal, debit_line, _credit_line = FinanceService._create_balanced_journal(
            chama=loan.chama,
            actor=actor,
            reference=disbursement_reference or f"loan:{loan.id}",
            description=f"Loan disbursement to {loan.member.full_name}",
            source_type=JournalEntrySource.LOAN,
            source_id=loan.id,
            idempotency_key=idempotency_key or f"loan_disburse:{loan.id}",
            entry_type=LedgerEntryType.LOAN_DISBURSEMENT,
            debit_account=FinanceService._get_or_create_account(
                loan.chama, "loan_receivable"
            ),
            credit_account=FinanceService._get_or_create_account(loan.chama, "cash"),
            amount=loan.principal,
            metadata={"loan_id": str(loan.id), "member_id": str(loan.member_id)},
        )
        FinanceService._refresh_financial_snapshot(loan.chama, disburse_date)
        FinanceService._recalculate_loan_balances(loan, actor=actor)

        create_audit_log(
            actor=actor,
            chama_id=loan.chama_id,
            action="loan_disbursed",
            entity_type="Loan",
            entity_id=loan.id,
            metadata={
                "principal": str(loan.principal),
                "journal_entry_id": str(journal.id),
                "idempotency_key": journal.idempotency_key,
                "disbursement_reference": loan.disbursement_reference,
            },
        )
        return LedgerPostResult(ledger_entry=debit_line, created=loan)

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
            loan.due_date = due_date
            loan.outstanding_principal = principal
            loan.outstanding_interest = to_decimal(total_interest)
            loan.outstanding_penalty = Decimal("0.00")
            loan.total_due = to_decimal(principal + total_interest)
            loan.save(
                update_fields=[
                    "due_date",
                    "outstanding_principal",
                    "outstanding_interest",
                    "outstanding_penalty",
                    "total_due",
                    "updated_at",
                ]
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
            loan.due_date = due_date
            loan.outstanding_principal = principal
            loan.outstanding_interest = Decimal("0.00")
            loan.outstanding_penalty = Decimal("0.00")
            loan.total_due = principal
            loan.save(
                update_fields=[
                    "due_date",
                    "outstanding_principal",
                    "outstanding_interest",
                    "outstanding_penalty",
                    "total_due",
                    "updated_at",
                ]
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
        total_interest = InstallmentSchedule.objects.filter(loan=loan).aggregate(
            total=Coalesce(
                Sum("expected_interest"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        loan.due_date = due_date
        loan.outstanding_principal = principal
        loan.outstanding_interest = to_decimal(total_interest)
        loan.outstanding_penalty = Decimal("0.00")
        loan.total_due = to_decimal(principal + to_decimal(total_interest))
        loan.save(
            update_fields=[
                "due_date",
                "outstanding_principal",
                "outstanding_interest",
                "outstanding_penalty",
                "total_due",
                "updated_at",
            ]
        )

    @staticmethod
    def _apply_amount_to_loan_schedule(
        *,
        loan: Loan,
        amount: Decimal,
        actor: User,
        paid_at=None,
    ) -> dict[str, Decimal]:
        remaining = to_decimal(amount)
        installments = list(
            InstallmentSchedule.objects.filter(loan=loan).order_by("due_date", "created_at")
        )
        allocation = {
            "principal": Decimal("0.00"),
            "interest": Decimal("0.00"),
            "penalty": Decimal("0.00"),
        }
        today = timezone.localdate()
        paid_at_value = paid_at or timezone.now()

        for installment in installments:
            if remaining <= Decimal("0.00"):
                break
            penalty_outstanding = to_decimal(
                installment.expected_penalty - installment.paid_penalty
            )
            if penalty_outstanding > Decimal("0.00") and remaining > Decimal("0.00"):
                applied = to_decimal(min(remaining, penalty_outstanding))
                installment.paid_penalty = to_decimal(installment.paid_penalty + applied)
                installment.paid_amount = to_decimal(installment.paid_amount + applied)
                allocation["penalty"] = to_decimal(allocation["penalty"] + applied)
                remaining = to_decimal(remaining - applied)
            interest_outstanding = to_decimal(
                installment.expected_interest - installment.paid_interest
            )
            if interest_outstanding > Decimal("0.00") and remaining > Decimal("0.00"):
                applied = to_decimal(min(remaining, interest_outstanding))
                installment.paid_interest = to_decimal(installment.paid_interest + applied)
                installment.paid_amount = to_decimal(installment.paid_amount + applied)
                allocation["interest"] = to_decimal(allocation["interest"] + applied)
                remaining = to_decimal(remaining - applied)
            principal_outstanding = to_decimal(
                installment.expected_principal - installment.paid_principal
            )
            if principal_outstanding > Decimal("0.00") and remaining > Decimal("0.00"):
                applied = to_decimal(min(remaining, principal_outstanding))
                installment.paid_principal = to_decimal(installment.paid_principal + applied)
                installment.paid_amount = to_decimal(installment.paid_amount + applied)
                allocation["principal"] = to_decimal(allocation["principal"] + applied)
                remaining = to_decimal(remaining - applied)

            expected_total = to_decimal(
                installment.expected_principal
                + installment.expected_interest
                + installment.expected_penalty
            )
            if installment.paid_amount >= expected_total:
                installment.status = InstallmentStatus.PAID
            elif installment.paid_amount > Decimal("0.00"):
                installment.status = InstallmentStatus.PARTIAL
            else:
                installment.status = InstallmentStatus.OVERDUE if installment.due_date < today else InstallmentStatus.DUE
            if installment.paid_amount > Decimal("0.00"):
                installment.paid_at = paid_at_value
            installment.updated_by = actor
            installment.save(
                update_fields=[
                    "paid_amount",
                    "paid_principal",
                    "paid_interest",
                    "paid_penalty",
                    "paid_at",
                    "status",
                    "updated_by",
                    "updated_at",
                ]
            )

        if remaining > Decimal("0.00"):
            raise FinanceServiceError(
                "Amount exceeds outstanding scheduled amounts. Use restructure or write-off workflows for excess balances."
            )
        return allocation

    @staticmethod
    def _reverse_amount_from_loan_schedule(
        *,
        loan: Loan,
        amount: Decimal,
        actor: User,
    ) -> dict[str, Decimal]:
        remaining_total = to_decimal(amount)
        if remaining_total <= Decimal("0.00"):
            raise FinanceServiceError("Repayment reversal amount must be greater than zero.")

        reversed_allocation = {
            "principal": Decimal("0.00"),
            "interest": Decimal("0.00"),
            "penalty": Decimal("0.00"),
        }
        today = timezone.localdate()
        installments = list(
            InstallmentSchedule.objects.select_for_update()
            .filter(loan=loan)
            .order_by("-due_date", "-created_at")
        )

        for installment in installments:
            if remaining_total <= Decimal("0.00"):
                break
            changed = False
            for component, paid_field in (
                ("principal", "paid_principal"),
                ("interest", "paid_interest"),
                ("penalty", "paid_penalty"),
            ):
                if remaining_total <= Decimal("0.00"):
                    continue
                paid_value = to_decimal(getattr(installment, paid_field))
                if paid_value <= Decimal("0.00"):
                    continue
                rollback_amount = to_decimal(min(remaining_total, paid_value))
                setattr(installment, paid_field, to_decimal(paid_value - rollback_amount))
                installment.paid_amount = to_decimal(installment.paid_amount - rollback_amount)
                reversed_allocation[component] = to_decimal(
                    reversed_allocation[component] + rollback_amount
                )
                remaining_total = to_decimal(remaining_total - rollback_amount)
                changed = True

            if not changed:
                continue

            expected_total = to_decimal(
                installment.expected_principal
                + installment.expected_interest
                + installment.expected_penalty
            )
            if installment.paid_amount >= expected_total:
                installment.status = InstallmentStatus.PAID
            elif installment.paid_amount > Decimal("0.00"):
                installment.status = InstallmentStatus.PARTIAL
            else:
                installment.status = (
                    InstallmentStatus.OVERDUE
                    if installment.due_date < today
                    else InstallmentStatus.DUE
                )
                installment.paid_at = None
            installment.updated_by = actor
            installment.save(
                update_fields=[
                    "paid_amount",
                    "paid_principal",
                    "paid_interest",
                    "paid_penalty",
                    "paid_at",
                    "status",
                    "updated_by",
                    "updated_at",
                ]
            )

        if remaining_total > Decimal("0.00"):
            raise FinanceServiceError(
                "Repayment reversal could not be applied safely to the loan schedule."
            )
        return reversed_allocation

    @staticmethod
    def _update_loan_status_after_repayment_change(loan: Loan, *, actor: User) -> Loan:
        has_overdue = InstallmentSchedule.objects.filter(
            loan=loan,
            status=InstallmentStatus.OVERDUE,
        ).exists()
        total_repaid = (
            loan.repayments.aggregate(
                total=Coalesce(
                    Sum("amount"),
                    Value(Decimal("0.00"), output_field=DecimalField()),
                )
            )["total"]
            or Decimal("0.00")
        )
        if loan.total_due <= Decimal("0.00"):
            loan.status = LoanStatus.PAID
            loan.repaid_at = timezone.now()
            loan.final_status = "repaid"
            loan.final_status_date = timezone.now()
            loan.final_status_by = actor
        elif loan.defaulted_at:
            loan.status = (
                LoanStatus.DEFAULTED_RECOVERING
                if to_decimal(total_repaid) > Decimal("0.00")
                else LoanStatus.DEFAULTED
            )
            loan.repaid_at = None
            loan.final_status = (
                "defaulted_recovering"
                if loan.status == LoanStatus.DEFAULTED_RECOVERING
                else "defaulted_unrecovered"
            )
            loan.final_status_date = timezone.now()
            loan.final_status_by = actor
        elif has_overdue:
            loan.status = LoanStatus.OVERDUE
            loan.repaid_at = None
            loan.final_status = "active"
            loan.final_status_date = timezone.now()
            loan.final_status_by = actor
        else:
            loan.status = LoanStatus.ACTIVE
            loan.repaid_at = None
            loan.final_status = "active"
            loan.final_status_date = timezone.now()
            loan.final_status_by = actor

        loan.updated_by = actor
        loan.save(
            update_fields=[
                "status",
                "repaid_at",
                "final_status",
                "final_status_date",
                "final_status_by",
                "updated_by",
                "updated_at",
            ]
        )
        return loan

    @staticmethod
    @transaction.atomic
    def reverse_repayment(repayment_id, payload: dict, actor: User) -> LedgerPostResult:
        repayment = get_object_or_404(
            Repayment.objects.select_for_update().select_related("loan", "loan__chama", "loan__member"),
            id=repayment_id,
        )
        loan = repayment.loan

        latest_repayment = (
            Repayment.objects.filter(loan=loan)
            .order_by("-date_paid", "-created_at")
            .first()
        )
        if not latest_repayment or latest_repayment.id != repayment.id:
            raise FinanceServiceError(
                "Only the latest loan repayment can be reversed safely."
            )

        reversal_reason = str(payload.get("reason", "")).strip()
        idempotency_key = str(payload.get("idempotency_key", "")).strip()
        if not idempotency_key:
            raise FinanceServiceError("idempotency_key is required for repayment reversal.")
        reversal_amount = to_decimal(payload.get("amount") or repayment.amount)
        if reversal_amount <= Decimal("0.00"):
            raise FinanceServiceError("Repayment reversal amount must be greater than zero.")
        if reversal_amount > to_decimal(repayment.amount):
            raise FinanceServiceError("Repayment reversal amount exceeds the repayment amount.")

        allocation = {
            "principal": to_decimal((repayment.allocation_breakdown or {}).get("principal", Decimal("0.00"))),
            "interest": to_decimal((repayment.allocation_breakdown or {}).get("interest", Decimal("0.00"))),
            "penalty": to_decimal((repayment.allocation_breakdown or {}).get("penalty", Decimal("0.00"))),
        }
        allocated_total = to_decimal(sum(allocation.values(), Decimal("0.00")))
        if allocated_total != to_decimal(repayment.amount):
            raise FinanceServiceError("Repayment allocation data is inconsistent; reversal blocked.")

        FinanceService._ensure_month_open(loan.chama, timezone.localdate())
        reversed_allocation = FinanceService._reverse_amount_from_loan_schedule(
            loan=loan,
            amount=reversal_amount,
            actor=actor,
        )

        reversal_entries: list[LedgerEntry] = []
        cash_account = FinanceService._get_or_create_account(
            loan.chama,
            FinanceService._payment_method_account_key(repayment.method),
        )
        for index, (component, account_key) in enumerate(
            (
                ("principal", "loan_receivable"),
                ("interest", "loan_interest_income"),
                ("penalty", "penalty_receivable"),
            ),
            start=1,
        ):
            component_amount = to_decimal(reversed_allocation[component])
            if component_amount <= Decimal("0.00"):
                continue
            _journal, debit_line, _credit_line = FinanceService._create_balanced_journal(
                chama=loan.chama,
                actor=actor,
                reference=f"repayment-reversal:{repayment.receipt_code}:{component}",
                description=f"Reversal of loan repayment {component} for {loan.member.full_name}",
                source_type=JournalEntrySource.ADJUSTMENT,
                source_id=repayment.id,
                idempotency_key=f"{idempotency_key}:{component}:{index}",
                entry_type=LedgerEntryType.ADJUSTMENT,
                debit_account=FinanceService._get_or_create_account(loan.chama, account_key),
                credit_account=cash_account,
                amount=component_amount,
                metadata={
                    "loan_id": str(loan.id),
                    "repayment_id": str(repayment.id),
                    "repayment_component": component,
                    "reversal_reason": reversal_reason,
                },
            )
            reversal_entries.append(debit_line)

        FinanceService._recalculate_loan_balances(loan, actor=actor)
        FinanceService._update_loan_status_after_repayment_change(loan, actor=actor)
        FinanceService._sync_member_loan_restrictions(loan, actor=actor)

        create_activity_log(
            actor=actor,
            chama_id=loan.chama_id,
            action="loan_repayment_reversed",
            entity_type="Repayment",
            entity_id=repayment.id,
            metadata={
                "loan_id": str(loan.id),
                "amount": str(reversal_amount),
                "receipt_code": repayment.receipt_code,
                "allocation_breakdown": {key: str(value) for key, value in reversed_allocation.items()},
                "reversal_entry_ids": [str(entry.id) for entry in reversal_entries],
                "reason": reversal_reason,
            },
        )

        FinanceService._refresh_financial_snapshot(loan.chama, timezone.localdate())
        return LedgerPostResult(
            ledger_entry=reversal_entries[0],
            created=repayment,
        )

    @staticmethod
    @transaction.atomic
    def post_repayment(loan_id, payload: dict, actor: User) -> LedgerPostResult:
        loan = get_object_or_404(Loan, id=loan_id)
        if loan.status in {
            LoanStatus.REJECTED,
            LoanStatus.CLEARED,
            LoanStatus.PAID,
            LoanStatus.CLOSED,
            LoanStatus.WRITTEN_OFF,
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
            allocation_breakdown={},
            recorded_by=actor,
            created_by=actor,
            updated_by=actor,
        )
        allocation = FinanceService._apply_amount_to_loan_schedule(
            loan=loan,
            amount=repayment.amount,
            actor=actor,
            paid_at=timezone.now(),
        )

        cash_account = FinanceService._get_or_create_account(
            loan.chama,
            FinanceService._payment_method_account_key(payload.get("method", MethodChoices.MPESA)),
        )
        first_ledger_entry = None
        for component, account_key in (
            ("principal", "loan_receivable"),
            ("interest", "loan_interest_income"),
            ("penalty", "penalty_receivable"),
        ):
            component_amount = to_decimal(allocation[component])
            if component_amount <= Decimal("0.00"):
                continue
            _journal, debit_line, _credit_line = FinanceService._create_balanced_journal(
                chama=loan.chama,
                actor=actor,
                reference=f"{repayment.receipt_code}:{component}",
                description=f"Loan repayment {component} from {loan.member.full_name}",
                source_type=JournalEntrySource.LOAN_REPAYMENT,
                source_id=repayment.id,
                idempotency_key=f"{payload['idempotency_key']}:{component}",
                entry_type=LedgerEntryType.LOAN_REPAYMENT,
                debit_account=cash_account,
                credit_account=FinanceService._get_or_create_account(loan.chama, account_key),
                amount=component_amount,
                metadata={
                    "loan_id": str(loan.id),
                    "member_id": str(loan.member_id),
                    "repayment_component": component,
                },
            )
            if first_ledger_entry is None:
                first_ledger_entry = debit_line

        repayment.allocation_breakdown = {
            key: str(value) for key, value in allocation.items()
        }
        repayment.updated_by = actor
        repayment.save(update_fields=["allocation_breakdown", "updated_by", "updated_at"])

        FinanceService._recalculate_loan_balances(loan, actor=actor)
        FinanceService._update_loan_status_after_repayment_change(loan, actor=actor)
        FinanceService._sync_member_loan_restrictions(loan, actor=actor)

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
                "allocation_breakdown": repayment.allocation_breakdown,
            },
        )

        FinanceService._refresh_financial_snapshot(loan.chama, date_paid)
        try:
            from apps.notifications.services import notify_loan_repayment_received

            notify_loan_repayment_received(loan, repayment)
        except Exception:  # noqa: BLE001
            logger.exception("Failed repayment notification for loan=%s", loan.id)
        return LedgerPostResult(ledger_entry=first_ledger_entry, created=repayment)

    @staticmethod
    @transaction.atomic
    def record_recovery_action(loan_id, payload: dict, actor: User) -> LoanRecoveryAction:
        loan = get_object_or_404(Loan, id=loan_id)
        action = FinanceService._record_loan_recovery_action(
            loan=loan,
            action_type=payload["action_type"],
            actor=actor,
            amount=to_decimal(payload.get("amount", Decimal("0.00"))),
            notes=str(payload.get("notes", "")).strip(),
            metadata=payload.get("metadata") or {},
        )
        metadata = payload.get("metadata") or {}
        recovery_meeting_date = metadata.get("recovery_meeting_date")
        recovery_officer_id = metadata.get("recovery_officer_id")
        loan_updated_fields: list[str] = []
        if recovery_meeting_date:
            loan.recovery_meeting_scheduled = True
            loan.recovery_meeting_date = _to_date(recovery_meeting_date)
            loan_updated_fields.extend(
                ["recovery_meeting_scheduled", "recovery_meeting_date"]
            )
        if payload.get("notes"):
            existing_notes = str(loan.recovery_notes or "").strip()
            appended_notes = str(payload["notes"]).strip()
            loan.recovery_notes = (
                f"{existing_notes}\n{appended_notes}".strip()
                if existing_notes
                else appended_notes
            )
            loan_updated_fields.append("recovery_notes")
        if recovery_officer_id:
            loan.recovery_officer_id = recovery_officer_id
            loan_updated_fields.append("recovery_officer")
        if loan.status == LoanStatus.DEFAULTED:
            FinanceService._set_loan_final_status(
                loan,
                final_status="defaulted_recovering",
                actor=actor,
            )
        if loan_updated_fields:
            loan.updated_by = actor
            loan.save(update_fields=loan_updated_fields + ["updated_by", "updated_at"])
        if action.action_type == LoanRecoveryActionType.GUARANTOR_RECOVERY and action.guarantor_id:
            guarantor = action.guarantor
            guarantor.recovery_triggered = True
            guarantor.recovery_triggered_at = timezone.now()
            guarantor.recovery_amount = to_decimal(
                guarantor.recovery_amount + action.amount
            )
            guarantor.save(
                update_fields=[
                    "recovery_triggered",
                    "recovery_triggered_at",
                    "recovery_amount",
                    "updated_at",
                ]
            )
        return action

    @staticmethod
    @transaction.atomic
    def offset_loan_from_savings(loan_id, payload: dict, actor: User) -> LedgerPostResult:
        loan = get_object_or_404(Loan, id=loan_id)
        policy = FinanceService._get_loan_policy(loan.chama)
        if not policy.allow_offset_from_savings:
            raise FinanceServiceError("Offset from savings is disabled by chama policy.")
        amount = to_decimal(payload["amount"])
        if amount > loan.total_due:
            raise FinanceServiceError("Offset amount exceeds total due.")

        allocation = FinanceService._apply_amount_to_loan_schedule(
            loan=loan,
            amount=amount,
            actor=actor,
            paid_at=timezone.now(),
        )
        first_ledger_entry = None
        offset_control_account = FinanceService._get_or_create_account(loan.chama, "adjustments")
        journal_ids = []
        for component, account_key in (
            ("principal", "loan_receivable"),
            ("interest", "loan_interest_income"),
            ("penalty", "penalty_receivable"),
        ):
            component_amount = to_decimal(allocation[component])
            if component_amount <= Decimal("0.00"):
                continue
            journal, debit_line, _credit_line = FinanceService._create_balanced_journal(
                chama=loan.chama,
                actor=actor,
                reference=f"loan-offset:{loan.id}:{component}",
                description=f"Loan offset {component} from member savings for {loan.member.full_name}",
                source_type=JournalEntrySource.ADJUSTMENT,
                source_id=loan.id,
                idempotency_key=f"{payload['idempotency_key']}:{component}",
                entry_type=LedgerEntryType.ADJUSTMENT,
                debit_account=offset_control_account,
                credit_account=FinanceService._get_or_create_account(loan.chama, account_key),
                amount=component_amount,
                metadata={"loan_id": str(loan.id), "recovery_type": "offset_from_savings", "component": component},
            )
            journal_ids.append(str(journal.id))
            if first_ledger_entry is None:
                first_ledger_entry = debit_line
        repayment = Repayment.objects.create(
            loan=loan,
            amount=amount,
            date_paid=timezone.localdate(),
            method=MethodChoices.CASH,
            receipt_code=f"OFFSET-{loan.id.hex[:12].upper()}",
            allocation_breakdown={key: str(value) for key, value in allocation.items()},
            recorded_by=actor,
            created_by=actor,
            updated_by=actor,
        )
        FinanceService._record_loan_recovery_action(
            loan=loan,
            action_type=LoanRecoveryActionType.OFFSET_FROM_SAVINGS,
            actor=actor,
            amount=amount,
            notes=str(payload.get("notes", "")).strip(),
            metadata={"journal_entry_ids": journal_ids, "repayment_id": str(repayment.id)},
        )
        FinanceService._recalculate_loan_balances(loan, actor=actor)
        if loan.total_due <= Decimal("0.00"):
            loan.status = LoanStatus.RECOVERED_FROM_OFFSET
            loan.repaid_at = timezone.now()
            loan.final_status = "repaid"
            loan.final_status_date = timezone.now()
            loan.final_status_by = actor
            loan.updated_by = actor
            loan.save(
                update_fields=[
                    "status",
                    "repaid_at",
                    "final_status",
                    "final_status_date",
                    "final_status_by",
                    "updated_by",
                    "updated_at",
                ]
            )
        FinanceService._sync_member_loan_restrictions(loan, actor=actor)
        try:
            from apps.notifications.services import notify_loan_recovery_action

            notify_loan_recovery_action(loan, LoanRecoveryActionType.OFFSET_FROM_SAVINGS)
        except Exception:  # noqa: BLE001
            logger.exception("Failed offset notification for loan=%s", loan.id)
        return LedgerPostResult(ledger_entry=first_ledger_entry, created=repayment)

    @staticmethod
    @transaction.atomic
    def write_off_loan(loan_id, payload: dict, actor: User) -> LoanRecoveryAction:
        loan = get_object_or_404(Loan, id=loan_id)
        if loan.total_due <= Decimal("0.00"):
            raise FinanceServiceError("Loan has no outstanding balance to write off.")

        if loan.outstanding_principal > Decimal("0.00"):
            FinanceService._create_balanced_journal(
                chama=loan.chama,
                actor=actor,
                reference=f"loan-writeoff-principal:{loan.id}",
                description=f"Principal write-off for {loan.member.full_name}",
                source_type=JournalEntrySource.ADJUSTMENT,
                source_id=loan.id,
                idempotency_key=f"{payload['idempotency_key']}:principal",
                entry_type=LedgerEntryType.ADJUSTMENT,
                debit_account=FinanceService._get_or_create_account(loan.chama, "adjustments"),
                credit_account=FinanceService._get_or_create_account(loan.chama, "loan_receivable"),
                amount=loan.outstanding_principal,
                metadata={"loan_id": str(loan.id), "write_off_component": "principal"},
            )
        if loan.outstanding_penalty > Decimal("0.00"):
            FinanceService._create_balanced_journal(
                chama=loan.chama,
                actor=actor,
                reference=f"loan-writeoff-penalty:{loan.id}",
                description=f"Penalty write-off for {loan.member.full_name}",
                source_type=JournalEntrySource.ADJUSTMENT,
                source_id=loan.id,
                idempotency_key=f"{payload['idempotency_key']}:penalty",
                entry_type=LedgerEntryType.ADJUSTMENT,
                debit_account=FinanceService._get_or_create_account(loan.chama, "adjustments"),
                credit_account=FinanceService._get_or_create_account(loan.chama, "penalty_receivable"),
                amount=loan.outstanding_penalty,
                metadata={"loan_id": str(loan.id), "write_off_component": "penalty"},
            )
        action = FinanceService._record_loan_recovery_action(
            loan=loan,
            action_type=LoanRecoveryActionType.WRITE_OFF,
            actor=actor,
            amount=loan.total_due,
            notes=str(payload.get("notes", "")).strip(),
            metadata={"idempotency_key": payload["idempotency_key"]},
        )
        loan.outstanding_principal = Decimal("0.00")
        loan.outstanding_interest = Decimal("0.00")
        loan.outstanding_penalty = Decimal("0.00")
        loan.total_due = Decimal("0.00")
        loan.status = LoanStatus.WRITTEN_OFF
        loan.final_status = "written_off"
        loan.final_status_date = timezone.now()
        loan.final_status_by = actor
        loan.write_off_amount = action.amount
        loan.write_off_reason = str(payload.get("notes", "")).strip()
        loan.updated_by = actor
        loan.save(
            update_fields=[
                "outstanding_principal",
                "outstanding_interest",
                "outstanding_penalty",
                "total_due",
                "status",
                "final_status",
                "final_status_date",
                "final_status_by",
                "write_off_amount",
                "write_off_reason",
                "updated_by",
                "updated_at",
            ]
        )
        FinanceService._sync_member_loan_restrictions(loan, actor=actor)
        try:
            from apps.notifications.services import notify_loan_recovery_action

            notify_loan_recovery_action(loan, LoanRecoveryActionType.WRITE_OFF)
        except Exception:  # noqa: BLE001
            logger.exception("Failed write-off notification for loan=%s", loan.id)
        return action

    @staticmethod
    @transaction.atomic
    def refresh_loan_delinquency(loan_id, *, actor: User | None = None) -> Loan:
        loan = get_object_or_404(Loan, id=loan_id)
        policy = FinanceService._get_loan_policy(loan.chama)
        today = timezone.localdate()
        previous_status = loan.status
        overdue_installments = []
        for installment in InstallmentSchedule.objects.filter(loan=loan).order_by("due_date", "created_at"):
            expected_total = to_decimal(
                installment.expected_principal + installment.expected_interest + installment.expected_penalty
            )
            if installment.paid_amount >= expected_total:
                new_status = InstallmentStatus.PAID
            elif installment.paid_amount > Decimal("0.00"):
                new_status = InstallmentStatus.PARTIAL
            elif installment.due_date < today:
                new_status = InstallmentStatus.OVERDUE
                overdue_installments.append(installment)
            else:
                new_status = InstallmentStatus.DUE
            if installment.status != new_status:
                installment.status = new_status
                installment.updated_by = actor
                installment.save(update_fields=["status", "updated_by", "updated_at"])

            if (
                new_status == InstallmentStatus.OVERDUE
                and (today - installment.due_date).days > int(policy.grace_period_days or 0)
                and installment.expected_penalty <= Decimal("0.00")
            ):
                if loan.late_penalty_type == "fixed":
                    penalty_amount = to_decimal(loan.late_penalty_value)
                else:
                    base_due = to_decimal(installment.expected_principal + installment.expected_interest)
                    penalty_rate = to_decimal(
                        loan.late_penalty_value or policy.penalty_rate,
                        precision="0.0001",
                    )
                    penalty_amount = to_decimal(base_due * (penalty_rate / Decimal("100")))
                if penalty_amount > Decimal("0.00"):
                    installment.expected_penalty = penalty_amount
                    installment.expected_amount = to_decimal(installment.expected_amount + penalty_amount)
                    installment.updated_by = actor
                    installment.save(
                        update_fields=[
                            "expected_penalty",
                            "expected_amount",
                            "updated_by",
                            "updated_at",
                        ]
                    )
                    FinanceService._create_balanced_journal(
                        chama=loan.chama,
                        actor=actor or loan.updated_by or loan.created_by,
                        reference=f"loan-penalty:{loan.id}:{installment.id}",
                        description=f"Penalty accrual for overdue installment {installment.id}",
                        source_type=JournalEntrySource.PENALTY,
                        source_id=installment.id,
                        idempotency_key=f"loan-installment-penalty:{installment.id}",
                        entry_type=LedgerEntryType.PENALTY,
                        debit_account=FinanceService._get_or_create_account(loan.chama, "penalty_receivable"),
                        credit_account=FinanceService._get_or_create_account(loan.chama, "penalty_income"),
                        amount=penalty_amount,
                        metadata={"loan_id": str(loan.id), "installment_id": str(installment.id)},
                    )
                    FinanceService._record_loan_recovery_action(
                        loan=loan,
                        action_type=LoanRecoveryActionType.PENALTY_APPLIED,
                        actor=actor,
                        amount=penalty_amount,
                        notes="Automatic overdue installment penalty applied.",
                        metadata={"installment_id": str(installment.id)},
                    )

        FinanceService._recalculate_loan_balances(loan, actor=actor)
        earliest_overdue = (
            InstallmentSchedule.objects.filter(loan=loan, status=InstallmentStatus.OVERDUE)
            .order_by("due_date")
            .first()
        )
        days_overdue = 0
        if loan.total_due <= Decimal("0.00"):
            loan.status = LoanStatus.PAID
            if not loan.repaid_at:
                loan.repaid_at = timezone.now()
            loan.escalation_level = "none"
        elif earliest_overdue:
            days_overdue = max((today - earliest_overdue.due_date).days, 0)
            if days_overdue >= int(policy.default_after_days_overdue or 30):
                loan.status = LoanStatus.DEFAULTED
                if not loan.defaulted_at:
                    loan.defaulted_at = timezone.now()
                loan.escalation_level = "recovery"
                loan.last_escalation_sent_at = timezone.now()
            else:
                loan.status = LoanStatus.OVERDUE
                loan.escalation_level = (
                    "escalated"
                    if days_overdue >= int(policy.recovery_review_after_days_overdue or 14)
                    else "reminder"
                )
                if previous_status != LoanStatus.OVERDUE:
                    loan.last_reminder_sent_at = timezone.now()
                if loan.escalation_level == "escalated":
                    loan.last_escalation_sent_at = timezone.now()
            if not loan.escalation_started_at:
                loan.escalation_started_at = timezone.now()
        else:
            next_due = InstallmentSchedule.objects.filter(
                loan=loan,
                status__in=[InstallmentStatus.DUE, InstallmentStatus.PARTIAL],
            ).order_by("due_date").first()
            if next_due and next_due.due_date <= today + timedelta(days=7):
                loan.status = LoanStatus.DUE_SOON
            else:
                loan.status = LoanStatus.ACTIVE
            loan.escalation_level = "none"
        loan.updated_by = actor
        loan.save(
            update_fields=[
                "status",
                "defaulted_at",
                "repaid_at",
                "escalation_level",
                "escalation_started_at",
                "last_reminder_sent_at",
                "last_escalation_sent_at",
                "updated_by",
                "updated_at",
            ]
        )

        FinanceService._sync_member_loan_restrictions(loan, actor=actor)
        try:
            from apps.notifications.services import (
                notify_loan_defaulted,
                notify_loan_overdue,
            )

            if loan.status == LoanStatus.DEFAULTED and previous_status != LoanStatus.DEFAULTED:
                FinanceService._record_loan_recovery_action(
                    loan=loan,
                    action_type=LoanRecoveryActionType.REMINDER,
                    actor=actor,
                    notes="Loan moved from overdue to default status.",
                    metadata={"days_overdue": days_overdue},
                )
                guarantors = FinanceService._sync_guarantor_state(
                    loan,
                    notify_guarantors=bool(policy.notify_guarantors_on_overdue),
                    trigger_recovery=True,
                )
                if guarantors:
                    FinanceService._record_loan_recovery_action(
                        loan=loan,
                        action_type=LoanRecoveryActionType.GUARANTOR_NOTIFIED,
                        actor=actor,
                        notes="Accepted guarantors were notified after loan default.",
                        metadata={"guarantor_count": len(guarantors)},
                    )
                    FinanceService._notify_guarantors(
                        loan,
                        guarantors=guarantors,
                        defaulted=True,
                    )
                    FinanceService._set_loan_final_status(
                        loan,
                        final_status="defaulted_recovering",
                        actor=actor,
                    )
                elif days_overdue >= (
                    int(policy.default_after_days_overdue or 30)
                    + max(int(policy.recovery_review_after_days_overdue or 14), 7)
                ):
                    FinanceService._set_loan_final_status(
                        loan,
                        final_status="defaulted_unrecovered",
                        actor=actor,
                    )
                notify_loan_defaulted(loan)
                FinanceService._notify_loan_admins(
                    loan,
                    subject="Loan default escalation",
                    message=(
                        f"{loan.member.full_name} has defaulted on loan {loan.id}. "
                        f"Outstanding due is KES {loan.total_due:,.2f}."
                    ),
                    idempotency_suffix="defaulted",
                )
            elif loan.status == LoanStatus.OVERDUE and previous_status != LoanStatus.OVERDUE:
                FinanceService._record_loan_recovery_action(
                    loan=loan,
                    action_type=LoanRecoveryActionType.REMINDER,
                    actor=actor,
                    notes="Loan moved into overdue status.",
                    metadata={"days_overdue": days_overdue},
                )
                guarantors = FinanceService._sync_guarantor_state(
                    loan,
                    notify_guarantors=bool(policy.notify_guarantors_on_overdue),
                    trigger_recovery=False,
                )
                if guarantors and policy.notify_guarantors_on_overdue:
                    FinanceService._record_loan_recovery_action(
                        loan=loan,
                        action_type=LoanRecoveryActionType.GUARANTOR_NOTIFIED,
                        actor=actor,
                        notes="Accepted guarantors were notified after loan became overdue.",
                        metadata={"guarantor_count": len(guarantors)},
                    )
                    FinanceService._notify_guarantors(
                        loan,
                        guarantors=guarantors,
                        defaulted=False,
                    )
                notify_loan_overdue(loan)
                FinanceService._notify_loan_admins(
                    loan,
                    subject="Loan overdue alert",
                    message=(
                        f"{loan.member.full_name} has an overdue loan {loan.id}. "
                        f"Outstanding due is KES {loan.total_due:,.2f}."
                    ),
                    idempotency_suffix="overdue",
                )
        except Exception:  # noqa: BLE001
            logger.exception("Failed delinquency notification for loan=%s", loan.id)
        if loan.status == LoanStatus.PAID:
            FinanceService._set_loan_final_status(
                loan,
                final_status="repaid",
                actor=actor,
            )
        elif loan.status == LoanStatus.DEFAULTED:
            recovery_started = loan.recovery_meeting_scheduled or loan.recovery_actions.exclude(
                action_type__in=[
                    LoanRecoveryActionType.REMINDER,
                    LoanRecoveryActionType.PENALTY_APPLIED,
                ]
            ).exists()
            if recovery_started:
                FinanceService._set_loan_final_status(
                    loan,
                    final_status="defaulted_recovering",
                    actor=actor,
                )
            elif days_overdue >= (
                int(policy.default_after_days_overdue or 30)
                + max(int(policy.recovery_review_after_days_overdue or 14), 7)
            ):
                FinanceService._set_loan_final_status(
                    loan,
                    final_status="defaulted_unrecovered",
                    actor=actor,
                )
        elif loan.status in {LoanStatus.ACTIVE, LoanStatus.DUE_SOON, LoanStatus.OVERDUE}:
            FinanceService._set_loan_final_status(
                loan,
                final_status="active",
                actor=actor,
            )
        return loan

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

        journal, debit_line, _credit_line = FinanceService._create_balanced_journal(
            chama=chama,
            actor=actor,
            reference=f"penalty:{penalty.id}",
            description=f"Penalty issued to {member.full_name}: {penalty.reason[:80]}",
            source_type=JournalEntrySource.PENALTY,
            source_id=penalty.id,
            idempotency_key=payload["idempotency_key"],
            entry_type=LedgerEntryType.PENALTY,
            debit_account=FinanceService._get_or_create_account(chama, "penalty_receivable"),
            credit_account=FinanceService._get_or_create_account(chama, "penalty_income"),
            amount=penalty.amount,
            metadata={"penalty_id": str(penalty.id), "member_id": str(member.id)},
        )
        create_audit_log(
            actor=actor,
            chama_id=chama.id,
            action="penalty_issued",
            entity_type="Penalty",
            entity_id=penalty.id,
            metadata={"journal_entry_id": str(journal.id), "amount": str(penalty.amount)},
        )
        return LedgerPostResult(ledger_entry=debit_line, created=penalty)

    @staticmethod
    @transaction.atomic
    def mark_penalty_paid(penalty_id, payload: dict, actor: User) -> LedgerPostResult:
        penalty = get_object_or_404(Penalty, id=penalty_id)
        if penalty.status not in {PenaltyStatus.UNPAID, PenaltyStatus.PARTIAL}:
            raise FinanceServiceError("Only unpaid or partially paid penalties can be marked paid.")

        payment_amount = to_decimal(payload.get("amount") or penalty.outstanding_amount)
        if payment_amount <= Decimal("0.00"):
            raise FinanceServiceError("Penalty payment amount must be greater than zero.")
        if payment_amount > penalty.outstanding_amount:
            raise FinanceServiceError("Penalty payment amount exceeds the outstanding balance.")

        penalty.amount_paid = to_decimal(penalty.amount_paid + payment_amount)
        penalty.status = (
            PenaltyStatus.PAID
            if penalty.amount_paid >= penalty.amount
            else PenaltyStatus.PARTIAL
        )
        penalty.resolved_by = actor
        penalty.resolved_at = timezone.now() if penalty.status == PenaltyStatus.PAID else None
        penalty.updated_by = actor
        penalty.save(
            update_fields=[
                "amount_paid",
                "status",
                "resolved_by",
                "resolved_at",
                "updated_by",
                "updated_at",
            ]
        )

        _journal, debit_line, _credit_line = FinanceService._create_balanced_journal(
            chama=penalty.chama,
            actor=actor,
            reference=f"penalty-paid:{penalty.id}",
            description=f"Penalty paid by {penalty.member.full_name}",
            source_type=JournalEntrySource.PENALTY,
            source_id=penalty.id,
            idempotency_key=payload["idempotency_key"],
            entry_type=LedgerEntryType.PENALTY,
            debit_account=FinanceService._get_or_create_account(
                penalty.chama,
                FinanceService._payment_method_account_key(payload.get("method", MethodChoices.MPESA)),
            ),
            credit_account=FinanceService._get_or_create_account(penalty.chama, "penalty_receivable"),
            amount=payment_amount,
            metadata={
                "penalty_id": str(penalty.id),
                "member_id": str(penalty.member_id),
                "amount_paid": str(payment_amount),
                "penalty_status": penalty.status,
                "outstanding_amount": str(penalty.outstanding_amount),
            },
        )
        return LedgerPostResult(ledger_entry=debit_line, created=penalty)

    @staticmethod
    @transaction.atomic
    def waive_penalty(penalty_id, actor: User) -> LedgerPostResult:
        penalty = get_object_or_404(Penalty, id=penalty_id)
        if penalty.status not in {PenaltyStatus.UNPAID, PenaltyStatus.PARTIAL}:
            raise FinanceServiceError("Only unpaid or partially paid penalties can be waived.")

        penalty.status = PenaltyStatus.WAIVED
        waived_amount = penalty.outstanding_amount
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

        _journal, debit_line, _credit_line = FinanceService._create_balanced_journal(
            chama=penalty.chama,
            actor=actor,
            reference=f"penalty-waive:{penalty.id}",
            description=f"Penalty waived for {penalty.member.full_name}",
            source_type=JournalEntrySource.ADJUSTMENT,
            source_id=penalty.id,
            idempotency_key=f"penalty_waive:{penalty.id}",
            entry_type=LedgerEntryType.ADJUSTMENT,
            debit_account=FinanceService._get_or_create_account(penalty.chama, "penalty_income"),
            credit_account=FinanceService._get_or_create_account(penalty.chama, "penalty_receivable"),
            amount=waived_amount,
            metadata={
                "penalty_id": str(penalty.id),
                "member_id": str(penalty.member_id),
                "waived_amount": str(waived_amount),
            },
        )
        return LedgerPostResult(ledger_entry=debit_line, created=penalty)

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

        if adjustment.direction == LedgerDirection.CREDIT:
            debit_account = FinanceService._get_or_create_account(chama, "cash")
            credit_account = FinanceService._get_or_create_account(chama, "adjustments")
        else:
            debit_account = FinanceService._get_or_create_account(chama, "adjustments")
            credit_account = FinanceService._get_or_create_account(chama, "cash")

        journal, debit_line, _credit_line = FinanceService._create_balanced_journal(
            chama=chama,
            actor=actor,
            reference=f"adjustment:{adjustment.id}",
            description=adjustment.reason,
            source_type=JournalEntrySource.ADJUSTMENT,
            source_id=adjustment.id,
            idempotency_key=payload["idempotency_key"],
            entry_type=LedgerEntryType.ADJUSTMENT,
            debit_account=debit_account,
            credit_account=credit_account,
            amount=adjustment.amount,
            metadata={"direction": adjustment.direction},
        )
        FinanceService._refresh_financial_snapshot(chama)

        create_audit_log(
            actor=actor,
            chama_id=chama.id,
            action="finance_manual_adjustment",
            entity_type="ManualAdjustment",
            entity_id=adjustment.id,
            metadata={
                "direction": adjustment.direction,
                "amount": str(adjustment.amount),
                "journal_entry_id": str(journal.id),
                "idempotency_key": adjustment.idempotency_key,
            },
        )

        return LedgerPostResult(ledger_entry=debit_line, created=adjustment)

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
                status__in=[
                    InstallmentStatus.DUE,
                    InstallmentStatus.PARTIAL,
                    InstallmentStatus.OVERDUE,
                ],
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

        total_contributions = FinanceService._net_contribution_sum(contributions_qs)
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
            status__in=[PenaltyStatus.PARTIAL, PenaltyStatus.PAID, PenaltyStatus.WAIVED]
        ).aggregate(
            total=Coalesce(
                Sum("amount_paid"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]

        contributions = [
            {
                "id": str(obj.id),
                "amount": str(obj.amount),
                "refunded_amount": str(to_decimal(obj.refunded_amount)),
                "net_amount": str(to_decimal(obj.amount - obj.refunded_amount)),
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
    def compute_loan_application_queue(chama_id, *, mask_members: bool = False):
        chama = get_object_or_404(Chama, id=chama_id)
        applications = (
            LoanApplication.objects.select_related(
                "member",
                "loan_product",
                "reviewed_by",
                "approved_by",
                "created_loan",
            )
            .prefetch_related("guarantors", "approval_logs")
            .filter(chama=chama)
            .order_by("-submitted_at", "-created_at")
        )
        status_counts = {
            row["status"]: row["count"]
            for row in applications.values("status").annotate(count=Count("id"))
        }
        results = []
        for application in applications:
            member_name = application.member.full_name
            member_phone = application.member.phone
            if mask_members:
                member_name = f"Member #{str(application.member_id)[:8]}"
                member_phone = f"***{member_phone[-4:]}" if member_phone else ""
            accepted_guarantors = application.guarantors.filter(
                status=LoanGuarantorStatus.ACCEPTED
            )
            guaranteed_total = accepted_guarantors.aggregate(
                total=Coalesce(
                    Sum("guaranteed_amount"),
                    Value(Decimal("0.00"), output_field=DecimalField()),
                )
            )["total"]
            results.append(
                {
                    "id": str(application.id),
                    "member_id": str(application.member_id),
                    "member_name": member_name,
                    "member_phone": member_phone,
                    "loan_product_name": (
                        application.loan_product.name if application.loan_product else ""
                    ),
                    "requested_amount": str(to_decimal(application.requested_amount)),
                    "requested_term_months": application.requested_term_months,
                    "purpose": application.purpose,
                    "status": application.status,
                    "submitted_at": application.submitted_at.isoformat(),
                    "reviewed_at": (
                        application.reviewed_at.isoformat()
                        if application.reviewed_at
                        else None
                    ),
                    "approved_at": (
                        application.approved_at.isoformat()
                        if application.approved_at
                        else None
                    ),
                    "created_loan_id": (
                        str(application.created_loan_id)
                        if application.created_loan_id
                        else None
                    ),
                    "guarantor_count": application.guarantors.count(),
                    "accepted_guarantor_count": accepted_guarantors.count(),
                    "guaranteed_total": str(to_decimal(guaranteed_total)),
                    "eligibility_status": application.eligibility_status,
                    "recommended_max_amount": str(
                        to_decimal(application.recommended_max_amount)
                    ),
                    "rejection_reason": application.rejection_reason,
                }
            )
        return {
            "chama_id": str(chama.id),
            "count": len(results),
            "status_counts": status_counts,
            "results": results,
        }

    @staticmethod
    def compute_loan_portfolio(chama_id, *, mask_members: bool = False):
        chama = get_object_or_404(Chama, id=chama_id)
        policy = FinanceService._get_loan_policy(chama)
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

        guarantor_exposure = list(
            LoanGuarantor.objects.filter(
                loan__chama=chama,
                status=LoanGuarantorStatus.ACCEPTED,
            )
            .values("guarantor_id", "guarantor__full_name")
            .annotate(
                guaranteed_total=Coalesce(
                    Sum("guaranteed_amount"),
                    Value(Decimal("0.00"), output_field=DecimalField()),
                )
            )
            .order_by("-guaranteed_total", "guarantor__full_name")
        )
        penalties_collected = Penalty.objects.filter(
            chama=chama,
            status__in=[PenaltyStatus.PARTIAL, PenaltyStatus.PAID, PenaltyStatus.WAIVED],
        ).aggregate(
            total=Coalesce(
                Sum("amount_paid"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        recovery_summary = list(
            LoanRecoveryAction.objects.filter(loan__chama=chama)
            .values("action_type")
            .annotate(count=Coalesce(Count("id"), 0))
            .order_by("action_type")
        )
        aging = {
            "current": {"count": 0, "outstanding": Decimal("0.00")},
            "1_30": {"count": 0, "outstanding": Decimal("0.00")},
            "31_60": {"count": 0, "outstanding": Decimal("0.00")},
            "61_90": {"count": 0, "outstanding": Decimal("0.00")},
            "90_plus": {"count": 0, "outstanding": Decimal("0.00")},
        }
        for loan in list(loans):
            if loan.total_due <= Decimal("0.00"):
                continue
            overdue = (
                loan.installments.filter(status=InstallmentStatus.OVERDUE)
                .order_by("due_date")
                .first()
            )
            if not overdue:
                bucket = "current"
            else:
                days = max((timezone.localdate() - overdue.due_date).days, 0)
                if days <= 30:
                    bucket = "1_30"
                elif days <= 60:
                    bucket = "31_60"
                elif days <= 90:
                    bucket = "61_90"
                else:
                    bucket = "90_plus"
            aging[bucket]["count"] += 1
            aging[bucket]["outstanding"] = to_decimal(
                aging[bucket]["outstanding"] + to_decimal(loan.total_due)
            )

        liquidity_available = FinanceService._chama_available_liquidity(chama)
        liquidity_exposure = to_decimal(
            max(liquidity_available - to_decimal(policy.reserve_liquidity_amount), Decimal("0.00"))
        )

        return {
            "chama_id": str(chama.id),
            "total_loans_out": str(total_loans_out),
            "total_repayments": str(total_repayments),
            "outstanding": str(outstanding),
            "overdue_count": overdue_loans.count(),
            "defaulters_count": len(defaulters_by_loan),
            "repayment_rate_percent": str(to_decimal(repayment_rate)),
            "defaulters": list(defaulters_by_loan.values()),
            "guarantor_exposure": [
                {
                    "guarantor_id": str(row["guarantor_id"]),
                    "guarantor_name": row["guarantor__full_name"],
                    "guaranteed_total": str(to_decimal(row["guaranteed_total"])),
                }
                for row in guarantor_exposure
            ],
            "penalties_collected": str(to_decimal(penalties_collected)),
            "recovery_summary": recovery_summary,
            "aging": {
                key: {
                    "count": value["count"],
                    "outstanding": str(to_decimal(value["outstanding"])),
                }
                for key, value in aging.items()
            },
            "liquidity": {
                "available_cash": str(liquidity_available),
                "reserve_requirement": str(to_decimal(policy.reserve_liquidity_amount)),
                "lendable_liquidity": str(liquidity_exposure),
            },
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
            status__in=[PenaltyStatus.UNPAID, PenaltyStatus.PARTIAL],
        ).aggregate(
            total=Coalesce(
                Sum(F("amount") - F("amount_paid")),
                Value(Decimal("0.00"), output_field=DecimalField()),
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

    @staticmethod
    def _resolve_expense_category(
        *,
        chama: Chama,
        payload: dict,
        actor: User,
    ) -> tuple[ExpenseCategory | None, str]:
        category_id = payload.get("category_id")
        category_name = str(payload.get("category", "")).strip()

        if category_id:
            category = get_object_or_404(
                ExpenseCategory.objects.filter(chama=chama, is_active=True),
                id=category_id,
            )
            return category, category.name

        if not category_name:
            return None, ""

        category, _created = ExpenseCategory.objects.get_or_create(
            chama=chama,
            name=category_name,
            defaults={
                "description": "",
                "created_by": actor,
                "updated_by": actor,
            },
        )
        return category, category.name

    @staticmethod
    def _notify_expense_reviewers(expense: Expense, *, actor: User) -> int:
        try:
            from apps.notifications.models import NotificationType
            from apps.notifications.services import NotificationService
        except Exception:  # noqa: BLE001
            return 0

        reviewers = Membership.objects.select_related("user").filter(
            chama=expense.chama,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            role__in=[MembershipRole.TREASURER, MembershipRole.CHAMA_ADMIN],
        )
        sent = 0
        for reviewer in reviewers:
            if reviewer.user_id == actor.id:
                continue
            NotificationService.send_notification(
                user=reviewer.user,
                chama=expense.chama,
                channels=["in_app", "email"],
                message=(
                    f"Expense request for KES {expense.amount:,.2f} needs review. "
                    f"{expense.description}"
                ),
                subject="Expense review required",
                notification_type=NotificationType.PAYMENT_CONFIRMATION,
                idempotency_key=f"expense-review:{expense.id}:{reviewer.user_id}",
                actor=actor,
            )
            sent += 1
        return sent

    @staticmethod
    def _notify_expense_requester(
        expense: Expense,
        *,
        actor: User,
        subject: str,
        message: str,
        suffix: str,
    ) -> None:
        if not expense.requested_by_id:
            return
        try:
            from apps.notifications.models import NotificationType
            from apps.notifications.services import NotificationService

            NotificationService.send_notification(
                user=expense.requested_by,
                chama=expense.chama,
                channels=["in_app", "email"],
                message=message,
                subject=subject,
                notification_type=NotificationType.PAYMENT_CONFIRMATION,
                idempotency_key=f"expense-requester:{expense.id}:{suffix}",
                actor=actor,
            )
        except Exception:  # noqa: BLE001
            return

    @staticmethod
    @transaction.atomic
    def submit_expense(payload: dict, actor: User) -> Expense:
        chama = get_object_or_404(Chama, id=payload["chama_id"])
        expense_date = _to_date(payload.get("expense_date") or timezone.localdate())
        FinanceService._ensure_month_open(chama, expense_date)

        request_key = str(payload.get("idempotency_key") or "").strip()
        if request_key and Expense.objects.filter(
            chama=chama,
            metadata__idempotency_key=request_key,
        ).exists():
            raise IdempotencyConflictError("Duplicate expense request idempotency_key.")

        category_ref, category_name = FinanceService._resolve_expense_category(
            chama=chama,
            payload=payload,
            actor=actor,
        )

        expense = Expense.objects.create(
            chama=chama,
            requested_by=actor,
            category_ref=category_ref,
            description=payload["description"],
            category=category_name,
            amount=to_decimal(payload["amount"]),
            expense_date=expense_date,
            status=ExpenseStatus.PENDING,
            vendor_name=str(payload.get("vendor_name", "")).strip(),
            receipt_file=payload.get("receipt_file"),
            receipt_reference=str(payload.get("receipt_reference", "")).strip(),
            notes=str(payload.get("notes", "")).strip(),
            metadata={
                **(payload.get("metadata") or {}),
                "idempotency_key": request_key,
            },
            created_by=actor,
            updated_by=actor,
        )

        create_activity_log(
            actor=actor,
            chama_id=chama.id,
            action="expense_submitted",
            entity_type="Expense",
            entity_id=expense.id,
            metadata={
                "amount": str(expense.amount),
                "category": expense.category,
                "note": expense.notes,
            },
        )
        create_audit_log(
            actor=actor,
            chama_id=chama.id,
            action="expense_request_created",
            entity_type="Expense",
            entity_id=expense.id,
            metadata={
                "status": expense.status,
                "amount": str(expense.amount),
                "expense_date": expense.expense_date.isoformat(),
                "idempotency_key": request_key,
            },
        )
        FinanceService._notify_expense_reviewers(expense, actor=actor)
        return expense

    @staticmethod
    @transaction.atomic
    def approve_expense(*, expense_id, payload: dict, actor: User) -> Expense:
        expense = get_object_or_404(Expense.objects.select_related("chama", "requested_by"), id=expense_id)
        if expense.status not in {ExpenseStatus.PENDING, ExpenseStatus.REJECTED}:
            raise FinanceServiceError("Only pending or rejected expenses can be approved.")

        expense.status = ExpenseStatus.APPROVED
        expense.approved_by = actor
        expense.approved_at = timezone.now()
        expense.rejected_by = None
        expense.rejected_at = None
        expense.rejection_reason = ""
        if payload.get("note"):
            expense.notes = str(payload.get("note")).strip()
        expense.updated_by = actor
        expense.save(
            update_fields=[
                "status",
                "approved_by",
                "approved_at",
                "rejected_by",
                "rejected_at",
                "rejection_reason",
                "notes",
                "updated_by",
                "updated_at",
            ]
        )

        create_activity_log(
            actor=actor,
            chama_id=expense.chama_id,
            action="expense_approved",
            entity_type="Expense",
            entity_id=expense.id,
            metadata={"note": str(payload.get("note", "")).strip()},
        )
        create_audit_log(
            actor=actor,
            chama_id=expense.chama_id,
            action="expense_request_approved",
            entity_type="Expense",
            entity_id=expense.id,
            metadata={"status": expense.status, "note": str(payload.get("note", "")).strip()},
        )
        FinanceService._notify_expense_requester(
            expense,
            actor=actor,
            subject="Expense approved",
            message=(
                f"Your expense request for KES {expense.amount:,.2f} was approved "
                "and is ready for payment."
            ),
            suffix="approved",
        )
        return expense

    @staticmethod
    @transaction.atomic
    def reject_expense(*, expense_id, payload: dict, actor: User) -> Expense:
        expense = get_object_or_404(Expense.objects.select_related("chama", "requested_by"), id=expense_id)
        if expense.status == ExpenseStatus.PAID:
            raise FinanceServiceError("Paid expenses cannot be rejected.")
        if expense.status == ExpenseStatus.CANCELLED:
            raise FinanceServiceError("Cancelled expenses cannot be rejected.")

        reason = str(payload.get("note", "")).strip() or "Rejected by reviewer."
        expense.status = ExpenseStatus.REJECTED
        expense.rejected_by = actor
        expense.rejected_at = timezone.now()
        expense.rejection_reason = reason
        expense.approved_by = None
        expense.approved_at = None
        expense.updated_by = actor
        expense.save(
            update_fields=[
                "status",
                "rejected_by",
                "rejected_at",
                "rejection_reason",
                "approved_by",
                "approved_at",
                "updated_by",
                "updated_at",
            ]
        )

        create_activity_log(
            actor=actor,
            chama_id=expense.chama_id,
            action="expense_rejected",
            entity_type="Expense",
            entity_id=expense.id,
            metadata={"reason": reason},
        )
        create_audit_log(
            actor=actor,
            chama_id=expense.chama_id,
            action="expense_request_rejected",
            entity_type="Expense",
            entity_id=expense.id,
            metadata={"status": expense.status, "reason": reason},
        )
        FinanceService._notify_expense_requester(
            expense,
            actor=actor,
            subject="Expense rejected",
            message=(
                f"Your expense request for KES {expense.amount:,.2f} was rejected. "
                f"Reason: {reason}"
            ),
            suffix="rejected",
        )
        return expense

    @staticmethod
    @transaction.atomic
    def mark_expense_paid(*, expense_id, payload: dict, actor: User) -> LedgerPostResult:
        expense = get_object_or_404(Expense.objects.select_related("chama", "requested_by"), id=expense_id)
        if expense.status != ExpenseStatus.APPROVED:
            raise FinanceServiceError("Only approved expenses can be paid.")
        if expense.journal_entry_id:
            raise FinanceServiceError("Expense payment has already been posted.")

        FinanceService._ensure_month_open(expense.chama, expense.expense_date)
        payment_reference = str(payload.get("payment_reference", "")).strip()
        idempotency_key = (
            str(payload.get("idempotency_key") or "").strip() or f"expense-pay:{expense.id}"
        )
        note = str(payload.get("note", "")).strip()

        journal, debit_line, _credit_line = FinanceService._create_balanced_journal(
            chama=expense.chama,
            actor=actor,
            reference=payment_reference or expense.receipt_reference or f"expense:{expense.id}",
            description=expense.description,
            source_type=JournalEntrySource.EXPENSE,
            source_id=expense.id,
            idempotency_key=idempotency_key,
            entry_type=LedgerEntryType.EXPENSE,
            debit_account=FinanceService._get_or_create_account(expense.chama, "expense_control"),
            credit_account=FinanceService._get_or_create_account(expense.chama, "cash"),
            amount=expense.amount,
            metadata={
                "category": expense.category,
                "vendor_name": expense.vendor_name,
                "payment_reference": payment_reference,
                "note": note,
            },
        )
        expense.status = ExpenseStatus.PAID
        expense.paid_by = actor
        expense.paid_at = timezone.now()
        expense.payment_reference = payment_reference
        expense.journal_entry = journal
        if note:
            expense.notes = note
        expense.updated_by = actor
        expense.save(
            update_fields=[
                "status",
                "paid_by",
                "paid_at",
                "payment_reference",
                "journal_entry",
                "notes",
                "updated_by",
                "updated_at",
            ]
        )
        FinanceService._refresh_financial_snapshot(expense.chama, expense.expense_date)

        create_activity_log(
            actor=actor,
            chama_id=expense.chama_id,
            action="expense_paid",
            entity_type="Expense",
            entity_id=expense.id,
            metadata={
                "payment_reference": payment_reference,
                "note": note,
                "journal_entry_id": str(journal.id),
                "ledger_entry_id": str(debit_line.id),
            },
        )
        create_audit_log(
            actor=actor,
            chama_id=expense.chama_id,
            action="expense_payment_posted",
            entity_type="Expense",
            entity_id=expense.id,
            metadata={
                "status": expense.status,
                "payment_reference": payment_reference,
                "journal_entry_id": str(journal.id),
                "ledger_entry_id": str(debit_line.id),
                "idempotency_key": idempotency_key,
            },
        )
        FinanceService._notify_expense_requester(
            expense,
            actor=actor,
            subject="Expense paid",
            message=(
                f"Your expense request for KES {expense.amount:,.2f} has been paid "
                "and posted to the chama ledger."
            ),
            suffix="paid",
        )
        return LedgerPostResult(ledger_entry=debit_line, created=expense)

    @staticmethod
    @transaction.atomic
    def create_expense(payload: dict, actor: User) -> Expense:
        return FinanceService.submit_expense(payload, actor)

    @staticmethod
    def finance_summary(chama_id):
        chama = get_object_or_404(Chama, id=chama_id)
        snapshot = FinancialSnapshot.objects.filter(chama=chama).order_by("-snapshot_date").first()
        if not snapshot:
            snapshot = FinanceService._refresh_financial_snapshot(chama)
        return {
            "chama_id": str(chama.id),
            "snapshot_date": snapshot.snapshot_date.isoformat(),
            "total_balance": str(snapshot.total_balance),
            "total_contributions": str(snapshot.total_contributions),
            "total_loans": str(snapshot.total_loans),
            "total_expenses": str(snapshot.total_expenses),
            "currency": CurrencyChoices.KES,
        }

    @staticmethod
    def finance_reports(chama_id):
        chama = get_object_or_404(Chama, id=chama_id)
        snapshot = FinancialSnapshot.objects.filter(chama=chama).order_by("-snapshot_date").first()
        if not snapshot:
            snapshot = FinanceService._refresh_financial_snapshot(chama)
        return {
            "summary": FinanceService.finance_summary(chama_id),
            "accounts": [
                {
                    "code": account.code,
                    "name": account.name,
                    "type": account.type,
                }
                for account in Account.objects.filter(chama=chama, is_active=True).order_by("type", "code")
            ],
            "latest_snapshot_id": str(snapshot.id),
        }

    @staticmethod
    def member_contributions(chama_id):
        return list(
            Contribution.objects.filter(chama_id=chama_id)
            .values("member_id", "member__full_name")
            .annotate(
                total=Coalesce(
                    Sum(F("amount") - F("refunded_amount")),
                    Value(Decimal("0.00"), output_field=DecimalField()),
                )
            )
            .order_by("-total", "member__full_name")
        )


# Legacy re-export for older modules expecting LedgerService in this module.
from apps.finance.ledger_service import LedgerService  # noqa: E402  # isort: skip
