"""
Smart Notification and Automation System for MyChama

Implements intelligent notification and reminder automation:
- Contribution reminders
- Overdue alerts
- Meeting reminders
- Loan repayment reminders
- Fine reminders
- Approval-required alerts
- Monthly summary notifications
- Smart escalation logic
- Multi-channel delivery (in-app, email, SMS)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from django.utils import timezone

logger = logging.getLogger(__name__)


class NotificationChannel(Enum):
    IN_APP = "in_app"
    EMAIL = "email"
    SMS = "sms"
    PUSH = "push"


class NotificationPriority(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class EscalationLevel(Enum):
    NONE = "none"
    REMINDER = "reminder"
    WARNING = "warning"
    ESCALATED = "escalated"
    CRITICAL = "critical"


@dataclass
class SmartNotification:
    """Smart notification with context and escalation"""
    notification_type: str
    recipient_id: str
    recipient_type: str  # "member", "admin", "treasurer"
    title: str
    message: str
    priority: NotificationPriority
    channels: list[NotificationChannel]
    context: dict = field(default_factory=dict)
    action_url: str | None = None
    action_text: str | None = None
    scheduled_at: datetime | None = None
    expires_at: datetime | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class AutomationRule:
    """Automation rule definition"""
    rule_id: str
    name: str
    description: str
    trigger_event: str
    conditions: dict
    actions: list[dict]
    is_active: bool = True
    priority: int = 0
    created_at: datetime = field(default_factory=timezone.now)


@dataclass
class EscalationPolicy:
    """Escalation policy for overdue items"""
    policy_id: str
    name: str
    trigger_condition: str
    escalation_steps: list[dict]
    max_escalation_level: int
    is_active: bool = True


# ============================================================================
# CONTRIBUTION REMINDERS
# ============================================================================

def generate_contribution_reminders(
    chama_id: str,
    members: list[dict],
    contribution_schedule: dict,
    current_date: date = None,
) -> list[SmartNotification]:
    """
    Generate smart contribution reminders.
    
    Logic:
    - 3 days before due: Gentle reminder
    - 1 day before due: Urgent reminder
    - Due date: Final reminder
    - 1 day after: Overdue notice
    - 7 days after: Admin alert
    """
    if current_date is None:
        current_date = timezone.now().date()
    
    notifications = []
    due_day = contribution_schedule.get("due_day", 1)
    contribution_schedule.get("grace_period_days", 0)
    
    # Calculate next due date
    if current_date.day <= due_day:
        next_due = current_date.replace(day=due_day)
    else:
        # Next month
        if current_date.month == 12:
            next_due = current_date.replace(year=current_date.year + 1, month=1, day=due_day)
        else:
            next_due = current_date.replace(month=current_date.month + 1, day=due_day)
    
    days_until_due = (next_due - current_date).days
    
    for member in members:
        member_id = member.get("id")
        member_name = member.get("name", "Member")
        
        # Check if already paid this period
        last_payment = member.get("last_contribution_date")
        if last_payment:
            last_payment_date = last_payment if isinstance(last_payment, date) else date.fromisoformat(str(last_payment))
            if last_payment_date.month == next_due.month and last_payment_date.year == next_due.year:
                continue  # Already paid this month
        
        # Determine reminder type and priority
        if days_until_due == 3:
            notification = SmartNotification(
                notification_type="contribution_reminder",
                recipient_id=member_id,
                recipient_type="member",
                title="Contribution Due Soon",
                message=f"Hi {member_name}, your contribution of KES {contribution_schedule.get('amount', 0):,.2f} is due on {next_due.strftime('%B %d, %Y')}.",
                priority=NotificationPriority.LOW,
                channels=[NotificationChannel.IN_APP, NotificationChannel.EMAIL],
                context={
                    "due_date": next_due.isoformat(),
                    "amount": str(contribution_schedule.get("amount", 0)),
                    "days_until_due": days_until_due,
                },
                action_url="/contributions/pay",
                action_text="Pay Now",
            )
            notifications.append(notification)
        
        elif days_until_due == 1:
            notification = SmartNotification(
                notification_type="contribution_reminder_urgent",
                recipient_id=member_id,
                recipient_type="member",
                title="Contribution Due Tomorrow",
                message=f"Hi {member_name}, your contribution of KES {contribution_schedule.get('amount', 0):,.2f} is due tomorrow. Please pay to avoid late fees.",
                priority=NotificationPriority.MEDIUM,
                channels=[NotificationChannel.IN_APP, NotificationChannel.SMS],
                context={
                    "due_date": next_due.isoformat(),
                    "amount": str(contribution_schedule.get("amount", 0)),
                },
                action_url="/contributions/pay",
                action_text="Pay Now",
            )
            notifications.append(notification)
        
        elif days_until_due == 0:
            notification = SmartNotification(
                notification_type="contribution_due_today",
                recipient_id=member_id,
                recipient_type="member",
                title="Contribution Due Today",
                message=f"Hi {member_name}, your contribution of KES {contribution_schedule.get('amount', 0):,.2f} is due today. Pay now to avoid penalties.",
                priority=NotificationPriority.HIGH,
                channels=[NotificationChannel.IN_APP, NotificationChannel.SMS, NotificationChannel.PUSH],
                context={
                    "due_date": next_due.isoformat(),
                    "amount": str(contribution_schedule.get("amount", 0)),
                },
                action_url="/contributions/pay",
                action_text="Pay Now",
            )
            notifications.append(notification)
    
    return notifications


# ============================================================================
# OVERDUE ALERTS
# ============================================================================

def generate_overdue_alerts(
    chama_id: str,
    overdue_contributions: list[dict],
    overdue_loans: list[dict],
    escalation_policy: dict,
) -> list[SmartNotification]:
    """
    Generate overdue alerts with smart escalation.
    
    Escalation logic:
    - Day 1-3: Member reminder
    - Day 4-7: Member warning
    - Day 8-14: Admin notification
    - Day 15+: Critical alert to all admins
    """
    notifications = []
    current_date = timezone.now().date()
    
    # Overdue contributions
    for contrib in overdue_contributions:
        member_id = contrib.get("member_id")
        due_date = contrib.get("due_date")
        amount = Decimal(str(contrib.get("amount", 0)))
        
        if isinstance(due_date, str):
            due_date = date.fromisoformat(due_date)
        
        days_overdue = (current_date - due_date).days
        
        if days_overdue <= 3:
            # Gentle reminder
            notifications.append(SmartNotification(
                notification_type="contribution_overdue_reminder",
                recipient_id=member_id,
                recipient_type="member",
                title="Contribution Overdue",
                message=f"Your contribution of KES {amount:,.2f} was due on {due_date.strftime('%B %d, %Y')}. Please pay as soon as possible.",
                priority=NotificationPriority.MEDIUM,
                channels=[NotificationChannel.IN_APP, NotificationChannel.SMS],
                context={"days_overdue": days_overdue, "amount": str(amount)},
                action_url="/contributions/pay",
                action_text="Pay Now",
            ))
        
        elif days_overdue <= 7:
            # Warning
            notifications.append(SmartNotification(
                notification_type="contribution_overdue_warning",
                recipient_id=member_id,
                recipient_type="member",
                title="Contribution Overdue - Warning",
                message=f"Your contribution of KES {amount:,.2f} is {days_overdue} days overdue. Late fees may apply.",
                priority=NotificationPriority.HIGH,
                channels=[NotificationChannel.IN_APP, NotificationChannel.SMS, NotificationChannel.EMAIL],
                context={"days_overdue": days_overdue, "amount": str(amount)},
                action_url="/contributions/pay",
                action_text="Pay Now",
            ))
        
        elif days_overdue <= 14:
            # Escalate to admin
            notifications.append(SmartNotification(
                notification_type="contribution_overdue_admin",
                recipient_id=chama_id,
                recipient_type="admin",
                title="Member Contribution Overdue",
                message=f"A member's contribution of KES {amount:,.2f} is {days_overdue} days overdue. Consider following up.",
                priority=NotificationPriority.HIGH,
                channels=[NotificationChannel.IN_APP, NotificationChannel.EMAIL],
                context={"member_id": member_id, "days_overdue": days_overdue, "amount": str(amount)},
                action_url="/admin/contributions/overdue",
                action_text="Review",
            ))
        
        else:
            # Critical alert
            notifications.append(SmartNotification(
                notification_type="contribution_overdue_critical",
                recipient_id=chama_id,
                recipient_type="admin",
                title="Critical: Contribution Significantly Overdue",
                message=f"A member's contribution of KES {amount:,.2f} is {days_overdue} days overdue. Immediate action required.",
                priority=NotificationPriority.URGENT,
                channels=[NotificationChannel.IN_APP, NotificationChannel.EMAIL, NotificationChannel.SMS],
                context={"member_id": member_id, "days_overdue": days_overdue, "amount": str(amount)},
                action_url="/admin/contributions/overdue",
                action_text="Take Action",
            ))
    
    # Overdue loans
    for loan in overdue_loans:
        member_id = loan.get("member_id")
        due_date = loan.get("due_date")
        outstanding = Decimal(str(loan.get("outstanding_balance", 0)))
        
        if isinstance(due_date, str):
            due_date = date.fromisoformat(due_date)
        
        days_overdue = (current_date - due_date).days
        
        if days_overdue <= 7:
            notifications.append(SmartNotification(
                notification_type="loan_repayment_reminder",
                recipient_id=member_id,
                recipient_type="member",
                title="Loan Repayment Overdue",
                message=f"Your loan repayment of KES {outstanding:,.2f} was due on {due_date.strftime('%B %d, %Y')}. Please make payment.",
                priority=NotificationPriority.HIGH,
                channels=[NotificationChannel.IN_APP, NotificationChannel.SMS],
                context={"days_overdue": days_overdue, "outstanding": str(outstanding)},
                action_url="/loans/repay",
                action_text="Repay Now",
            ))
        
        else:
            # Escalate to admin and notify guarantors
            notifications.append(SmartNotification(
                notification_type="loan_overdue_escalated",
                recipient_id=chama_id,
                recipient_type="admin",
                title="Loan Significantly Overdue",
                message=f"A loan of KES {outstanding:,.2f} is {days_overdue} days overdue. Recovery action may be needed.",
                priority=NotificationPriority.URGENT,
                channels=[NotificationChannel.IN_APP, NotificationChannel.EMAIL],
                context={"member_id": member_id, "days_overdue": days_overdue, "outstanding": str(outstanding)},
                action_url="/admin/loans/overdue",
                action_text="Review",
            ))
    
    return notifications


# ============================================================================
# MEETING REMINDERS
# ============================================================================

def generate_meeting_reminders(
    chama_id: str,
    upcoming_meetings: list[dict],
    members: list[dict],
    current_date: date = None,
) -> list[SmartNotification]:
    """
    Generate meeting reminders.
    
    Logic:
    - 7 days before: Save the date
    - 3 days before: Agenda reminder
    - 1 day before: Final reminder
    - 2 hours before: Starting soon
    """
    if current_date is None:
        current_date = timezone.now().date()
    
    notifications = []
    
    for meeting in upcoming_meetings:
        meeting_date = meeting.get("date")
        if isinstance(meeting_date, str):
            meeting_date = date.fromisoformat(meeting_date)
        
        days_until = (meeting_date - current_date).days
        meeting_title = meeting.get("title", "Chama Meeting")
        meeting_time = meeting.get("time", "TBD")
        meeting_location = meeting.get("location", "TBD")
        
        for member in members:
            member_id = member.get("id")
            
            if days_until == 7:
                notifications.append(SmartNotification(
                    notification_type="meeting_save_the_date",
                    recipient_id=member_id,
                    recipient_type="member",
                    title="Upcoming Meeting",
                    message=f"Reminder: {meeting_title} is scheduled for {meeting_date.strftime('%B %d, %Y')} at {meeting_time}.",
                    priority=NotificationPriority.LOW,
                    channels=[NotificationChannel.IN_APP],
                    context={
                        "meeting_id": meeting.get("id"),
                        "meeting_date": meeting_date.isoformat(),
                    },
                    action_url=f"/meetings/{meeting.get('id')}",
                    action_text="View Details",
                ))
            
            elif days_until == 3:
                notifications.append(SmartNotification(
                    notification_type="meeting_agenda_reminder",
                    recipient_id=member_id,
                    recipient_type="member",
                    title="Meeting Agenda Available",
                    message=f"The agenda for {meeting_title} on {meeting_date.strftime('%B %d, %Y')} is now available.",
                    priority=NotificationPriority.MEDIUM,
                    channels=[NotificationChannel.IN_APP, NotificationChannel.EMAIL],
                    context={
                        "meeting_id": meeting.get("id"),
                        "meeting_date": meeting_date.isoformat(),
                    },
                    action_url=f"/meetings/{meeting.get('id')}/agenda",
                    action_text="View Agenda",
                ))
            
            elif days_until == 1:
                notifications.append(SmartNotification(
                    notification_type="meeting_tomorrow",
                    recipient_id=member_id,
                    recipient_type="member",
                    title="Meeting Tomorrow",
                    message=f"{meeting_title} is tomorrow at {meeting_time}. Location: {meeting_location}",
                    priority=NotificationPriority.HIGH,
                    channels=[NotificationChannel.IN_APP, NotificationChannel.SMS],
                    context={
                        "meeting_id": meeting.get("id"),
                        "meeting_date": meeting_date.isoformat(),
                    },
                    action_url=f"/meetings/{meeting.get('id')}",
                    action_text="View Details",
                ))
    
    return notifications


# ============================================================================
# LOAN REPAYMENT REMINDERS
# ============================================================================

def generate_loan_repayment_reminders(
    chama_id: str,
    active_loans: list[dict],
    current_date: date = None,
) -> list[SmartNotification]:
    """
    Generate loan repayment reminders.
    
    Logic:
    - 7 days before due: Gentle reminder
    - 3 days before due: Urgent reminder
    - 1 day before due: Final reminder
    - Due date: Payment due today
    """
    if current_date is None:
        current_date = timezone.now().date()
    
    notifications = []
    
    for loan in active_loans:
        member_id = loan.get("member_id")
        next_payment_date = loan.get("next_payment_date")
        payment_amount = Decimal(str(loan.get("next_payment_amount", 0)))
        
        if isinstance(next_payment_date, str):
            next_payment_date = date.fromisoformat(next_payment_date)
        
        days_until_due = (next_payment_date - current_date).days
        
        if days_until_due == 7:
            notifications.append(SmartNotification(
                notification_type="loan_repayment_reminder",
                recipient_id=member_id,
                recipient_type="member",
                title="Loan Repayment Due Soon",
                message=f"Your loan repayment of KES {payment_amount:,.2f} is due on {next_payment_date.strftime('%B %d, %Y')}.",
                priority=NotificationPriority.LOW,
                channels=[NotificationChannel.IN_APP],
                context={
                    "loan_id": loan.get("id"),
                    "due_date": next_payment_date.isoformat(),
                    "amount": str(payment_amount),
                },
                action_url="/loans/repay",
                action_text="Repay Now",
            ))
        
        elif days_until_due == 3:
            notifications.append(SmartNotification(
                notification_type="loan_repayment_urgent",
                recipient_id=member_id,
                recipient_type="member",
                title="Loan Repayment Due Soon",
                message=f"Your loan repayment of KES {payment_amount:,.2f} is due in 3 days. Please prepare for payment.",
                priority=NotificationPriority.MEDIUM,
                channels=[NotificationChannel.IN_APP, NotificationChannel.SMS],
                context={
                    "loan_id": loan.get("id"),
                    "due_date": next_payment_date.isoformat(),
                    "amount": str(payment_amount),
                },
                action_url="/loans/repay",
                action_text="Repay Now",
            ))
        
        elif days_until_due == 1:
            notifications.append(SmartNotification(
                notification_type="loan_repayment_tomorrow",
                recipient_id=member_id,
                recipient_type="member",
                title="Loan Repayment Tomorrow",
                message=f"Your loan repayment of KES {payment_amount:,.2f} is due tomorrow. Please make payment to avoid penalties.",
                priority=NotificationPriority.HIGH,
                channels=[NotificationChannel.IN_APP, NotificationChannel.SMS],
                context={
                    "loan_id": loan.get("id"),
                    "due_date": next_payment_date.isoformat(),
                    "amount": str(payment_amount),
                },
                action_url="/loans/repay",
                action_text="Repay Now",
            ))
        
        elif days_until_due == 0:
            notifications.append(SmartNotification(
                notification_type="loan_repayment_due_today",
                recipient_id=member_id,
                recipient_type="member",
                title="Loan Repayment Due Today",
                message=f"Your loan repayment of KES {payment_amount:,.2f} is due today. Please make payment immediately.",
                priority=NotificationPriority.URGENT,
                channels=[NotificationChannel.IN_APP, NotificationChannel.SMS, NotificationChannel.PUSH],
                context={
                    "loan_id": loan.get("id"),
                    "due_date": next_payment_date.isoformat(),
                    "amount": str(payment_amount),
                },
                action_url="/loans/repay",
                action_text="Repay Now",
            ))
    
    return notifications


# ============================================================================
# APPROVAL ALERTS
# ============================================================================

def generate_approval_alerts(
    chama_id: str,
    pending_approvals: list[dict],
    admins: list[dict],
) -> list[SmartNotification]:
    """
    Generate alerts for pending approvals.
    
    Types:
    - Loan requests
    - Expense requests
    - Withdrawal requests
    - Join requests
    - Role changes
    """
    notifications = []
    
    for approval in pending_approvals:
        approval_type = approval.get("type", "")
        requester_name = approval.get("requester_name", "A member")
        amount = approval.get("amount")
        days_pending = approval.get("days_pending", 0)
        
        # Determine priority based on days pending
        if days_pending > 7:
            priority = NotificationPriority.HIGH
        elif days_pending > 3:
            priority = NotificationPriority.MEDIUM
        else:
            priority = NotificationPriority.LOW
        
        for admin in admins:
            admin_id = admin.get("id")
            
            if approval_type == "loan_request":
                message = f"{requester_name} has requested a loan of KES {amount:,.2f}. Pending for {days_pending} days."
                action_url = f"/admin/loans/pending/{approval.get('id')}"
            elif approval_type == "expense_request":
                message = f"{requester_name} has requested an expense of KES {amount:,.2f}. Pending for {days_pending} days."
                action_url = f"/admin/expenses/pending/{approval.get('id')}"
            elif approval_type == "join_request":
                message = f"{requester_name} has requested to join the chama. Pending for {days_pending} days."
                action_url = f"/admin/members/pending/{approval.get('id')}"
            else:
                message = f"A {approval_type} request is pending approval. Pending for {days_pending} days."
                action_url = f"/admin/approvals/{approval.get('id')}"
            
            notifications.append(SmartNotification(
                notification_type=f"{approval_type}_approval_needed",
                recipient_id=admin_id,
                recipient_type="admin",
                title=f"Approval Required: {approval_type.replace('_', ' ').title()}",
                message=message,
                priority=priority,
                channels=[NotificationChannel.IN_APP, NotificationChannel.EMAIL],
                context={
                    "approval_id": approval.get("id"),
                    "approval_type": approval_type,
                    "days_pending": days_pending,
                },
                action_url=action_url,
                action_text="Review",
            ))
    
    return notifications


# ============================================================================
# MONTHLY SUMMARY NOTIFICATIONS
# ============================================================================

def generate_monthly_summary_notification(
    chama_id: str,
    summary_data: dict,
    admins: list[dict],
) -> list[SmartNotification]:
    """
    Generate monthly summary notification for admins.
    """
    notifications = []
    
    month = summary_data.get("month", "")
    total_contributions = summary_data.get("total_contributions", 0)
    total_expenses = summary_data.get("total_expenses", 0)
    active_members = summary_data.get("active_members", 0)
    overdue_loans = summary_data.get("overdue_loans", 0)
    
    message = (
        f"Monthly Summary for {month}:\n"
        f"• Total Contributions: KES {total_contributions:,.2f}\n"
        f"• Total Expenses: KES {total_expenses:,.2f}\n"
        f"• Active Members: {active_members}\n"
        f"• Overdue Loans: {overdue_loans}"
    )
    
    for admin in admins:
        notifications.append(SmartNotification(
            notification_type="monthly_summary",
            recipient_id=admin.get("id"),
            recipient_type="admin",
            title=f"Monthly Summary - {month}",
            message=message,
            priority=NotificationPriority.LOW,
            channels=[NotificationChannel.IN_APP, NotificationChannel.EMAIL],
            context=summary_data,
            action_url="/reports/monthly",
            action_text="View Full Report",
        ))
    
    return notifications


# ============================================================================
# SMART ESCALATION LOGIC
# ============================================================================

def apply_escalation_policy(
    notification: SmartNotification,
    escalation_policy: dict,
    previous_notifications: list[dict],
) -> SmartNotification:
    """
    Apply smart escalation to notifications.
    
    Logic:
    - If user ignores 1 reminder → send in-app
    - If still unpaid → send SMS
    - If still unpaid after threshold → alert admin/treasurer
    - If loan risk rises → flag on admin dashboard
    """
    # Count previous notifications of same type
    same_type_count = sum(
        1 for n in previous_notifications
        if n.get("notification_type") == notification.notification_type
        and n.get("recipient_id") == notification.recipient_id
    )
    
    # Apply escalation based on count
    if same_type_count == 0:
        # First notification - use original channels
        pass
    elif same_type_count == 1:
        # Second notification - add SMS if not already present
        if NotificationChannel.SMS not in notification.channels:
            notification.channels.append(NotificationChannel.SMS)
        notification.priority = NotificationPriority.MEDIUM
    elif same_type_count >= 2:
        # Third+ notification - escalate to admin
        if notification.recipient_type == "member":
            # Create admin notification
            admin_notification = SmartNotification(
                notification_type=f"{notification.notification_type}_escalated",
                recipient_id=notification.context.get("chama_id", ""),
                recipient_type="admin",
                title=f"Escalated: {notification.title}",
                message=f"Member has not responded to {same_type_count} reminders. {notification.message}",
                priority=NotificationPriority.HIGH,
                channels=[NotificationChannel.IN_APP, NotificationChannel.EMAIL],
                context={
                    **notification.context,
                    "original_recipient": notification.recipient_id,
                    "escalation_count": same_type_count,
                },
                action_url="/admin/escalated",
                action_text="Review",
            )
            return admin_notification
    
    return notification


# ============================================================================
# AUTOMATION RULES ENGINE
# ============================================================================

def evaluate_automation_rules(
    event_type: str,
    event_data: dict,
    active_rules: list[AutomationRule],
) -> list[dict]:
    """
    Evaluate automation rules against an event.
    
    Returns list of actions to execute.
    """
    actions_to_execute = []
    
    for rule in active_rules:
        if not rule.is_active:
            continue
        
        if rule.trigger_event != event_type:
            continue
        
        # Evaluate conditions
        conditions_met = True
        for condition_key, condition_value in rule.conditions.items():
            event_value = event_data.get(condition_key)
            
            if isinstance(condition_value, dict):
                # Operator-based condition
                operator = condition_value.get("operator", "equals")
                expected = condition_value.get("value")
                
                if operator == "equals" and event_value != expected:
                    conditions_met = False
                elif operator == "greater_than" and not (event_value > expected):
                    conditions_met = False
                elif operator == "less_than" and not (event_value < expected):
                    conditions_met = False
                elif operator == "contains" and expected not in str(event_value):
                    conditions_met = False
            else:
                # Direct equality
                if event_value != condition_value:
                    conditions_met = False
            
            if not conditions_met:
                break
        
        if conditions_met:
            actions_to_execute.extend(rule.actions)
    
    return actions_to_execute
