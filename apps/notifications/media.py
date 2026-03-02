"""
Rich Media Notification Service

This module provides enhanced notification capabilities with media attachments
(images, documents, buttons) for various channels.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class MediaAttachment:
    """Media attachment for notifications."""
    url: str
    media_type: str  # image, document, audio, video
    caption: Optional[str] = None
    filename: Optional[str] = None


@dataclass
class ActionButton:
    """Action button for rich notifications."""
    text: str
    url: Optional[str] = None
    callback_data: Optional[str] = None
    action_type: str = "button"  # button, link, callback


@dataclass
class RichNotificationData:
    """Rich notification data with media and buttons."""
    # Media attachments
    image_url: Optional[str] = None
    image_caption: Optional[str] = None
    document_url: Optional[str] = None
    document_caption: Optional[str] = None
    document_filename: Optional[str] = None
    audio_url: Optional[str] = None
    video_url: Optional[str] = None
    
    # Action buttons (up to 4 for most platforms)
    buttons: Optional[list] = None
    
    # Preview text (for push notifications)
    preview_text: Optional[str] = None
    
    # Channel-specific options
    channel_options: Optional[dict] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON storage."""
        return {
            "image_url": self.image_url,
            "image_caption": self.image_caption,
            "document_url": self.document_url,
            "document_caption": self.document_caption,
            "document_filename": self.document_filename,
            "audio_url": self.audio_url,
            "video_url": self.video_url,
            "buttons": [b.__dict__ for b in self.buttons] if self.buttons else None,
            "preview_text": self.preview_text,
            "channel_options": self.channel_options,
        }


def create_image_attachment(url: str, caption: str = None) -> MediaAttachment:
    """Create an image media attachment."""
    return MediaAttachment(url=url, media_type="image", caption=caption)


def create_document_attachment(url: str, filename: str, caption: str = None) -> MediaAttachment:
    """Create a document media attachment."""
    return MediaAttachment(url=url, media_type="document", caption=caption, filename=filename)


def create_action_button(
    text: str,
    url: str = None,
    callback_data: str = None,
) -> ActionButton:
    """Create an action button."""
    action_type = "link" if url else ("callback" if callback_data else "button")
    return ActionButton(text=text, url=url, callback_data=callback_data, action_type=action_type)


class RichNotificationBuilder:
    """Builder for rich notifications with media and buttons."""
    
    def __init__(self):
        self._image_url = None
        self._image_caption = None
        self._document_url = None
        self._document_caption = None
        self._document_filename = None
        self._audio_url = None
        self._video_url = None
        self._buttons = []
        self._preview_text = None
        self._channel_options = {}
    
    def set_image(self, url: str, caption: str = None) -> "RichNotificationBuilder":
        """Set notification image."""
        self._image_url = url
        self._image_caption = caption
        return self
    
    def set_document(self, url: str, filename: str, caption: str = None) -> "RichNotificationBuilder":
        """Set document attachment."""
        self._document_url = url
        self._document_filename = filename
        self._document_caption = caption
        return self
    
    def set_audio(self, url: str) -> "RichNotificationBuilder":
        """Set audio attachment."""
        self._audio_url = url
        return self
    
    def set_video(self, url: str) -> "RichNotificationBuilder":
        """Set video attachment."""
        self._video_url = url
        return self
    
    def add_button(self, text: str, url: str = None, callback_data: str = None) -> "RichNotificationBuilder":
        """Add an action button (max 4)."""
        if len(self._buttons) >= 4:
            raise ValueError("Maximum 4 buttons allowed")
        self._buttons.append(create_action_button(text, url, callback_data))
        return self
    
    def set_preview_text(self, text: str) -> "RichNotificationBuilder":
        """Set preview text for push notifications."""
        self._preview_text = text
        return self
    
    def set_channel_option(self, key: str, value) -> "RichNotificationBuilder":
        """Set channel-specific option."""
        self._channel_options[key] = value
        return self
    
    def build(self) -> RichNotificationData:
        """Build the rich notification data."""
        return RichNotificationData(
            image_url=self._image_url,
            image_caption=self._image_caption,
            document_url=self._document_url,
            document_caption=self._document_caption,
            document_filename=self._document_filename,
            audio_url=self._audio_url,
            video_url=self._video_url,
            buttons=self._buttons if self._buttons else None,
            preview_text=self._preview_text,
            channel_options=self._channel_options if self._channel_options else None,
        )
    
    def to_metadata(self) -> dict:
        """Convert to metadata dict for storing in Notification.metadata."""
        return {"rich_data": self.build().to_dict()}


# Pre-built rich notification templates

def create_meeting_reminder_rich() -> RichNotificationData:
    """Create a rich meeting reminder with image and action button."""
    return RichNotificationBuilder() \
        .set_image(
            "https://example.com/images/meeting-reminder.jpg",
            "Weekly Chama Meeting"
        ) \
        .add_button("View Agenda", "https://chama.example.com/meetings/agenda") \
        .add_button("Mark Calendar", "https://chama.example.com/calendar/add") \
        .set_preview_text("Don't forget our chama meeting this Saturday!") \
        .build()


def create_payment_confirmation_rich(amount: str, chama_name: str) -> RichNotificationData:
    """Create a rich payment confirmation with details."""
    return RichNotificationBuilder() \
        .set_image(
            "https://example.com/images/payment-success.png",
            "Payment Received"
        ) \
        .add_button("View Receipt", "https://chama.example.com/receipts/view") \
        .add_button("View Balance", "https://chama.example.com/balance") \
        .set_preview_text(f"KES {amount} received by {chama_name}") \
        .build()


def create_loan_approval_rich(loan_amount: str, chama_name: str) -> RichNotificationData:
    """Create a rich loan approval notification."""
    return RichNotificationBuilder() \
        .set_image(
            "https://example.com/images/loan-approved.png",
            "Loan Approved"
        ) \
        .add_button("Accept Loan", "https://chama.example.com/loans/accept") \
        .add_button("View Terms", "https://chama.example.com/loans/terms") \
        .set_preview_text(f"Your loan of KES {loan_amount} has been approved!") \
        .build()


# Channel-specific formatters

def format_for_email(rich_data: RichNotificationData, base_message: str) -> tuple[str, str]:
    """
    Format rich notification for email.
    Returns (subject, html_body).
    """
    html_parts = [f"<p>{base_message}</p>"]
    
    if rich_data.image_url:
        img_tag = f'<img src="{rich_data.image_url}" alt="{rich_data.image_caption or "Image"}" style="max-width:100%; height:auto;">'
        if rich_data.image_caption:
            img_tag += f'<p><em>{rich_data.image_caption}</em></p>'
        html_parts.append(img_tag)
    
    if rich_data.buttons:
        buttons_html = '<div style="margin: 20px 0;">'
        for button in rich_data.buttons:
            if button.url:
                buttons_html += f'''
                <a href="{button.url}" style="
                    display: inline-block;
                    padding: 12px 24px;
                    background-color: #4CAF50;
                    color: white;
                    text-decoration: none;
                    border-radius: 4px;
                    margin-right: 10px;
                ">{button.text}</a>
                '''
        buttons_html += '</div>'
        html_parts.append(buttons_html)
    
    if rich_data.document_url:
        doc_link = f'<p><a href="{rich_data.document_url}">📎 Download Document</a></p>'
        html_parts.append(doc_link)
    
    return "", "\n".join(html_parts)


def format_for_whatsapp(rich_data: RichNotificationData, base_message: str) -> dict:
    """
    Format rich notification for WhatsApp.
    Returns dict with message and optional media.
    """
    result = {"message": base_message}
    
    if rich_data.image_url:
        result["media"] = {
            "type": "image",
            "url": rich_data.image_url,
            "caption": rich_data.image_caption,
        }
    
    if rich_data.buttons:
        # WhatsApp supports interactive buttons
        buttons = []
        for button in rich_data.buttons[:3]:  # WhatsApp max 3 buttons
            buttons.append({"type": "reply", "reply": {"id": button.callback_data or button.text, "title": button.text}})
        
        result["interactive"] = {
            "type": "button",
            "body": {"text": base_message},
            "action": {"buttons": buttons},
        }
    
    return result


def format_for_telegram(rich_data: RichNotificationData, base_message: str) -> dict:
    """
    Format rich notification for Telegram.
    Returns dict with message and inline keyboard.
    """
    result = {"text": base_message, "parse_mode": "HTML"}
    
    if rich_data.buttons:
        keyboard_buttons = []
        row = []
        for button in rich_data.buttons:
            if button.url:
                row.append({"text": button.text, "url": button.url})
            elif button.callback_data:
                row.append({"text": button.text, "callback_data": button.callback_data})
            
            if len(row) >= 2:
                keyboard_buttons.append(row)
                row = []
        
        if row:
            keyboard_buttons.append(row)
        
        if keyboard_buttons:
            import json
            result["reply_markup"] = json.dumps({"inline_keyboard": keyboard_buttons})
    
    return result


def format_for_push(rich_data: RichNotificationData, title: str, body: str) -> dict:
    """
    Format rich notification for Push (FCM).
    Returns dict with notification and data payloads.
    """
    result = {
        "notification": {
            "title": title,
            "body": rich_data.preview_text or body,
        },
        "data": {},
    }
    
    if rich_data.image_url:
        result["notification"]["image"] = rich_data.image_url
        result["data"]["image_url"] = rich_data.image_url
    
    if rich_data.buttons:
        result["data"]["buttons"] = [b.__dict__ for b in rich_data.buttons]
    
    return result
