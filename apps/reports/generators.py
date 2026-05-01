"""
Report Generation Engine for Digital Chama

Implements report algorithms:
- Fund summary
- Contribution compliance
- Loan delinquency
- Reconciliation
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from django.db.models import Count, Sum
from django.utils import timezone

from apps.accounts.models import Membership
from apps.chama.models import Chama
from apps.finance.models import LedgerEntry, Loan, LoanRepayment, Payment


class ReportQueryBuilder:
    """
    Build safe queries from filters.
    Enforce permissions and validate date ranges.
    """
    
    MAX_DATE_RANGE_DAYS = 365  # 1 year max unless admin
    
    @classmethod
    def validate_date_range(cls, date_from: str, date_to: str, is_admin: bool = False) -> tuple:
        """Validate and return date objects."""
        try:
            from_date = datetime.strptime(date_from, "%Y-%m-%d").date()
            to_date = datetime.strptime(date_to, "%Y-%m-%d").date()
            
            # Check range
            if (to_date - from_date).days > cls.MAX_DATE_RANGE_DAYS and not is_admin:
                raise ValueError(f"Date range cannot exceed {cls.MAX_DATE_RANGE_DAYS} days")
            
            if from_date > to_date:
                raise ValueError("date_from must be before date_to")
            
            return from_date, to_date
        except ValueError as e:
            raise ValueError(f"Invalid date format: {e}")
    
    @classmethod
    def validate_member_access(cls, user, chama_id: int) -> Membership | None:
        """Validate user has access to chama data."""
        try:
            return Membership.objects.get(user=user, chama_id=chama_id)
        except Membership.DoesNotExist:
            return None


class FundSummaryReport:
    """
    Fund Summary Report (Admin)
    
    Computes:
    - Opening balance at date_from
    - Total inflows (credits)
    - Total outflows (debits)
    - Closing balance at date_to
    """
    
    @classmethod
    def generate(cls, chama_id: int, date_from: str, date_to: str) -> dict[str, Any]:
        from_date, to_date = ReportQueryBuilder.validate_date_range(date_from, date_to, is_admin=True)
        
        chama = Chama.objects.get(id=chama_id)
        
        # Opening balance (balance at date_from)
        opening_balance = cls._get_balance_at_date(chama_id, from_date)
        
        # Inflows (contributions)
        inflows = LedgerEntry.objects.filter(
            chama_id=chama_id,
            entry_type=LedgerEntry.ENTRY_CONTRIBUTION,
            status=LedgerEntry.STATUS_SUCCESS,
            created_at__date__gte=from_date,
            created_at__date__lte=to_date,
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        
        # Outflows (withdrawals + fees)
        outflows = LedgerEntry.objects.filter(
            chama_id=chama_id,
            entry_type__in=[LedgerEntry.ENTRY_WITHDRAWAL, LedgerEntry.ENTRY_FEE],
            status=LedgerEntry.STATUS_SUCCESS,
            created_at__date__gte=from_date,
            created_at__date__lte=to_date,
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        
        # Loan disbursements (outflow)
        loan_disbursements = Loan.objects.filter(
            chama_id=chama_id,
            status__in=[Loan.STATUS_ACTIVE, Loan.STATUS_APPROVED],
            created_at__date__gte=from_date,
            created_at__date__lte=to_date,
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        
        # Loan repayments (inflow)
        repayments = LoanRepayment.objects.filter(
            loan__chama_id=chama_id,
            status=LoanRepayment.STATUS_COMPLETED,
            created_at__date__gte=from_date,
            created_at__date__lte=to_date,
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        
        total_inflows = inflows + repayments
        total_outflows = outflows + loan_disbursements
        closing_balance = opening_balance + total_inflows - total_outflows
        
        return {
            "chama": {
                "id": chama.id,
                "name": chama.name,
            },
            "period": {
                "from": date_from,
                "to": date_to,
            },
            "summary": {
                "opening_balance": float(opening_balance),
                "contributions": float(inflows),
                "loan_repayments": float(repayments),
                "total_inflows": float(total_inflows),
                "withdrawals": float(outflows),
                "loan_disbursements": float(loan_disbursements),
                "total_outflows": float(total_outflows),
                "closing_balance": float(closing_balance),
            },
            "currency": "KES",
            "generated_at": timezone.now().isoformat(),
        }
    
    @classmethod
    def _get_balance_at_date(cls, chama_id: int, date) -> Decimal:
        """Get balance at specific date."""
        balance = LedgerEntry.objects.filter(
            chama_id=chama_id,
            status=LedgerEntry.STATUS_SUCCESS,
            created_at__date__lt=date,
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        return balance


class LedgerReport:
    """
    Ledger Report (Admin)
    
    All transactions with filters.
    """
    
    @classmethod
    def generate(cls, chama_id: int, date_from: str, date_to: str, 
                 entry_type: str = None, status: str = None,
                 member_id: int = None, limit: int = 1000) -> dict[str, Any]:
        from_date, to_date = ReportQueryBuilder.validate_date_range(date_from, date_to)
        
        queryset = LedgerEntry.objects.filter(
            chama_id=chama_id,
            created_at__date__gte=from_date,
            created_at__date__lte=to_date,
        ).select_related("owner").order_by("-created_at")
        
        # Apply filters
        if entry_type:
            queryset = queryset.filter(entry_type=entry_type)
        if status:
            queryset = queryset.filter(status=status)
        if member_id:
            queryset = queryset.filter(owner_id=member_id)
        
        # Get total count
        total_count = queryset.count()
        
        # Paginate for preview
        transactions = queryset[:limit]
        
        return {
            "chama_id": chama_id,
            "period": {"from": date_from, "to": date_to},
            "filters": {"entry_type": entry_type, "status": status, "member_id": member_id},
            "total_count": total_count,
            "returned_count": len(transactions),
            "transactions": [
                {
                    "id": t.id,
                    "date": t.created_at.isoformat(),
                    "type": t.entry_type,
                    "amount": float(t.amount),
                    "status": t.status,
                    "member_id": t.owner_id,
                    "member_name": t.owner.get_full_name() if t.owner else None,
                    "description": t.description or "",
                    "reference": t.reference or "",
                }
                for t in transactions
            ],
            "generated_at": timezone.now().isoformat(),
        }


class ContributionsReport:
    """
    Contributions Report (Admin)
    
    By member and by month.
    """
    
    @classmethod
    def generate(cls, chama_id: int, date_from: str, date_to: str,
                 member_id: int = None) -> dict[str, Any]:
        from_date, to_date = ReportQueryBuilder.validate_date_range(date_from, date_to)
        
        # Base queryset
        contributions = LedgerEntry.objects.filter(
            chama_id=chama_id,
            entry_type=LedgerEntry.ENTRY_CONTRIBUTION,
            status=LedgerEntry.STATUS_SUCCESS,
            created_at__date__gte=from_date,
            created_at__date__lte=to_date,
        ).select_related("owner")
        
        if member_id:
            contributions = contributions.filter(owner_id=member_id)
        
        # By Member
        by_member = contributions.values("owner__id", "owner__first_name", "owner__last_name").annotate(
            total_amount=Sum("amount"),
            count=Count("id"),
        ).order_by("-total_amount")
        
        # By Month
        by_month = contributions.extra(
            select={"month": "TO_CHAR(created_at, 'YYYY-MM')"}
        ).values("month").annotate(
            total_amount=Sum("amount"),
            count=Count("id"),
        ).order_by("month")
        
        # Total
        total = contributions.aggregate(
            total_amount=Sum("amount"),
            count=Count("id"),
        )
        
        return {
            "chama_id": chama_id,
            "period": {"from": date_from, "to": date_to},
            "summary": {
                "total_amount": float(total["total_amount"] or 0),
                "total_count": total["count"],
            },
            "by_member": [
                {
                    "member_id": m["owner__id"],
                    "member_name": f"{m['owner__first_name']} {m['owner__last_name']}",
                    "total_amount": float(m["total_amount"]),
                    "count": m["count"],
                }
                for m in by_member
            ],
            "by_month": [
                {
                    "month": m["month"],
                    "total_amount": float(m["total_amount"]),
                    "count": m["count"],
                }
                for m in by_month
            ],
            "generated_at": timezone.now().isoformat(),
        }


class LoansReport:
    """
    Loans Report (Admin)
    
    By status: requested, approved, disbursed, active, defaulted.
    """
    
    @classmethod
    def generate(cls, chama_id: int, date_from: str = None, date_to: str = None,
                 status: str = None, member_id: int = None) -> dict[str, Any]:
        
        queryset = Loan.objects.filter(chama_id=chama_id).select_related("borrower")
        
        if date_from and date_to:
            from_date, to_date = ReportQueryBuilder.validate_date_range(date_from, date_to)
            queryset = queryset.filter(created_at__date__gte=from_date, created_at__date__lte=to_date)
        
        if status:
            queryset = queryset.filter(status=status)
        if member_id:
            queryset = queryset.filter(borrower_id=member_id)
        
        # By status
        by_status = queryset.values("status").annotate(
            count=Count("id"),
            total_amount=Sum("amount"),
        )
        
        # Active loans with balances
        active_loans = queryset.filter(status=Loan.STATUS_ACTIVE)
        total_disbursed = active_loans.aggregate(Sum("amount"))["amount__sum"] or Decimal("0")
        total_expected = sum(loan.total_repayment for loan in active_loans)
        total_outstanding = sum(loan.remaining_balance for loan in active_loans)
        
        return {
            "chama_id": chama_id,
            "period": {"from": date_from, "to": date_to},
            "filters": {"status": status, "member_id": member_id},
            "by_status": [
                {
                    "status": s["status"],
                    "count": s["count"],
                    "total_amount": float(s["total_amount"] or 0),
                }
                for s in by_status
            ],
            "active_summary": {
                "total_disbursed": float(total_disbursed),
                "total_expected_repayment": float(total_expected),
                "total_outstanding": float(total_outstanding),
                "loan_count": active_loans.count(),
            },
            "loans": [
                {
                    "id": loan.id,
                    "borrower": f"{loan.borrower.first_name} {loan.borrower.last_name}" if loan.borrower else None,
                    "amount": float(loan.amount),
                    "status": loan.status,
                    "remaining_balance": float(loan.remaining_balance),
                    "monthly_repayment": float(loan.monthly_repayment),
                    "term_months": loan.term_months,
                    "created_at": loan.created_at.isoformat(),
                }
                for loan in queryset[:100]
            ],
            "generated_at": timezone.now().isoformat(),
        }


class ArrearsReport:
    """
    Arrears/Delinquency Report (Admin)
    
    Overdue loans bucketed by days:
    - 1-7 days
    - 8-30 days
    - 31-90 days
    - 90+ days
    """
    
    @classmethod
    def generate(cls, chama_id: int) -> dict[str, Any]:
        today = timezone.now().date()
        
        # Get active loans with overdue payments
        overdue_loans = Loan.objects.filter(
            chama_id=chama_id,
            status=Loan.STATUS_ACTIVE,
        ).select_related("borrower")
        
        buckets = {
            "1-7_days": [],
            "8-30_days": [],
            "31-90_days": [],
            "90_plus_days": [],
        }
        
        total_overdue = Decimal("0")
        
        for loan in overdue_loans:
            if loan.next_repayment_date and loan.next_repayment_date < today:
                days_overdue = (today - loan.next_repayment_date).days
                
                bucket = cls._get_bucket(days_overdue)
                buckets[bucket].append({
                    "loan_id": loan.id,
                    "borrower": f"{loan.borrower.first_name} {loan.borrower.last_name}" if loan.borrower else None,
                    "amount": float(loan.amount),
                    "remaining_balance": float(loan.remaining_balance),
                    "overdue_amount": float(loan.monthly_repayment),
                    "days_overdue": days_overdue,
                    "next_repayment_date": loan.next_repayment_date.isoformat(),
                })
                total_overdue += loan.monthly_repayment
        
        return {
            "chama_id": chama_id,
            "report_date": today.isoformat(),
            "buckets": {
                "1-7_days": {
                    "count": len(buckets["1-7_days"]),
                    "total_amount": sum(l["overdue_amount"] for l in buckets["1-7_days"]),
                    "loans": buckets["1-7_days"],
                },
                "8-30_days": {
                    "count": len(buckets["8-30_days"]),
                    "total_amount": sum(l["overdue_amount"] for l in buckets["8-30_days"]),
                    "loans": buckets["8-30_days"],
                },
                "31-90_days": {
                    "count": len(buckets["31-90_days"]),
                    "total_amount": sum(l["overdue_amount"] for l in buckets["31-90_days"]),
                    "loans": buckets["31-90_days"],
                },
                "90_plus_days": {
                    "count": len(buckets["90_plus_days"]),
                    "total_amount": sum(l["overdue_amount"] for l in buckets["90_plus_days"]),
                    "loans": buckets["90_plus_days"],
                },
            },
            "total_overdue": float(total_overdue),
            "total_loans": sum(len(buckets[b]) for b in buckets),
            "generated_at": timezone.now().isoformat(),
        }
    
    @classmethod
    def _get_bucket(cls, days: int) -> str:
        if days <= 7:
            return "1-7_days"
        elif days <= 30:
            return "8-30_days"
        elif days <= 90:
            return "31-90_days"
        else:
            return "90_plus_days"


class ContributionComplianceReport:
    """
    Member Contribution Compliance Report
    
    For each member:
    - Expected contributions in period
    - Actual contributions
    - Compliance percentage
    """
    
    @classmethod
    def generate(cls, chama_id: int, date_from: str, date_to: str) -> dict[str, Any]:
        from_date, to_date = ReportQueryBuilder.validate_date_range(date_from, date_to)
        
        chama = Chama.objects.get(id=chama_id)
        members = Membership.objects.filter(
            chama_id=chama_id,
            status=Membership.STATUS_ACTIVE,
        ).select_related("user")
        
        # Calculate expected contributions (monthly)
        months = ((to_date.year - from_date.year) * 12 + to_date.month - from_date.month) + 1
        
        member_compliance = []
        total_expected = Decimal("0")
        total_actual = Decimal("0")
        
        for member in members:
            # Skip if joined after period
            if member.join_date and member.join_date > to_date:
                continue
            
            # Calculate expected months
            actual_months = months
            if member.join_date and member.join_date > from_date:
                actual_months = ((to_date.year - member.join_date.year) * 12 + 
                                to_date.month - member.join_date.month) + 1
            
            expected_amount = Decimal(str(actual_months)) * (chama.minimum_contribution or Decimal("5000"))
            
            # Get actual contributions
            actual = LedgerEntry.objects.filter(
                owner=member.user,
                chama_id=chama_id,
                entry_type=LedgerEntry.ENTRY_CONTRIBUTION,
                status=LedgerEntry.STATUS_SUCCESS,
                created_at__date__gte=from_date,
                created_at__date__lte=to_date,
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            
            compliance_pct = (actual / expected_amount * 100) if expected_amount > 0 else 0
            
            member_compliance.append({
                "member_id": member.user_id,
                "member_name": f"{member.user.first_name} {member.user.last_name}",
                "expected_amount": float(expected_amount),
                "actual_amount": float(actual),
                "compliance_percent": float(compliance_pct),
                "status": "compliant" if compliance_pct >= 100 else "partial" if compliance_pct > 0 else "none",
            })
            
            total_expected += expected_amount
            total_actual += actual
        
        # Sort by compliance (worst first)
        member_compliance.sort(key=lambda x: x["compliance_percent"])
        
        return {
            "chama_id": chama_id,
            "period": {"from": date_from, "to": date_to},
            "summary": {
                "total_expected": float(total_expected),
                "total_actual": float(total_actual),
                "overall_compliance": float((total_actual / total_expected * 100) if total_expected > 0 else 0),
                "member_count": len(member_compliance),
                "compliant_count": sum(1 for m in member_compliance if m["compliance_percent"] >= 100),
                "partial_count": sum(1 for m in member_compliance if 0 < m["compliance_percent"] < 100),
                "non_compliant_count": sum(1 for m in member_compliance if m["compliance_percent"] == 0),
            },
            "members": member_compliance,
            "generated_at": timezone.now().isoformat(),
        }


class ReconciliationReport:
    """
    Reconciliation Report
    
    Find anomalies:
    - Payment success but no ledger transaction
    - Ledger exists but no payment receipt
    - Duplicate receipt numbers
    """
    
    @classmethod
    def generate(cls, chama_id: int, date_from: str, date_to: str) -> dict[str, Any]:
        from_date, to_date = ReportQueryBuilder.validate_date_range(date_from, date_to)
        
        anomalies = {
            "payment_no_ledger": [],
            "ledger_no_payment": [],
            "duplicate_receipts": [],
        }
        
        # Payments with success status
        payments = Payment.objects.filter(
            chama_id=chama_id,
            status=Payment.STATUS_SUCCESS,
            created_at__date__gte=from_date,
            created_at__date__lte=to_date,
        )
        
        # Check: payment success but no ledger
        for payment in payments:
            has_ledger = LedgerEntry.objects.filter(
                reference=payment.receipt_number,
            ).exists()
            
            if not has_ledger:
                anomalies["payment_no_ledger"].append({
                    "payment_id": payment.id,
                    "receipt_number": payment.receipt_number,
                    "amount": float(payment.amount),
                    "phone": payment.phone_number,
                    "created_at": payment.created_at.isoformat(),
                })
        
        # Check: duplicate receipt numbers
        receipt_counts = payments.values("receipt_number").annotate(
            count=Count("id")
        ).filter(count__gt=1)
        
        for rcpt in receipt_counts:
            dup_payments = payments.filter(receipt_number=rcpt["receipt_number"])
            anomalies["duplicate_receipts"].append({
                "receipt_number": rcpt["receipt_number"],
                "count": rcpt["count"],
                "payments": [
                    {"id": p.id, "amount": float(p.amount), "date": p.created_at.isoformat()}
                    for p in dup_payments
                ],
            })
        
        return {
            "chama_id": chama_id,
            "period": {"from": date_from, "to": date_to},
            "summary": {
                "total_payments": payments.count(),
                "payments_missing_ledger": len(anomalies["payment_no_ledger"]),
                "duplicate_receipts": len(anomalies["duplicate_receipts"]),
                "total_anomalies": sum(len(anomalies[k]) for k in anomalies),
            },
            "anomalies": anomalies,
            "generated_at": timezone.now().isoformat(),
        }


class MemberStatementReport:
    """
    Member Statement Report
    
    Account statement with:
    - Contributions
    - Repayments
    - Withdrawals
    - Fees
    """
    
    @classmethod
    def generate(cls, chama_id: int, member_id: int, date_from: str, date_to: str) -> dict[str, Any]:
        from_date, to_date = ReportQueryBuilder.validate_date_range(date_from, date_to)
        
        member = Membership.objects.get(chama_id=chama_id, user_id=member_id)
        
        # Get all transactions
        transactions = LedgerEntry.objects.filter(
            chama_id=chama_id,
            owner_id=member_id,
            created_at__date__gte=from_date,
            created_at__date__lte=to_date,
        ).order_by("created_at")
        
        # Calculate running balance
        # First get opening balance
        opening_balance = LedgerEntry.objects.filter(
            chama_id=chama_id,
            owner_id=member_id,
            created_at__date__lt=from_date,
            status=LedgerEntry.STATUS_SUCCESS,
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        
        # Build transaction list with running balance
        running = opening_balance
        tx_list = []
        
        for tx in transactions:
            if tx.entry_type in [LedgerEntry.ENTRY_CONTRIBUTION, LedgerEntry.ENTRY_LOAN_DISBURSEMENT, LedgerEntry.ENTRY_LOAN_REPAYMENT]:
                running += tx.amount
            else:
                running -= tx.amount
            
            tx_list.append({
                "date": tx.created_at.isoformat(),
                "type": tx.entry_type,
                "description": tx.description or "",
                "amount": float(tx.amount),
                "running_balance": float(running),
                "status": tx.status,
                "reference": tx.reference or "",
            })
        
        # Get loan info
        loans = Loan.objects.filter(
            chama_id=chama_id,
            borrower_id=member_id,
            status__in=[Loan.STATUS_ACTIVE, Loan.STATUS_APPROVED],
        )
        
        return {
            "member": {
                "id": member_id,
                "name": f"{member.user.first_name} {member.user.last_name}",
                "phone": member.user.phone_number[-4:],  # Masked
            },
            "chama_id": chama_id,
            "period": {"from": date_from, "to": date_to},
            "opening_balance": float(opening_balance),
            "closing_balance": float(running),
            "transactions": tx_list,
            "active_loans": [
                {
                    "id": loan.id,
                    "amount": float(loan.amount),
                    "remaining": float(loan.remaining_balance),
                    "monthly": float(loan.monthly_repayment),
                }
                for loan in loans
            ],
            "currency": "KES",
            "generated_at": timezone.now().isoformat(),
        }


# Registry of all report generators
REPORT_GENERATORS = {
    "chama_summary": FundSummaryReport.generate,
    "chama_ledger": LedgerReport.generate,
    "chama_contributions": ContributionsReport.generate,
    "chama_loans": LoansReport.generate,
    "chama_arrears": ArrearsReport.generate,
    "chama_reconciliation": ReconciliationReport.generate,
    "member_contribution_compliance": ContributionComplianceReport.generate,
    "member_statement": MemberStatementReport.generate,
}
