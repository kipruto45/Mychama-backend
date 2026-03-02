from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView

from apps.chama.models import MemberStatus, Membership, MembershipRole
from apps.chama.services import get_effective_role
from apps.finance.models import ContributionType, InstallmentStatus, Loan
from apps.payments.models import (
    CallbackLog,
    PaymentActivityLog,
    PaymentIntent,
    PaymentIntentType,
    PaymentReconciliationRun,
    WithdrawalApprovalLog,
)
from apps.payments.services import PaymentWorkflowError, PaymentWorkflowService

ADMIN_ROLES = {MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER}
MANAGER_READ_ROLES = {
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.TREASURER,
    MembershipRole.SECRETARY,
    MembershipRole.AUDITOR,
}


def _resolve_membership(request, *, chama_id=None):
    session = getattr(request, "session", None)
    active_chama_id = session.get("active_chama_id") if session is not None else None
    scoped_chama_id = (
        chama_id
        or request.GET.get("chama_id")
        or request.POST.get("chama_id")
        or active_chama_id
    )
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
        if session is not None:
            session["active_chama_id"] = str(membership.chama_id)

    return membership


def _resolve_loan_access(request, loan: Loan):
    membership = _resolve_membership(request, chama_id=loan.chama_id)
    if not membership:
        return None
    effective_role = get_effective_role(request.user, loan.chama_id, membership)
    if effective_role == MembershipRole.MEMBER and loan.member_id != request.user.id:
        return None
    return membership


def _to_decimal(raw_value) -> Decimal | None:
    if raw_value in (None, ""):
        return None
    try:
        return Decimal(str(raw_value))
    except (InvalidOperation, TypeError, ValueError):
        return None


@method_decorator(login_required, name="dispatch")
class PaymentFormView(TemplateView):
    template_name = "payments/payment_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Make Payment"
        context["active_membership"] = _resolve_membership(self.request)
        return context

    def post(self, request, *args, **kwargs):
        membership = _resolve_membership(request)
        if not membership:
            messages.error(request, "You must be an approved active member of a chama.")
            return redirect("chama:chama_create")

        amount = _to_decimal(request.POST.get("amount"))
        if not amount or amount <= Decimal("0.00"):
            messages.error(request, "Provide a valid amount.")
            return redirect("payments:payment_form")

        payment_type = (request.POST.get("payment_type") or "contribution").strip().lower()
        payment_method = (request.POST.get("payment_method") or "mpesa").strip().lower()
        phone = (request.POST.get("phone") or request.user.phone or "").strip()

        try:
            if payment_type == "loan":
                loan = (
                    Loan.objects.filter(
                        chama=membership.chama,
                        member=request.user,
                        status__in=["approved", "disbursing", "disbursed", "active"],
                    )
                    .order_by("-requested_at")
                    .first()
                )
                if not loan:
                    messages.error(request, "No active loan found for repayment.")
                    return redirect("payments:payment_form")

                PaymentWorkflowService.initiate_loan_repayment_stk(
                    loan_id=loan.id,
                    payload={
                        "amount": str(amount),
                        "phone": phone,
                    },
                    actor=request.user,
                )
            else:
                contribution_type = (
                    ContributionType.objects.filter(chama=membership.chama, is_active=True)
                    .order_by("name")
                    .first()
                )
                if not contribution_type:
                    messages.error(request, "No active contribution type found for this chama.")
                    return redirect("payments:payment_form")

                payload = {
                    "chama_id": str(membership.chama_id),
                    "amount": str(amount),
                    "purpose": "CONTRIBUTION",
                    "reference_id": str(contribution_type.id),
                    "phone": phone,
                }

                if payment_method == "mpesa":
                    PaymentWorkflowService.initiate_deposit_stk(payload=payload, actor=request.user)
                else:
                    PaymentWorkflowService.create_deposit_c2b_intent(payload=payload, actor=request.user)

        except PaymentWorkflowError as exc:
            messages.error(request, str(exc))
            return redirect("payments:payment_form")

        messages.success(request, "Payment request submitted successfully.")
        return redirect(f"{reverse_url('payments:transactions_my')}?chama_id={membership.chama_id}")


@method_decorator(login_required, name="dispatch")
class PaymentHistoryView(TemplateView):
    template_name = "payments/payment_history.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["title"] = "Payment History"
        context["active_membership"] = membership
        if membership:
            context["payments"] = PaymentIntent.objects.filter(
                chama=membership.chama,
                created_by=self.request.user,
            ).order_by("-created_at")[:100]
        else:
            context["payments"] = []
        return context


@method_decorator(login_required, name="dispatch")
class PaymentMethodsView(TemplateView):
    template_name = "payments/payment_methods.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Payment Methods"
        context["active_membership"] = _resolve_membership(self.request)
        return context


@method_decorator(login_required, name="dispatch")
class DepositSelectView(TemplateView):
    template_name = "payments/deposit_select.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["active_membership"] = membership
        context["chama_id"] = str(membership.chama_id) if membership else ""
        return context


@method_decorator(login_required, name="dispatch")
class DepositSTKPushView(TemplateView):
    template_name = "payments/deposit_stk_push.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["active_membership"] = membership
        context["chama_id"] = str(membership.chama_id) if membership else ""
        return context

    def post(self, request, *args, **kwargs):
        membership = _resolve_membership(request)
        if not membership:
            messages.error(request, "You must be an approved active member of a chama.")
            return redirect("chama:chama_create")

        amount = _to_decimal(request.POST.get("amount"))
        reference_id = (request.POST.get("contribution_type_id") or "").strip()
        phone = (request.POST.get("phone") or request.user.phone or "").strip()

        if not amount or amount <= Decimal("0.00"):
            messages.error(request, "Provide a valid amount.")
            return redirect(f"{reverse_url('payments:deposit_stk_push')}?chama_id={membership.chama_id}")

        try:
            result = PaymentWorkflowService.initiate_deposit_stk(
                payload={
                    "chama_id": str(membership.chama_id),
                    "amount": str(amount),
                    "purpose": "CONTRIBUTION",
                    "reference_id": reference_id,
                    "phone": phone,
                },
                actor=request.user,
            )
        except PaymentWorkflowError as exc:
            messages.error(request, str(exc))
            return redirect(f"{reverse_url('payments:deposit_stk_push')}?chama_id={membership.chama_id}")

        checkout_request_id = getattr(result.get("stk_transaction"), "checkout_request_id", "")
        if checkout_request_id:
            messages.success(request, f"STK initiated. Checkout ID: {checkout_request_id}")
        else:
            messages.success(request, "STK initiated successfully.")
        return redirect(f"{reverse_url('payments:transactions_my')}?chama_id={membership.chama_id}")


@method_decorator(login_required, name="dispatch")
class DepositPaybillInstructionsView(TemplateView):
    template_name = "payments/deposit_paybill_instructions.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["active_membership"] = membership
        context["chama_id"] = str(membership.chama_id) if membership else ""
        context["timestamp"] = timezone.now().strftime("%Y%m%d%H%M")
        context["shortcode"] = getattr(settings, "DARAJA_SHORTCODE", "")
        return context


@method_decorator(login_required, name="dispatch")
class TransactionsMyView(TemplateView):
    template_name = "payments/transactions_my.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["active_membership"] = membership
        context["chama_id"] = str(membership.chama_id) if membership else ""

        if not membership:
            context["transactions"] = []
            return context

        context["transactions"] = PaymentWorkflowService.list_my_transactions(
            actor=self.request.user,
            chama_id=membership.chama_id,
        )
        return context


@method_decorator(login_required, name="dispatch")
class AdminTransactionsView(TemplateView):
    template_name = "payments/admin_transactions.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["active_membership"] = membership
        context["chama_id"] = str(membership.chama_id) if membership else ""

        effective_role = (
            get_effective_role(self.request.user, membership.chama_id, membership)
            if membership
            else None
        )
        if not membership or effective_role not in MANAGER_READ_ROLES:
            context["transactions"] = []
            return context

        queryset = PaymentIntent.objects.filter(chama=membership.chama).order_by("-created_at")
        status_filter = (self.request.GET.get("status") or "").strip()
        type_filter = (self.request.GET.get("intent_type") or "").strip()
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if type_filter:
            queryset = queryset.filter(intent_type=type_filter)
        context["transactions"] = queryset[:200]
        return context


@method_decorator(login_required, name="dispatch")
class ReconciliationRunsView(TemplateView):
    template_name = "payments/reconciliation_runs.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["active_membership"] = membership
        context["chama_id"] = str(membership.chama_id) if membership else ""

        effective_role = (
            get_effective_role(self.request.user, membership.chama_id, membership)
            if membership
            else None
        )
        if not membership or effective_role not in MANAGER_READ_ROLES:
            context["runs"] = []
            return context

        context["runs"] = PaymentReconciliationRun.objects.filter(
            chama=membership.chama
        ).order_by("-run_at")[:100]
        return context


@method_decorator(login_required, name="dispatch")
class CallbackStatusPublicView(TemplateView):
    template_name = "payments/callback_status_public.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["callback_total"] = CallbackLog.objects.count()
        return context


@method_decorator(login_required, name="dispatch")
class WithdrawRequestView(TemplateView):
    template_name = "payments/withdraw_request.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["active_membership"] = membership
        context["chama_id"] = str(membership.chama_id) if membership else ""
        return context

    def post(self, request, *args, **kwargs):
        membership = _resolve_membership(request)
        if not membership:
            messages.error(request, "You must be an approved active member of a chama.")
            return redirect("chama:chama_create")
        effective_role = get_effective_role(request.user, membership.chama_id, membership)
        if effective_role not in ADMIN_ROLES:
            messages.error(request, "Only treasurer/admin can request withdrawals.")
            return redirect(f"{reverse_url('payments:withdraw_request')}?chama_id={membership.chama_id}")

        amount = _to_decimal(request.POST.get("amount"))
        phone = (request.POST.get("phone") or request.user.phone or "").strip()
        reason = (request.POST.get("reason") or "").strip()
        if not amount or amount <= Decimal("0.00"):
            messages.error(request, "Provide a valid amount.")
            return redirect(f"{reverse_url('payments:withdraw_request')}?chama_id={membership.chama_id}")

        try:
            intent = PaymentWorkflowService.request_withdrawal(
                payload={
                    "chama_id": str(membership.chama_id),
                    "amount": str(amount),
                    "phone": phone,
                    "reason": reason,
                    "purpose": "OTHER",
                    "reference_type": "MANUAL",
                },
                actor=request.user,
            )
        except PaymentWorkflowError as exc:
            messages.error(request, str(exc))
            return redirect(f"{reverse_url('payments:withdraw_request')}?chama_id={membership.chama_id}")

        messages.success(request, "Withdrawal request created.")
        return redirect("payments:withdraw_detail", intent_id=intent.id)


@method_decorator(login_required, name="dispatch")
class WithdrawApprovalsView(TemplateView):
    template_name = "payments/withdraw_approvals.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["active_membership"] = membership
        effective_role = (
            get_effective_role(self.request.user, membership.chama_id, membership)
            if membership
            else None
        )
        if not membership or effective_role not in MANAGER_READ_ROLES:
            context["queue"] = []
            return context

        context["queue"] = PaymentIntent.objects.filter(
            chama=membership.chama,
            intent_type=PaymentIntentType.WITHDRAWAL,
        ).order_by("-created_at")[:200]
        return context


@method_decorator(login_required, name="dispatch")
class WithdrawDetailView(TemplateView):
    template_name = "payments/withdraw_detail.html"

    def _get_intent(self):
        intent = get_object_or_404(PaymentIntent, id=self.kwargs["intent_id"])
        membership = _resolve_membership(self.request, chama_id=intent.chama_id)
        return intent, membership

    def post(self, request, *args, **kwargs):
        intent, membership = self._get_intent()
        effective_role = (
            get_effective_role(request.user, membership.chama_id, membership)
            if membership
            else None
        )
        if not membership or effective_role not in ADMIN_ROLES:
            messages.error(request, "Only treasurer/admin can take this action.")
            return redirect("payments:withdraw_detail", intent_id=intent.id)

        action = (request.POST.get("action") or "").strip().lower()
        try:
            if action == "approve":
                PaymentWorkflowService.approve_withdrawal_intent(
                    intent_id=intent.id,
                    actor=request.user,
                    note=(request.POST.get("note") or "").strip(),
                )
                messages.success(request, "Approval action recorded.")
            elif action == "send":
                PaymentWorkflowService.send_b2c_payout(intent_id=intent.id, actor=request.user)
                messages.success(request, "Payout send requested.")
            else:
                messages.info(request, "No action performed.")
        except PaymentWorkflowError as exc:
            messages.error(request, str(exc))

        return redirect("payments:withdraw_detail", intent_id=intent.id)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        intent, membership = self._get_intent()
        context["active_membership"] = membership
        context["intent"] = intent
        return context


@method_decorator(login_required, name="dispatch")
class LoanDisbursementsQueueView(TemplateView):
    template_name = "payments/loan_disbursements_queue.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["active_membership"] = membership
        effective_role = (
            get_effective_role(self.request.user, membership.chama_id, membership)
            if membership
            else None
        )
        if not membership or effective_role not in MANAGER_READ_ROLES:
            context["queue"] = []
            return context

        context["queue"] = PaymentIntent.objects.filter(
            chama=membership.chama,
            intent_type=PaymentIntentType.LOAN_DISBURSEMENT,
        ).order_by("-created_at")[:200]
        return context


@method_decorator(login_required, name="dispatch")
class LoanDisbursementDetailView(TemplateView):
    template_name = "payments/loan_disbursement_detail.html"

    def _get_intent(self):
        intent = get_object_or_404(
            PaymentIntent,
            id=self.kwargs["intent_id"],
            intent_type=PaymentIntentType.LOAN_DISBURSEMENT,
        )
        membership = _resolve_membership(self.request, chama_id=intent.chama_id)
        return intent, membership

    def post(self, request, *args, **kwargs):
        intent, membership = self._get_intent()
        effective_role = (
            get_effective_role(request.user, membership.chama_id, membership)
            if membership
            else None
        )
        if not membership or effective_role not in ADMIN_ROLES:
            messages.error(request, "Only treasurer/admin can take this action.")
            return redirect("payments:loan_disbursement_detail", intent_id=intent.id)

        action = (request.POST.get("action") or "").strip().lower()
        try:
            if action == "approve":
                PaymentWorkflowService.approve_withdrawal_intent(
                    intent_id=intent.id,
                    actor=request.user,
                    note=(request.POST.get("note") or "").strip(),
                )
                messages.success(request, "Approval action recorded.")
            elif action == "send":
                PaymentWorkflowService.send_b2c_payout(intent_id=intent.id, actor=request.user)
                messages.success(request, "Disbursement send requested.")
            else:
                messages.info(request, "No action performed.")
        except PaymentWorkflowError as exc:
            messages.error(request, str(exc))

        return redirect("payments:loan_disbursement_detail", intent_id=intent.id)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        intent, membership = self._get_intent()
        context["active_membership"] = membership
        context["intent"] = intent
        context["approval_logs"] = WithdrawalApprovalLog.objects.filter(
            payment_intent=intent
        ).select_related("actor").order_by("created_at")
        context["activity_logs"] = PaymentActivityLog.objects.filter(
            payment_intent=intent
        ).select_related("actor").order_by("created_at")
        return context


@method_decorator(login_required, name="dispatch")
class LoanPayView(TemplateView):
    template_name = "loans/loan_pay.html"

    def dispatch(self, request, *args, **kwargs):
        self.loan = get_object_or_404(
            Loan.objects.prefetch_related("installments"),
            id=kwargs["loan_id"],
        )
        self.membership = _resolve_loan_access(request, self.loan)
        if not self.membership:
            return render(request, "errors/403.html", status=403)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        loan = self.loan
        context["active_membership"] = self.membership

        status_payload = PaymentWorkflowService.loan_repayment_status(
            loan_id=loan.id,
            actor=self.request.user,
            chama_id=loan.chama_id,
        )
        next_due = loan.installments.filter(status__in=[InstallmentStatus.DUE, InstallmentStatus.OVERDUE]).order_by(
            "due_date", "created_at"
        ).first()

        context["loan"] = loan
        context["outstanding_balance"] = status_payload.get("outstanding_balance", "0.00")
        context["next_due"] = next_due
        return context


@method_decorator(login_required, name="dispatch")
class LoanPaySTKView(TemplateView):
    template_name = "loans/loan_pay_stk.html"

    def dispatch(self, request, *args, **kwargs):
        self.loan = get_object_or_404(Loan, id=kwargs["loan_id"])
        self.membership = _resolve_loan_access(request, self.loan)
        if not self.membership:
            return render(request, "errors/403.html", status=403)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_membership"] = self.membership
        context["loan"] = self.loan
        return context

    def post(self, request, *args, **kwargs):
        amount = _to_decimal(request.POST.get("amount"))
        phone = (request.POST.get("phone") or request.user.phone or "").strip()

        payload = {"phone": phone}
        if amount and amount > Decimal("0.00"):
            payload["amount"] = str(amount)

        try:
            PaymentWorkflowService.initiate_loan_repayment_stk(
                loan_id=self.loan.id,
                payload=payload,
                actor=request.user,
            )
        except PaymentWorkflowError as exc:
            messages.error(request, str(exc))
            return redirect("payments:loan_pay_stk", loan_id=self.loan.id)

        messages.success(request, "Loan repayment STK initiated.")
        return redirect("payments:loan_repayment_history", loan_id=self.loan.id)


@method_decorator(login_required, name="dispatch")
class LoanPayPaybillInstructionsView(TemplateView):
    template_name = "loans/loan_pay_paybill_instructions.html"

    def dispatch(self, request, *args, **kwargs):
        self.loan = get_object_or_404(Loan, id=kwargs["loan_id"])
        self.membership = _resolve_loan_access(request, self.loan)
        if not self.membership:
            return render(request, "errors/403.html", status=403)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        loan = self.loan
        context["active_membership"] = self.membership
        context["loan"] = loan
        context["shortcode"] = getattr(settings, "DARAJA_SHORTCODE", "")
        context["latest_intent"] = (
            PaymentIntent.objects.filter(
                intent_type=PaymentIntentType.LOAN_REPAYMENT,
                reference_type="LOAN",
                reference_id=loan.id,
                created_by=self.request.user,
            )
            .order_by("-created_at")
            .first()
        )
        return context


@method_decorator(login_required, name="dispatch")
class LoanRepaymentHistoryView(TemplateView):
    template_name = "loans/loan_repayment_history.html"

    def dispatch(self, request, *args, **kwargs):
        self.loan = get_object_or_404(Loan, id=kwargs["loan_id"])
        self.membership = _resolve_loan_access(request, self.loan)
        if not self.membership:
            return render(request, "errors/403.html", status=403)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        loan = self.loan
        membership = self.membership
        context["active_membership"] = membership
        context["loan"] = loan

        queryset = PaymentIntent.objects.filter(
            intent_type=PaymentIntentType.LOAN_REPAYMENT,
            reference_type="LOAN",
            reference_id=loan.id,
        ).order_by("-created_at")
        effective_role = get_effective_role(
            self.request.user,
            loan.chama_id,
            membership,
        )
        if effective_role == MembershipRole.MEMBER:
            queryset = queryset.filter(created_by=self.request.user)

        context["payment_intents"] = queryset[:200]
        return context


@method_decorator(login_required, name="dispatch")
class LoanDisbursementStatusView(TemplateView):
    template_name = "loans/loan_disbursement_status.html"

    def dispatch(self, request, *args, **kwargs):
        self.loan = get_object_or_404(Loan, id=kwargs["loan_id"])
        self.membership = _resolve_loan_access(request, self.loan)
        if not self.membership:
            return render(request, "errors/403.html", status=403)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        loan = self.loan
        membership = self.membership
        context["active_membership"] = membership
        context["loan"] = loan

        disbursement_intent = PaymentIntent.objects.filter(
            intent_type=PaymentIntentType.LOAN_DISBURSEMENT,
            reference_type="LOAN",
            reference_id=loan.id,
        ).order_by("-created_at").first()

        context["disbursement_intent"] = disbursement_intent
        return context


# ---------------------------------------------------------------------------
# Helper for reverse without importing at module import time.
# ---------------------------------------------------------------------------
def reverse_url(name: str) -> str:
    from django.urls import reverse

    return reverse(name)


# Function-based views for backward compatibility
@login_required
def payment_form_view(request):
    return PaymentFormView.as_view()(request)


@login_required
def payment_history_view(request):
    return PaymentHistoryView.as_view()(request)


@login_required
def payment_methods_view(request):
    return PaymentMethodsView.as_view()(request)


@login_required
def deposit_select_view(request):
    return DepositSelectView.as_view()(request)


@login_required
def deposit_stk_push_view(request):
    return DepositSTKPushView.as_view()(request)


@login_required
def deposit_paybill_instructions_view(request):
    return DepositPaybillInstructionsView.as_view()(request)


@login_required
def transactions_my_view(request):
    return TransactionsMyView.as_view()(request)


@login_required
def admin_transactions_view(request):
    return AdminTransactionsView.as_view()(request)


@login_required
def reconciliation_runs_view(request):
    return ReconciliationRunsView.as_view()(request)


@login_required
def callback_status_public_view(request):
    return CallbackStatusPublicView.as_view()(request)


@login_required
def withdraw_request_view(request):
    return WithdrawRequestView.as_view()(request)


@login_required
def withdraw_approvals_view(request):
    return WithdrawApprovalsView.as_view()(request)


@login_required
def withdraw_detail_view(request, intent_id):
    return WithdrawDetailView.as_view()(request, intent_id=intent_id)


@login_required
def loan_disbursements_queue_view(request):
    return LoanDisbursementsQueueView.as_view()(request)


@login_required
def loan_disbursement_detail_view(request, intent_id):
    return LoanDisbursementDetailView.as_view()(request, intent_id=intent_id)


@login_required
def loan_pay_view(request, loan_id):
    return LoanPayView.as_view()(request, loan_id=loan_id)


@login_required
def loan_pay_stk_view(request, loan_id):
    return LoanPaySTKView.as_view()(request, loan_id=loan_id)


@login_required
def loan_pay_paybill_instructions_view(request, loan_id):
    return LoanPayPaybillInstructionsView.as_view()(request, loan_id=loan_id)


@login_required
def loan_repayment_history_view(request, loan_id):
    return LoanRepaymentHistoryView.as_view()(request, loan_id=loan_id)


@login_required
def loan_disbursement_status_view(request, loan_id):
    return LoanDisbursementStatusView.as_view()(request, loan_id=loan_id)
