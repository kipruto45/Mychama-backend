"""
Mobile API Views for Push Notifications

These endpoints are designed for the Flutter mobile app to:
- Register device tokens for push notifications
- Get notification list
- Get unread count
- Mark notifications as read
- Test sending notifications
"""

from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import permissions, serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.notifications.models import (
    DevicePlatform,
    DeviceToken,
    Notification,
    NotificationInboxStatus,
)
from apps.notifications.push import send_push_to_user
from apps.notifications.serializers import (
    DeviceTokenRegisterSerializer,
    MobileNotificationSerializer,
)

device_token_unregister_request = inline_serializer(
    name="DeviceTokenUnregisterRequest",
    fields={"token": serializers.CharField()},
)
device_token_response = inline_serializer(
    name="DeviceTokenRegisterResponse",
    fields={
        "success": serializers.BooleanField(),
        "message": serializers.CharField(),
        "device_id": serializers.CharField(required=False),
    },
)
mobile_unread_count_response = inline_serializer(
    name="MobileUnreadCountResponse",
    fields={"unread_count": serializers.IntegerField()},
)
mobile_mark_read_request = inline_serializer(
    name="MobileMarkReadRequest",
    fields={"id": serializers.UUIDField()},
)
mobile_mark_read_response = inline_serializer(
    name="MobileMarkReadResponse",
    fields={"success": serializers.BooleanField()},
)
mobile_mark_all_read_response = inline_serializer(
    name="MobileMarkAllReadResponse",
    fields={
        "success": serializers.BooleanField(),
        "marked_read_count": serializers.IntegerField(),
    },
)
mobile_test_send_request = inline_serializer(
    name="MobileTestSendRequest",
    fields={
        "user_id": serializers.UUIDField(),
        "title": serializers.CharField(required=False),
        "message": serializers.CharField(required=False),
        "type": serializers.CharField(required=False),
        "route": serializers.CharField(required=False, allow_blank=True),
    },
)
mobile_notification_list_response = inline_serializer(
    name="MobileNotificationListResponse",
    fields={
        "notifications": MobileNotificationSerializer(many=True),
        "results": MobileNotificationSerializer(many=True),
        "unread_count": serializers.IntegerField(),
        "total_count": serializers.IntegerField(),
        "count": serializers.IntegerField(),
        "page": serializers.IntegerField(),
        "page_size": serializers.IntegerField(),
        "has_next": serializers.BooleanField(),
        "filters": serializers.DictField(),
    },
)


class DeviceTokenRegisterView(APIView):
    """
    POST /api/notifications/devices/register/
    
    Register a device token for push notifications.
    Uses update_or_create for idempotency.
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = DeviceTokenRegisterSerializer

    @extend_schema(
        tags=["Notifications"],
        operation_id="register_mobile_device_token",
        request=DeviceTokenRegisterSerializer,
        responses={200: device_token_response, 201: device_token_response},
    )
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
    serializer_class = device_token_unregister_request

    @extend_schema(
        tags=["Notifications"],
        operation_id="unregister_mobile_device_token",
        request=device_token_unregister_request,
        responses={200: device_token_response, 404: device_token_response},
    )
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
    serializer_class = mobile_notification_list_response

    @extend_schema(
        tags=["Notifications"],
        operation_id="list_mobile_notifications",
        responses={200: mobile_notification_list_response},
    )
    def get(self, request):
        unread_only = request.query_params.get("unread", "false").lower() == "true"
        try:
            page = max(1, int(request.query_params.get("page", 1)))
            page_size = min(100, max(1, int(request.query_params.get("page_size", 20))))
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                {"detail": "page and page_size must be valid positive integers."}
            ) from exc

        notification_type = str(request.query_params.get("type", "")).strip()
        category = str(request.query_params.get("category", "")).strip()
        chama_id = str(request.query_params.get("chama_id", "")).strip()
        search = str(request.query_params.get("search", "")).strip()

        queryset = Notification.objects.select_related("chama").filter(
            recipient=request.user,
        ).exclude(inbox_status=NotificationInboxStatus.ARCHIVED)

        if unread_only:
            queryset = queryset.filter(inbox_status=NotificationInboxStatus.UNREAD)
        if notification_type:
            queryset = queryset.filter(type=notification_type)
        if category:
            queryset = queryset.filter(category=category)
        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        if search:
            queryset = queryset.filter(
                Q(subject__icontains=search)
                | Q(message__icontains=search)
                | Q(chama__name__icontains=search)
            )

        total_count = queryset.count()

        unread_count = Notification.objects.filter(
            recipient=request.user,
            inbox_status=NotificationInboxStatus.UNREAD,
        ).count()

        offset = (page - 1) * page_size
        notifications = queryset.order_by("-created_at")[offset : offset + page_size]
        serializer = MobileNotificationSerializer(notifications, many=True)

        return Response(
            {
                "notifications": serializer.data,
                "results": serializer.data,
                "unread_count": unread_count,
                "total_count": total_count,
                "count": total_count,
                "page": page,
                "page_size": page_size,
                "has_next": offset + page_size < total_count,
                "filters": {
                    "unread": unread_only,
                    "type": notification_type or None,
                    "category": category or None,
                    "chama_id": chama_id or None,
                    "search": search or None,
                },
            }
        )


class MobileUnreadCountView(APIView):
    """
    GET /api/notifications/unread-count/
    
    Get the count of unread notifications.
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = mobile_unread_count_response

    @extend_schema(
        tags=["Notifications"],
        operation_id="get_mobile_unread_notification_count",
        responses={200: mobile_unread_count_response},
    )
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
    serializer_class = mobile_mark_read_request

    @extend_schema(
        tags=["Notifications"],
        operation_id="mark_mobile_notification_read",
        request=mobile_mark_read_request,
        responses={200: mobile_mark_read_response},
    )
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
    serializer_class = mobile_mark_all_read_response

    @extend_schema(
        tags=["Notifications"],
        operation_id="mark_all_mobile_notifications_read",
        responses={200: mobile_mark_all_read_response},
    )
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
    serializer_class = mobile_test_send_request

    @extend_schema(
        tags=["Notifications"],
        operation_id="send_mobile_test_notification",
        request=mobile_test_send_request,
        responses={200: inline_serializer(
            name="MobileTestSendResponse",
            fields={
                "success": serializers.BooleanField(),
                "notification_id": serializers.CharField(),
                "push_result": serializers.JSONField(),
            },
        )},
    )
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
