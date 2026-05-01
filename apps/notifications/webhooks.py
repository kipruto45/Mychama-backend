"""
Webhook endpoints for notification delivery callbacks.

This module provides webhook handlers to receive delivery status updates
from email/SMS/WhatsApp providers.
"""

import hashlib
import hmac
import logging
from datetime import datetime

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.notifications.models import (
    NotificationDelivery,
    NotificationDeliveryStatus,
)

logger = logging.getLogger(__name__)


def verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify webhook signature using HMAC-SHA256."""
    if not signature or not secret:
        return False
    
    expected_signature = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(f"sha256={expected_signature}", signature)


class BaseWebhookMixin:
    """Base mixin for webhook views with common functionality."""
    
    def get_delivery_record(self, message_id: str):
        """Find delivery record by provider message ID."""
        return NotificationDelivery.objects.filter(
            external_message_id=message_id
        ).first()
    
    def update_delivery_status(
        self,
        delivery: NotificationDelivery,
        status: str,
        error_message: str = None,
        delivered_at: datetime = None,
    ):
        """Update delivery record status."""
        delivery.status = status
        if error_message:
            delivery.error_message = error_message
        if delivered_at:
            delivery.delivered_at = delivered_at
        elif status == NotificationDeliveryStatus.DELIVERED:
            delivery.delivered_at = timezone.now()
        delivery.save(update_fields=['status', 'error_message', 'delivered_at', 'updated_at'])
        
        logger.info(f"Updated delivery {delivery.id} status to {status}")
        return delivery


@method_decorator(csrf_exempt, name='dispatch')
class SendGridWebhookView(BaseWebhookMixin, View):
    """
    Handle SendGrid webhook events.
    
    SendGrid sends POST requests with event data.
    Events: processed, dropped, delivered, bounced, deferred, opened, clicked
    
    Reference: https://docs.sendgrid.com/for-developers/tracking-events/event
    """
    
    def post(self, request):
        # Verify signature if configured
        signature = request.headers.get('X-Signature', '')
        secret = getattr(settings, 'SENDGRID_WEBHOOK_SECRET', '')
        
        if secret:
            payload = request.body
            if not verify_webhook_signature(payload, signature, secret):
                logger.warning("Invalid SendGrid webhook signature")
                return JsonResponse({'error': 'Invalid signature'}, status=401)
        
        import json
        try:
            events = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
        
        if not isinstance(events, list):
            events = [events]
        
        processed_count = 0
        
        for event in events:
            message_id = event.get('smtp-id') or event.get('message_id')
            event_type = event.get('event', '').lower()
            
            if not message_id:
                continue
            
            delivery = self.get_delivery_record(message_id)
            if not delivery:
                logger.debug(f"No delivery record found for message_id: {message_id}")
                continue
            
            # Map SendGrid event to our status
            status_map = {
                'delivered': NotificationDeliveryStatus.DELIVERED,
                'bounced': NotificationDeliveryStatus.FAILED,
                'dropped': NotificationDeliveryStatus.FAILED,
                'blocked': NotificationDeliveryStatus.FAILED,
                'failed': NotificationDeliveryStatus.FAILED,
            }
            
            status = status_map.get(event_type)
            if status:
                error_msg = event.get('reason') or event.get('error')
                self.update_delivery_status(
                    delivery,
                    status,
                    error_message=error_msg,
                    delivered_at=timezone.now() if status == NotificationDeliveryStatus.DELIVERED else None
                )
                processed_count += 1
        
        return JsonResponse({'status': 'ok', 'processed': processed_count})


@method_decorator(csrf_exempt, name='dispatch')
class MailgunWebhookView(BaseWebhookMixin, View):
    """
    Handle Mailgun webhook events.
    
    Mailgun sends POST requests with form data.
    Events: delivered, opened, clicked, bounced, dropped, unsubscribed
    
    Reference: https://documentation.mailgun.com/en/latest/user_manual.html#webhooks
    """
    
    def post(self, request):
        # Verify signature if configured
        signature = request.POST.get('signature', '')
        timestamp = request.POST.get('timestamp', '')
        token = request.POST.get('token', '')
        secret = getattr(settings, 'MAILGUN_WEBHOOK_SECRET', '')
        
        if secret:
            if not self._verify_mailgun_signature(secret, timestamp, token, signature):
                logger.warning("Invalid Mailgun webhook signature")
                return JsonResponse({'error': 'Invalid signature'}, status=401)
        
        event_type = request.POST.get('event', '').lower()
        message_id = request.POST.get('message-id') or request.POST.get('Message-Id')
        
        if not message_id:
            return JsonResponse({'status': 'ok', 'note': 'No message_id'})
        
        delivery = self.get_delivery_record(message_id)
        if not delivery:
            logger.debug(f"No delivery record found for message_id: {message_id}")
            return JsonResponse({'status': 'ok', 'note': 'No delivery record'})
        
        # Map Mailgun event to our status
        status_map = {
            'delivered': NotificationDeliveryStatus.DELIVERED,
            'opened': NotificationDeliveryStatus.DELIVERED,
            'clicked': NotificationDeliveryStatus.DELIVERED,
            'bounced': NotificationDeliveryStatus.FAILED,
            'dropped': NotificationDeliveryStatus.FAILED,
            'unsubscribed': NotificationDeliveryStatus.FAILED,
        }
        
        status = status_map.get(event_type)
        if status:
            error_msg = request.POST.get('error') or request.POST.get('reason')
            self.update_delivery_status(
                delivery,
                status,
                error_message=error_msg,
                delivered_at=timezone.now() if status == NotificationDeliveryStatus.DELIVERED else None
            )
        
        return JsonResponse({'status': 'ok'})
    
    def _verify_mailgun_signature(self, secret: str, timestamp: str, token: str, signature: str) -> bool:
        """Verify Mailgun webhook signature."""
        import hashlib
        import hmac
        
        if not signature or not timestamp or not token:
            return False
        
        # Check timestamp is not too old (5 minutes)
        try:
            ts = int(timestamp)
            if abs(timezone.now().timestamp() - ts) > 300:
                return False
        except (ValueError, TypeError):
            return False
        
        # Compute expected signature
        expected = hmac.new(
            secret.encode(),
            f"{timestamp}{token}".encode(),
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(expected, signature)


@method_decorator(csrf_exempt, name='dispatch')
class AfricaTalkingWebhookView(BaseWebhookMixin, View):
    """
    Handle Africa's Talking delivery report webhook.
    
    Africa's Talking sends callbacks for SMS delivery status.
    Status: Success, Queued, Submitted, Pending, Failed, Canceled
    """
    
    def post(self, request):
        # Verify callback token if configured
        getattr(settings, 'OTP_SMS_CALLBACK_TOKEN', '')
        
        # Africa's Talking may send data in different formats
        import json
        
        try:
            # Try JSON first
            data = json.loads(request.body)
        except json.JSONDecodeError:
            # Fall back to form data
            data = dict(request.POST)
        
        # Handle bulk status response
        if 'SMSMessageData' in data:
            results = data['SMSMessageData'].get('Recipients', [])
            for recipient in results:
                self._process_africas_talking_status(recipient)
        elif 'status' in data:
            # Single message status
            self._process_africas_talking_status(data)
        
        return JsonResponse({'status': 'ok'})
    
    def _process_africas_talking_status(self, recipient: dict):
        """Process a single recipient status from Africa's Talking."""
        message_id = recipient.get('messageId')
        status = recipient.get('status', '').lower()
        
        if not message_id:
            return
        
        delivery = self.get_delivery_record(message_id)
        if not delivery:
            logger.debug(f"No delivery record found for message_id: {message_id}")
            return
        
        # Map AT status to our status
        status_map = {
            'success': NotificationDeliveryStatus.DELIVERED,
            'submitted': NotificationDeliveryStatus.SENT,
            'queued': NotificationDeliveryStatus.QUEUED,
            'pending': NotificationDeliveryStatus.QUEUED,
            'failed': NotificationDeliveryStatus.FAILED,
            'canceled': NotificationDeliveryStatus.FAILED,
        }
        
        new_status = status_map.get(status)
        if new_status:
            error_msg = recipient.get('errorMessage') or recipient.get('failureReason')
            self.update_delivery_status(
                delivery,
                new_status,
                error_message=error_msg,
                delivered_at=timezone.now() if new_status == NotificationDeliveryStatus.DELIVERED else None
            )


@method_decorator(csrf_exempt, name='dispatch')
class WhatsAppWebhookView(BaseWebhookMixin, View):
    """
    Handle WhatsApp Business API webhook for delivery status.
    
    Meta sends callbacks for message status updates.
    Statuses: sent, delivered, read, failed
    """
    
    def get(self, request):
        # WhatsApp requires verification on setup
        mode = request.GET.get('hub.mode')
        token = request.GET.get('hub.verify_token')
        challenge = request.GET.get('hub.challenge')
        
        verify_token = getattr(settings, 'WHATSAPP_VERIFY_TOKEN', '')
        
        if mode == 'subscribe' and token == verify_token:
            return JsonResponse(challenge, status=200)
        
        return JsonResponse({'error': 'Verification failed'}, status=403)
    
    def post(self, request):
        import json
        
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
        
        # Verify webhook if configured
        signature = request.headers.get('X-Hub-Signature-256', '')
        secret = getattr(settings, 'WHATSAPP_WEBHOOK_SECRET', '')
        
        if secret:
            payload = request.body
            if not verify_webhook_signature(payload, signature, secret):
                logger.warning("Invalid WhatsApp webhook signature")
                return JsonResponse({'error': 'Invalid signature'}, status=401)
        
        # Process entries
        entries = data.get('entry', [])
        
        for entry in entries:
            changes = entry.get('changes', [])
            for change in changes:
                change.get('value', {}).get('messages', [])
                statuses = change.get('value', {}).get('statuses', [])
                
                # Process message delivery statuses
                for status_update in statuses:
                    self._process_whatsapp_status(status_update)
        
        return JsonResponse({'status': 'ok'})
    
    def _process_whatsapp_status(self, status_update: dict):
        """Process a single WhatsApp status update."""
        message_id = status_update.get('id')
        status = status_update.get('status', '').lower()
        
        if not message_id:
            return
        
        delivery = self.get_delivery_record(message_id)
        if not delivery:
            logger.debug(f"No delivery record found for message_id: {message_id}")
            return
        
        # Map WhatsApp status to our status
        status_map = {
            'sent': NotificationDeliveryStatus.SENT,
            'delivered': NotificationDeliveryStatus.DELIVERED,
            'read': NotificationDeliveryStatus.DELIVERED,
            'failed': NotificationDeliveryStatus.FAILED,
        }
        
        new_status = status_map.get(status)
        if new_status:
            error_msg = status_update.get('errors', [{}])[0].get('message') if status_update.get('errors') else None
            self.update_delivery_status(
                delivery,
                new_status,
                error_message=error_msg,
                delivered_at=timezone.now() if new_status == NotificationDeliveryStatus.DELIVERED else None
            )


# URL patterns for webhook endpoints
WEBHOOK_URLS = {
    'sendgrid': '/webhooks/email/sendgrid/',
    'mailgun': '/webhooks/email/mailgun/',
    'africas_talking': '/webhooks/sms/africastalking/',
    'whatsapp': '/webhooks/whatsapp/',
}
