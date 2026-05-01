from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.utils import timezone

from apps.chama.models import Chama
from apps.finance.models import (
    InstallmentSchedule,
    InstallmentStatus,
    Loan,
    LoanApplication,
    LoanApplicationStatus,
    LoanProduct,
    LoanStatus,
)
from apps.finance.serializers import LoanProductSerializer


ZERO = Decimal("0.00")
ACTIVE_LOAN_STATUSES = {
    LoanStatus.APPROVED,
    LoanStatus.DISBURSING,
    LoanStatus.DISBURSED,
    LoanStatus.ACTIVE,
    LoanStatus.DUE_SOON,
    LoanStatus.OVERDUE,
    LoanStatus.RESTRUCTURED,
    LoanStatus.DEFAULTED_RECOVERING,
}
COMPLETED_LOAN_STATUSES = {
    LoanStatus.PAID,
    LoanStatus.CLEARED,
    LoanStatus.CLOSED,
    LoanStatus.RECOVERED_FROM_GUARANTOR,
    LoanStatus.RECOVERED_FROM_OFFSET,
}
PENDING_APPLICATION_STATUSES = {
    LoanApplicationStatus.SUBMITTED,
    LoanApplicationStatus.IN_REVIEW,
    LoanApplicationStatus.TREASURER_APPROVED,
    LoanApplicationStatus.COMMITTEE_APPROVED,
    LoanApplicationStatus.APPROVED,
}


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value in (None, ""):
        return ZERO
    return Decimal(str(value))


def _date_to_iso(value) -> str | None:
    return value.isoformat() if value else None


def _loan_state_from_status(status: str) -> str:
    lowered = str(status or "").lower()
    if lowered in {"overdue", "defaulted", "defaulted_recovering", "written_off"}:
        return "overdue"
    if lowered in {
        "approved",
        "disbursing",
        "disbursed",
        "active",
        "due_soon",
        "restructured",
    }:
        return "active"
    if lowered in {"paid", "cleared", "closed", "recovered_from_offset", "recovered_from_guarantor"}:
        return "completed"
    return "no_active_loan"


def _application_state_from_status(status: str) -> str:
    lowered = str(status or "").lower()
    if lowered == LoanApplicationStatus.REJECTED:
        return "rejected"
    if lowered == LoanApplicationStatus.CANCELLED:
        return "cancelled"
    if lowered in {LoanApplicationStatus.APPROVED, LoanApplicationStatus.DISBURSED}:
        return "approved"
    if lowered in PENDING_APPLICATION_STATUSES:
        return "submitted_pending_review"
    return "not_started"


def _eligibility_state(*, eligible: bool, recommended_max_amount: Decimal) -> str:
    if eligible:
        return "eligible"
    if recommended_max_amount > ZERO:
        return "partially_eligible"
    return "not_eligible"


def _pick_contribution_limit(metrics: dict[str, Any]) -> Decimal:
    multiple_cap = _to_decimal(metrics.get("policy_savings_multiple_cap"))
    if multiple_cap > ZERO:
        return multiple_cap
    return _to_decimal(metrics.get("contributions_total"))


def _build_application_preview(application: LoanApplication | None) -> dict[str, Any] | None:
    if not application:
        return None

    return {
        "id": str(application.id),
        "status": str(application.status),
        "state": _application_state_from_status(application.status),
        "amount": str(_to_decimal(application.requested_amount)),
        "duration_months": int(application.requested_term_months or 0),
        "purpose": application.purpose or "",
        "reference": f"APP-{str(application.id).split('-')[0].upper()}",
        "submitted_at": application.submitted_at.isoformat() if application.submitted_at else None,
        "approved_at": application.approved_at.isoformat() if application.approved_at else None,
        "rejection_reason": application.rejection_reason or "",
        "recommended_max_amount": str(_to_decimal(application.recommended_max_amount)),
        "created_loan_id": str(application.created_loan_id) if application.created_loan_id else None,
    }


def _build_next_installment(loan: Loan) -> dict[str, Any] | None:
    installment = (
        InstallmentSchedule.objects.filter(loan=loan)
        .exclude(status=InstallmentStatus.PAID)
        .order_by("due_date", "created_at")
        .first()
    )
    if not installment:
        return None

    return {
        "id": str(installment.id),
        "due_date": installment.due_date.isoformat(),
        "expected_amount": str(_to_decimal(installment.expected_amount)),
        "status": str(installment.status),
        "paid_amount": str(_to_decimal(installment.paid_amount)),
    }


def _build_active_loan_preview(loan: Loan | None) -> dict[str, Any] | None:
    if not loan:
        return None

    principal = _to_decimal(loan.principal)
    outstanding = _to_decimal(loan.outstanding_principal)
    repaid = max(principal - outstanding, ZERO)
    progress = int(min(100, (repaid / principal) * Decimal("100"))) if principal > ZERO else 0
    next_installment = _build_next_installment(loan)

    return {
        "id": str(loan.id),
        "status": str(loan.status),
        "state": _loan_state_from_status(loan.status),
        "amount": str(principal),
        "outstanding_balance": str(_to_decimal(loan.total_due or outstanding)),
        "outstanding_principal": str(outstanding),
        "outstanding_interest": str(_to_decimal(loan.outstanding_interest)),
        "outstanding_penalty": str(_to_decimal(loan.outstanding_penalty)),
        "duration_months": int(loan.duration_months or 0),
        "interest_rate": str(_to_decimal(loan.interest_rate)),
        "interest_type": loan.interest_type,
        "purpose": loan.purpose or "",
        "approved_at": loan.approved_at.isoformat() if loan.approved_at else None,
        "disbursed_at": loan.disbursed_at.isoformat() if loan.disbursed_at else None,
        "due_date": _date_to_iso(loan.due_date),
        "progress_percent": progress,
        "next_installment": next_installment,
        "loan_product": LoanProductSerializer(loan.loan_product).data if loan.loan_product else None,
    }


def _build_history_preview(*, applications: list[LoanApplication], loans: list[Loan]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    for application in applications:
        items.append(
            {
                "id": f"application:{application.id}",
                "record_type": "application",
                "application_id": str(application.id),
                "loan_id": str(application.created_loan_id) if application.created_loan_id else None,
                "status": str(application.status),
                "amount": str(_to_decimal(application.requested_amount)),
                "purpose": application.purpose or "",
                "date": application.submitted_at.isoformat() if application.submitted_at else None,
            }
        )

    for loan in loans:
        items.append(
            {
                "id": f"loan:{loan.id}",
                "record_type": "loan",
                "application_id": str(loan.source_application_record_id)
                if getattr(loan, "source_application_record_id", None)
                else None,
                "loan_id": str(loan.id),
                "status": str(loan.status),
                "amount": str(_to_decimal(loan.principal)),
                "purpose": loan.purpose or "",
                "date": (
                    loan.disbursed_at.isoformat()
                    if loan.disbursed_at
                    else loan.requested_at.isoformat() if loan.requested_at else None
                ),
            }
        )

    items.sort(key=lambda item: item.get("date") or "", reverse=True)
    return items[:6]


def build_member_loan_workspace(*, chama: Chama, member) -> dict[str, Any]:
    from apps.finance.services import FinanceService

    currency = getattr(chama, "currency", "") or "KES"
    today = timezone.localdate()
    policy = FinanceService._get_loan_policy(chama)

    applications_qs = (
        LoanApplication.objects.select_related("loan_product", "created_loan")
        .filter(chama=chama, member=member)
        .order_by("-submitted_at", "-created_at")
    )
    loans_qs = (
        Loan.objects.select_related("loan_product")
        .filter(chama=chama, member=member)
        .order_by("-requested_at", "-created_at")
    )
    products_qs = LoanProduct.objects.filter(chama=chama, is_active=True).order_by("-is_default", "name")

    active_application = applications_qs.filter(status__in=PENDING_APPLICATION_STATUSES).first()
    latest_rejected_application = applications_qs.filter(status=LoanApplicationStatus.REJECTED).first()
    active_loan = loans_qs.filter(status__in=ACTIVE_LOAN_STATUSES).first()
    history_preview = _build_history_preview(
        applications=list(applications_qs[:6]),
        loans=list(loans_qs[:6]),
    )

    default_product = products_qs.filter(is_default=True).first() or products_qs.first()
    eligibility_reasons: list[str] = []
    eligibility_metrics: dict[str, Any] = {}
    recommended_max_amount = ZERO
    eligibility_state = "unknown_loading"

    if default_product:
        result = FinanceService.evaluate_loan_eligibility(
            chama=chama,
            member=member,
            principal=Decimal("1.00"),
            duration_months=int(default_product.min_duration_months or 1),
            loan_product=default_product,
        )
        recommended_max_amount = _to_decimal(result.recommended_max_amount)
        eligibility_reasons = list(result.reasons)
        eligibility_metrics = dict(result.metrics or {})
        eligibility_state = _eligibility_state(
            eligible=result.eligible,
            recommended_max_amount=recommended_max_amount,
        )
    else:
        eligibility_state = "not_eligible"
        eligibility_reasons = ["No loan product is available for this chama right now."]

    current_financial_standing = {
        "savings_position": str(_to_decimal(eligibility_metrics.get("contributions_total"))),
        "successful_contributions": int(eligibility_metrics.get("successful_contributions", 0) or 0),
        "contribution_compliance_percent": str(
            _to_decimal(eligibility_metrics.get("contribution_compliance_percent"))
        ),
        "active_loans_count": int(eligibility_metrics.get("active_loans_count", 0) or 0),
        "available_liquidity": str(_to_decimal(eligibility_metrics.get("available_liquidity"))),
        "effective_lendable_liquidity": str(
            _to_decimal(eligibility_metrics.get("effective_lendable_liquidity"))
        ),
    }

    approval_requirements = dict(eligibility_metrics.get("approval_requirements", {}))
    savings_summary = dict(eligibility_metrics.get("savings_summary", {}))
    policy_checks = list(eligibility_metrics.get("policy_checks", []))
    risk_notes = list(eligibility_metrics.get("risk_notes", []))
    next_steps = list(eligibility_metrics.get("next_steps", []))

    guidance: list[str] = []
    if eligibility_state == "eligible":
        guidance.append("You currently qualify for a new loan under the active chama policy.")
        guidance.append("Review the repayment preview before you submit your application.")
    elif eligibility_state == "partially_eligible":
        guidance.extend(next_steps or [
            "You are close to qualifying but still need to improve a few conditions.",
            "Review the blocked checks before trying again.",
        ])
    else:
        guidance.extend(next_steps or [
            "You do not qualify for a new loan right now.",
            "Improve the highlighted conditions before trying again.",
        ])

    next_due = _build_next_installment(active_loan) if active_loan else None
    active_loan_preview = _build_active_loan_preview(active_loan)
    active_application_preview = _build_application_preview(active_application)
    rejected_application_preview = _build_application_preview(latest_rejected_application)

    can_start_application = (
        default_product is not None
        and (
            not getattr(policy, "block_pending_loan_applications", True)
            or active_application is None
        )
        and (
            active_loan is None
            or int(eligibility_metrics.get("active_loans_count", 0) or 0)
            < int(getattr(policy, "max_active_loans", 1) or 1)
        )
        and eligibility_state == "eligible"
    )

    return {
        "summary": {
            "currency": currency,
            "eligibility_status": eligibility_state,
            "max_eligible_amount": str(recommended_max_amount),
            "active_loan_balance": (
                active_loan_preview["outstanding_balance"] if active_loan_preview else "0.00"
            ),
            "next_repayment_due": next_due["due_date"] if next_due else None,
            "next_repayment_amount": next_due["expected_amount"] if next_due else "0.00",
            "loan_state": (
                active_loan_preview["state"]
                if active_loan_preview
                else "pending_application"
                if active_application_preview
                else "no_active_loan"
            ),
            "has_pending_application": active_application is not None,
        },
        "eligibility": {
            "state": eligibility_state,
            "max_eligible_amount": str(recommended_max_amount),
            "reasons": eligibility_reasons,
            "next_steps": next_steps,
            "risk_notes": risk_notes,
            "contribution_based_limit": str(_pick_contribution_limit(eligibility_metrics)),
            "current_financial_standing": current_financial_standing,
            "conditions": [
                {
                    "key": "contribution_history",
                    "label": "Contribution history",
                    "value": current_financial_standing["successful_contributions"],
                },
                {
                    "key": "savings_position",
                    "label": "Savings position",
                    "value": current_financial_standing["savings_position"],
                },
                {
                    "key": "outstanding_obligations",
                    "label": "Outstanding obligations",
                    "value": str(current_financial_standing["active_loans_count"]),
                },
                {
                    "key": "repayment_behaviour",
                    "label": "Repayment behaviour",
                    "value": active_loan_preview["state"] if active_loan_preview else "clear",
                },
            ],
            "policy_checks": policy_checks,
            "policy_summary": {
                "minimum_membership_days": int(getattr(policy, "min_membership_days", 0) or 0),
                "minimum_contributions": int(getattr(policy, "min_contribution_cycles", 0) or 0),
                "minimum_savings_threshold": str(_to_decimal(getattr(policy, "min_savings_threshold", ZERO))),
                "minimum_loan_amount": str(_to_decimal(getattr(policy, "minimum_loan_amount", ZERO))),
                "loan_cap_multiplier": str(_to_decimal(getattr(policy, "loan_cap_multiplier", ZERO))),
                "max_active_loans": int(getattr(policy, "max_active_loans", 1) or 1),
                "repayment_capacity_ratio_limit": str(
                    _to_decimal(getattr(policy, "repayment_capacity_ratio_limit", ZERO))
                ),
            },
            "approval_requirements": approval_requirements,
            "savings_summary": savings_summary,
            "repayment_history_score": str(
                _to_decimal(eligibility_metrics.get("repayment_history_score"))
            ),
            "contribution_consistency_score": str(
                _to_decimal(eligibility_metrics.get("contribution_consistency_score"))
            ),
            "installment_estimate": str(_to_decimal(eligibility_metrics.get("installment_estimate"))),
            "total_repayment_estimate": str(
                _to_decimal(eligibility_metrics.get("total_repayment_estimate"))
            ),
            "metrics": eligibility_metrics,
            "guidance": guidance,
            "selected_product": (
                LoanProductSerializer(default_product).data if default_product else None
            ),
        },
        "active_application": active_application_preview,
        "active_loan": active_loan_preview,
        "latest_rejected_application": rejected_application_preview,
        "history_preview": history_preview,
        "loan_rules": {
            "can_start_application": can_start_application,
            "blocks_duplicate_applications": bool(
                getattr(policy, "block_pending_loan_applications", True)
            ),
            "blocks_when_active_loan_exists": int(getattr(policy, "max_active_loans", 1) or 1) <= 1,
            "available_products": LoanProductSerializer(products_qs, many=True).data,
            "default_product": LoanProductSerializer(default_product).data if default_product else None,
            "policy_highlights": [
                f"Minimum savings: KES {_to_decimal(getattr(policy, 'min_savings_threshold', ZERO)):,.2f}.",
                f"Maximum loan limit: {str(_to_decimal(getattr(policy, 'loan_cap_multiplier', ZERO)))}x eligible savings.",
                "Applications are tracked here as they move through review and approval.",
            ],
            "frequency_summary": (
                f"Repayment period {default_product.min_duration_months}-{default_product.max_duration_months} months"
                if default_product
                else "No active loan product"
            ),
            "late_penalty": {
                "type": getattr(default_product, "late_penalty_type", "") if default_product else "",
                "value": str(_to_decimal(getattr(default_product, "late_penalty_value", ZERO))),
            },
        },
        "empty_state": {
            "title": "No active loan yet",
            "description": "Check your eligibility and start a loan application when you are ready."
            if default_product
            else "No loan product is available for this chama right now.",
        },
        "server_time": timezone.now().isoformat(),
        "today": today.isoformat(),
    }
