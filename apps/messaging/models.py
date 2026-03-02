# Messaging Module Models
# Handles announcements, channels, direct messages, and conversations

from django.db import models
from django.conf import settings
from apps.chama.models import Chama
from apps.accounts.models import User


class ConversationType(models.TextChoices):
    CHANNEL = 'CHANNEL', 'Channel'
    DIRECT = 'DIRECT', 'Direct Message'
    ANNOUNCEMENT = 'ANNOUNCEMENT', 'Announcement'


class MessageType(models.TextChoices):
    TEXT = 'TEXT', 'Text'
    ATTACHMENT = 'ATTACHMENT', 'Attachment'
    SYSTEM = 'SYSTEM', 'System'


class AudienceType(models.TextChoices):
    ALL = 'ALL', 'All Members'
    ROLE = 'ROLE', 'By Role'
    SELECTED = 'SELECTED', 'Selected Members'


class Conversation(models.Model):
    """Represents a conversation thread (channel or DM)"""
    chama = models.ForeignKey(Chama, on_delete=models.CASCADE, related_name='conversations')
    conversation_type = models.CharField(max_length=20, choices=ConversationType.choices)
    name = models.CharField(max_length=255, blank=True)  # For channels
    description = models.TextField(blank=True)
    
    # For DM - links to participants
    is_group = models.BooleanField(default=False)
    
    # Channel settings
    is_pinned = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False)
    
    # Roles that can access this channel (for role-based channels)
    allowed_roles = models.JSONField(default=list, blank=True)
    
    # Metadata
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_conversations')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-updated_at']
    
    def __str__(self):
        return f"{self.get_conversation_type_display()}: {self.name or 'DM'}"


class ConversationMember(models.Model):
    """Links users to conversations with their settings"""
    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Admin'
        MODERATOR = 'MODERATOR', 'Moderator'
        MEMBER = 'MEMBER', 'Member'
    
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='conversation_memberships')
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)
    
    # User settings for this conversation
    is_muted = models.BooleanField(default=False)
    notifications_enabled = models.BooleanField(default=True)
    
    # Join info
    joined_at = models.DateTimeField(auto_now_add=True)
    last_read_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        unique_together = ['conversation', 'user']
    
    def __str__(self):
        return f"{self.user.get_full_name()} in {self.conversation.name}"


class Message(models.Model):
    """Individual message in a conversation"""
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='sent_messages')
    message_type = models.CharField(max_length=20, choices=MessageType.choices, default=MessageType.TEXT)
    
    # Message content
    body = models.TextField()
    
    # For system messages - action type
    action_type = models.CharField(max_length=50, blank=True)  # e.g., 'user_joined', 'message_pinned'
    action_data = models.JSONField(default=dict, blank=True)
    
    # Metadata
    mentions = models.JSONField(default=list, blank=True)  # List of user IDs mentioned
    is_pinned = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['created_at']
    
    def __str__(self):
        return f"Message by {self.sender.get_full_name() if self.sender else 'System'}"


class MessageAttachment(models.Model):
    """Attachments for messages"""
    class AttachmentType(models.TextChoices):
        IMAGE = 'IMAGE', 'Image'
        DOCUMENT = 'DOCUMENT', 'Document'
        VIDEO = 'VIDEO', 'Video'
        AUDIO = 'AUDIO', 'Audio'
    
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name='attachments')
    file_url = models.URLField()
    file_name = models.CharField(max_length=255)
    file_type = models.CharField(max_length=20, choices=AttachmentType.choices)
    file_size = models.PositiveIntegerField()  # in bytes
    
    uploaded_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"Attachment: {self.file_name}"


class MessageReadReceipt(models.Model):
    """Tracks read status of messages"""
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name='read_receipts')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='message_read_receipts')
    read_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['message', 'user']
    
    def __str__(self):
        return f"{self.user.get_full_name()} read message {self.message.id}"


class Announcement(models.Model):
    """Broadcast announcements to members"""
    chama = models.ForeignKey(Chama, on_delete=models.CASCADE, related_name='announcements')
    
    # Content
    title = models.CharField(max_length=255)
    body = models.TextField()
    
    # Audience targeting
    audience_type = models.CharField(max_length=20, choices=AudienceType.choices)
    audience_roles = models.JSONField(default=list, blank=True)  # List of role names
    audience_members = models.JSONField(default=list, blank=True)  # List of user IDs
    
    # Scheduling
    is_scheduled = models.BooleanField(default=False)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    
    # Status
    is_draft = models.BooleanField(default=True)
    is_sent = models.BooleanField(default=False)
    
    # Created by
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_announcements')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return self.title


class AnnouncementLog(models.Model):
    """Tracks delivery status of announcements"""
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name='logs')
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='announcement_logs')
    
    status = models.CharField(max_length=20)  # QUEUED, SENT, DELIVERED, READ, FAILED
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-sent_at']
    
    def __str__(self):
        return f"Announcement log for {self.recipient.get_full_name()}"


class MessageTemplate(models.Model):
    """Reusable message templates"""
    chama = models.ForeignKey(Chama, on_delete=models.CASCADE, related_name='message_templates')
    
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=100)  # e.g., 'contribution_reminder', 'meeting_reminder'
    
    # Template content with placeholders
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField()
    
    # For channels or announcements
    is_channel_template = models.BooleanField(default=False)
    is_announcement_template = models.BooleanField(default=False)
    
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_templates')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['name']
    
    def __str__(self):
        return self.name


class ReportedMessage(models.Model):
    """Reports for moderation"""
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name='reports')
    reported_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reported_messages')
    
    reason = models.TextField()
    status = models.CharField(max_length=20, default='PENDING')  # PENDING, REVIEWED, RESOLVED
    
    resolved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='resolved_reports')
    resolution_notes = models.TextField(blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Report on message {self.message.id}"
