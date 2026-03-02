"""
Mobile API Views for Push Notifications

These endpoints are designed for the Flutter mobile app to:
- Register device tokens for push notifications
- Get notification list
- Get unread count
- Mark notifications as read
- Test sending notifications
"""

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.notifications.models import (
    DeviceToken,
    DevicePlatform,
    Notification,
    NotificationInboxStatus,
)
from apps.notifications.serializers import (
    DeviceTokenRegisterSerializer,
    MobileNotificationSerializer,
)
from apps.notifications.push import send_push_to_user


class DeviceTokenRegisterView(APIView):
    """
    POST /api/notifications/devices/register/
    
    Register a device token for push notifications.
    Uses update_or_create for idempotency.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = DeviceTokenRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        data = serializer.validated_data
        
        # Create or update device token
        device, created = DeviceToken.objects.update_or_create(
            token=data["token"],
            defaults={
                "user": request.user,
                "platform": data.get("platform", DevicePlatform.ANDROID),
                "device_name": data.get("device_name", ""),
                "app_version": data.get("app_version", ""),
                "is_active": True,
            },
        )
        
        return Response(
            {
                "success": True,
                "message": "Device token registered successfully",
                "device_id": str(device.id),
            },
            status=status.HTTP_200_OK if not created else status.HTTP_201_CREATED,
        )


class DeviceTokenUnregisterView(APIView):
    """
    POST /api/notifications/devices/unregister/
    
    Unregister a device token (mark as inactive).
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        token = request.data.get("token")
        if not token:
            return Response(
                {"detail": "Token is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        updated = DeviceToken.objects.filter(
            token=token,
            user=request.user,
        ).update(is_active=False)
        
        if updated:
            return Response({"success": True, "message": "Device token unregistered"})
        
        return Response(
            {"detail": "Token not found"},
            status=status.HTTP_404_NOT_FOUND,
        )


class MobileNotificationListView(APIView):
    """
    GET /api/notifications/
    
    Get list of notifications for the authenticated user.
    Supports ?unread=true filter and pagination.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        # Get filter params
        unread_only = request.query_params.get("unread", "false").lower() == "true"
        page = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("page_size", 20))
        
        # Build query
        queryset = Notification.objects.filter(
            recipient=request.user,
        )
        
        if unread_only:
            queryset = queryset.filter(
                inbox_status=NotificationInboxStatus.UNREAD,
            )
        
        # Get total count
        total_count = queryset.count()
        
        # Get unread count
        unread_count = Notification.objects.filter(
            recipient=request.user,
            inbox_status=NotificationInboxStatus.UNREAD,
        ).count()
        
        # Paginate
        offset = (page - 1) * page_size
        notifications = queryset[offset : offset + page_size]
        
        serializer = MobileNotificationSerializer(notifications, many=True)
        
        return Response({
            "notifications": serializer.data,
            "unread_count": unread_count,
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "has_next": offset + page_size < total_count,
        })


class MobileUnreadCountView(APIView):
    """
    GET /api/notifications/unread-count/
    
    Get the count of unread notifications.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        count = Notification.objects.filter(
            recipient=request.user,
            inbox_status=NotificationInboxStatus.UNREAD,
        ).count()
        
        return Response({"unread_count": count})


class MobileMarkReadView(APIView):
    """
    POST /api/notifications/mark-read/
    
    Mark a single notification as read.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        notification_id = request.data.get("id")
        
        if not notification_id:
            return Response(
                {"detail": "Notification ID is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        try:
            notification = Notification.objects.get(
                id=notification_id,
                recipient=request.user,
            )
        except Notification.DoesNotExist:
            return Response(
                {"detail": "Notification not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        notification.inbox_status = NotificationInboxStatus.READ
        notification.read_at = timezone.now()
        notification.save(update_fields=["inbox_status", "read_at"])
        
        return Response({"success": True})


class MobileMarkAllReadView(APIView):
    """
    POST /api/notifications/mark-all-read/
    
    Mark all notifications as read for the user.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        updated_count = Notification.objects.filter(
            recipient=request.user,
            inbox_status=NotificationInboxStatus.UNREAD,
        ).update(
            inbox_status=NotificationInboxStatus.READ,
            read_at=timezone.now(),
        )
        
        return Response({
            "success": True,
            "marked_read_count": updated_count,
        })


class MobileTestSendView(APIView):
    """
    POST /api/notifications/test-send/
    
    Test endpoint to send a notification to a user.
    Creates in-app notification and sends push via FCM.
    
    Admin only endpoint.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        # Check if admin
        if not request.user.is_superuser:
            return Response(
                {"detail": "Admin access required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        
        user_id = request.data.get("user_id")
        title = request.data.get("title", "Test Notification")
        message = request.data.get("message", "This is a test message")
        notification_type = request.data.get("type", "system")
        route = request.data.get("route", "")
        
        if not user_id:
            return Response(
                {"detail": "user_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # Get user
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response(
                {"detail": "User not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        # Prepare metadata with route for deep linking
        metadata = {}
        if route:
            metadata["route"] = route
        
        # Create in-app notification
        notification = Notification.objects.create(
            chama_id=None,  # System notification not tied to a chama
            recipient=user,
            type=notification_type,
            category="system",
            subject=title,
            message=message,
            metadata=metadata,
            inbox_status=NotificationInboxStatus.UNREAD,
            send_push=True,
        )
        
        # Send push notification
        push_result = send_push_to_user(
            user=user,
            title=title,
            body=message,
            data=metadata if metadata else None,
        )
        
        return Response({
            "success": True,
            "notification_id": str(notification.id),
            "push_result": push_result,
        })
