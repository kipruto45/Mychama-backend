"""
Smart Communication Automations

Production-grade automations for:
- Smart reminder timing (learns optimal send times)
- Multilingual message selector
- Do Not Disturb enforcement
- Daily digest bundler
- WhatsApp message dispatcher (future-ready)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Q
from django.utils import timezone

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.chama.models import Chama


@dataclass
class OptimalReminderTime:
    """Optimal reminder time result."""
    user_id: str
    preferred_hour: int
    preferred_minute: int
    preferred_days: list[int]
    success_rate: float
    sample_size: int


def learn_optimal_reminder_time(
    user: "User",
    days_lookback: int = 90,
) -> OptimalReminderTime:
    """Learn optimal reminder time for a user based on historical payment patterns."""
    from apps.finance.models import Payment, PaymentStatus
    
    today = timezone.now().date()
    lookback_date = today - timedelta(days=days_lookback)
    
    payments = Payment.objects.filter(
        member=user,
        status=PaymentStatus.COMPLETED,
        payment_date__gte=lookback_date,
    ).order_by("payment_date")
    
    payment_times = defaultdict(list)
    
    for payment in payments:
        dt = payment.payment_date
        day_of_week = dt.isoweekday()
        hour = dt.hour
        payment_times[day_of_week].append(hour)
    
    preferred_days = list(payment_times.keys())
    sample_size = payments.count()
    success_rate = 0.0
    
    if sample_size > 0:
        all_hours = []
        for day, hours in payment_times.items():
            all_hours.extend(hours)
        
        if all_hours:
            from statistics import median
            preferred_hour = int(median(all_hours))
            preferred_minute = 0
            
            for hour, count in sorted(
                {h: all_hours.count(h) for h in all_hours}.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:3]:
                pass
            
            success_rate = min(100, (sample_size / days_lookback) * 100)
            
            return OptimalReminderTime(
                user_id=str(user.id),
                preferred_hour=preferred_hour,
                preferred_minute=preferred_minute,
                preferred_days=preferred_days,
                success_rate=success_rate,
                sample_size=sample_size,
            )
    
    default_hour = int(getattr(settings, "DEFAULT_REMINDER_HOUR", 9))
    default_days = [1, 2, 3, 4, 5]
    
    return OptimalReminderTime(
        user_id=str(user.id),
        preferred_hour=default_hour,
        preferred_minute=0,
        preferred_days=default_days,
        success_rate=0.0,
        sample_size=0,
    )


@dataclass
class ReminderSchedule:
    """Scheduled reminder."""
    user_id: str
    chama_id: str
    reminder_type: str
    scheduled_at: datetime
    message: str
    channel: str
    language: str


def schedule_smart_reminder(
    user: "User",
    chama: "Chama",
    reminder_type: str,
    base_message: str,
    channels: list[str],
) -> list[ReminderSchedule]:
    """Schedule reminder at optimal time for user."""
    optimal = learn_optimal_reminder_time(user)
    
    preferred_language = getattr(user, "preferred_language", "en")
    
    schedules = []
    now = timezone.now()
    
    next_send_hour = optimal.preferred_hour
    next_send_minute = optimal.preferred_minute
    
    if now.hour >= optimal.preferred_hour:
        next_send_date = now.date() + timedelta(days=1)
    else:
        next_send_date = now.date()
    
    scheduled_at = datetime.combine(
        next_send_date,
        time(next_send_hour, next_send_minute),
        tzinfo=now.tzinfo,
    )
    
    for channel in channels:
        schedules.append(ReminderSchedule(
            user_id=str(user.id),
            chama_id=str(chama.id),
            reminder_type=reminder_type,
            scheduled_at=scheduled_at,
            message=base_message,
            channel=channel,
            language=preferred_language,
        ))
    
    return schedules


LANGUAGE_TEMPLATES = {
    "en": {
        "contribution_reminder": "Reminder: Your contribution of KES {amount} is due on {due_date}. Pay now via M-Pesa.",
        "loan_reminder": "Reminder: Loan repayment of KES {amount} is due on {due_date}. Please ensure your payment is made.",
        "meeting_reminder": "Reminder: {chama_name} meeting on {meeting_date} at {meeting_time}. Your attendance matters!",
    },
    "sw": {
        "contribution_reminder": "Kikumbus: Malipo yako ya KES {amount} yako wasiliana tarehe {due_date}. Toa malipo sasa kupitia M-Pesa.",
        "loan_reminder": "Kikumbus: Malipo ya mkopo wa KES {amount} yako wasiliana tarehe {due_date}. Hakikisha umelipa.",
        "meeting_reminder": "Kikumbus: Mkutano wa {chama_name} tarehe {meeting_date} saa {meeting_time}. Kushiriki koko ni muhimu!",
    },
}


def get_localized_message(
    message_key: str,
    language: str,
    **kwargs,
) -> str:
    """Get localized message in the user's preferred language."""
    lang = language or "en"
    templates = LANGUAGE_TEMPLATES.get(lang, LANGUAGE_TEMPLATES["en"])
    
    template = templates.get(message_key, "")
    
    if not template:
        template = LANGUAGE_TEMPLATES["en"].get(message_key, message_key)
    
    try:
        return template.format(**kwargs)
    except (KeyError, ValueError):
        return template


def should_respect_quiet_hours(
    user: "User",
    channel: str = "sms",
) -> bool:
    """Check if notifications should respect quiet hours for this user."""
    from apps.notifications.models import NotificationPreference
    
    preference = NotificationPreference.objects.filter(
        user=user,
    ).first()
    
    if not preference:
        return channel.lower() == "sms"
    
    return bool(preference.quiet_hours_start) and bool(preference.quiet_hours_end)


def is_in_quiet_hours(user: "User", now: datetime | None = None) -> bool:
    """Check if current time is within user's quiet hours."""
    from apps.notifications.models import NotificationPreference
    
    now = now or timezone.localtime(timezone.now())
    
    preference = NotificationPreference.objects.filter(
        user=user,
    ).first()
    
    if not preference or not preference.quiet_hours_start:
        default_start = int(getattr(settings, "AUTOMATION_QUIET_HOURS_START", 21))
        default_end = int(getattr(settings, "AUTOMATION_QUIET_HOURS_END", 7))
        start = default_start
        end = default_end
    else:
        start = preference.quiet_hours_start.hour
        end = preference.quiet_hours_end.hour
    
    current_hour = now.hour
    
    if start <= end:
        return start <= current_hour < end
    
    return current_hour >= start or current_hour < end


@dataclass
class DigestItem:
    """Digest item."""
    item_type: str
    title: str
    body: str
    action_url: str | None


@dataclass
class DailyDigest:
    """Daily digest result."""
    user_id: str
    date: date
    items: list[DigestItem]
    item_count: int
    priority_count: int
    should_send: bool
    message_preview: str


def build_daily_digest(
    user: "User",
    max_items: int = 10,
) -> DailyDigest:
    """Build daily digest for a user."""
    from apps.notifications.models import Notification, NotificationInboxStatus, NotificationPriority
    
    today = timezone.now().date()
    
    notifications = Notification.objects.filter(
        recipient=user,
        created_at__date=today,
    ).exclude(
        inbox_status=NotificationInboxStatus.ARCHIVED,
    ).order_by("-priority", "-created_at")[:max_items]
    
    items = []
    priority_count = 0
    
    for notif in notifications:
        items.append(DigestItem(
            item_type=notif.type,
            title=notif.subject or notif.type,
            body=notif.message[:100] + "..." if len(notif.message) > 100 else notif.message,
            action_url=notif.action_url,
        ))
        
        if notif.priority in [NotificationPriority.HIGH, NotificationPriority.CRITICAL]:
            priority_count += 1
    
    item_count = len(items)
    should_send = item_count > 0
    
    if item_count == 0:
        should_send = False
    
    message_preview = f"You have {item_count} updates today."
    if priority_count > 0:
        message_preview = f"You have {item_count} updates ({priority_count} important)"
    
    return DailyDigest(
        user_id=str(user.id),
        date=today,
        items=items,
        item_count=item_count,
        priority_count=priority_count,
        should_send=should_send,
        message_preview=message_preview,
    )


@dataclass
class WhatsAppMessage:
    """WhatsApp message payload."""
    phone_number: str
    template_name: str
    parameters: dict
    language: str = "en"


def prepare_whatsapp_message(
    user: "User",
    template_name: str,
    parameters: dict,
    language: str | None = None,
) -> WhatsAppMessage | None:
    """Prepare WhatsApp message (future-ready hook for WhatsApp Business API)."""
    if not getattr(settings, "WHATSAPP_ENABLED", False):
        return None
    
    preferred_language = language or getattr(user, "preferred_language", "en")
    
    return WhatsAppMessage(
        phone_number=user.phone or "",
        template_name=template_name,
        parameters=parameters,
        language=preferred_language,
    )


@dataclass
class BulkMessageJob:
    """Bulk message job."""
    job_id: str
    chama_id: str
    message_type: str
    recipient_count: int
    scheduled_at: datetime
    status: str


def create_bulk_whatsapp_job(
    chama: "Chama",
    message_type: str,
    scheduled_at: datetime | None = None,
) -> BulkMessageJob:
    """Create bulk WhatsApp announcement job."""
    from apps.chama.models import Membership, MembershipStatus
    
    recipient_count = Membership.objects.filter(
        chama=chama,
        status=MembershipStatus.ACTIVE,
        is_active=True,
    ).count()
    
    import uuid
    job_id = str(uuid.uuid4())
    
    return BulkMessageJob(
        job_id=job_id,
        chama_id=str(chama.id),
        message_type=message_type,
        recipient_count=recipient_count,
        scheduled_at=scheduled_at or timezone.now(),
        status="pending",
    )