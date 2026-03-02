from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView

from apps.chama.models import MemberStatus, Membership, MembershipRole
from apps.chama.services import get_effective_role
from apps.finance.forms import ContributionForm, LoanForm
from apps.finance.models import (
    Contribution,
    ContributionType,
    InstallmentStatus,
    LedgerDirection,
    Loan,
    LoanProduct,
    LoanStatus,
    ManualAdjustment,
    Repayment,
)
from apps.finance.services import FinanceService, FinanceServiceError, IdempotencyConflictError

MANAGER_ROLES = {
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.TREASURER,
    MembershipRole.SECRETARY,
    MembershipRole.AUDITOR,
}


@dataclass
class TransactionRow:
    id: str
    date: datetime
    type: str
    description: str
    amount: Decimal
    reference: str
    receipt: str | None = None


def _resolve_membership(request):
    scoped_chama_id = request.GET.get("chama_id") or request.session.get("active_chama_id")
    memberships = Membership.objects.select_related("chama").filter(
        user=request.user,
        is_active=True,
        is_approved=True,
        status=MemberStatus.ACTIVE,
        exited_at__isnull=True,
    )

    membership = None
    if scoped_chama_id:
        membership = memberships.filter(chama_id=scoped_chama_id).first()

    if membership is None:
        membership = memberships.order_by("joined_at").first()

    if membership:
        request.session["active_chama_id"] = str(membership.chama_id)

    return membership


def _parse_decimal(raw_value, *, default: Decimal | None = None) -> Decimal | None:
    if raw_value in (None, ""):
        return default
    try:
        return Decimal(str(raw_value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _loan_ui_status(raw_status: str) -> str:
    mapping = {
        LoanStatus.REQUESTED: "pending",
        LoanStatus.APPROVED: "approved",
        LoanStatus.DISBURSING: "active",
        LoanStatus.DISBURSED: "active",
        LoanStatus.ACTIVE: "active",
        LoanStatus.PAID: "completed",
        LoanStatus.CLOSED: "completed",
        LoanStatus.CLEARED: "completed",
        LoanStatus.DEFAULTED: "defaulted",
        LoanStatus.REJECTED: "rejected",
    }
    return mapping.get(raw_status, raw_status)


@method_decorator(login_required, name="dispatch")
class ContributionFormView(TemplateView):
    template_name = "finance/contribution_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["title"] = "Make Contribution"
        context["form"] = ContributionForm()
        context["active_membership"] = membership

        if not membership:
            context["contribution_types"] = []
            context["monthly_total"] = Decimal("0.00")
            return context

        contribution_types = ContributionType.objects.filter(
            chama=membership.chama,
            is_active=True,
        ).order_by("name")
        month_start = timezone.localdate().replace(day=1)
        monthly_total = Contribution.objects.filter(
            chama=membership.chama,
            member=self.request.user,
            date_paid__gte=month_start,
        ).aggregate(
            total=Coalesce(
                Sum("amount"),
                Value(Decimal("0.00"), output_field=DecimalField(max_digits=12, decimal_places=2)),
            )
        )["total"]

        context["contribution_types"] = contribution_types
        context["monthly_total"] = monthly_total
        return context

    def post(self, request, *args, **kwargs):
        membership = _resolve_membership(request)
        if not membership:
            messages.error(request, "You must be an approved active member of a chama.")
            return redirect("chama:chama_create")

        amount = _parse_decimal(request.POST.get("amount"))
        if not amount or amount <= Decimal("0.00"):
            messages.error(request, "Please provide a valid contribution amount.")
            return redirect("finance:contribution_form")

        contribution_type_id = request.POST.get("contribution_type")
        contribution_type = None
        if contribution_type_id:
            contribution_type = ContributionType.objects.filter(
                id=contribution_type_id,
                chama=membership.chama,
                is_active=True,
            ).first()
        if contribution_type is None:
            contribution_type = (
                ContributionType.objects.filter(chama=membership.chama, is_active=True)
                .order_by("name")
                .first()
            )

        if contribution_type is None:
            messages.error(request, "No active contribution type is configured for this chama.")
            return redirect("finance:contribution_form")

        method = request.POST.get("payment_method") or request.POST.get("method") or "mpesa"
        receipt_code = request.POST.get("receipt_code") or f"CNT-{timezone.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6].upper()}"
        idempotency_key = f"web-contribution:{membership.chama_id}:{request.user.id}:{uuid.uuid4().hex}"

        payload = {
            "chama_id": str(membership.chama_id),
            "member_id": str(request.user.id),
            "contribution_type_id": str(contribution_type.id),
            "amount": str(amount),
            "date_paid": timezone.localdate().isoformat(),
            "method": method,
            "receipt_code": receipt_code,
            "idempotency_key": idempotency_key,
        }

        try:
            FinanceService.post_contribution(payload, actor=request.user)
        except (FinanceServiceError, IdempotencyConflictError) as exc:
            messages.error(request, str(exc))
            return redirect("finance:contribution_form")

        messages.success(request, "Contribution submitted successfully.")
        return redirect("finance:transaction_history")


@method_decorator(login_required, name="dispatch")
class LoanApplicationView(TemplateView):
    template_name = "finance/loan_application.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["title"] = "Apply for Loan"
        context["form"] = LoanForm()
        context["active_membership"] = membership

        if not membership:
            context["loan_products"] = []
            return context

        loan_products = LoanProduct.objects.filter(chama=membership.chama, is_active=True).order_by("name")
        context["loan_products"] = loan_products
        context["default_loan_product"] = loan_products.filter(is_default=True).first() or loan_products.first()
        return context

    def post(self, request, *args, **kwargs):
        membership = _resolve_membership(request)
        if not membership:
            messages.error(request, "You must be an approved active member of a chama.")
            return redirect("chama:chama_create")

        principal = _parse_decimal(request.POST.get("amount") or request.POST.get("principal"))
        duration_raw = request.POST.get("repayment_period") or request.POST.get("duration_months")
        try:
            duration_months = int(duration_raw)
        except (TypeError, ValueError):
            duration_months = 0

        if not principal or principal <= Decimal("0.00"):
            messages.error(request, "Please provide a valid loan amount.")
            return redirect("finance:loan_application")
        if duration_months <= 0:
            messages.error(request, "Please provide a valid repayment period in months.")
            return redirect("finance:loan_application")

        loan_product = None
        loan_product_id = request.POST.get("loan_product_id")
        if loan_product_id:
            loan_product = LoanProduct.objects.filter(
                id=loan_product_id,
                chama=membership.chama,
                is_active=True,
            ).first()
        if loan_product is None:
            loan_product = (
                LoanProduct.objects.filter(chama=membership.chama, is_active=True, is_default=True)
                .order_by("created_at")
                .first()
                or LoanProduct.objects.filter(chama=membership.chama, is_active=True).order_by("created_at").first()
            )

        if loan_product is None:
            messages.error(request, "No active loan policy is configured for this chama.")
            return redirect("finance:loan_application")

        payload = {
            "chama_id": str(membership.chama_id),
            "member_id": str(request.user.id),
            "loan_product_id": str(loan_product.id),
            "principal": str(principal),
            "duration_months": duration_months,
        }

        try:
            FinanceService.request_loan(payload, actor=request.user)
        except FinanceServiceError as exc:
            messages.error(request, str(exc))
            return redirect("finance:loan_application")

        messages.success(request, "Loan application submitted successfully.")
        return redirect("finance:loan_list")


@method_decorator(login_required, name="dispatch")
class LoanListView(TemplateView):
    template_name = "finance/loan_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["active_membership"] = membership

        if not membership:
            context["loans"] = []
            return context

        effective_role = get_effective_role(self.request.user, membership.chama_id, membership)
        is_admin = effective_role in {MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER}

        if is_admin:
            context["title"] = "Loan Management"
            # Show all loans for admins
            loans = (
                Loan.objects.filter(chama=membership.chama)
                .select_related("member")
                .prefetch_related("installments")
                .order_by("-requested_at")
            )
        else:
            context["title"] = "My Loans"
            # Show only user's loans for members
            loans = (
                Loan.objects.filter(chama=membership.chama, member=self.request.user)
                .prefetch_related("installments")
                .order_by("-requested_at")
            )

        # Calculate stats for admins
        if is_admin:
            context["total_loans"] = loans.count()
            context["pending_loans"] = loans.filter(status=LoanStatus.REQUESTED).count()
            context["active_loans"] = loans.filter(status__in=[LoanStatus.APPROVED, LoanStatus.DISBURSING, LoanStatus.DISBURSED, LoanStatus.ACTIVE]).count()
            context["total_owed"] = sum(
                loan.principal - sum(installment.amount for installment in loan.installments.filter(status=InstallmentStatus.PAID))
                for loan in loans.filter(status__in=[LoanStatus.DISBURSED, LoanStatus.ACTIVE])
            )

        for loan in loans:
            loan.amount = loan.principal
            loan.application_date = loan.requested_at
            loan.repayment_period = loan.duration_months
            if loan.duration_months > 0:
                loan.monthly_payment = (loan.principal / Decimal(loan.duration_months)).quantize(Decimal("0.01"))
            else:
                loan.monthly_payment = Decimal("0.00")

            next_due_installment = (
                loan.installments.filter(status__in=[InstallmentStatus.DUE, InstallmentStatus.OVERDUE])
                .order_by("due_date")
                .first()
            )
            loan.due_date = next_due_installment.due_date if next_due_installment else None

            total_installments = loan.installments.count()
            paid_installments = loan.installments.filter(status=InstallmentStatus.PAID).count()
            loan.repayment_progress = int((paid_installments / total_installments) * 100) if total_installments else 0

            loan.status = _loan_ui_status(loan.status)

        context["loans"] = loans
        context["is_admin"] = is_admin
        return context


@method_decorator(login_required, name="dispatch")
class ExpenseFormView(TemplateView):
    template_name = "finance/expense_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["title"] = "Record Expense"
        context["active_membership"] = membership
        context["today"] = timezone.localdate()

        if not membership:
            context["chama_members"] = []
            return context

        context["chama_members"] = Membership.objects.select_related("user").filter(
            chama=membership.chama,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        )
        return context

    def post(self, request, *args, **kwargs):
        membership = _resolve_membership(request)
        if not membership:
            messages.error(request, "You must be an approved active member of a chama.")
            return redirect("chama:chama_create")

        effective_role = get_effective_role(request.user, membership.chama_id, membership)
        if effective_role not in {MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER}:
            messages.error(request, "Only chama admin or treasurer can record expenses.")
            return redirect("finance:expense_form")

        amount = _parse_decimal(request.POST.get("amount"))
        if not amount or amount <= Decimal("0.00"):
            messages.error(request, "Please provide a valid expense amount.")
            return redirect("finance:expense_form")

        title = (request.POST.get("title") or "Expense").strip()
        description = (request.POST.get("description") or "").strip()
        category = (request.POST.get("category") or "other").strip().lower()
        reason = f"{title} [{category}]"
        if description:
            reason = f"{reason}: {description}"

        idempotency_key = f"web-expense:{membership.chama_id}:{uuid.uuid4().hex}"
        payload = {
            "chama_id": str(membership.chama_id),
            "amount": str(amount),
            "direction": LedgerDirection.DEBIT,
            "reason": reason,
            "idempotency_key": idempotency_key,
        }

        try:
            FinanceService.post_manual_adjustment(payload, actor=request.user)
        except (FinanceServiceError, IdempotencyConflictError) as exc:
            messages.error(request, str(exc))
            return redirect("finance:expense_form")

        messages.success(request, "Expense recorded successfully.")
        return redirect("finance:transaction_history")


@method_decorator(login_required, name="dispatch")
class TransactionHistoryView(TemplateView):
    template_name = "finance/transaction_history.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["title"] = "Transaction History"
        context["active_membership"] = membership

        if not membership:
            context["transactions"] = []
            context["total_income"] = Decimal("0.00")
            context["total_expenses"] = Decimal("0.00")
            context["current_balance"] = Decimal("0.00")
            context["total_transactions"] = 0
            return context

        effective_role = get_effective_role(self.request.user, membership.chama_id, membership)
        is_manager = effective_role in MANAGER_ROLES
        contribution_filters = {"chama": membership.chama}
        loan_filters = {"chama": membership.chama}
        manual_filters = {"chama": membership.chama}

        if not is_manager:
            contribution_filters["member"] = self.request.user
            loan_filters["member"] = self.request.user
            manual_filters["created_by"] = self.request.user

        contributions = Contribution.objects.filter(**contribution_filters).select_related("member")
        loans = Loan.objects.filter(**loan_filters)
        repayments = Repayment.objects.filter(loan__in=loans)
        manual_adjustments = ManualAdjustment.objects.filter(**manual_filters)

        rows: list[TransactionRow] = []

        for row in contributions:
            rows.append(
                TransactionRow(
                    id=str(row.id),
                    date=datetime.combine(row.date_paid, datetime.min.time()),
                    type="contribution",
                    description=f"Contribution by {row.member.full_name}",
                    amount=row.amount,
                    reference=row.receipt_code,
                    receipt=row.receipt_code,
                )
            )

        for row in repayments:
            rows.append(
                TransactionRow(
                    id=str(row.id),
                    date=datetime.combine(row.date_paid, datetime.min.time()),
                    type="loan",
                    description=f"Loan repayment ({row.loan_id})",
                    amount=row.amount,
                    reference=row.receipt_code,
                    receipt=row.receipt_code,
                )
            )

        for row in manual_adjustments:
            tx_type = "expense" if row.direction == LedgerDirection.DEBIT else "contribution"
            rows.append(
                TransactionRow(
                    id=str(row.id),
                    date=row.created_at,
                    type=tx_type,
                    description=row.reason,
                    amount=row.amount,
                    reference=row.idempotency_key or str(row.id),
                    receipt=None,
                )
            )

        rows.sort(key=lambda item: item.date, reverse=True)

        total_income = sum((item.amount for item in rows if item.type != "expense"), Decimal("0.00"))
        total_expenses = sum((item.amount for item in rows if item.type == "expense"), Decimal("0.00"))
        current_balance = total_income - total_expenses

        context["transactions"] = rows
        context["total_income"] = total_income
        context["total_expenses"] = total_expenses
        context["current_balance"] = current_balance
        context["total_transactions"] = len(rows)
        return context


# Function-based views for backward compatibility
@login_required
def contribution_form_view(request):
    return ContributionFormView.as_view()(request)


@login_required
def loan_application_view(request):
    membership = _resolve_membership(request)
    if membership:
        effective_role = get_effective_role(request.user, membership.chama_id, membership)
        # Redirect admins to loan list instead of application form
        if effective_role in {MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER}:
            return redirect('finance:loan_list')
    return LoanApplicationView.as_view()(request)


@login_required
def loan_list_view(request):
    return LoanListView.as_view()(request)


@login_required
def expense_form_view(request):
    return ExpenseFormView.as_view()(request)


@login_required
def transaction_history_view(request):
    return TransactionHistoryView.as_view()(request)
