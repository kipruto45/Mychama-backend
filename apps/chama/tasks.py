from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.notifications.models import NotificationType
from apps.notifications.services import NotificationService

logger = logging.getLogger(__name__)


@shared_task
def send_expiring_invite_reminders():
    """
    Send reminders for invites expiring soon.
    Run daily at 9:00 AM.
    
    Reminds admins about invites expiring in:
    - 7 days
    - 3 days
    - 1 day
    """
    from apps.chama.models import InviteLink
    
    # Get invites expiring within the next 7 days
    now = timezone.now()
    seven_days = now + timedelta(days=7)
    one_day = now + timedelta(days=1)
    
    # Find active invites expiring soon
    expiring_invites = InviteLink.objects.filter(
        is_active=True,
        expires_at__gte=now,
        expires_at__lte=seven_days,
    ).select_related('chama', 'created_by')
    
    # Track which invites we've already reminded about
    reminder_days = [7, 3, 1]
    reminder_counts = {7: 0, 3: 0, 1: 0}
    
    for invite in expiring_invites:
        days_until_expiry = (invite.expires_at - now).days
        
        # Determine if we should send a reminder
        should_remind = False
        reminder_type = None
        
        if days_until_expiry in reminder_days:
            # Check if we already sent a reminder for this timeframe (using cache)
            cache_key = f"invite_reminder_{invite.id}_{days_until_expiry}"
            from django.core.cache import cache
            
            if not cache.get(cache_key):
                should_remind = True
                reminder_type = days_until_expiry
                # Set cache to prevent duplicate reminders (expires after the reminder period)
                cache.set(cache_key, True, timeout=86400 * (days_until_expiry + 1))
        
        if should_remind and reminder_type:
            try:
                # Send reminder to the admin who created the invite
                message = (
                    f"Reminder: Your invite link for {invite.chama.name} "
                    f"expires in {reminder_type} day(s) ({invite.expires_at.strftime('%Y-%m-%d')}). "
                    f"Uses: {invite.current_uses}/{invite.max_uses or 'unlimited'}"
                )
                
                NotificationService.send_notification(
                    user=invite.created_by,
                    message=message,
                    channels=["in_app", "email"],
                    notification_type=NotificationType.SYSTEM,
                    category="invite",
                )
                
                reminder_counts[reminder_type] += 1
                logger.info(f"Sent {reminder_type}-day expiry reminder for invite {invite.id}")
                
            except Exception as e:
                logger.error(f"Failed to send reminder for invite {invite.id}: {e}")
    
    logger.info(f"Expiring invite reminder summary: {reminder_counts}")
    return {
        "status": "completed",
        "reminders_sent": reminder_counts,
        "time": now.isoformat(),
    }


@shared_task
def cleanup_expired_invite_links():
    """
    Clean up expired invite links.
    Run daily at 2:00 AM.
    """
    from apps.chama.models import InviteLink
    
    now = timezone.now()
    
    # Find and deactivate expired invites
    expired_invites = InviteLink.objects.filter(
        is_active=True,
        expires_at__lt=now,
    )
    
    expired_count = expired_invites.count()
    
    # Deactivate expired invites
    expired_invites.update(
        is_active=False,
        revoked_at=now,
        revoke_reason="Auto-expired by system",
    )
    
    logger.info(f"Deactivated {expired_count} expired invite links")
    return {
        "status": "completed",
        "expired_count": expired_count,
        "time": now.isoformat(),
    }


@shared_task
def send_unused_invite_reminders():
    """
    Send reminders for invites that have never been used but were created recently.
    Run weekly on Mondays at 10:00 AM.
    
    Reminds admins about unused invites created in the last 14 days.
    """
    from apps.chama.models import InviteLink
    
    now = timezone.now()
    fourteen_days_ago = now - timedelta(days=14)
    
    # Find unused invites created in the last 14 days that are still active
    unused_invites = InviteLink.objects.filter(
        is_active=True,
        created_at__gte=fourteen_days_ago,
        current_uses=0,
    ).select_related('chama', 'created_by')
    
    reminder_count = 0
    
    for invite in unused_invites:
        # Check if we already sent a reminder for this invite (using cache)
        cache_key = f"unused_invite_reminder_{invite.id}"
        from django.core.cache import cache
        
        if not cache.get(cache_key):
            try:
                days_created = (now - invite.created_at).days
                message = (
                    f"Your invite link for {invite.chama.name} has not been used yet. "
                    f"Created {days_created} day(s) ago. "
                    f"Share it with potential members or revoke it if no longer needed."
                )
                
                NotificationService.send_notification(
                    user=invite.created_by,
                    message=message,
                    channels=["in_app", "email"],
                    notification_type=NotificationType.SYSTEM,
                    category="invite",
                )
                
                # Set cache to prevent duplicate reminders (expires after 7 days)
                cache.set(cache_key, True, timeout=604800)
                
                reminder_count += 1
                logger.info(f"Sent unused invite reminder for invite {invite.id}")
                
            except Exception as e:
                logger.error(f"Failed to send unused reminder for invite {invite.id}: {e}")
    
    logger.info(f"Sent {reminder_count} unused invite reminders")
    return {
        "status": "completed",
        "reminders_sent": reminder_count,
        "time": now.isoformat(),
    }
