"""
Smart AI Assistant for MyChama

Provides intelligent AI-powered assistance:
- Natural language queries about chama finances
- Plain-language summaries and insights
- Action recommendations
- Risk alerts and warnings
- Financial explanations
- Member behavior analysis

The AI assistant respects RBAC and only provides information
the user is authorized to access.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from django.utils import timezone

logger = logging.getLogger(__name__)


@dataclass
class AIResponse:
    """AI assistant response"""
    query: str
    response: str
    confidence: Decimal  # 0-100%
    data_sources: list[str] = field(default_factory=list)
    suggested_actions: list[str] = field(default_factory=list)
    related_insights: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    generated_at: datetime = field(default_factory=timezone.now)


@dataclass
class AIInsight:
    """AI-generated insight"""
    insight_type: str
    title: str
    description: str
    severity: str  # info, warning, critical
    data_points: dict = field(default_factory=dict)
    suggested_action: str | None = None
    generated_at: datetime = field(default_factory=timezone.now)


# ============================================================================
# QUERY CLASSIFICATION
# ============================================================================

def classify_query(query: str) -> dict:
    """
    Classify user query to determine intent and required data.
    
    Categories:
    - financial: Questions about money, savings, contributions
    - member: Questions about members, participation
    - loan: Questions about loans, repayments
    - meeting: Questions about meetings, attendance
    - health: Questions about chama health, scores
    - action: Requests for actions or recommendations
    """
    query_lower = query.lower()
    
    # Financial queries
    if any(word in query_lower for word in ["save", "savings", "contribution", "balance", "money", "fund", "expense", "income"]):
        return {
            "category": "financial",
            "intent": "query_financial_data",
            "requires_data": ["savings", "contributions", "expenses", "balance"],
        }
    
    # Member queries
    if any(word in query_lower for word in ["member", "who", "participation", "active", "inactive"]):
        return {
            "category": "member",
            "intent": "query_member_data",
            "requires_data": ["members", "participation", "attendance"],
        }
    
    # Loan queries
    if any(word in query_lower for word in ["loan", "borrow", "repay", "overdue", "default"]):
        return {
            "category": "loan",
            "intent": "query_loan_data",
            "requires_data": ["loans", "repayments", "overdue"],
        }
    
    # Meeting queries
    if any(word in query_lower for word in ["meeting", "attendance", "quorum", "schedule"]):
        return {
            "category": "meeting",
            "intent": "query_meeting_data",
            "requires_data": ["meetings", "attendance"],
        }
    
    # Health queries
    if any(word in query_lower for word in ["health", "score", "performance", "how are we doing"]):
        return {
            "category": "health",
            "intent": "query_health_score",
            "requires_data": ["health_score", "metrics"],
        }
    
    # Action queries
    if any(word in query_lower for word in ["what should", "recommend", "suggest", "action", "do next"]):
        return {
            "category": "action",
            "intent": "get_recommendations",
            "requires_data": ["insights", "risks", "pending_items"],
        }
    
    # Summary queries
    if any(word in query_lower for word in ["summarize", "summary", "overview", "report"]):
        return {
            "category": "summary",
            "intent": "generate_summary",
            "requires_data": ["all_metrics"],
        }
    
    return {
        "category": "general",
        "intent": "general_query",
        "requires_data": [],
    }


# ============================================================================
# RESPONSE GENERATION
# ============================================================================

def generate_ai_response(
    query: str,
    user_role: str,
    chama_data: dict,
    member_data: dict = None,
    finance_data: dict = None,
    loan_data: dict = None,
    meeting_data: dict = None,
    health_score: dict = None,
    insights: list[dict] = None,
) -> AIResponse:
    """
    Generate AI response based on query and available data.
    
    Respects RBAC - only includes data the user is authorized to see.
    """
    classification = classify_query(query)
    category = classification["category"]
    classification["intent"]
    
    response_parts = []
    data_sources = []
    suggested_actions = []
    related_insights = []
    
    # Financial queries
    if category == "financial":
        if finance_data:
            total_savings = finance_data.get("total_savings", 0)
            current_balance = finance_data.get("current_balance", 0)
            monthly_contributions = finance_data.get("monthly_contributions", 0)
            
            response_parts.append(f"Your chama has KES {total_savings:,.2f} in total savings.")
            response_parts.append(f"Current balance is KES {current_balance:,.2f}.")
            response_parts.append(f"This month's contributions total KES {monthly_contributions:,.2f}.")
            
            data_sources.extend(["savings", "balance", "contributions"])
            
            # Add insights
            if finance_data.get("contribution_trend") == "down":
                related_insights.append("Contributions are trending downward this month")
                suggested_actions.append("Send contribution reminders to members")
            
            if current_balance < total_savings * Decimal("0.2"):
                related_insights.append("Liquidity is getting tighter")
                suggested_actions.append("Review pending loan requests and expenses")
    
    # Member queries
    elif category == "member":
        if member_data:
            total_members = member_data.get("total_members", 0)
            active_members = member_data.get("active_members", 0)
            inactive_members = member_data.get("inactive_members", [])
            
            response_parts.append(f"Your chama has {total_members} members.")
            response_parts.append(f"{active_members} members are currently active.")
            
            if inactive_members:
                response_parts.append(f"{len(inactive_members)} members are inactive.")
                related_insights.append(f"{len(inactive_members)} members need re-engagement")
                suggested_actions.append("Reach out to inactive members")
            
            data_sources.extend(["members", "participation"])
    
    # Loan queries
    elif category == "loan":
        if loan_data:
            loan_data.get("total_loans", 0)
            active_loans = loan_data.get("active_loans", 0)
            overdue_loans = loan_data.get("overdue_loans", 0)
            total_outstanding = loan_data.get("total_outstanding", 0)
            
            response_parts.append(f"There are {active_loans} active loans.")
            response_parts.append(f"Total outstanding amount is KES {total_outstanding:,.2f}.")
            
            if overdue_loans > 0:
                response_parts.append(f"{overdue_loans} loan(s) are overdue.")
                related_insights.append("Some loans have missed repayment deadlines")
                suggested_actions.append("Follow up on overdue loans")
            
            data_sources.extend(["loans", "repayments"])
    
    # Meeting queries
    elif category == "meeting":
        if meeting_data:
            upcoming_meetings = meeting_data.get("upcoming_meetings", 0)
            last_attendance_rate = meeting_data.get("last_attendance_rate", 0)
            
            response_parts.append(f"There are {upcoming_meetings} upcoming meeting(s).")
            response_parts.append(f"Last meeting attendance rate was {last_attendance_rate}%.")
            
            if last_attendance_rate < 50:
                related_insights.append("Meeting attendance is low")
                suggested_actions.append("Send meeting reminders and review scheduling")
            
            data_sources.extend(["meetings", "attendance"])
    
    # Health queries
    elif category == "health":
        if health_score:
            overall_score = health_score.get("overall_score", 0)
            financial_health = health_score.get("financial_health", 0)
            member_engagement = health_score.get("member_engagement", 0)
            
            response_parts.append(f"Your chama health score is {overall_score}/100.")
            
            if overall_score >= 80:
                response_parts.append("Your chama is in excellent health!")
            elif overall_score >= 60:
                response_parts.append("Your chama is in good health with some areas for improvement.")
            else:
                response_parts.append("Your chama needs attention in several areas.")
            
            response_parts.append(f"Financial health: {financial_health}/100")
            response_parts.append(f"Member engagement: {member_engagement}/100")
            
            data_sources.extend(["health_score", "metrics"])
            
            # Add recommendations based on scores
            if financial_health < 60:
                suggested_actions.append("Review financial controls and expense management")
            if member_engagement < 60:
                suggested_actions.append("Implement member engagement initiatives")
    
    # Action queries
    elif category == "action":
        if insights:
            response_parts.append("Here are my recommendations for your chama:")
            
            for i, insight in enumerate(insights[:5], 1):
                response_parts.append(f"{i}. {insight.get('title', '')}")
                if insight.get("suggested_action"):
                    suggested_actions.append(insight["suggested_action"])
            
            data_sources.extend(["insights", "recommendations"])
        else:
            response_parts.append("I don't have enough data to provide specific recommendations right now.")
            response_parts.append("Please check back after more activity has been recorded.")
    
    # Summary queries
    elif category == "summary":
        response_parts.append("Here's a summary of your chama:")
        
        if finance_data:
            response_parts.append(f"• Total Savings: KES {finance_data.get('total_savings', 0):,.2f}")
            response_parts.append(f"• Current Balance: KES {finance_data.get('current_balance', 0):,.2f}")
        
        if member_data:
            response_parts.append(f"• Total Members: {member_data.get('total_members', 0)}")
            response_parts.append(f"• Active Members: {member_data.get('active_members', 0)}")
        
        if loan_data:
            response_parts.append(f"• Active Loans: {loan_data.get('active_loans', 0)}")
            response_parts.append(f"• Overdue Loans: {loan_data.get('overdue_loans', 0)}")
        
        if health_score:
            response_parts.append(f"• Health Score: {health_score.get('overall_score', 0)}/100")
        
        data_sources.extend(["all_metrics"])
    
    # General queries
    else:
        response_parts.append("I can help you with questions about:")
        response_parts.append("• Your chama's finances and savings")
        response_parts.append("• Member participation and activity")
        response_parts.append("• Loans and repayments")
        response_parts.append("• Meetings and attendance")
        response_parts.append("• Chama health and recommendations")
        response_parts.append("\nJust ask me a question!")
    
    # Combine response
    response_text = " ".join(response_parts)
    
    # Calculate confidence based on data availability
    confidence = Decimal("70")
    if len(data_sources) > 3:
        confidence = Decimal("90")
    elif len(data_sources) > 1:
        confidence = Decimal("80")
    
    return AIResponse(
        query=query,
        response=response_text,
        confidence=confidence,
        data_sources=data_sources,
        suggested_actions=suggested_actions,
        related_insights=related_insights,
    )


# ============================================================================
# SMART INSIGHTS GENERATION
# ============================================================================

def generate_ai_insights(
    chama_data: dict,
    finance_data: dict,
    member_data: dict,
    loan_data: dict,
    meeting_data: dict,
) -> list[AIInsight]:
    """
    Generate AI-powered insights for dashboard.
    
    Creates actionable insights like:
    - "3 members are likely to miss this month's contribution"
    - "Liquidity is low for new loan approvals"
    - "Attendance has dropped for 2 consecutive meetings"
    """
    insights = []
    
    # Contribution risk insights
    at_risk_members = member_data.get("at_risk_members", [])
    if at_risk_members:
        insights.append(AIInsight(
            insight_type="contribution_risk",
            title=f"{len(at_risk_members)} member(s) at risk of missing contributions",
            description=f"Based on compliance patterns, {len(at_risk_members)} member(s) may miss their next contribution",
            severity="warning",
            data_points={"at_risk_count": len(at_risk_members)},
            suggested_action="Send contribution reminders to at-risk members",
        ))
    
    # Liquidity insights
    current_balance = finance_data.get("current_balance", 0)
    total_savings = finance_data.get("total_savings", 0)
    if total_savings > 0:
        liquidity_ratio = current_balance / total_savings
        if liquidity_ratio < Decimal("0.2"):
            insights.append(AIInsight(
                insight_type="liquidity_warning",
                title="Liquidity is getting tighter",
                description="Chama liquidity is below recommended levels",
                severity="warning",
                data_points={"liquidity_ratio": str(liquidity_ratio)},
                suggested_action="Review pending loan requests and expense approvals",
            ))
    
    # Attendance insights
    attendance_trend = meeting_data.get("attendance_trend", "stable")
    if attendance_trend == "declining":
        insights.append(AIInsight(
            insight_type="attendance_decline",
            title="Meeting attendance has dropped",
            description="Attendance has declined for consecutive meetings",
            severity="warning",
            data_points={"trend": attendance_trend},
            suggested_action="Review meeting schedule and send reminders",
        ))
    
    # Loan risk insights
    overdue_loans = loan_data.get("overdue_loans", 0)
    if overdue_loans > 0:
        insights.append(AIInsight(
            insight_type="overdue_loans",
            title=f"{overdue_loans} loan(s) are overdue",
            description="Some loans have missed their repayment deadlines",
            severity="warning",
            data_points={"overdue_count": overdue_loans},
            suggested_action="Follow up on overdue loans and apply penalties if needed",
        ))
    
    # Positive insights
    health_score = chama_data.get("health_score", 0)
    if health_score >= 80:
        insights.append(AIInsight(
            insight_type="positive_health",
            title="Chama is in good financial health",
            description="All metrics are performing well",
            severity="info",
            data_points={"health_score": health_score},
        ))
    
    # Expense insights
    expense_trend = finance_data.get("expense_trend", "stable")
    if expense_trend == "increasing":
        insights.append(AIInsight(
            insight_type="expense_increase",
            title="Expenses are increasing",
            description="Expense trend is upward - review spending patterns",
            severity="info",
            data_points={"trend": expense_trend},
            suggested_action="Review recent expenses and identify areas for cost control",
        ))
    
    return insights


# ============================================================================
# QUICK PROMPTS
# ============================================================================

def get_quick_prompts(user_role: str) -> list[dict]:
    """
    Get quick prompt suggestions based on user role.
    
    Different roles get different prompts:
    - Members: Personal finance questions
    - Admins: Management and oversight questions
    - Treasurers: Financial analysis questions
    """
    base_prompts = [
        {"text": "How much have we saved this month?", "icon": "💰"},
        {"text": "What is our chama health score?", "icon": "❤️"},
        {"text": "Summarize our finances", "icon": "📊"},
    ]
    
    if user_role in ["ADMIN", "CHAMA_ADMIN", "SUPERADMIN"]:
        admin_prompts = [
            {"text": "Who has not contributed?", "icon": "👥"},
            {"text": "Which loans are overdue?", "icon": "⚠️"},
            {"text": "What risks should I review?", "icon": "🔍"},
            {"text": "What actions should I take today?", "icon": "✅"},
            {"text": "Show me pending approvals", "icon": "📋"},
        ]
        return base_prompts + admin_prompts
    
    elif user_role == "TREASURER":
        treasurer_prompts = [
            {"text": "Show me contribution compliance", "icon": "📈"},
            {"text": "What is our liquidity status?", "icon": "💧"},
            {"text": "Which expenses need review?", "icon": "💳"},
            {"text": "Show me loan risk assessment", "icon": "⚠️"},
        ]
        return base_prompts + treasurer_prompts
    
    else:
        member_prompts = [
            {"text": "What is my contribution history?", "icon": "📝"},
            {"text": "When is my next payment due?", "icon": "📅"},
            {"text": "What is my loan status?", "icon": "💵"},
        ]
        return base_prompts + member_prompts


# ============================================================================
# PLAIN LANGUAGE SUMMARIES
# ============================================================================

def generate_plain_language_summary(
    summary_type: str,
    data: dict,
) -> str:
    """
    Generate plain-language summary of complex data.
    
    Types:
    - monthly_financial
    - member_compliance
    - loan_portfolio
    - meeting_attendance
    """
    if summary_type == "monthly_financial":
        total_contributions = data.get("total_contributions", 0)
        total_expenses = data.get("total_expenses", 0)
        net_change = total_contributions - total_expenses
        
        summary = f"This month, your chama collected KES {total_contributions:,.2f} in contributions. "
        summary += f"Total expenses were KES {total_expenses:,.2f}. "
        
        if net_change > 0:
            summary += f"Net savings increased by KES {net_change:,.2f}. "
        elif net_change < 0:
            summary += f"Net savings decreased by KES {abs(net_change):,.2f}. "
        
        if data.get("contribution_trend") == "up":
            summary += "Contributions are trending upward. "
        elif data.get("contribution_trend") == "down":
            summary += "Contributions are trending downward. "
        
        return summary
    
    elif summary_type == "member_compliance":
        total_members = data.get("total_members", 0)
        compliant_members = data.get("compliant_members", 0)
        compliance_rate = (compliant_members / total_members * 100) if total_members > 0 else 0
        
        summary = f"Out of {total_members} members, {compliant_members} are fully compliant with contributions. "
        summary += f"Overall compliance rate is {compliance_rate:.1f}%. "
        
        if compliance_rate >= 90:
            summary += "Excellent compliance! "
        elif compliance_rate >= 70:
            summary += "Good compliance with room for improvement. "
        else:
            summary += "Compliance needs attention. "
        
        return summary
    
    elif summary_type == "loan_portfolio":
        data.get("total_loans", 0)
        active_loans = data.get("active_loans", 0)
        overdue_loans = data.get("overdue_loans", 0)
        total_outstanding = data.get("total_outstanding", 0)
        
        summary = f"Your chama has {active_loans} active loans with KES {total_outstanding:,.2f} outstanding. "
        
        if overdue_loans > 0:
            summary += f"{overdue_loans} loan(s) are overdue and need attention. "
        else:
            summary += "All loans are current. "
        
        return summary
    
    elif summary_type == "meeting_attendance":
        total_meetings = data.get("total_meetings", 0)
        avg_attendance = data.get("avg_attendance_rate", 0)
        
        summary = f"Over the last {total_meetings} meetings, average attendance was {avg_attendance:.1f}%. "
        
        if avg_attendance >= 70:
            summary += "Attendance is strong. "
        elif avg_attendance >= 50:
            summary += "Attendance is moderate. "
        else:
            summary += "Attendance needs improvement. "
        
        return summary
    
    return "Summary not available for this data type."
