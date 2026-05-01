from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.chama.models import Chama, Membership
from apps.finance.models import (
    Contribution,
    InstallmentSchedule,
    InstallmentStatus,
    LedgerDirection,
    LedgerEntry,
    LedgerEntryType,
    Loan,
    LoanApprovalLog,
    LoanStatus,
    Repayment,
)
from apps.finance.services import FinanceService
from apps.issues.models import Issue, IssueStatus
from apps.meetings.models import Attendance, AttendanceStatus, Meeting
from apps.reports.models import ReportType
from core.algorithms.analytics import compute_member_activity_cohorts
from core.utils import parse_iso_date, to_decimal


class ReportServiceError(Exception):
    pass


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    start_date = date(year, month, 1)
    if month == 12:
        end_date = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end_date = date(year, month + 1, 1) - timedelta(days=1)
    return start_date, end_date


def _sum_amount(queryset, **filters) -> Decimal:
    return queryset.filter(**filters).aggregate(
        total=Coalesce(
            Sum("amount"),
            Value(Decimal("0.00"), output_field=DecimalField()),
        )
    )["total"]


class ReportService:
    @staticmethod
    def build_member_statement(
        *,
        chama_id,
        member_id,
        from_date=None,
        to_date=None,
    ) -> dict:
        payload = FinanceService.compute_member_statement(
            chama_id,
            member_id,
            from_date,
            to_date,
        )
        payload["generated_at"] = timezone.now().isoformat()
        payload["report_type"] = ReportType.MEMBER_STATEMENT
        return payload

    @staticmethod
    def build_loan_statement(
        *,
        chama_id,
        member_id,
        from_date=None,
        to_date=None,
    ) -> dict:
        payload = ReportService.build_member_statement(
            chama_id=chama_id,
            member_id=member_id,
            from_date=from_date,
            to_date=to_date,
        )
        payload["report_type"] = ReportType.LOAN_STATEMENT
        return payload

    @staticmethod
    def build_chama_summary(*, chama_id, month: int, year: int) -> dict:
        chama = get_object_or_404(Chama, id=chama_id)
        start_date, end_date = _month_bounds(year=year, month=month)

        month_ledger = LedgerEntry.objects.filter(
            chama=chama,
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )

        credits_total = _sum_amount(month_ledger, direction=LedgerDirection.CREDIT)
        debits_total = _sum_amount(month_ledger, direction=LedgerDirection.DEBIT)
        net_cashflow = to_decimal(credits_total - debits_total)

        contributions_total = _sum_amount(
            month_ledger,
            entry_type=LedgerEntryType.CONTRIBUTION,
            direction=LedgerDirection.CREDIT,
        )
        repayments_total = _sum_amount(
            month_ledger,
            entry_type=LedgerEntryType.REPAYMENT,
            direction=LedgerDirection.CREDIT,
        )
        penalties_issued_total = _sum_amount(
            month_ledger,
            entry_type=LedgerEntryType.PENALTY,
            direction=LedgerDirection.DEBIT,
        )
        penalties_collected_total = _sum_amount(
            month_ledger,
            entry_type=LedgerEntryType.PENALTY,
            direction=LedgerDirection.CREDIT,
        )
        loans_out_total = _sum_amount(
            month_ledger,
            entry_type=LedgerEntryType.LOAN_DISBURSEMENT,
            direction=LedgerDirection.DEBIT,
        )

        defaulted_loans = Loan.objects.select_related("member").filter(
            chama=chama,
            status=LoanStatus.DEFAULTED,
        )
        overdue_loans = (
            Loan.objects.select_related("member")
            .filter(
                chama=chama,
                installments__status=InstallmentStatus.OVERDUE,
            )
            .exclude(status=LoanStatus.CLEARED)
            .distinct()
        )

        defaulter_rows = {}
        for loan in list(defaulted_loans) + list(overdue_loans):
            repayments_total_for_loan = loan.repayments.aggregate(
                total=Coalesce(
                    Sum("amount"),
                    Value(Decimal("0.00"), output_field=DecimalField()),
                )
            )["total"]
            overdue_count = loan.installments.filter(
                status=InstallmentStatus.OVERDUE
            ).count()

            defaulter_rows[str(loan.id)] = {
                "loan_id": str(loan.id),
                "member_id": str(loan.member_id),
                "member_name": loan.member.full_name,
                "member_phone": loan.member.phone,
                "status": loan.status,
                "principal": str(loan.principal),
                "overdue_installments": overdue_count,
                "outstanding_balance": str(
                    max(loan.principal - repayments_total_for_loan, Decimal("0.00"))
                ),
            }

        overall_dashboard = FinanceService.compute_chama_dashboard(chama.id)

        return {
            "report_type": ReportType.CHAMA_SUMMARY,
            "generated_at": timezone.now().isoformat(),
            "chama_id": str(chama.id),
            "chama_name": chama.name,
            "month": month,
            "year": year,
            "period_start": start_date.isoformat(),
            "period_end": end_date.isoformat(),
            "totals": {
                "contributions": str(to_decimal(contributions_total)),
                "repayments": str(to_decimal(repayments_total)),
                "penalties_issued": str(to_decimal(penalties_issued_total)),
                "penalties_collected": str(to_decimal(penalties_collected_total)),
                "loans_out": str(to_decimal(loans_out_total)),
            },
            "cashflow": {
                "credits": str(to_decimal(credits_total)),
                "debits": str(to_decimal(debits_total)),
                "net": str(net_cashflow),
            },
            "defaulters_count": len(defaulter_rows),
            "defaulters": list(defaulter_rows.values()),
            "overall_dashboard": overall_dashboard,
        }

    @staticmethod
    def build_loan_monthly_summary(*, chama_id, month: int, year: int) -> dict:
        chama = get_object_or_404(Chama, id=chama_id)
        start_date, end_date = _month_bounds(year=year, month=month)
        ledger = LedgerEntry.objects.filter(
            chama=chama,
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )
        loans_requested = Loan.objects.filter(
            chama=chama,
            requested_at__date__gte=start_date,
            requested_at__date__lte=end_date,
        ).count()
        approvals = LoanApprovalLog.objects.filter(
            loan__chama=chama,
            acted_at__date__gte=start_date,
            acted_at__date__lte=end_date,
        ).count()
        portfolio = FinanceService.compute_loan_portfolio(chama.id)
        return {
            "report_type": ReportType.LOAN_MONTHLY_SUMMARY,
            "generated_at": timezone.now().isoformat(),
            "chama_id": str(chama.id),
            "chama_name": chama.name,
            "month": month,
            "year": year,
            "period_start": start_date.isoformat(),
            "period_end": end_date.isoformat(),
            "totals": {
                "loans_out": str(
                    to_decimal(
                        _sum_amount(
                            ledger,
                            entry_type=LedgerEntryType.LOAN_DISBURSEMENT,
                            direction=LedgerDirection.DEBIT,
                        )
                    )
                ),
                "repayments": str(
                    to_decimal(
                        _sum_amount(
                            ledger,
                            entry_type=LedgerEntryType.REPAYMENT,
                            direction=LedgerDirection.CREDIT,
                        )
                    )
                ),
                "loans_requested_count": loans_requested,
                "approval_actions_count": approvals,
            },
            "portfolio": portfolio,
        }

    @staticmethod
    def build_loan_schedule(*, chama_id, loan_id) -> dict:
        loan = get_object_or_404(
            Loan.objects.select_related("member", "chama"),
            id=loan_id,
            chama_id=chama_id,
        )
        schedule = InstallmentSchedule.objects.filter(loan=loan).order_by(
            "due_date", "created_at"
        )
        return {
            "report_type": ReportType.LOAN_SCHEDULE,
            "generated_at": timezone.now().isoformat(),
            "loan": {
                "loan_id": str(loan.id),
                "member_id": str(loan.member_id),
                "member_name": loan.member.full_name,
                "principal": str(loan.principal),
                "interest_type": loan.interest_type,
                "interest_rate": str(loan.interest_rate),
                "duration_months": loan.duration_months,
                "status": loan.status,
            },
            "schedule": [
                {
                    "id": str(item.id),
                    "due_date": item.due_date.isoformat(),
                    "expected_amount": str(item.expected_amount),
                    "expected_principal": str(item.expected_principal),
                    "expected_interest": str(item.expected_interest),
                    "expected_penalty": str(item.expected_penalty),
                    "status": item.status,
                }
                for item in schedule
            ],
        }

    @staticmethod
    def build_loan_approvals_log(
        *, chama_id, month: int | None = None, year: int | None = None
    ) -> dict:
        chama = get_object_or_404(Chama, id=chama_id)
        queryset = LoanApprovalLog.objects.select_related(
            "loan", "actor", "loan__member"
        ).filter(loan__chama=chama)
        if month and year:
            start_date, end_date = _month_bounds(year=year, month=month)
            queryset = queryset.filter(
                acted_at__date__gte=start_date,
                acted_at__date__lte=end_date,
            )
        rows = [
            {
                "loan_id": str(item.loan_id),
                "member_id": str(item.loan.member_id),
                "member_name": item.loan.member.full_name,
                "stage": item.stage,
                "decision": item.decision,
                "actor_id": str(item.actor_id) if item.actor_id else None,
                "actor_name": item.actor.full_name if item.actor else None,
                "note": item.note,
                "acted_at": item.acted_at.isoformat(),
            }
            for item in queryset.order_by("acted_at", "created_at")
        ]
        return {
            "report_type": ReportType.LOAN_APPROVALS_LOG,
            "generated_at": timezone.now().isoformat(),
            "chama_id": str(chama.id),
            "chama_name": chama.name,
            "month": month,
            "year": year,
            "rows": rows,
            "count": len(rows),
        }

    @staticmethod
    def build_chama_health_score(*, chama_id) -> dict:
        chama = get_object_or_404(Chama, id=chama_id)
        portfolio = FinanceService.compute_loan_portfolio(chama.id, mask_members=False)

        now = timezone.localdate()
        last_3_months = [now - timedelta(days=30 * idx) for idx in range(3)]
        contribution_month_hits = 0
        for month_ref in last_3_months:
            monthly_total = Contribution.objects.filter(
                chama=chama,
                date_paid__year=month_ref.year,
                date_paid__month=month_ref.month,
            ).aggregate(
                total=Coalesce(
                    Sum("amount"),
                    Value(Decimal("0.00"), output_field=DecimalField()),
                )
            )[
                "total"
            ]
            if Decimal(monthly_total or Decimal("0.00")) > Decimal("0.00"):
                contribution_month_hits += 1
        contribution_consistency = Decimal(contribution_month_hits) / Decimal("3")

        meetings = Meeting.objects.filter(
            chama=chama,
            date__date__gte=now - timedelta(days=90),
        )
        attendance_total = Attendance.objects.filter(meeting__in=meetings).count()
        attendance_present = Attendance.objects.filter(
            meeting__in=meetings,
            status=AttendanceStatus.PRESENT,
        ).count()
        attendance_rate = Decimal("1.00")
        if attendance_total > 0:
            attendance_rate = Decimal(attendance_present) / Decimal(attendance_total)

        repayment_rate = Decimal(str(portfolio.get("repayment_rate_percent", "0")))
        repayment_rate = (repayment_rate / Decimal("100")).quantize(Decimal("0.0001"))

        issue_total = Issue.objects.filter(chama=chama).count()
        issue_open = Issue.objects.filter(
            chama=chama,
            status__in=[
                IssueStatus.OPEN,
                IssueStatus.PENDING_ASSIGNMENT,
                IssueStatus.ASSIGNED,
                IssueStatus.CLARIFICATION_REQUESTED,
                IssueStatus.UNDER_INVESTIGATION,
                IssueStatus.IN_PROGRESS,
                IssueStatus.RESOLUTION_PROPOSED,
                IssueStatus.AWAITING_CHAIRPERSON_APPROVAL,
                IssueStatus.REOPENED,
                IssueStatus.ESCALATED,
                IssueStatus.IN_VOTE,
            ],
        ).count()
        issue_closure_rate = Decimal("1.00")
        if issue_total > 0:
            issue_closure_rate = Decimal(issue_total - issue_open) / Decimal(
                issue_total
            )

        weighted = (
            contribution_consistency * Decimal("0.30")
            + attendance_rate * Decimal("0.20")
            + repayment_rate * Decimal("0.35")
            + issue_closure_rate * Decimal("0.15")
        )
        health_score = int(
            max(0, min(100, (weighted * Decimal("100")).quantize(Decimal("1"))))
        )

        return {
            "report_type": ReportType.CHAMA_HEALTH_SCORE,
            "generated_at": timezone.now().isoformat(),
            "chama_id": str(chama.id),
            "chama_name": chama.name,
            "health_score": health_score,
            "components": {
                "contribution_consistency_percent": str(
                    to_decimal(contribution_consistency * Decimal("100"))
                ),
                "attendance_rate_percent": str(
                    to_decimal(attendance_rate * Decimal("100"))
                ),
                "repayment_rate_percent": str(
                    to_decimal(repayment_rate * Decimal("100"))
                ),
                "issue_closure_rate_percent": str(
                    to_decimal(issue_closure_rate * Decimal("100"))
                ),
            },
            "portfolio": portfolio,
        }

    @staticmethod
    def build_collection_forecast(*, chama_id, months: int = 3) -> dict:
        chama = get_object_or_404(Chama, id=chama_id)
        months = max(1, min(int(months), 12))
        source_months = 6
        since = timezone.now() - timedelta(days=31 * source_months)
        ledger = (
            LedgerEntry.objects.filter(
                chama=chama,
                created_at__gte=since,
                direction=LedgerDirection.CREDIT,
                entry_type__in=[
                    LedgerEntryType.CONTRIBUTION,
                    LedgerEntryType.REPAYMENT,
                ],
            )
            .annotate(month=TruncMonth("created_at"))
            .values("month")
            .annotate(
                total=Coalesce(
                    Sum("amount"), Value(Decimal("0.00"), output_field=DecimalField())
                )
            )
            .order_by("month")
        )
        history = [
            {
                "month": item["month"].date().isoformat() if item["month"] else None,
                "collected": str(to_decimal(item["total"])),
            }
            for item in ledger
        ]
        totals = [Decimal(str(item["total"])) for item in ledger]
        baseline = Decimal("0.00")
        if totals:
            baseline = sum(totals) / Decimal(len(totals))
        baseline = to_decimal(baseline)

        forecast = []
        for idx in range(1, months + 1):
            target_month = (
                timezone.localdate().replace(day=1) + timedelta(days=32 * idx)
            ).replace(day=1)
            forecast.append(
                {
                    "month": target_month.isoformat(),
                    "forecast_amount": str(baseline),
                }
            )

        return {
            "report_type": ReportType.COLLECTION_FORECAST,
            "generated_at": timezone.now().isoformat(),
            "chama_id": str(chama.id),
            "chama_name": chama.name,
            "baseline_monthly_collection": str(baseline),
            "history": history,
            "forecast": forecast,
        }

    @staticmethod
    def build_defaulter_risk(*, chama_id) -> dict:
        chama = get_object_or_404(Chama, id=chama_id)
        portfolio = FinanceService.compute_loan_portfolio(chama.id, mask_members=False)
        rows = []
        for row in portfolio.get("defaulters", []):
            overdue = int(row.get("overdue_installments") or 0)
            outstanding = Decimal(str(row.get("outstanding_balance") or "0.00"))
            risk = "low"
            if overdue >= 3 or outstanding >= Decimal("50000.00"):
                risk = "high"
            elif overdue >= 1 or outstanding >= Decimal("10000.00"):
                risk = "medium"
            rows.append({**row, "risk_tier": risk})

        return {
            "report_type": ReportType.DEFAULTER_RISK,
            "generated_at": timezone.now().isoformat(),
            "chama_id": str(chama.id),
            "chama_name": chama.name,
            "count": len(rows),
            "rows": rows,
        }

    @staticmethod
    def build_member_cohort_analysis(*, chama_id, months: int = 6) -> dict:
        chama = get_object_or_404(Chama, id=chama_id)
        horizon = max(1, min(int(months), 24))

        memberships = Membership.objects.filter(
            chama=chama,
            is_approved=True,
        ).only("user_id", "joined_at")
        join_month_by_member = {
            str(item.user_id): item.joined_at.date() for item in memberships
        }

        activity_months_by_member: dict[str, set[date]] = {
            member_id: set() for member_id in join_month_by_member
        }

        contribution_rows = Contribution.objects.filter(
            chama=chama,
            member_id__in=list(join_month_by_member.keys()),
        ).values("member_id", "date_paid")
        for row in contribution_rows:
            member_id = str(row["member_id"])
            activity_months_by_member.setdefault(member_id, set()).add(row["date_paid"])

        repayment_rows = Repayment.objects.filter(
            loan__chama=chama,
            loan__member_id__in=list(join_month_by_member.keys()),
        ).values("loan__member_id", "date_paid")
        for row in repayment_rows:
            member_id = str(row["loan__member_id"])
            activity_months_by_member.setdefault(member_id, set()).add(row["date_paid"])

        cohort_matrix = compute_member_activity_cohorts(
            join_month_by_member=join_month_by_member,
            activity_months_by_member=activity_months_by_member,
            horizon_months=horizon,
        )

        return {
            "report_type": ReportType.COHORT_ANALYSIS,
            "generated_at": timezone.now().isoformat(),
            "chama_id": str(chama.id),
            "chama_name": chama.name,
            "horizon_months": horizon,
            "cohort_count": len(cohort_matrix),
            "member_count": len(join_month_by_member),
            "cohorts": cohort_matrix,
        }

    @staticmethod
    def build_report_payload(report_type: str, parameters: dict) -> dict:
        if report_type == ReportType.MEMBER_STATEMENT:
            from_date = parameters.get("from_date")
            to_date = parameters.get("to_date")
            return ReportService.build_member_statement(
                chama_id=parameters["chama_id"],
                member_id=parameters["member_id"],
                from_date=parse_iso_date(from_date) if from_date else None,
                to_date=parse_iso_date(to_date) if to_date else None,
            )

        if report_type == ReportType.CHAMA_SUMMARY:
            return ReportService.build_chama_summary(
                chama_id=parameters["chama_id"],
                month=int(parameters["month"]),
                year=int(parameters["year"]),
            )

        if report_type == ReportType.LOAN_STATEMENT:
            from_date = parameters.get("from_date")
            to_date = parameters.get("to_date")
            return ReportService.build_loan_statement(
                chama_id=parameters["chama_id"],
                member_id=parameters["member_id"],
                from_date=parse_iso_date(from_date) if from_date else None,
                to_date=parse_iso_date(to_date) if to_date else None,
            )

        if report_type == ReportType.LOAN_MONTHLY_SUMMARY:
            return ReportService.build_loan_monthly_summary(
                chama_id=parameters["chama_id"],
                month=int(parameters["month"]),
                year=int(parameters["year"]),
            )

        if report_type == ReportType.LOAN_SCHEDULE:
            return ReportService.build_loan_schedule(
                chama_id=parameters["chama_id"],
                loan_id=parameters["loan_id"],
            )

        if report_type == ReportType.LOAN_APPROVALS_LOG:
            return ReportService.build_loan_approvals_log(
                chama_id=parameters["chama_id"],
                month=int(parameters["month"]) if parameters.get("month") else None,
                year=int(parameters["year"]) if parameters.get("year") else None,
            )

        if report_type == ReportType.CHAMA_HEALTH_SCORE:
            return ReportService.build_chama_health_score(
                chama_id=parameters["chama_id"]
            )

        if report_type == ReportType.COLLECTION_FORECAST:
            return ReportService.build_collection_forecast(
                chama_id=parameters["chama_id"],
                months=int(parameters.get("months", 3)),
            )

        if report_type == ReportType.DEFAULTER_RISK:
            return ReportService.build_defaulter_risk(chama_id=parameters["chama_id"])

        if report_type == ReportType.COHORT_ANALYSIS:
            return ReportService.build_member_cohort_analysis(
                chama_id=parameters["chama_id"],
                months=int(parameters.get("months", 6)),
            )

        raise ReportServiceError("Unsupported report type.")
