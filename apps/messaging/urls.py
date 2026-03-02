# Messaging Module URL Configuration

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ConversationViewSet, MessageViewSet, AnnouncementViewSet,
    MessageTemplateViewSet, ReportedMessageViewSet, AnnouncementLogViewSet,
    SendAnnouncementView, MarkReadView, GetUnreadCountView
)

router = DefaultRouter()
router.register(r'conversations', ConversationViewSet, basename='conversation')
router.register(r'messages', MessageViewSet, basename='message')
router.register(r'announcements', AnnouncementViewSet, basename='announcement')
router.register(r'templates', MessageTemplateViewSet, basename='message-template')
router.register(r'reports', ReportedMessageViewSet, basename='reported-message')
router.register(r'logs', AnnouncementLogViewSet, basename='announcement-log')

urlpatterns = [
    path('send/', SendAnnouncementView.as_view(), name='send-announcement'),
    path('mark-read/', MarkReadView.as_view(), name='mark-read'),
    path('unread-count/', GetUnreadCountView.as_view(), name='unread-count'),
    path('', include(router.urls)),
]
