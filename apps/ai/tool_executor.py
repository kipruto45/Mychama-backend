# MyChama AI Chatbot - Tool Executor
# apps/ai/tool_executor.py

import logging
from typing import Dict, Any, Optional, List
from decimal import Decimal
from django.contrib.auth.models import User
from rest_framework.exceptions import PermissionDenied

logger = logging.getLogger(__name__)


class ToolExecutor:
    """
    Executes tools/functions for the chatbot.
    
    Responsibilities:
    - Validate tool permissions
    - Execute tool logic
    - Return structured results
    - Handle errors gracefully
    """
    
    # Tools available by role
    ROLE_TOOLS = {
        'member': [
            'get_current_user_profile',
            'get_current_user_wallet_summary',
            'get_current_user_loan_summary',
            'get_current_user_contribution_summary',
            'get_current_user_kyc_status',
            'get_current_user_notifications_summary',
            'get_current_user_pending_actions',
            'get_upcoming_meetings',
            'get_recent_announcements'
        ],
        'chama_admin': [
            # All member tools plus:
            'get_current_chama_summary',
            'get_pending_join_requests',
            'get_overdue_loans',
            'get_missed_contributions',
            'get_chama_issues_summary',
            'get_chama_health_score',
            'get_chama_member_list'
        ],
        'system_admin': [
            # All admin tools plus:
            'get_platform_health_summary',
            'get_kyc_exception_summary',
            'get_fraud_alerts_summary',
            'get_escalated_issues_summary',
            'get_audit_trail'
        ]
    }
    
    def __init__(self, user: User, chama_id: Optional[str] = None):
        self.user = user
        self.chama_id = chama_id
    
    def get_tools_schema(self, role: str) -> List[Dict[str, Any]]:
        """
        Get JSON schema of available tools for role.
        Used by LLM to understand what tools it can call.
        """
        allowed_tools = self.ROLE_TOOLS.get(role, [])
        
        schema_map = {
            'get_current_user_profile': {
                'name': 'get_current_user_profile',
                'description': 'Get current user profile information',
                'parameters': {'type': 'object', 'properties': {}}
            },
            'get_current_user_wallet_summary': {
                'name': 'get_current_user_wallet_summary',
                'description': 'Get current user wallet balance and recent transactions',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'include_history': {
                            'type': 'boolean',
                            'description': 'Include transaction history'
                        }
                    }
                }
            },
            'get_current_user_loan_summary': {
                'name': 'get_current_user_loan_summary',
                'description': 'Get user active loans, borrowed, repaid amounts and overdue count',
                'parameters': {'type': 'object', 'properties': {}}
            },
            'get_current_user_contribution_summary': {
                'name': 'get_current_user_contribution_summary',
                'description': 'Get user contribution history this month and overdue status',
                'parameters': {'type': 'object', 'properties': {}}
            },
            'get_current_user_kyc_status': {
                'name': 'get_current_user_kyc_status',
                'description': 'Get user KYC verification status and documents',
                'parameters': {'type': 'object', 'properties': {}}
            },
            'get_current_user_notifications_summary': {
                'name': 'get_current_user_notifications_summary',
                'description': 'Get unread notifications and notification history',
                'parameters': {'type': 'object', 'properties': {}}
            },
            'get_current_user_pending_actions': {
                'name': 'get_current_user_pending_actions',
                'description': 'Get list of actionable items for user',
                'parameters': {'type': 'object', 'properties': {}}
            },
            'get_current_chama_summary': {
                'name': 'get_current_chama_summary',
                'description': 'Get current chama summary (admins only)',
                'parameters': {'type': 'object', 'properties': {}}
            },
            'get_pending_join_requests': {
                'name': 'get_pending_join_requests',
                'description': 'Get pending join requests for chama (admins only)',
                'parameters': {'type': 'object', 'properties': {}}
            },
            'get_overdue_loans': {
                'name': 'get_overdue_loans',
                'description': 'Get overdue loans in chama (admins only)',
                'parameters': {'type': 'object', 'properties': {}}
            },
            'get_missed_contributions': {
                'name': 'get_missed_contributions',
                'description': 'Get members who missed contributions (admins only)',
                'parameters': {'type': 'object', 'properties': {}}
            },
            'get_chama_health_score': {
                'name': 'get_chama_health_score',
                'description': 'Get chama health score (admins only)',
                'parameters': {'type': 'object', 'properties': {}}
            },
            'get_platform_health_summary': {
                'name': 'get_platform_health_summary',
                'description': 'Get platform health metrics (system admins only)',
                'parameters': {'type': 'object', 'properties': {}}
            },
        }
        
        return [
            schema_map[tool]
            for tool in allowed_tools
            if tool in schema_map
        ]
    
    def validate_permission(self, tool_name: str, role: str) -> bool:
        """
        Validate that user/role can call this tool.
        Raises PermissionDenied if not allowed.
        """
        allowed_tools = self.ROLE_TOOLS.get(role, [])
        
        if tool_name not in allowed_tools:
            raise PermissionDenied(f"Tool '{tool_name}' not available for role '{role}'")
        
        return True
    
    def execute(self, tool_name: str, tool_args: Dict) -> Dict[str, Any]:
        """
        Execute a tool and return result.
        """
        # Map tool name to handler
        handler_name = f'_execute_{tool_name}'
        
        if not hasattr(self, handler_name):
            raise ValueError(f"Unknown tool: {tool_name}")
        
        handler = getattr(self, handler_name)
        return handler(tool_args)
    
    # ==================== MEMBER TOOLS ====================
    
    def _execute_get_current_user_profile(self, args: Dict) -> Dict:
        """Get current user profile"""
        try:
            from apps.accounts.models import UserProfile
            profile = self.user.userprofile
            return {
                'user_id': str(self.user.id),
                'name': f"{self.user.first_name} {self.user.last_name}".strip(),
                'email': self.user.email,
                'phone': profile.phone if hasattr(profile, 'phone') else '',
                'kyc_status': profile.kyc_status if hasattr(profile, 'kyc_status') else 'pending'
            }
        except Exception as e:
            logger.error(f"Error fetching user profile: {e}")
            return {'error': str(e)}
    
    def _execute_get_current_user_wallet_summary(self, args: Dict) -> Dict:
        """Get user wallet summary"""
        try:
            from apps.payments.models import Wallet
            wallet = Wallet.objects.get(user=self.user, chama_id=self.chama_id)
            return {
                'balance': float(wallet.balance),
                'currency': 'KES',
                'last_transaction': wallet.last_updated.isoformat() if wallet.last_updated else None,
                'available_for_withdrawal': float(wallet.balance) > 1000
            }
        except Exception as e:
            logger.error(f"Error fetching wallet: {e}")
            return {'balance': 0, 'currency': 'KES', 'error': 'Unable to fetch wallet'}
    
    def _execute_get_current_user_loan_summary(self, args: Dict) -> Dict:
        """Get user loan summary"""
        try:
            from apps.finance.models import Loan
            from django.utils import timezone
            
            loans = Loan.objects.filter(borrower=self.user, chama_id=self.chama_id)
            
            active_loans = loans.filter(status='active').count()
            total_borrowed = sum(l.amount for l in loans)
            total_repaid = sum(l.repaid_amount if hasattr(l, 'repaid_amount') else 0 for l in loans)
            overdue = loans.filter(
                due_date__lt=timezone.now(),
                status='active'
            ).count()
            
            return {
                'active_loans': active_loans,
                'total_borrowed': float(total_borrowed),
                'total_repaid': float(total_repaid),
                'overdue_count': overdue,
                'currency': 'KES'
            }
        except Exception as e:
            logger.error(f"Error fetching loans: {e}")
            return {'active_loans': 0, 'error': 'Unable to fetch loans'}
    
    def _execute_get_current_user_contribution_summary(self, args: Dict) -> Dict:
        """Get user contribution summary"""
        try:
            from apps.finance.models import Contribution
            from django.utils import timezone
            from datetime import timedelta
            
            contributions = Contribution.objects.filter(
                member=self.user,
                chama_id=self.chama_id
            )
            
            # This month
            now = timezone.now()
            month_start = now.replace(day=1, hour=0, minute=0, second=0)
            this_month = contributions.filter(
                date__gte=month_start
            ).aggregate(total=models.Sum('amount'))['total'] or 0
            
            total = sum(c.amount for c in contributions)
            overdue = contributions.filter(
                status='overdue'
            ).count()
            
            return {
                'total_contributed': float(total),
                'this_month': float(this_month),
                'overdue_count': overdue,
                'currency': 'KES'
            }
        except Exception as e:
            logger.error(f"Error fetching contributions: {e}")
            return {'total_contributed': 0, 'error': 'Unable to fetch contributions'}
    
    def _execute_get_current_user_kyc_status(self, args: Dict) -> Dict:
        """Get user KYC status"""
        try:
            from apps.kyc.models import KYCVerification
            kyc = KYCVerification.objects.filter(user=self.user).latest('created_at')
            
            return {
                'status': kyc.status if kyc else 'not_started',
                'documents_submitted': kyc.documents_submitted if hasattr(kyc, 'documents_submitted') else [],
                'last_updated': kyc.updated_at.isoformat() if kyc and kyc.updated_at else None
            }
        except Exception as e:
            logger.error(f"Error fetching KYC: {e}")
            return {'status': 'unknown', 'error': 'Unable to fetch KYC status'}
    
    def _execute_get_current_user_notifications_summary(self, args: Dict) -> Dict:
        """Get user notifications summary"""
        try:
            from apps.notifications.models import Notification
            notifs = Notification.objects.filter(recipient=self.user)
            
            unread = notifs.filter(read_at__isnull=True).count()
            recent = notifs.order_by('-created_at')[:5].values('title', 'body', 'created_at')
            
            return {
                'unread_count': unread,
                'recent_notifications': list(recent)
            }
        except Exception as e:
            logger.error(f"Error fetching notifications: {e}")
            return {'unread_count': 0, 'recent_notifications': []}
    
    def _execute_get_current_user_pending_actions(self, args: Dict) -> Dict:
        """Get pending actions for user"""
        actions = []
        
        # KYC
        try:
            from apps.kyc.models import KYCVerification
            kyc = KYCVerification.objects.filter(user=self.user).latest('created_at')
            if kyc.status != 'verified':
                actions.append({
                    'type': 'kyc_incomplete',
                    'message': 'Complete your KYC verification',
                    'action': 'open_screen:kyc'
                })
        except:
            pass
        
        # Overdue contributions
        try:
            from apps.finance.models import Contribution
            overdue = Contribution.objects.filter(
                member=self.user,
                status='overdue',
                chama_id=self.chama_id
            ).count()
            if overdue > 0:
                actions.append({
                    'type': 'overdue_contribution',
                    'message': f'You have {overdue} overdue contribution(s)',
                    'action': 'open_screen:contributions'
                })
        except:
            pass
        
        return {'pending_actions': actions}
    
    def _execute_get_upcoming_meetings(self, args: Dict) -> Dict:
        """Get upcoming meetings"""
        try:
            from apps.meetings.models import Meeting
            from django.utils import timezone
            
            meetings = Meeting.objects.filter(
                chama_id=self.chama_id,
                date__gte=timezone.now()
            ).order_by('date')[:5]
            
            return {
                'upcoming_meetings': [
                    {
                        'title': m.title,
                        'date': m.date.isoformat(),
                        'location': m.location if hasattr(m, 'location') else ''
                    }
                    for m in meetings
                ]
            }
        except Exception as e:
            logger.error(f"Error fetching meetings: {e}")
            return {'upcoming_meetings': []}
    
    def _execute_get_recent_announcements(self, args: Dict) -> Dict:
        """Get recent announcements"""
        try:
            from apps.notifications.models import Announcement
            
            announcements = Announcement.objects.filter(
                chama_id=self.chama_id
            ).order_by('-created_at')[:5]
            
            return {
                'recent_announcements': [
                    {
                        'title': a.title,
                        'body': a.body[:100],
                        'date': a.created_at.isoformat()
                    }
                    for a in announcements
                ]
            }
        except Exception as e:
            logger.error(f"Error fetching announcements: {e}")
            return {'recent_announcements': []}
    
    # ==================== ADMIN TOOLS ====================
    
    def _execute_get_current_chama_summary(self, args: Dict) -> Dict:
        """Get chama summary (admin only)"""
        try:
            from apps.chama.models import Chama
            chama = Chama.objects.get(id=self.chama_id)
            
            return {
                'name': chama.name,
                'member_count': chama.members.count(),
                'total_funds': float(chama.total_savings if hasattr(chama, 'total_savings') else 0),
                'status': chama.status if hasattr(chama, 'status') else 'active'
            }
        except Exception as e:
            logger.error(f"Error fetching chama summary: {e}")
            return {'error': str(e)}
    
    def _execute_get_pending_join_requests(self, args: Dict) -> Dict:
        """Get pending join requests (admin only)"""
        try:
            from apps.chama.models import MembershipRequest
            
            requests = MembershipRequest.objects.filter(
                chama_id=self.chama_id,
                status='pending'
            )
            
            return {
                'pending_requests': [
                    {
                        'user_name': r.user.get_full_name(),
                        'phone': r.user.userprofile.phone if hasattr(r.user, 'userprofile') else '',
                        'requested_at': r.created_at.isoformat()
                    }
                    for r in requests
                ]
            }
        except Exception as e:
            logger.error(f"Error fetching join requests: {e}")
            return {'pending_requests': []}
    
    def _execute_get_overdue_loans(self, args: Dict) -> Dict:
        """Get overdue loans (admin only)"""
        try:
            from apps.finance.models import Loan
            from django.utils import timezone
            
            overdue = Loan.objects.filter(
                chama_id=self.chama_id,
                due_date__lt=timezone.now(),
                status='active'
            )
            
            return {
                'overdue_loans': [
                    {
                        'member': l.borrower.get_full_name(),
                        'amount': float(l.amount),
                        'due_date': l.due_date.isoformat(),
                        'days_overdue': (timezone.now().date() - l.due_date.date()).days
                    }
                    for l in overdue
                ]
            }
        except Exception as e:
            logger.error(f"Error fetching overdue loans: {e}")
            return {'overdue_loans': []}
    
    def _execute_get_missed_contributions(self, args: Dict) -> Dict:
        """Get missed contributions (admin only)"""
        try:
            from apps.finance.models import Contribution
            
            missed = Contribution.objects.filter(
                chama_id=self.chama_id,
                status='overdue'
            )
            
            return {
                'missed_contributions': [
                    {
                        'member': c.member.get_full_name(),
                        'amount': float(c.amount),
                        'due_date': c.due_date.isoformat() if hasattr(c, 'due_date') else ''
                    }
                    for c in missed
                ]
            }
        except Exception as e:
            logger.error(f"Error fetching missed contributions: {e}")
            return {'missed_contributions': []}
    
    def _execute_get_chama_health_score(self, args: Dict) -> Dict:
        """Get chama health score (admin only)"""
        try:
            # This would aggregate various metrics
            # For now, return placeholder
            return {
                'health_score': 75,
                'factors': {
                    'contribution_rate': 0.85,
                    'loan_repayment_rate': 0.92,
                    'member_engagement': 0.78
                }
            }
        except Exception as e:
            logger.error(f"Error calculating health score: {e}")
            return {'health_score': 0, 'error': str(e)}
    
    # ==================== SYSTEM ADMIN TOOLS ====================
    
    def _execute_get_platform_health_summary(self, args: Dict) -> Dict:
        """Get platform health summary (system admin only)"""
        # Would query monitoring/health systems
        return {
            'status': 'healthy',
            'uptime_percentage': 99.9,
            'active_users': 1250,
            'transactions_today': 3450
        }
    
    def _execute_get_kyc_exception_summary(self, args: Dict) -> Dict:
        """Get KYC exceptions (system admin only)"""
        try:
            from apps.kyc.models import KYCVerification
            
            rejected = KYCVerification.objects.filter(status='rejected').count()
            pending = KYCVerification.objects.filter(status='pending').count()
            
            return {
                'rejected_count': rejected,
                'pending_count': pending,
                'needs_review': pending > 100
            }
        except Exception as e:
            return {'rejected_count': 0, 'pending_count': 0}
    
    def _execute_get_fraud_alerts_summary(self, args: Dict) -> Dict:
        """Get fraud alerts (system admin only)"""
        try:
            from apps.ai.models import FraudFlag
            
            high_severity = FraudFlag.objects.filter(
                severity='high',
                resolved=False
            ).count()
            
            critical = FraudFlag.objects.filter(
                severity='critical',
                resolved=False
            ).count()
            
            return {
                'high_severity_count': high_severity,
                'critical_count': critical,
                'needs_attention': critical > 0
            }
        except Exception as e:
            return {'high_severity_count': 0, 'critical_count': 0}
    
    def _execute_get_escalated_issues_summary(self, args: Dict) -> Dict:
        """Get escalated issues (system admin only)"""
        try:
            from apps.support.models import Ticket
            
            escalated = Ticket.objects.filter(
                priority='high',
                status='open'
            ).count()
            
            return {
                'escalated_count': escalated,
                'needs_review': escalated > 5
            }
        except Exception as e:
            return {'escalated_count': 0}
    
    def _execute_get_audit_trail(self, args: Dict) -> Dict:
        """Get audit trail (system admin only)"""
        # Would query audit logs
        return {'recent_activities': []}
