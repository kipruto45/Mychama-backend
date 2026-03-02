"""
AI Chat Engine for Digital Chama - Production Grade

Implements:
- Tool-based function calling (no hallucination)
- Structured context injection
- Prompt guardrails
- Confidence scoring
- Fallback mode
- Rate limiting
"""

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Optional, Dict, Any, List

from django.db.models import Sum, Count, Avg
from django.utils import timezone

from apps.ai.models import AIInteraction
from apps.chama.models import Chama, Membership
from apps.finance.models import LedgerEntry, Loan

logger = logging.getLogger(__name__)


class FunctionTool:
    """
    Base class for tool-based function calling.
    Each tool returns structured data that AI must use.
    """
    
    name: str = ""
    description: str = ""
    
    def execute(self, user, chama: Optional[Chama], **kwargs) -> Dict[str, Any]:
        """Execute the tool and return structured data."""
        raise NotImplementedError
    
    def validate_context(self, context: Dict[str, Any]) -> bool:
        """Check if required context is available."""
        return True


class GetWalletBalance(FunctionTool):
    """Get user's wallet balance in a chama."""
    
    name = "get_wallet_balance"
    description = "Get the user's current wallet balance including contributions, withdrawals, and loans"
    
    def execute(self, user, chama: Optional[Chama], **kwargs) -> Dict[str, Any]:
        if not chama:
            return {"error": "No chama selected", "available": False}
        
        # Get contributions
        total_contributions = LedgerEntry.objects.filter(
            owner=user,
            chama=chama,
            entry_type=LedgerEntry.ENTRY_CONTRIBUTION,
            status=LedgerEntry.STATUS_SUCCESS,
        ).aggregate(Sum("amount"))["amount__sum"] or Decimal("0")
        
        # Get withdrawals
        total_withdrawals = LedgerEntry.objects.filter(
            owner=user,
            chama=chama,
            entry_type=LedgerEntry.ENTRY_WITHDRAWAL,
            status=LedgerEntry.STATUS_SUCCESS,
        ).aggregate(Sum("amount"))["amount__sum"] or Decimal("0")
        
        # Get outstanding loans
        outstanding_loans = Loan.objects.filter(
            borrower=user,
            chama=chama,
            status__in=[Loan.STATUS_ACTIVE, Loan.STATUS_APPROVED],
        ).aggregate(Sum("amount"))["amount__sum"] or Decimal("0")
        
        net_balance = total_contributions - total_withdrawals - outstanding_loans
        
        return {
            "available": True,
            "total_contributions": float(total_contributions),
            "total_withdrawals": float(total_withdrawals),
            "outstanding_loans": float(outstanding_loans),
            "net_balance": float(net_balance),
            "currency": "KES",
        }


class GetContributions(FunctionTool):
    """Get user's contribution history."""
    
    name = "get_contributions"
    description = "Get recent contribution history for the user"
    
    def execute(self, user, chama: Optional[Chama], **kwargs) -> Dict[str, Any]:
        if not chama:
            return {"error": "No chama selected", "available": False}
        
        months = kwargs.get("months", 6)
        cutoff = timezone.now() - timedelta(days=months * 30)
        
        contributions = LedgerEntry.objects.filter(
            owner=user,
            chama=chama,
            entry_type=LedgerEntry.ENTRY_CONTRIBUTION,
            status=LedgerEntry.STATUS_SUCCESS,
            created_at__gte=cutoff,
        ).order_by("-created_at")[:10].values(
            "amount", "created_at", "entry_type"
        )
        
        total = sum(c["amount"] for c in contributions)
        count = len(contributions)
        
        return {
            "available": True,
            "recent_contributions": [
                {"amount": float(c["amount"]), "date": c["created_at"].isoformat()}
                for c in contributions
            ],
            "total_contributed": float(total),
            "contribution_count": count,
            "minimum_contribution": float(chama.minimum_contribution or 0),
            "currency": "KES",
        }


class GetLoans(FunctionTool):
    """Get user's loan information."""
    
    name = "get_loans"
    description = "Get user's active and pending loans with payment status"
    
    def execute(self, user, chama: Optional[Chama], **kwargs) -> Dict[str, Any]:
        if not chama:
            return {"error": "No chama selected", "available": False}
        
        loans = Loan.objects.filter(
            borrower=user,
            chama=chama,
            status__in=[Loan.STATUS_PENDING, Loan.STATUS_ACTIVE, Loan.STATUS_APPROVED],
        )
        
        loan_data = []
        total_outstanding = Decimal("0")
        
        for loan in loans:
            loan_data.append({
                "id": loan.id,
                "amount": float(loan.amount),
                "status": loan.status,
                "remaining_balance": float(loan.remaining_balance),
                "monthly_repayment": float(loan.monthly_repayment),
                "next_repayment_date": loan.next_repayment_date.isoformat() if loan.next_repayment_date else None,
                "term_months": loan.term_months,
            })
            total_outstanding += loan.remaining_balance
        
        return {
            "available": True,
            "active_loans": loan_data,
            "total_outstanding": float(total_outstanding),
            "loan_count": len(loan_data),
            "currency": "KES",
        }


class GetLoanEligibility(FunctionTool):
    """Calculate loan eligibility using deterministic risk engine."""
    
    name = "calculate_loan_eligibility"
    description = "Calculate if user is eligible for a loan with exact amounts"
    
    def execute(self, user, chama: Optional[Chama], **kwargs) -> Dict[str, Any]:
        if not chama:
            return {"error": "No chama selected", "available": False}
        
        from apps.ai.risk_engine import RiskEngine
        
        eligibility = RiskEngine.calculate_loan_eligibility(user, chama)
        
        return {
            "available": True,
            "eligible": eligibility["eligible"],
            "max_loan_amount": float(eligibility["max_loan_amount"]),
            "suggested_amount": float(eligibility["suggested_amount"]),
            "suggested_term_months": eligibility["suggested_term_months"],
            "interest_rate": float(eligibility["interest_rate"]),
            "risk_score": eligibility["risk_score"],
            "risk_level": eligibility["risk_level"],
            "ineligibility_reason": eligibility.get("ineligibility_reason", ""),
            "risk_factors": eligibility.get("risk_factors", []),
            "currency": "KES",
        }


class GetMeetings(FunctionTool):
    """Get upcoming chama meetings."""
    
    name = "get_meetings"
    description = "Get upcoming chama meetings and schedule"
    
    def execute(self, user, chama: Optional[Chama], **kwargs) -> Dict[str, Any]:
        if not chama:
            return {"error": "No chama selected", "available": False}
        
        from apps.meetings.models import Meeting
        
        meetings = Meeting.objects.filter(
            chama=chama,
            scheduled_at__gte=timezone.now(),
        ).order_by("scheduled_at")[:5]
        
        meeting_data = []
        for m in meetings:
            meeting_data.append({
                "date": m.scheduled_at.isoformat(),
                "agenda": m.agenda or "",
                "location": m.location or "",
                "title": m.title or "Chama Meeting",
            })
        
        return {
            "available": True,
            "upcoming_meetings": meeting_data,
            "meeting_count": len(meeting_data),
        }


class GetMemberCount(FunctionTool):
    """Get chama member count."""
    
    name = "get_member_count"
    description = "Get number of active members in the chama"
    
    def execute(self, user, chama: Optional[Chama], **kwargs) -> Dict[str, Any]:
        if not chama:
            return {"error": "No chama selected", "available": False}
        
        members = Membership.objects.filter(
            chama=chama,
            status=Membership.STATUS_ACTIVE,
        )
        
        # Group by role
        roles = {}
        for member in members:
            role = member.get_role_display()
            roles[role] = roles.get(role, 0) + 1
        
        return {
            "available": True,
            "total_members": members.count(),
            "members_by_role": roles,
        }


class GetWithdrawals(FunctionTool):
    """Get user's withdrawal history."""
    
    name = "get_withdrawals"
    description = "Get user's withdrawal history and available balance"
    
    def execute(self, user, chama: Optional[Chama], **kwargs) -> Dict[str, Any]:
        if not chama:
            return {"error": "No chama selected", "available": False}
        
        months = kwargs.get("months", 6)
        cutoff = timezone.now() - timedelta(days=months * 30)
        
        withdrawals = LedgerEntry.objects.filter(
            owner=user,
            chama=chama,
            entry_type=LedgerEntry.ENTRY_WITHDRAWAL,
            status=LedgerEntry.STATUS_SUCCESS,
            created_at__gte=cutoff,
        ).order_by("-created_at")[:10]
        
        total_withdrawn = sum(w.amount for w in withdrawals)
        
        return {
            "available": True,
            "recent_withdrawals": [
                {"amount": float(w.amount), "date": w.created_at.isoformat()}
                for w in withdrawals
            ],
            "total_withdrawn": float(total_withdrawn),
            "withdrawal_count": len(withdrawals),
            "currency": "KES",
        }


# Register all tools
AVAILABLE_TOOLS = {
    "get_wallet_balance": GetWalletBalance(),
    "get_contributions": GetContributions(),
    "get_loans": GetLoans(),
    "calculate_loan_eligibility": GetLoanEligibility(),
    "get_meetings": GetMeetings(),
    "get_member_count": GetMemberCount(),
    "get_withdrawals": GetWithdrawals(),
}


class ChatEngine:
    """
    Production-grade AI Chat Engine using tool-based function calling.
    
    Key principles:
    1. NEVER guess financial data - use tools only
    2. Return structured context to AI
    3. Include confidence scores
    4. Provide fallback for all queries
    5. Rate limit to prevent abuse
    """
    
    # Rate limiting: 20 messages per hour
    RATE_LIMIT = 20
    RATE_WINDOW_HOURS = 1
    
    # Intent keywords mapping to tools
    INTENT_TOOLS = {
        "balance": ["get_wallet_balance", "get_contributions"],
        "contribution": ["get_contributions"],
        "withdrawal": ["get_withdrawals", "get_wallet_balance"],
        "loan": ["get_loans", "calculate_loan_eligibility"],
        "repay": ["get_loans"],
        "next_meeting": ["get_meetings"],
        "members": ["get_member_count"],
        "eligible": ["calculate_loan_eligibility"],
    }
    
    @classmethod
    def process_message(
        cls,
        user,
        message: str,
        chama: Optional[Chama] = None,
    ) -> tuple[str, dict]:
        """
        Process user message using tool-based function calling.
        
        Returns: (response_text, context_data)
        """
        # Rate limiting check
        if not cls._check_rate_limit(user):
            return cls._rate_limit_response(), {"rate_limited": True}
        
        message_lower = message.lower().strip()
        
        # Detect intent
        intent = cls._detect_intent(message_lower)
        
        # Get tools for this intent
        tool_names = cls.INTENT_TOOLS.get(intent, [])
        
        # Execute tools in parallel for better performance
        tool_results = {}
        if tool_names:
            # Use ThreadPoolExecutor for parallel execution
            from concurrent.futures import ThreadPoolExecutor, as_completed
            
            def execute_tool(tool_name):
                tool = AVAILABLE_TOOLS.get(tool_name)
                if tool:
                    try:
                        result = tool.execute(user, chama)
                        return tool_name, result
                    except Exception as e:
                        logger.error(f"Tool {tool_name} failed: {e}")
                        return tool_name, {"error": str(e), "available": False}
                return tool_name, {"error": "Tool not found", "available": False}
            
            # Execute tools in parallel
            with ThreadPoolExecutor(max_workers=min(len(tool_names), 4)) as executor:
                futures = {executor.submit(execute_tool, name): name for name in tool_names}
                for future in as_completed(futures):
                    try:
                        tool_name, result = future.result()
                        tool_results[tool_name] = result
                    except Exception as e:
                        logger.error(f"Tool execution failed: {e}")
        
        # Generate response using structured data
        response = cls._generate_response(intent, tool_results, chama)
        
        # Calculate confidence
        confidence = cls._calculate_confidence(tool_results, intent)
        
        # Store interaction
        context_data = {
            "intent": intent,
            "chama_id": chama.id if chama else None,
            "tools_used": tool_names,
            "confidence": confidence,
        }
        
        try:
            AIInteraction.objects.create(
                user=user,
                chama=chama,
                question=message,
                response=response,
                context_data=context_data,
            )
        except Exception as e:
            logger.error(f"Error saving AI interaction: {e}")
        
        return response, context_data
    
    @classmethod
    def _detect_intent(cls, message: str) -> str:
        """Detect user intent from message."""
        intents = {
            "balance": ["balance", "how much", "my money", "total", "funds"],
            "contribution": ["contribute", "contribution", "pay", "deposit"],
            "withdrawal": ["withdraw", "withdrawal", "take out", "cash out"],
            "loan": ["loan", "borrow", "credit", "apply for loan", "eligible"],
            "repay": ["repay", "payment", "pay back", "installment", "due"],
            "next_meeting": ["meeting", "next", "when", "gathering"],
            "members": ["members", "how many", "who", "member count"],
        }
        
        for intent, keywords in intents.items():
            for keyword in keywords:
                if keyword in message:
                    return intent
        
        return "fallback"
    
    @classmethod
    def _check_rate_limit(cls, user) -> bool:
        """Check if user has exceeded rate limit."""
        from apps.ai.models import AIInteraction
        
        window_start = timezone.now() - timedelta(hours=cls.RATE_WINDOW_HOURS)
        count = AIInteraction.objects.filter(
            user=user,
            created_at__gte=window_start,
        ).count()
        
        return count < cls.RATE_LIMIT
    
    @classmethod
    def _rate_limit_response(cls) -> str:
        """Return rate limit message."""
        return f"""⚠️ Rate Limit Exceeded

You've sent too many messages. Please wait a moment before trying again.

Limit: {cls.RATE_LIMIT} messages per hour

Need immediate help? Contact your chama secretary directly."""
    
    @classmethod
    def _calculate_confidence(cls, tool_results: Dict, intent: str) -> float:
        """Calculate confidence score based on tool availability."""
        if intent == "fallback":
            return 0.5
        
        if not tool_results:
            return 0.0
        
        # Check how many tools returned valid data
        valid_count = sum(1 for r in tool_results.values() if r.get("available", False))
        total_count = len(tool_results)
        
        if total_count == 0:
            return 0.0
        
        return valid_count / total_count
    
    @classmethod
    def _generate_response(
        cls,
        intent: str,
        tool_results: Dict,
        chama: Optional[Chama],
    ) -> str:
        """Generate response using structured tool data."""
        
        if intent == "balance":
            return cls._format_balance_response(tool_results, chama)
        elif intent == "contribution":
            return cls._format_contribution_response(tool_results, chama)
        elif intent == "withdrawal":
            return cls._format_withdrawal_response(tool_results, chama)
        elif intent == "loan":
            return cls._format_loan_response(tool_results, chama)
        elif intent == "repay":
            return cls._format_repayment_response(tool_results, chama)
        elif intent == "next_meeting":
            return cls._format_meeting_response(tool_results, chama)
        elif intent == "members":
            return cls._format_members_response(tool_results, chama)
        else:
            return cls._format_fallback_response()
    
    @classmethod
    def _format_balance_response(cls, results: Dict, chama: Optional[Chama]) -> str:
        """Format balance response - NO guessing."""
        wallet = results.get("get_wallet_balance", {})
        
        if not wallet.get("available"):
            return "Please select a chama to check your balance."
        
        return f"""📊 **Your Balance in {chama.name}**

💵 Total Contributions: KES {wallet['total_contributions']:,.0f}
➖ Total Withdrawals: KES {wallet['total_withdrawals']:,.0f}
💳 Outstanding Loans: KES {wallet['outstanding_loans']:,.0f}

**Net Balance: KES {wallet['net_balance']:,.0f}**

Would you like to make a contribution or withdrawal?"""
    
    @classmethod
    def _format_contribution_response(cls, results: Dict, chama: Optional[Chama]) -> str:
        """Format contribution response - NO guessing."""
        contrib = results.get("get_contributions", {})
        
        if not contrib.get("available"):
            return "Please select a chama to view contributions."
        
        response = f"💰 **Contributing to {chama.name}**\n\n"
        response += f"Minimum contribution: KES {contrib['minimum_contribution']:,.0f}\n"
        
        if contrib.get("recent_contributions"):
            response += "\n📝 Your recent contributions:\n"
            for c in contrib["recent_contributions"][:5]:
                from datetime import datetime
                date = datetime.fromisoformat(c["date"]).strftime("%b %d")
                response += f"- KES {c['amount']:,.0f} on {date}\n"
        
        response += f"\nTotal (last 6 months): KES {contrib['total_contributed']:,.0f}"
        
        return response
    
    @classmethod
    def _format_withdrawal_response(cls, results: Dict, chama: Optional[Chama]) -> str:
        """Format withdrawal response - NO guessing."""
        wallet = results.get("get_wallet_balance", {})
        
        if not wallet.get("available"):
            return "Please select a chama."
        
        available = wallet["net_balance"]
        
        response = f"🏧 **Withdrawing from {chama.name}**\n\n"
        response += f"Available balance: KES {available:,.0f}\n\n"
        
        if available <= 0:
            response += "You don't have any funds available for withdrawal."
        else:
            response += "To request a withdrawal, go to Wallet → Withdraw.\n"
            response += "Note: Withdrawals require approval from the chama secretary/treasurer."
        
        return response
    
    @classmethod
    def _format_loan_response(cls, results: Dict, chama: Optional[Chama]) -> str:
        """Format loan response - NO guessing."""
        loans = results.get("get_loans", {})
        eligibility = results.get("calculate_loan_eligibility", {})
        
        response = f"💳 **Loans in {chama.name}**\n\n"
        
        if loans.get("active_loans"):
            for loan in loans["active_loans"]:
                response += f"\n📌 {loan['status'].title()}\n"
                response += f"   Amount: KES {loan['amount']:,.0f}\n"
                response += f"   Remaining: KES {loan['remaining_balance']:,.0f}\n"
                response += f"   Monthly: KES {loan['monthly_repayment']:,.0f}\n"
                if loan.get("next_repayment_date"):
                    from datetime import datetime
                    date = datetime.fromisoformat(loan["next_repayment_date"]).strftime("%b %d, %Y")
                    response += f"   Next due: {date}\n"
        else:
            response += "You don't have any active loans.\n\n"
        
        if eligibility.get("available"):
            if eligibility["eligible"]:
                response += f"\n✅ **You're Eligible!**\n"
                response += f"Max amount: KES {eligibility['max_loan_amount']:,.0f}\n"
                response += f"Suggested: KES {eligibility['suggested_amount']:,.0f}\n"
                response += f"Interest: {eligibility['interest_rate']}%\n"
            else:
                response += f"\n❌ Not eligible: {eligibility.get('ineligibility_reason', 'Unknown reason')}"
        
        return response
    
    @classmethod
    def _format_repayment_response(cls, results: Dict, chama: Optional[Chama]) -> str:
        """Format repayment response - NO guessing."""
        loans = results.get("get_loans", {})
        
        if not loans.get("available"):
            return "Please select a chama."
        
        response = f"💰 **Loan Repayments in {chama.name}**\n\n"
        
        if not loans.get("active_loans"):
            return response + "No active loans to repay."
        
        for loan in loans["active_loans"]:
            response += f"\n📌 Loan: KES {loan['amount']:,.0f}\n"
            response += f"   Remaining: KES {loan['remaining_balance']:,.0f}\n"
            response += f"   Monthly: KES {loan['monthly_repayment']:,.0f}\n"
            if loan.get("next_repayment_date"):
                from datetime import datetime
                date = datetime.fromisoformat(loan["next_repayment_date"]).strftime("%b %d, %Y")
                response += f"   Next due: {date}\n"
        
        return response
    
    @classmethod
    def _format_meeting_response(cls, results: Dict, chama: Optional[Chama]) -> str:
        """Format meeting response - NO guessing."""
        meetings = results.get("get_meetings", {})
        
        if not meetings.get("available"):
            return "Please select a chama."
        
        response = f"📅 **Meetings for {chama.name}**\n\n"
        
        if meetings.get("upcoming_meetings"):
            for m in meetings["upcoming_meetings"]:
                from datetime import datetime
                dt = datetime.fromisoformat(m["date"])
                response += f"📌 {dt.strftime('%A, %b %d, %Y')}\n"
                response += f"   Time: {dt.strftime('%I:%M %p')}\n"
                if m.get("location"):
                    response += f"   Location: {m['location']}\n"
        else:
            response += "No upcoming meetings scheduled."
        
        return response
    
    @classmethod
    def _format_members_response(cls, results: Dict, chama: Optional[Chama]) -> str:
        """Format members response - NO guessing."""
        members = results.get("get_member_count", {})
        
        if not members.get("available"):
            return "Please select a chama."
        
        response = f"👥 **Members of {chama.name}**\n\n"
        response += f"Total members: {members['total_members']}\n\n"
        
        if members.get("members_by_role"):
            for role, count in members["members_by_role"].items():
                response += f"- {role}: {count}\n"
        
        return response
    
    @classmethod
    def _format_fallback_response(cls) -> str:
        """Format fallback response with strict guardrails."""
        return """I'm not sure I understood that. 

I can help you with:
- 💰 Your balance and account info
- 💵 Contributions and withdrawals  
- 💳 Loan applications and repayments
- 📅 Meeting schedules
- 👥 Member information

Try asking:
"What's my balance?"
"How do I apply for a loan?"
"When is the next meeting?"


**Note**: I never guess financial data. All information comes directly from your chama's records."""
