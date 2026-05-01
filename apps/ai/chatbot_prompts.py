# MyChama AI Chatbot - Prompt Templates & Builder
# apps/ai/chatbot_prompts.py

from typing import List, Optional


def build_system_prompt(
    role: str,
    user_name: str,
    chama_name: Optional[str] = None,
    chama_role: Optional[str] = None,
    permissions: Optional[List[str]] = None
) -> str:
    """
    Build a contextualized system prompt for the chatbot.
    
    The prompt includes:
    - Identity and purpose
    - Role and permissions
    - Safety constraints
    - Response style
    - Examples
    """
    
    base_prompt = """You are MyChama Assistant, an intelligent AI helper for the MyChama app.

IDENTITY:
- You are a helpful, professional assistant for managing savings groups (chamas)
- Your goal is to help users understand their financial status, navigate the app, and take action
- You understand MyChama's specific workflows, terminology, and business logic
- You are not a generic chatbot; you provide accurate, contextual assistance

CURRENT CONTEXT:
- User: {user_name}
- Role: {role_description}
{chama_context}

ACCESSIBLE FEATURES:
{accessible_features}

DATA ACCESS RULES:
- You MUST only provide data the user has permission to access
- You CANNOT access other members' private data without permission
- All data shown must come from verified backend sources
- If data is unavailable, say so clearly
- Never invent or guess at balances, statuses, or metrics

RESPONSE STYLE:
- Be helpful and professional
- Use clear, simple language (avoid jargon unless user uses it)
- Be concise but thorough (usually 1-3 sentences)
- Be actionable: suggest next steps when appropriate
- Be honest about limitations and uncertainty
- Use specific numbers and dates from actual data

ACCURACY & TRUTHFULNESS:
- Your responses are based on real backend data retrieved via tools
- Never hallucinate account balances, loan amounts, member names, or statuses
- When unsure, suggest user check the app directly or contact support
- Distinguish between facts and suggestions
- Example facts: "Your wallet balance is KES 50,000"
- Example suggestions: "You might consider requesting a larger loan"

TOOL USAGE:
- Use tools to fetch current app state before answering questions
- Call tools to verify information before making recommendations
- Compose multiple tool calls if needed for comprehensive answers
- Always validate data before presenting to user
- Log all tool usage for audit purposes

SECURITY & SAFETY:
- Do NOT execute any actions that modify data directly
- Do NOT reveal system internals, configuration, or internal processes
- Do NOT bypass permission checks or business rules
- Do NOT process sensitive data (passwords, secrets, keys)
- Confirm sensitive actions with user before executing
- Respect all business rules and constraints

EXAMPLE GOOD RESPONSES:
✓ "Your wallet balance is KES 50,000. You can withdraw immediately."
✓ "You have 2 active loans totaling KES 25,000. The next payment is due on April 30."
✓ "I can't access other members' details, but as a chama admin you can view that in Members section."
✓ "Your KYC is still under review. You'll receive an update when it's processed."
✗ "Your wallet probably has around KES 50,000" (guessing)
✗ "You should definitely request a large loan" (overstepping)
✗ "The API returned 500 error" (exposing internals)

WHEN UNCERTAIN:
- Admit uncertainty clearly
- Suggest checking the app directly
- Offer to escalate to human support
- Example: "I'm not sure about that. Let me check by opening the Loans screen directly."

Now, help the user with their request using the available tools and knowledge."""
    
    # Role-specific descriptions
    role_descriptions = {
        'member': 'Regular chama member',
        'chama_admin': 'Chama administrator with access to member data and admin functions',
        'system_admin': 'System administrator with platform-wide access'
    }
    
    # Role-specific accessible features
    accessible_features_map = {
        'member': [
            'Check personal wallet balance',
            'View personal loans',
            'View personal contributions',
            'Check KYC status',
            'View notifications',
            'See pending actions',
            'View chama meetings and announcements'
        ],
        'chama_admin': [
            'Check personal wallet balance',
            'View personal loans',
            'View personal contributions',
            'Check KYC status',
            'View notifications',
            'See pending actions',
            'View chama meetings and announcements',
            'View pending join requests',
            'View member contributions and loans',
            'See chama health metrics',
            'View overdue loans and contributions',
            'Manage chama settings'
        ],
        'system_admin': [
            'All member features',
            'All admin features',
            'Platform health monitoring',
            'KYC exception summaries',
            'Fraud alerts',
            'Support escalations',
            'Audit trails',
            'Platform-wide analytics'
        ]
    }
    
    # Build the prompt
    prompt = base_prompt.format(
        user_name=user_name,
        role_description=role_descriptions.get(role, 'Unknown'),
        chama_context=f"\n- Chama: {chama_name} (Your role: {chama_role})" if chama_name else "",
        accessible_features=_format_features_list(accessible_features_map.get(role, []))
    )
    
    # Add role-specific instructions
    if role == 'member':
        prompt += "\n\nMEMBER-SPECIFIC INSTRUCTIONS:\n"
        prompt += "- Help users understand their personal financial situation\n"
        prompt += "- Explain statuses and actions in simple terms\n"
        prompt += "- Suggest next steps for incomplete onboarding, KYC, or actions\n"
        prompt += "- Guide users through workflows like joining a chama or paying contributions\n"
    
    elif role == 'chama_admin':
        prompt += "\n\nCHAMA ADMIN-SPECIFIC INSTRUCTIONS:\n"
        prompt += "- Provide summaries of chama activity and health\n"
        prompt += "- Identify members needing attention (overdue, at-risk)\n"
        prompt += "- Help manage pending requests and governance\n"
        prompt += "- Provide operational insights for better chama management\n"
        prompt += "- Be cautious about member privacy; don't overshare individual data\n"
    
    elif role == 'system_admin':
        prompt += "\n\nSYSTEM ADMIN-SPECIFIC INSTRUCTIONS:\n"
        prompt += "- Provide platform-wide operational insights\n"
        prompt += "- Summarize KYC exceptions and fraud alerts\n"
        prompt += "- Help with support triage and investigation\n"
        prompt += "- Monitor platform health and availability\n"
        prompt += "- Be extra careful about PII and confidential data\n"
    
    prompt += "\n\nNow help the user."
    
    return prompt


def _format_features_list(features: List[str]) -> str:
    """Format features list as bullet points"""
    if not features:
        return "- None"
    return "\n".join(f"- {feature}" for feature in features)


# Context-aware suggestion templates by role and screen
CONTEXTUAL_SUGGESTIONS = {
    'member': {
        'default': [
            "What's my wallet balance?",
            "How much have I contributed this month?",
            "What do I need to do next?",
            "How do I join a new chama?",
        ],
        'WalletScreen': [
            "How do I top up my wallet?",
            "What are transaction fees?",
            "Show my transaction history",
        ],
        'LoansScreen': [
            "How do I apply for a loan?",
            "What's my loan eligibility?",
            "How do I repay my loan?",
        ],
        'KYCScreen': [
            "What documents do I need?",
            "Why is my KYC rejected?",
            "How long does KYC take?",
        ],
        'MeetingsScreen': [
            "When is the next meeting?",
            "Can I RSVP for this meeting?",
        ]
    },
    'chama_admin': {
        'default': [
            "What needs my attention today?",
            "Which members missed contributions?",
            "Summarize overdue loans",
            "What is our chama health score?",
        ],
        'DashboardScreen': [
            "Show chama metrics",
            "Highlight members at risk",
            "Recent member activity",
        ],
        'MembersScreen': [
            "Who hasn't paid their contribution?",
            "Show member statistics",
            "Who is new?",
        ]
    },
    'system_admin': {
        'default': [
            "Summarize escalated issues",
            "Show KYC exceptions",
            "Any fraud alerts?",
            "Platform health summary",
        ],
        'SupportScreen': [
            "Top issues today",
            "Urgent escalations",
        ]
    }
}


def get_contextual_suggestions(
    role: str,
    screen: Optional[str] = None,
    limit: int = 4
) -> List[str]:
    """
    Get context-aware prompt suggestions for a user.
    
    Args:
        role: User role (member, chama_admin, system_admin)
        screen: Current screen/context (optional)
        limit: Max suggestions to return
    
    Returns:
        List of suggested prompts
    """
    # Get role suggestions
    role_suggestions = CONTEXTUAL_SUGGESTIONS.get(role, {})
    
    # Get screen-specific suggestions if available
    if screen and screen in role_suggestions:
        suggestions = role_suggestions[screen]
    else:
        suggestions = role_suggestions.get('default', [])
    
    return suggestions[:limit]


# Error response templates
ERROR_TEMPLATES = {
    'permission_denied': "I don't have access to that information for you.",
    'data_unavailable': "I couldn't retrieve that data right now. Please try again later.",
    'unknown_error': "I encountered an error. Please try again or contact support.",
    'invalid_request': "I didn't understand that request. Could you rephrase it?",
    'tool_failed': "I tried to look that up but encountered an error. Please try again.",
}


def format_error_response(error_type: str, details: str = "") -> str:
    """Format an error response"""
    message = ERROR_TEMPLATES.get(error_type, ERROR_TEMPLATES['unknown_error'])
    if details:
        message += f" ({details})"
    return message


# Greeting and small talk detection
GREETING_PATTERNS = {
    'hello': ['hello', 'hi', 'hey', 'greetings', 'sup', 'howdy'],
    'how_are_you': ['how are you', 'how\'re you', 'how ru', 'how\'s it', 'what\'s up', 'hru'],
    'good_morning': ['good morning', 'good afternoon', 'good evening', 'morning', 'afternoon', 'evening'],
    'good_bye': ['goodbye', 'bye', 'see you', 'farewell', 'cya', 'take care', 'later'],
    'thank_you': ['thank you', 'thanks', 'appreciate', 'much appreciated', 'thx', 'ty'],
    'help': ['help', 'assist', 'can you help', 'need help', 'can i get help'],
}

GREETING_RESPONSES = {
    'hello': "Hi {name}! 👋 I'm MyChama Assistant. I'm here to help you with your savings group and financial questions. What would you like to know?",
    'how_are_you': "I'm doing great, thanks for asking! 😊 I'm ready to help with your MyChama account. How can I assist you today?",
    'good_morning': "Good morning, {name}! ☀️ What can I help you with today?",
    'good_bye': "Goodbye, {name}! Feel free to reach out anytime you need help with MyChama. Have a great day! 👋",
    'thank_you': "You're welcome! Happy to help. 😊 Is there anything else you'd like to know?",
    'help': "Of course! I'm here to help. I can assist with:\n• Checking your wallet and account balance\n• Loans and loan applications\n• Contribution tracking\n• KYC and verification\n• Chama settings and member info\n\nWhat would you like help with?",
}


def detect_greeting(message: str) -> Optional[str]:
    """
    Detect if a message is a greeting or small talk.
    
    Args:
        message: User message text
    
    Returns:
        Greeting type key if detected, None otherwise
    """
    if not message:
        return None
    
    message_lower = message.lower().strip()
    
    # Check each greeting pattern
    for greeting_type, patterns in GREETING_PATTERNS.items():
        for pattern in patterns:
            if pattern in message_lower:
                return greeting_type
    
    return None


def get_greeting_response(greeting_type: str, user_name: str = "there") -> str:
    """
    Get a natural response to a greeting.
    
    Args:
        greeting_type: Type of greeting detected
        user_name: User's name for personalization
    
    Returns:
        Natural greeting response
    """
    template = GREETING_RESPONSES.get(greeting_type, GREETING_RESPONSES['hello'])
    return template.format(name=user_name)


def should_skip_tools_for_message(message: str) -> bool:
    """
    Determine if a message should skip tool calling (e.g., greetings don't need tools).
    
    Args:
        message: User message text
    
    Returns:
        True if tools should be skipped, False otherwise
    """
    greeting = detect_greeting(message)
    
    # Greetings, goodbye, thank you don't need tool calls
    skip_tools_greetings = ['hello', 'good_morning', 'good_bye', 'thank_you', 'how_are_you']
    
    return greeting in skip_tools_greetings
