"""
AI Assistant Service

Manages AI chat integration, context fetching, and response generation.
"""

import logging

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama

logger = logging.getLogger(__name__)


class AIService:
    """Service for managing AI assistant."""

    # AI Configuration
    OPENAI_API_KEY = getattr(settings, 'OPENAI_API_KEY', '')
    AI_MODEL = getattr(settings, 'AI_MODEL', 'gpt-3.5-turbo')
    MAX_TOKENS = getattr(settings, 'AI_MAX_TOKENS', 500)

    @staticmethod
    def get_chama_context(chama: Chama, user: User) -> dict:
        """
        Get context data for AI responses.
        Returns relevant chama data for the user.
        """
        from django.db.models import Count, Sum

        from apps.finance.models import Account, Contribution, Loan
        from apps.meetings.models import Meeting

        # Get account balance
        account = Account.objects.filter(chama=chama, account_type='main').first()
        balance = account.balance if account else 0

        # Get user's contributions
        user_contributions = Contribution.objects.filter(
            membership__chama=chama,
            membership__user=user,
        ).aggregate(
            total=Sum('amount'),
            paid=Sum('amount_paid'),
            pending=Count('id', filter=models.Q(status='pending')),
        )

        # Get user's loans
        user_loans = Loan.objects.filter(
            chama=chama,
            user=user,
        ).aggregate(
            total_borrowed=Sum('principal_amount'),
            total_repaid=Sum('amount_repaid'),
            active_count=Count('id', filter=models.Q(status='active')),
        )

        # Get upcoming meetings
        upcoming_meetings = Meeting.objects.filter(
            chama=chama,
            start_time__gt=timezone.now(),
            status='scheduled',
        ).count()

        # Get member count
        from apps.chama.models import Membership
        member_count = Membership.objects.filter(
            chama=chama,
            status='active',
        ).count()

        return {
            'chama_name': chama.name,
            'balance': balance,
            'member_count': member_count,
            'user_contributions': {
                'total': user_contributions['total'] or 0,
                'paid': user_contributions['paid'] or 0,
                'pending': user_contributions['pending'] or 0,
            },
            'user_loans': {
                'total_borrowed': user_loans['total_borrowed'] or 0,
                'total_repaid': user_loans['total_repaid'] or 0,
                'active_count': user_loans['active_count'] or 0,
            },
            'upcoming_meetings': upcoming_meetings,
        }

    @staticmethod
    def generate_system_prompt(context: dict) -> str:
        """
        Generate system prompt for AI.
        """
        return f"""You are a helpful AI assistant for {context['chama_name']}, a savings group (chama) management platform.

Your role is to help members with questions about their chama, contributions, loans, meetings, and financial matters.

Current context:
- Chama: {context['chama_name']}
- Total Balance: KES {context['balance']:,.2f}
- Members: {context['member_count']}
- Your Total Contributions: KES {context['user_contributions']['total']:,.2f}
- Your Pending Contributions: {context['user_contributions']['pending']}
- Your Active Loans: {context['user_loans']['active_count']}
- Upcoming Meetings: {context['upcoming_meetings']}

Guidelines:
1. Be helpful, friendly, and professional
2. Provide accurate information based on the context
3. If you don't know something, say so honestly
4. Encourage good financial habits
5. Remind users about pending contributions or upcoming meetings
6. Never share sensitive information about other members
7. Keep responses concise and actionable

You can help with:
- Contribution schedules and reminders
- Loan eligibility and repayment
- Meeting schedules and agendas
- Financial summaries and insights
- General chama operations
"""

    @staticmethod
    @transaction.atomic
    def chat(
        user: User,
        chama: Chama,
        message: str,
        conversation_history: list[dict] = None,
    ) -> dict:
        """
        Process a chat message and generate AI response.
        Returns response details.
        """
        from apps.ai.models import ChatMessage

        # Get context
        context = AIService.get_chama_context(chama, user)

        # Generate system prompt
        system_prompt = AIService.generate_system_prompt(context)

        # Prepare messages for AI
        messages = [
            {"role": "system", "content": system_prompt},
        ]

        # Add conversation history
        if conversation_history:
            for msg in conversation_history[-10:]:  # Last 10 messages
                messages.append({
                    "role": msg.get('role', 'user'),
                    "content": msg.get('content', ''),
                })

        # Add current message
        messages.append({"role": "user", "content": message})

        # Generate response
        try:
            response = AIService._generate_response(messages)

            # Save chat message
            chat_message = ChatMessage.objects.create(
                user=user,
                chama=chama,
                message=message,
                response=response,
                context=context,
            )

            logger.info(
                f"AI chat: {user.full_name} in {chama.name}"
            )

            return {
                'response': response,
                'chat_id': str(chat_message.id),
                'context': context,
            }

        except Exception as e:
            logger.error(
                f"AI chat error: {e}"
            )
            return {
                'response': "I'm sorry, I encountered an error. Please try again later.",
                'error': str(e),
            }

    @staticmethod
    def _generate_response(messages: list[dict]) -> str:
        """
        Generate AI response using OpenAI API.
        """
        import openai

        if not AIService.OPENAI_API_KEY:
            return "AI service is not configured. Please contact your administrator."

        try:
            client = openai.OpenAI(api_key=AIService.OPENAI_API_KEY)
            
            response = client.chat.completions.create(
                model=AIService.AI_MODEL,
                messages=messages,
                max_tokens=AIService.MAX_TOKENS,
                temperature=0.7,
            )

            return response.choices[0].message.content

        except Exception as e:
            logger.error(
                f"OpenAI API error: {e}"
            )
            raise

    @staticmethod
    def get_quick_prompts() -> list[dict]:
        """
        Get quick prompt suggestions for users.
        """
        return [
            {
                'id': 'contribution_status',
                'title': 'Contribution Status',
                'prompt': 'What is my contribution status?',
            },
            {
                'id': 'loan_eligibility',
                'title': 'Loan Eligibility',
                'prompt': 'Am I eligible for a loan?',
            },
            {
                'id': 'next_meeting',
                'title': 'Next Meeting',
                'prompt': 'When is the next meeting?',
            },
            {
                'id': 'balance_summary',
                'title': 'Balance Summary',
                'prompt': 'What is our current balance?',
            },
            {
                'id': 'pending_contributions',
                'title': 'Pending Contributions',
                'prompt': 'Do I have any pending contributions?',
            },
            {
                'id': 'financial_advice',
                'title': 'Financial Advice',
                'prompt': 'Give me some financial advice for our chama.',
            },
        ]

    @staticmethod
    def get_chat_history(
        user: User,
        chama: Chama = None,
    ) -> list[dict]:
        """
        Get chat history for a user.
        """
        from apps.ai.models import ChatMessage

        queryset = ChatMessage.objects.filter(user=user)

        if chama:
            queryset = queryset.filter(chama=chama)

        messages = queryset.order_by('-created_at')

        return [
            {
                'id': str(msg.id),
                'message': msg.message,
                'response': msg.response,
                'chama_name': msg.chama.name if msg.chama else None,
                'created_at': msg.created_at.isoformat(),
            }
            for msg in messages
        ]

    @staticmethod
    def get_ai_insights(chama: Chama) -> list[dict]:
        """
        Get AI-generated insights for a chama.
        """
        from django.db.models import Avg, Count, Sum

        from apps.finance.models import Contribution, Loan
        from apps.meetings.models import Meeting

        insights = []

        # Contribution insights
        contributions = Contribution.objects.filter(
            membership__chama=chama,
        ).aggregate(
            total=Sum('amount'),
            avg=Avg('amount'),
            pending=Count('id', filter=models.Q(status='pending')),
            overdue=Count('id', filter=models.Q(status='overdue')),
        )

        if contributions['overdue'] > 0:
            insights.append({
                'type': 'warning',
                'title': 'Overdue Contributions',
                'message': f"You have {contributions['overdue']} overdue contributions. Consider following up with members.",
                'priority': 'high',
            })

        # Loan insights
        loans = Loan.objects.filter(chama=chama).aggregate(
            active=Count('id', filter=models.Q(status='active')),
            overdue=Count('id', filter=models.Q(status='overdue')),
        )

        if loans['overdue'] > 0:
            insights.append({
                'type': 'alert',
                'title': 'Overdue Loans',
                'message': f"You have {loans['overdue']} overdue loans. Review repayment status.",
                'priority': 'high',
            })

        # Meeting insights
        upcoming_meetings = Meeting.objects.filter(
            chama=chama,
            start_time__gt=timezone.now(),
            status='scheduled',
        ).count()

        if upcoming_meetings > 0:
            insights.append({
                'type': 'info',
                'title': 'Upcoming Meetings',
                'message': f"You have {upcoming_meetings} upcoming meetings. Review the agenda.",
                'priority': 'medium',
            })

        return insights

    @staticmethod
    def get_admin_insights(chama: Chama) -> list[dict]:
        """
        Get AI-generated insights for chama admins.
        """

        from apps.chama.models import Membership
        from apps.finance.models import Account, Contribution

        insights = []

        # Balance insights
        account = Account.objects.filter(chama=chama, account_type='main').first()
        if account and account.balance < 1000:
            insights.append({
                'type': 'warning',
                'title': 'Low Balance',
                'message': 'Chama balance is below KES 1,000. Consider increasing contributions.',
                'priority': 'high',
            })

        # Member engagement
        total_members = Membership.objects.filter(chama=chama, status='active').count()
        active_contributors = Contribution.objects.filter(
            membership__chama=chama,
            status='paid',
        ).values('membership__user').distinct().count()

        if total_members > 0:
            engagement_rate = (active_contributors / total_members) * 100
            if engagement_rate < 70:
                insights.append({
                    'type': 'warning',
                    'title': 'Low Member Engagement',
                    'message': f'Only {engagement_rate:.1f}% of members are actively contributing.',
                    'priority': 'medium',
                })

        return insights
