# Messaging Module Views
# API endpoints for messaging, announcements, and channels

from django.db.models import Q, Count
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.billing.gating import BillingAccessMixin
from .models import (
    Conversation, ConversationMember, Message, MessageAttachment,
    Announcement, AnnouncementLog, MessageTemplate, ReportedMessage
)
from .serializers import (
    ConversationSerializer, ConversationListSerializer, ConversationMemberSerializer,
    MessageSerializer, AnnouncementSerializer, AnnouncementCreateSerializer,
    MessageTemplateSerializer, ReportedMessageSerializer, SendMessageSerializer
)


class MessagingBillingMixin(BillingAccessMixin):
    billing_feature_key = "messaging_access"


class ConversationViewSet(MessagingBillingMixin, viewsets.ModelViewSet):
    """ViewSet for managing conversations/channels"""
    serializer_class = ConversationSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        chama_id = self.request.query_params.get('chama_id')
        
        queryset = Conversation.objects.filter(
            members__user=user
        ).distinct()
        
        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        
        return queryset
    
    def get_serializer_class(self):
        if self.action == 'list':
            return ConversationListSerializer
        return ConversationSerializer
    
    def perform_create(self, serializer):
        conversation = serializer.save(
            created_by=self.request.user
        )
        # Add creator as member
        ConversationMember.objects.create(
            conversation=conversation,
            user=self.request.user,
            role=ConversationMember.Role.ADMIN
        )
    
    @action(detail=True, methods=['post'])
    def add_member(self, request, pk=None):
        """Add a member to conversation"""
        conversation = self.get_object()
        user_id = request.data.get('user_id')
        role = request.data.get('role', ConversationMember.Role.MEMBER)
        
        member, created = ConversationMember.objects.get_or_create(
            conversation=conversation,
            user_id=user_id,
            defaults={'role': role}
        )
        
        return Response({
            'status': 'success',
            'member_id': member.id,
            'created': created
        })
    
    @action(detail=True, methods=['post'])
    def remove_member(self, request, pk=None):
        """Remove a member from conversation"""
        conversation = self.get_object()
        user_id = request.data.get('user_id')
        
        deleted, _ = ConversationMember.objects.filter(
            conversation=conversation,
            user_id=user_id
        ).delete()
        
        return Response({
            'status': 'success',
            'deleted': deleted > 0
        })
    
    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        """Mark conversation as read"""
        conversation = self.get_object()
        
        member, _ = ConversationMember.objects.get_or_create(
            conversation=conversation,
            user=request.user,
            defaults={'role': ConversationMember.Role.MEMBER}
        )
        member.last_read_at = timezone.now()
        member.save()
        
        return Response({'status': 'success'})


class MessageViewSet(MessagingBillingMixin, viewsets.ModelViewSet):
    """ViewSet for managing messages"""
    serializer_class = MessageSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        conversation_id = self.request.query_params.get('conversation_id')
        
        queryset = Message.objects.select_related('sender', 'conversation').all()
        
        if conversation_id:
            queryset = queryset.filter(conversation_id=conversation_id)
        
        return queryset
    
    def perform_create(self, serializer):
        message = serializer.save(sender=self.request.user)
        
        # Create read receipt for sender
        from .models import MessageReadReceipt
        MessageReadReceipt.objects.get_or_create(
            message=message,
            user=self.request.user
        )
    
    @action(detail=False, methods=['post'])
    def send(self, request):
        """Send a message to a conversation"""
        serializer = SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        data = serializer.validated_data
        
        # Get conversation
        try:
            conversation = Conversation.objects.get(id=data['conversation_id'])
        except Conversation.DoesNotExist:
            return Response(
                {'error': 'Conversation not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Create message
        message = Message.objects.create(
            conversation=conversation,
            sender=request.user,
            body=data['body'],
            mentions=data.get('mentions', [])
        )
        
        # Handle attachments if provided
        for url in data.get('attachments', []):
            MessageAttachment.objects.create(
                message=message,
                file_url=url,
                file_name=url.split('/')[-1],
                file_type='DOCUMENT',
                file_size=0
            )
        
        return Response(
            MessageSerializer(message).data,
            status=status.HTTP_201_CREATED
        )
    
    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        """Mark message as read"""
        message = self.get_object()
        
        from .models import MessageReadReceipt
        receipt, created = MessageReadReceipt.objects.get_or_create(
            message=message,
            user=request.user
        )
        
        return Response({'status': 'success'})
    
    @action(detail=True, methods=['post'])
    def report(self, request, pk=None):
        """Report a message"""
        message = self.get_object()
        reason = request.data.get('reason', '')
        
        report = ReportedMessage.objects.create(
            message=message,
            reported_by=request.user,
            reason=reason
        )
        
        return Response({
            'status': 'success',
            'report_id': report.id
        })


class AnnouncementViewSet(MessagingBillingMixin, viewsets.ModelViewSet):
    """ViewSet for managing announcements"""
    serializer_class = AnnouncementSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        chama_id = self.request.query_params.get('chama_id')
        
        queryset = Announcement.objects.select_related('created_by').all()
        
        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        
        return queryset
    
    def get_serializer_class(self):
        if self.action == 'create' or self.action == 'update':
            return AnnouncementCreateSerializer
        return AnnouncementSerializer
    
    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
    
    @action(detail=True, methods=['post'])
    def send_now(self, request, pk=None):
        """Send announcement immediately"""
        announcement = self.get_object()
        
        if announcement.is_sent:
            return Response(
                {'error': 'Announcement already sent'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get recipients
        recipients = self._get_recipients(announcement)
        
        # Create logs for each recipient
        for recipient in recipients:
            AnnouncementLog.objects.create(
                announcement=announcement,
                recipient=recipient,
                status='QUEUED'
            )
        
        # In production, this would trigger actual sending
        announcement.is_sent = True
        announcement.sent_at = timezone.now()
        announcement.is_draft = False
        announcement.save()
        
        return Response({
            'status': 'success',
            'recipients_count': len(recipients)
        })
    
    @action(detail=True, methods=['post'])
    def schedule(self, request, pk=None):
        """Schedule announcement"""
        announcement = self.get_object()
        
        scheduled_at = request.data.get('scheduled_at')
        if not scheduled_at:
            return Response(
                {'error': 'scheduled_at required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        announcement.is_scheduled = True
        announcement.scheduled_at = scheduled_at
        announcement.save()
        
        return Response({'status': 'success'})
    
    def _get_recipients(self, announcement):
        """Get list of recipients based on audience settings"""
        from apps.accounts.models import User
        from apps.chama.models import Membership
        
        members = Membership.objects.filter(chama=announcement.chama)
        
        if announcement.audience_type == 'ALL':
            return [m.user for m in members]
        
        elif announcement.audience_type == 'ROLE':
            role_members = members.filter(role__in=announcement.audience_roles)
            return [m.user for m in role_members]
        
        elif announcement.audience_type == 'SELECTED':
            return User.objects.filter(id__in=announcement.audience_members)
        
        return []


class MessageTemplateViewSet(MessagingBillingMixin, viewsets.ModelViewSet):
    """ViewSet for managing message templates"""
    serializer_class = MessageTemplateSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        chama_id = self.request.query_params.get('chama_id')
        
        queryset = MessageTemplate.objects.all()
        
        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        
        return queryset
    
    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class ReportedMessageViewSet(MessagingBillingMixin, viewsets.ModelViewSet):
    """ViewSet for managing reported messages"""
    serializer_class = ReportedMessageSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        chama_id = self.request.query_params.get('chama_id')
        
        queryset = ReportedMessage.objects.select_related(
            'message__conversation', 'reported_by'
        ).all()
        
        return queryset
    
    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        """Resolve a report"""
        report = self.get_object()
        
        resolution_notes = request.data.get('resolution_notes', '')
        
        report.status = 'RESOLVED'
        report.resolved_by = request.user
        report.resolution_notes = resolution_notes
        report.resolved_at = timezone.now()
        report.save()
        
        return Response({'status': 'success'})
