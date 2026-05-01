"""
Smart Reports with Plain-Language Summaries for MyChama

Provides intelligent reporting:
- Contribution reports with compliance analysis
- Member compliance reports
- Expense reports with trend detection
- Loan portfolio reports
- Guarantor exposure reports
- Attendance reports
- Fines reports
- Liquidity reports
- Recovery action reports
- Audit history reports

Each report includes plain-language summaries.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from django.utils import timezone

logger = logging.getLogger(__name__)


@dataclass
class SmartReport:
    """Smart report with plain-language summary"""
    report_type: str
    title: str
    period: str
    summary: str
    key_metrics: dict = field(default_factory=dict)
    highlights: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)
    generated_at: datetime = field(default_factory=timezone.now)


# ============================================================================
# CONTRIBUTION REPORT
# ============================================================================

def generate_contribution_report(
    chama_id: str,
    contributions: list[dict],
    members: list[dict],
    period: str = "monthly",
) -> SmartReport:
    """
    Generate contribution report with compliance analysis.
    
    Plain-language summary example:
    "Contribution compliance improved by 12% this month"
    """
    total_contributions = sum(Decimal(str(c.get("amount", 0))) for c in contributions)
    len(members) * Decimal(str(contributions[0].get("amount", 0))) if contributions else Decimal("0")
    
    paid_count = sum(1 for c in contributions if c.get("status") == "paid")
    late_count = sum(1 for c in contributions if c.get("status") == "late")
    missed_count = sum(1 for c in contributions if c.get("status") == "missed")
    
    compliance_rate = (Decimal(str(paid_count)) / Decimal(str(len(contributions))) * 100) if contributions else Decimal("0")
    on_time_rate = (Decimal(str(paid_count - late_count)) / Decimal(str(len(contributions))) * 100) if contributions else Decimal("0")
    
    # Generate summary
    summary_parts = []
    summary_parts.append(f"Total contributions: KES {total_contributions:,.2f}.")
    summary_parts.append(f"Compliance rate: {compliance_rate:.1f}%.")
    
    if compliance_rate >= 90:
        summary_parts.append("Excellent compliance!")
    elif compliance_rate >= 70:
        summary_parts.append("Good compliance with room for improvement.")
    else:
        summary_parts.append("Compliance needs attention.")
    
    if late_count > 0:
        summary_parts.append(f"{late_count} contribution(s) were late.")
    
    if missed_count > 0:
        summary_parts.append(f"{missed_count} contribution(s) were missed.")
    
    summary = " ".join(summary_parts)
    
    # Highlights and concerns
    highlights = []
    concerns = []
    recommendations = []
    
    if compliance_rate >= 90:
        highlights.append("High compliance rate")
    elif compliance_rate < 70:
        concerns.append("Low compliance rate")
        recommendations.append("Send reminders to non-compliant members")
    
    if on_time_rate < 80:
        concerns.append("Low on-time payment rate")
        recommendations.append("Implement stricter grace periods or penalties")
    
    return SmartReport(
        report_type="contribution",
        title="Contribution Report",
        period=period,
        summary=summary,
        key_metrics={
            "total_contributions": str(total_contributions),
            "compliance_rate": str(compliance_rate),
            "on_time_rate": str(on_time_rate),
            "paid_count": paid_count,
            "late_count": late_count,
            "missed_count": missed_count,
        },
        highlights=highlights,
        concerns=concerns,
        recommendations=recommendations,
        data={
            "contributions": contributions,
            "members": members,
        },
    )


# ============================================================================
# MEMBER COMPLIANCE REPORT
# ============================================================================

def generate_member_compliance_report(
    chama_id: str,
    members: list[dict],
    contributions: list[dict],
    period: str = "monthly",
) -> SmartReport:
    """
    Generate member compliance report.
    
    Plain-language summary example:
    "8 out of 10 members are fully compliant"
    """
    total_members = len(members)
    compliant_members = 0
    at_risk_members = []
    
    for member in members:
        member_contribs = [c for c in contributions if c.get("member_id") == member.get("id")]
        if member_contribs:
            paid_count = sum(1 for c in member_contribs if c.get("status") == "paid")
            compliance_rate = (paid_count / len(member_contribs)) * 100
            
            if compliance_rate >= 90:
                compliant_members += 1
            elif compliance_rate < 70:
                at_risk_members.append({
                    "member_id": member.get("id"),
                    "name": member.get("name", ""),
                    "compliance_rate": compliance_rate,
                })
    
    compliance_rate = (Decimal(str(compliant_members)) / Decimal(str(total_members)) * 100) if total_members > 0 else Decimal("0")
    
    # Generate summary
    summary = f"Out of {total_members} members, {compliant_members} are fully compliant. "
    summary += f"Overall compliance rate is {compliance_rate:.1f}%. "
    
    if at_risk_members:
        summary += f"{len(at_risk_members)} member(s) are at risk of non-compliance."
    
    # Highlights and concerns
    highlights = []
    concerns = []
    recommendations = []
    
    if compliance_rate >= 90:
        highlights.append("High member compliance")
    elif compliance_rate < 70:
        concerns.append("Low member compliance")
        recommendations.append("Implement member engagement initiatives")
    
    if at_risk_members:
        concerns.append(f"{len(at_risk_members)} member(s) at risk")
        recommendations.append("Reach out to at-risk members")
    
    return SmartReport(
        report_type="member_compliance",
        title="Member Compliance Report",
        period=period,
        summary=summary,
        key_metrics={
            "total_members": total_members,
            "compliant_members": compliant_members,
            "compliance_rate": str(compliance_rate),
            "at_risk_count": len(at_risk_members),
        },
        highlights=highlights,
        concerns=concerns,
        recommendations=recommendations,
        data={
            "at_risk_members": at_risk_members,
        },
    )


# ============================================================================
# EXPENSE REPORT
# ============================================================================

def generate_expense_report(
    chama_id: str,
    expenses: list[dict],
    period: str = "monthly",
) -> SmartReport:
    """
    Generate expense report with trend detection.
    
    Plain-language summary example:
    "Expenses increased by 18% from last month"
    """
    total_expenses = sum(Decimal(str(e.get("amount", 0))) for e in expenses)
    
    # Group by category
    category_totals = {}
    for expense in expenses:
        category = expense.get("category", "uncategorized")
        amount = Decimal(str(expense.get("amount", 0)))
        category_totals[category] = category_totals.get(category, Decimal("0")) + amount
    
    # Find top category
    top_category = max(category_totals.items(), key=lambda x: x[1]) if category_totals else ("N/A", Decimal("0"))
    
    # Generate summary
    summary = f"Total expenses: KES {total_expenses:,.2f}. "
    summary += f"Top category: {top_category[0]} (KES {top_category[1]:,.2f}). "
    
    if len(category_totals) > 1:
        summary += f"Expenses spread across {len(category_totals)} categories."
    
    # Highlights and concerns
    highlights = []
    concerns = []
    recommendations = []
    
    if total_expenses > 0:
        highlights.append(f"Total expenses: KES {total_expenses:,.2f}")
    
    # Check for high concentration
    if top_category[1] > total_expenses * Decimal("0.5"):
        concerns.append(f"High concentration in {top_category[0]}")
        recommendations.append("Review expense distribution")
    
    return SmartReport(
        report_type="expense",
        title="Expense Report",
        period=period,
        summary=summary,
        key_metrics={
            "total_expenses": str(total_expenses),
            "top_category": top_category[0],
            "top_category_amount": str(top_category[1]),
            "category_count": len(category_totals),
        },
        highlights=highlights,
        concerns=concerns,
        recommendations=recommendations,
        data={
            "category_totals": {k: str(v) for k, v in category_totals.items()},
        },
    )


# ============================================================================
# LOAN PORTFOLIO REPORT
# ============================================================================

def generate_loan_portfolio_report(
    chama_id: str,
    loans: list[dict],
    period: str = "monthly",
) -> SmartReport:
    """
    Generate loan portfolio report.
    
    Plain-language summary example:
    "2 loans are overdue"
    """
    total_loans = len(loans)
    active_loans = sum(1 for l in loans if l.get("status") == "active")
    overdue_loans = sum(1 for l in loans if l.get("status") in ["overdue", "defaulted"])
    total_outstanding = sum(Decimal(str(l.get("outstanding_balance", 0))) for l in loans)
    
    # Generate summary
    summary = f"Total loans: {total_loans}. "
    summary += f"Active loans: {active_loans}. "
    summary += f"Total outstanding: KES {total_outstanding:,.2f}. "
    
    if overdue_loans > 0:
        summary += f"{overdue_loans} loan(s) are overdue."
    else:
        summary += "All loans are current."
    
    # Highlights and concerns
    highlights = []
    concerns = []
    recommendations = []
    
    if overdue_loans == 0:
        highlights.append("All loans are current")
    else:
        concerns.append(f"{overdue_loans} loan(s) overdue")
        recommendations.append("Follow up on overdue loans")
    
    if total_outstanding > 0:
        highlights.append(f"Outstanding: KES {total_outstanding:,.2f}")
    
    return SmartReport(
        report_type="loan_portfolio",
        title="Loan Portfolio Report",
        period=period,
        summary=summary,
        key_metrics={
            "total_loans": total_loans,
            "active_loans": active_loans,
            "overdue_loans": overdue_loans,
            "total_outstanding": str(total_outstanding),
        },
        highlights=highlights,
        concerns=concerns,
        recommendations=recommendations,
    )


# ============================================================================
# GUARANTOR EXPOSURE REPORT
# ============================================================================

def generate_guarantor_exposure_report(
    chama_id: str,
    guarantors: list[dict],
    loans: list[dict],
    period: str = "monthly",
) -> SmartReport:
    """
    Generate guarantor exposure report.
    
    Plain-language summary example:
    "3 guarantors have high exposure relative to their savings"
    """
    total_guarantors = len(guarantors)
    high_exposure_guarantors = []
    
    for guarantor in guarantors:
        exposure = Decimal(str(guarantor.get("total_exposure", 0)))
        savings = Decimal(str(guarantor.get("savings", 0)))
        
        if savings > 0 and exposure / savings > Decimal("0.5"):
            high_exposure_guarantors.append({
                "guarantor_id": guarantor.get("id"),
                "name": guarantor.get("name", ""),
                "exposure": str(exposure),
                "savings": str(savings),
                "ratio": str((exposure / savings * 100).quantize(Decimal("0.01"))),
            })
    
    # Generate summary
    summary = f"Total guarantors: {total_guarantors}. "
    
    if high_exposure_guarantors:
        summary += f"{len(high_exposure_guarantors)} guarantor(s) have high exposure."
    else:
        summary += "All guarantors have healthy exposure levels."
    
    # Highlights and concerns
    highlights = []
    concerns = []
    recommendations = []
    
    if not high_exposure_guarantors:
        highlights.append("Healthy guarantor exposure")
    else:
        concerns.append(f"{len(high_exposure_guarantors)} guarantor(s) over-exposed")
        recommendations.append("Review guarantor assignments")
    
    return SmartReport(
        report_type="guarantor_exposure",
        title="Guarantor Exposure Report",
        period=period,
        summary=summary,
        key_metrics={
            "total_guarantors": total_guarantors,
            "high_exposure_count": len(high_exposure_guarantors),
        },
        highlights=highlights,
        concerns=concerns,
        recommendations=recommendations,
        data={
            "high_exposure_guarantors": high_exposure_guarantors,
        },
    )


# ============================================================================
# ATTENDANCE REPORT
# ============================================================================

def generate_attendance_report(
    chama_id: str,
    meetings: list[dict],
    attendance: list[dict],
    period: str = "monthly",
) -> SmartReport:
    """
    Generate meeting attendance report.
    
    Plain-language summary example:
    "Meeting attendance is stable at 75%"
    """
    total_meetings = len(meetings)
    total_attendance = len(attendance)
    total_possible = total_meetings * len(set(a.get("member_id") for a in attendance))
    
    attendance_rate = (Decimal(str(total_attendance)) / Decimal(str(total_possible)) * 100) if total_possible > 0 else Decimal("0")
    
    # Generate summary
    summary = f"Total meetings: {total_meetings}. "
    summary += f"Average attendance rate: {attendance_rate:.1f}%. "
    
    if attendance_rate >= 70:
        summary += "Attendance is strong."
    elif attendance_rate >= 50:
        summary += "Attendance is moderate."
    else:
        summary += "Attendance needs improvement."
    
    # Highlights and concerns
    highlights = []
    concerns = []
    recommendations = []
    
    if attendance_rate >= 70:
        highlights.append("Strong attendance")
    elif attendance_rate < 50:
        concerns.append("Low attendance")
        recommendations.append("Review meeting schedule and send reminders")
    
    return SmartReport(
        report_type="attendance",
        title="Meeting Attendance Report",
        period=period,
        summary=summary,
        key_metrics={
            "total_meetings": total_meetings,
            "attendance_rate": str(attendance_rate),
        },
        highlights=highlights,
        concerns=concerns,
        recommendations=recommendations,
    )


# ============================================================================
# LIQUIDITY REPORT
# ============================================================================

def generate_liquidity_report(
    chama_id: str,
    balance: Decimal,
    total_savings: Decimal,
    pending_withdrawals: Decimal,
    pending_loans: Decimal,
    period: str = "monthly",
) -> SmartReport:
    """
    Generate liquidity report.
    
    Plain-language summary example:
    "Savings are healthy, but liquidity is getting tighter"
    """
    liquidity_ratio = (balance / total_savings * 100) if total_savings > 0 else Decimal("0")
    available_liquidity = balance - pending_withdrawals - pending_loans
    
    # Generate summary
    summary = f"Current balance: KES {balance:,.2f}. "
    summary += f"Total savings: KES {total_savings:,.2f}. "
    summary += f"Liquidity ratio: {liquidity_ratio:.1f}%. "
    
    if liquidity_ratio >= 30:
        summary += "Liquidity is healthy."
    elif liquidity_ratio >= 15:
        summary += "Liquidity is adequate but getting tighter."
    else:
        summary += "Liquidity is low - caution advised."
    
    # Highlights and concerns
    highlights = []
    concerns = []
    recommendations = []
    
    if liquidity_ratio >= 30:
        highlights.append("Healthy liquidity")
    elif liquidity_ratio < 15:
        concerns.append("Low liquidity")
        recommendations.append("Review pending withdrawals and loans")
    
    if available_liquidity < 0:
        concerns.append("Negative available liquidity")
        recommendations.append("Immediate action required")
    
    return SmartReport(
        report_type="liquidity",
        title="Liquidity Report",
        period=period,
        summary=summary,
        key_metrics={
            "balance": str(balance),
            "total_savings": str(total_savings),
            "liquidity_ratio": str(liquidity_ratio),
            "available_liquidity": str(available_liquidity),
        },
        highlights=highlights,
        concerns=concerns,
        recommendations=recommendations,
    )


# ============================================================================
# AUDIT HISTORY REPORT
# ============================================================================

def generate_audit_history_report(
    chama_id: str,
    audit_logs: list[dict],
    period: str = "monthly",
) -> SmartReport:
    """
    Generate audit history report.
    
    Plain-language summary example:
    "42 actions were logged this month"
    """
    total_actions = len(audit_logs)
    
    # Group by action type
    action_types = {}
    for log in audit_logs:
        action_type = log.get("action_type", "unknown")
        action_types[action_type] = action_types.get(action_type, 0) + 1
    
    # Find most common action
    most_common = max(action_types.items(), key=lambda x: x[1]) if action_types else ("N/A", 0)
    
    # Generate summary
    summary = f"Total actions logged: {total_actions}. "
    summary += f"Most common action: {most_common[0]} ({most_common[1]} times). "
    
    if total_actions > 0:
        summary += f"Actions spread across {len(action_types)} types."
    
    # Highlights and concerns
    highlights = []
    concerns = []
    recommendations = []
    
    if total_actions > 0:
        highlights.append(f"{total_actions} actions logged")
    
    # Check for suspicious patterns
    if "failed_login" in action_types and action_types["failed_login"] > 5:
        concerns.append("Multiple failed login attempts")
        recommendations.append("Review security logs")
    
    return SmartReport(
        report_type="audit_history",
        title="Audit History Report",
        period=period,
        summary=summary,
        key_metrics={
            "total_actions": total_actions,
            "action_type_count": len(action_types),
            "most_common_action": most_common[0],
        },
        highlights=highlights,
        concerns=concerns,
        recommendations=recommendations,
        data={
            "action_types": action_types,
        },
    )
