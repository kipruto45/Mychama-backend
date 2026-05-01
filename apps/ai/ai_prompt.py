"""
AI Prompt Builder for Digital Chama

This module builds prompts for the AI assistant based on:
- User role in the chama
- Available tools
- Chama context
- Safety rules for fintech
"""

from __future__ import annotations

from apps.chama.services import get_effective_role

# Role-based suggestions for quick actions
ROLE_SUGGESTIONS = {
    "MEMBER": [
        {"id": "balance", "text": "What's my current balance?", "category": "wallet"},
        {"id": "contributions", "text": "Show my contribution history", "category": "contributions"},
        {"id": "loans", "text": "Check my loans and repayments", "category": "loans"},
        {"id": "meetings", "text": "When is the next meeting?", "category": "meetings"},
        {"id": "payment_status", "text": "Did my payment go through?", "category": "payments"},
    ],
    "SECRETARY": [
        {"id": "balance", "text": "What's my current balance?", "category": "wallet"},
        {"id": "unpaid_members", "text": "Who hasn't paid this month?", "category": "contributions"},
        {"id": "meetings", "text": "List upcoming meetings", "category": "meetings"},
        {"id": "issues", "text": "Show open issues", "category": "governance"},
        {"id": "activity", "text": "Recent chama activity", "category": "activity"},
    ],
    "TREASURER": [
        {"id": "chama_balance", "text": "What's the chama's total balance?", "category": "wallet"},
        {"id": "loan_book", "text": "Show the loan book", "category": "loans"},
        {"id": "unpaid_members", "text": "Who hasn't paid this month?", "category": "contributions"},
        {"id": "fines", "text": "Fines summary", "category": "fines"},
        {"id": "overdue", "text": "Show overdue loans", "category": "loans"},
        {"id": "mpesa", "text": "Check M-Pesa transaction status", "category": "payments"},
    ],
    "AUDITOR": [
        {"id": "audit_logs", "text": "Show recent audit logs", "category": "audit"},
        {"id": "loan_book", "text": "Show the loan book", "category": "loans"},
        {"id": "chama_balance", "text": "Chama financial summary", "category": "wallet"},
        {"id": "activity", "text": "Recent activity feed", "category": "activity"},
    ],
    "CHAMA_ADMIN": [
        {"id": "chama_balance", "text": "What's the chama's total balance?", "category": "wallet"},
        {"id": "loan_book", "text": "Show the loan book", "category": "loans"},
        {"id": "unpaid_members", "text": "Who hasn't paid this month?", "category": "contributions"},
        {"id": "audit_logs", "text": "Show audit logs", "category": "audit"},
        {"id": "fines", "text": "Fines summary", "category": "fines"},
        {"id": "activity", "text": "Recent chama activity", "category": "activity"},
    ],
}


# Safety rules for financial AI
FINANCIAL_SAFETY_RULES = """
## FINANCIAL SAFETY RULES (STRICT)

1. NEVER confirm a payment as successful unless you have verified data from the payment system showing status=SUCCESS
2. NEVER claim M-Pesa STK push succeeded without verified transaction status
3. NEVER provide balance figures that are not directly from the database
4. NEVER promise loan approval - only provide eligibility assessment
5. If you cannot verify information, say "I cannot confirm" and suggest checking the relevant page
6. Always include source references in your responses when discussing financial data

## TOOL-FIRST POLICY (MANDATORY)

For any question about balances, payments, contributions, loans, fines, or member status, you MUST use a tool:
- "How much have I contributed?" → use get_my_wallet_summary
- "Who hasn't paid?" → use get_unpaid_members
- "Show my loans" → use get_my_loan_status
- "What's the chama balance?" → use get_chama_wallet_summary
- "Any overdue loans?" → use get_loan_book

If you make numerical claims without calling a tool, your response is INVALID.

## PRIVACY RULES

1. Never disclose full phone numbers - only show masked versions (e.g., 0700***123)
2. Never disclose other members' personal information beyond what the user is authorized to see
3. If asked about another member's data, check if the user has permission (admin/treasurer roles)

## RESPONSE GUIDELINES

1. Be concise and actionable
2. Provide specific amounts in KES currency
3. Include links to relevant pages when possible
4. For complex queries, summarize and suggest next steps
5. When tools return errors, explain the error simply to the user

## ANTI-HALLUCINATION RULES

1. Only state facts that come from tool outputs
2. If tool returns empty data, say "I found no records" - never fabricate
3. Use exact amounts from tools, never round or estimate
4. Dates must come from tool outputs, never guess
"""


# Response validation function
def validate_response(message: str, tool_outputs: list, response: str) -> tuple[bool, str]:
    """
    Validate that a response doesn't contain hallucinations.
    Returns (is_valid, error_message)
    """
    import re
    
    # Check for numerical claims that need tool validation
    numerical_patterns = [
        r'\d+[,\d]*\s*(KES|shillings)',  # Money amounts
        r'(total|sum|balance|amount)\s*:?\s*\d+',  # Balance/totals
        r'\d+\s*(members?|users?|people)',  # Counts
    ]
    
    has_numerical_claim = any(re.search(p, response, re.IGNORECASE) for p in numerical_patterns)
    
    if has_numerical_claim and not tool_outputs:
        return False, "Response contains numerical claims but no tool was used to validate them"
    
    # Check for specific financial claims
    financial_keywords = ['balance', 'paid', 'due', 'owed', 'arrears', 'contribution', 'loan']
    has_financial_claim = any(kw in response.lower() for kw in financial_keywords)
    
    if has_financial_claim and not tool_outputs:
        return False, "Response contains financial claims but no tool was used"
    
    return True, ""


def get_role_suggestions(user, chama) -> list[dict[str, str]]:
    """
    Get role-based suggestions for the AI chat.
    """
    try:
        from apps.chama.models import Membership

        membership = Membership.objects.filter(
            user=user,
            chama=chama,
            is_active=True,
            is_approved=True,
        ).first()

        if not membership:
            return ROLE_SUGGESTIONS.get("MEMBER", [])

        role = get_effective_role(user, chama.id, membership)
        return ROLE_SUGGESTIONS.get(role, ROLE_SUGGESTIONS.get("MEMBER", []))

    except Exception:
        return ROLE_SUGGESTIONS.get("MEMBER", [])


def build_system_prompt(user, chama) -> str:
    """
    Build the system prompt for the AI assistant.
    """
    try:
        from apps.chama.models import Membership

        membership = Membership.objects.filter(
            user=user,
            chama=chama,
            is_active=True,
            is_approved=True,
        ).first()

        if membership:
            role = get_effective_role(user, chama.id, membership)
        else:
            role = "MEMBER"

    except Exception:
        role = "MEMBER"

    return f"""You are Digital Chama's AI Assistant.

You help members of a Chama (group savings/loan scheme) manage their finances and participate in group activities.

Your role: {role}

You have access to tools that can query the actual database for:
- Wallet balances and transaction history
- Contribution status and history
- Loan applications and repayment schedules
- Meeting schedules and minutes
- Fines and penalties
- M-Pesa payment status
- Chama financial reports

{FINANCIAL_SAFETY_RULES}

Remember:
- Always verify financial data with tools before responding
- If data is missing or unclear, say so honestly
- Provide helpful, actionable responses
- Use markdown for formatting but keep it simple
- Include quick action buttons when appropriate
"""


def build_tool_context(tools: list[dict[str, str]]) -> str:
    """
    Build the tool context for the prompt.
    """
    if not tools:
        return "No tools available."

    tool_descriptions = []
    for tool in tools:
        tool_descriptions.append(f"- {tool['name']}: {tool['description']}")

    return f"""Available tools:
{chr(10).join(tool_descriptions)}

When user asks about data, use the appropriate tool to fetch real data.
"""


def format_response_with_actions(response: str, actions: list[dict[str, str]] = None) -> str:
    """
    Format a response with action buttons.
    """
    if not actions:
        return response

    action_links = []
    for action in actions:
        action_links.append(f"[{action['text']}](#{action.get('url', '#')})")

    if action_links:
        return f"""{response}

**Quick Actions:**
{' • '.join(action_links)}"""

    return response


# Quick action mappings
QUICK_ACTIONS = {
    "balance": {
        "text": "View My Wallet",
        "url": "/member/wallet",
    },
    "contributions": {
        "text": "View Contributions",
        "url": "/member/contributions",
    },
    "loans": {
        "text": "View My Loans",
        "url": "/member/loans",
    },
    "meetings": {
        "text": "View Meetings",
        "url": "/member/meetings",
    },
    "payment_status": {
        "text": "View Payments",
        "url": "/member/payments",
    },
    "chama_balance": {
        "text": "View Wallet",
        "url": "/treasurer/wallet",
    },
    "loan_book": {
        "text": "View Loans",
        "url": "/treasurer/loans",
    },
    "unpaid_members": {
        "text": "View Unpaid",
        "url": "/treasurer/reports",
    },
    "fines": {
        "text": "View Fines",
        "url": "/treasurer/fines",
    },
    "overdue": {
        "text": "View Overdue",
        "url": "/treasurer/loans?filter=overdue",
    },
    "mpesa": {
        "text": "View M-Pesa",
        "url": "/treasurer/mpesa",
    },
    "audit_logs": {
        "text": "View Audit Logs",
        "url": "/auditor/audit",
    },
    "activity": {
        "text": "View Activity",
        "url": "/treasurer/reports",
    },
    "issues": {
        "text": "View Issues",
        "url": "/secretary/issues",
    },
}


def get_quick_actions(suggestion_ids: list[str]) -> list[dict[str, str]]:
    """
    Get quick action buttons for suggestions.
    """
    return [QUICK_ACTIONS.get(sid, {}) for sid in suggestion_ids if sid in QUICK_ACTIONS]
