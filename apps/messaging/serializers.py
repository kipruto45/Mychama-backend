# Messaging Module Serializers

from rest_framework import serializers

from .models import (
    Announcement,
    AnnouncementLog,
    Conversation,
    ConversationMember,
    Message,
    MessageAttachment,
    MessageReadReceipt,
    MessageTemplate,
    ReportedMessage,
)


class MessageAttachmentSerializer(serializers.ModelSerializer):
    """Serializer for MessageAttachment"""
    class Meta:
        model = MessageAttachment
        fields = ['id', 'file_url', 'file_name', 'file_type', 'file_size', 'uploaded_at']
        read_only_fields = ['uploaded_at']


class MessageReadReceiptSerializer(serializers.ModelSerializer):
    """Serializer for MessageReadReceipt"""
    user_name = serializers.SerializerMethodField()
    
    class Meta:
        model = MessageReadReceipt
        fields = ['id', 'message', 'user', 'user_name', 'read_at']
        read_only_fields = ['read_at']
    
    def get_user_name(self, obj):
        return obj.user.get_full_name()


class MessageSerializer(serializers.ModelSerializer):
    """Serializer for Message"""
    sender_name = serializers.SerializerMethodField()
    message_type_display = serializers.CharField(source='get_message_type_display', read_only=True)
    attachments = MessageAttachmentSerializer(many=True, read_only=True)
    read_receipts = MessageReadReceiptSerializer(many=True, read_only=True)
    read_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Message
        fields = [
            'id', 'conversation', 'sender', 'sender_name', 'message_type', 'message_type_display',
            'body', 'action_type', 'action_data', 'mentions', 'is_pinned', 'is_deleted',
            'attachments', 'read_receipts', 'read_count', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']
    
    def get_sender_name(self, obj):
        if obj.sender:
            return obj.sender.get_full_name()
        return 'System'
    
    def get_read_count(self, obj):
        return obj.read_receipts.count()


class ConversationMemberSerializer(serializers.ModelSerializer):
    """Serializer for ConversationMember"""
    user_name = serializers.SerializerMethodField()
    role_display = serializers.CharField(source='get_role_display', read_only=True)
    
    class Meta:
        model = ConversationMember
        fields = ['id', 'conversation', 'user', 'user_name', 'role', 'role_display', 'is_muted', 'notifications_enabled', 'joined_at', 'last_read_at']
        read_only_fields = ['joined_at']
    
    def get_user_name(self, obj):
        return obj.user.get_full_name()


class ConversationSerializer(serializers.ModelSerializer):
    """Serializer for Conversation"""
    conversation_type_display = serializers.CharField(source='get_conversation_type_display', read_only=True)
    members = ConversationMemberSerializer(many=True, read_only=True)
    member_count = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Conversation
        fields = [
            'id', 'chama', 'conversation_type', 'conversation_type_display',
            'name', 'description', 'is_group', 'is_pinned', 'is_archived',
            'allowed_roles', 'created_by', 'members', 'member_count',
            'last_message', 'unread_count', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']
    
    def get_member_count(self, obj):
        return obj.members.count()
    
    def get_last_message(self, obj):
        last_msg = obj.messages.last()
        if last_msg:
            return MessageSerializer(last_msg).data
        return None
    
    def get_unread_count(self, obj):
        # This would need the user from context
        return 0


class ConversationListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing conversations"""
    """Serializer for Conversation list view"""
    conversation_type_display = serializers.CharField(source='get_conversation_type_display', read_only=True)
    member_count = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    
    class Meta:
        model = Conversation
        fields = [
            'id', 'conversation_type', 'conversation_type_display',
            'name', 'is_pinned', 'is_archived', 'member_count',
            'last_message', 'updated_at'
        ]
    
    def get_member_count(self, obj):
        return obj.members.count()
    
    def get_last_message(self, obj):
        last_msg = obj.messages.last()
        if last_msg:
            return {
                'id': last_msg.id,
                'body': last_msg.body[:50] + '...' if len(last_msg.body) > 50 else last_msg.body,
                'sender_name': last_msg.sender.get_full_name() if last_msg.sender else 'System',
                'created_at': last_msg.created_at
            }
        return None


class AnnouncementLogSerializer(serializers.ModelSerializer):
    """Serializer for AnnouncementLog"""
    recipient_name = serializers.SerializerMethodField()
    
    class Meta:
        model = AnnouncementLog
        fields = ['id', 'announcement', 'recipient', 'recipient_name', 'status', 'sent_at', 'delivered_at', 'read_at']
    
    def get_recipient_name(self, obj):
        return obj.recipient.get_full_name()


class AnnouncementSerializer(serializers.ModelSerializer):
    """Serializer for Announcement"""
    audience_type_display = serializers.CharField(source='get_audience_type_display', read_only=True)
    created_by_name = serializers.SerializerMethodField()
    logs = AnnouncementLogSerializer(many=True, read_only=True)
    total_recipients = serializers.SerializerMethodField()
    delivered_count = serializers.SerializerMethodField()
    read_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Announcement
        fields = [
            'id', 'chama', 'title', 'body', 'audience_type', 'audience_type_display',
            'audience_roles', 'audience_members', 'is_scheduled', 'scheduled_at',
            'sent_at', 'is_draft', 'is_sent', 'created_by', 'created_by_name',
            'logs', 'total_recipients', 'delivered_count', 'read_count',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at', 'sent_at']
    
    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name()
        return None
    
    def get_total_recipients(self, obj):
        return obj.logs.count()
    
    def get_delivered_count(self, obj):
        return obj.logs.filter(status='DELIVERED').count()
    
    def get_read_count(self, obj):
        return obj.logs.filter(status='READ').count()


class AnnouncementCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating announcements"""
    class Meta:
        model = Announcement
        fields = ['title', 'body', 'audience_type', 'audience_roles', 'audience_members', 'is_scheduled', 'scheduled_at', 'is_draft']


class MessageTemplateSerializer(serializers.ModelSerializer):
    """Serializer for MessageTemplate"""
    created_by_name = serializers.SerializerMethodField()
    
    class Meta:
        model = MessageTemplate
        fields = [
            'id', 'chama', 'name', 'description', 'category',
            'subject', 'body', 'is_channel_template', 'is_announcement_template',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']
    
    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name()
        return None


class ReportedMessageSerializer(serializers.ModelSerializer):
    """Serializer for ReportedMessage"""
    reported_by_name = serializers.SerializerMethodField()
    resolved_by_name = serializers.SerializerMethodField()
    message_detail = serializers.SerializerMethodField()
    
    class Meta:
        model = ReportedMessage
        fields = [
            'id', 'message', 'message_detail', 'reported_by', 'reported_by_name',
            'reason', 'status', 'resolved_by', 'resolved_by_name',
            'resolution_notes', 'created_at', 'resolved_at'
        ]
        read_only_fields = ['created_at', 'resolved_at']
    
    def get_reported_by_name(self, obj):
        return obj.reported_by.get_full_name()
    
    def get_resolved_by_name(self, obj):
        if obj.resolved_by:
            return obj.resolved_by.get_full_name()
        return None
    
    def get_message_detail(self, obj):
        return {
            'id': obj.message.id,
            'body': obj.message.body,
            'sender_name': obj.message.sender.get_full_name() if obj.message.sender else 'System',
            'conversation_name': obj.message.conversation.name if obj.message.conversation.name else 'DM'
        }


class SendMessageSerializer(serializers.Serializer):
    """Serializer for sending a message"""
    conversation_id = serializers.IntegerField()
    body = serializers.CharField()
    mentions = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    attachments = serializers.ListField(child=serializers.URLField(), required=False, default=list)
