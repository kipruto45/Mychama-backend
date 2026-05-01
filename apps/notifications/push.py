"""
Firebase Cloud Messaging (FCM) Push Notification Provider.

Supports:
- Individual device messaging
- Topic-based messaging
- Data messages
- Notification messages
- Image attachments
"""

import logging
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger(__name__)


@dataclass
class FCMResult:
    """Result of FCM send operation."""
    success: bool
    message_id: str | None = None
    error: str | None = None
    canonical_ids: int = 0
    failure_count: int = 0


class FCMProvider:
    """
    Firebase Cloud Messaging provider for push notifications.
    
    Requires:
    - FCM_API_KEY or Firebase service account credentials
    - Optional: FCM_PROJECT_ID for topic management
    """
    
    def __init__(self):
        self.api_key = getattr(settings, 'FCM_API_KEY', None)
        self.project_id = getattr(settings, 'FCM_PROJECT_ID', None)
        self.enabled = bool(self.api_key or getattr(settings, 'FIREBASE_SERVICE_ACCOUNT', None))
    
    def send(
        self,
        token: str,
        title: str,
        body: str,
        data: dict | None = None,
        image_url: str | None = None,
        sound: str = "default",
        badge: int | None = None,
        click_action: str | None = None,
        ttl: int = 3600,  # 1 hour default
        priority: str = "high",
    ) -> FCMResult:
        """
        Send push notification to a single device.
        
        Args:
            token: FCM device token
            title: Notification title
            body: Notification body
            data: Optional data payload
            image_url: Optional image URL
            sound: Sound to play (default: "default")
            badge: Badge number for iOS
            click_action: Action on tap (URL or intent)
            ttl: Time to live in seconds
            priority: Message priority ("high" or "normal")
        
        Returns:
            FCMResult with success status and details
        """
        if not self.enabled:
            return FCMResult(
                success=False,
                error="FCM is not configured. Set FCM_API_KEY or FIREBASE_SERVICE_ACCOUNT in settings."
            )
        
        # Try firebase-admin first, fallback to raw HTTP
        try:
            return self._send_firebase_admin(
                token=token,
                title=title,
                body=body,
                data=data,
                image_url=image_url,
                sound=sound,
                badge=badge,
                click_action=click_action,
                ttl=ttl,
                priority=priority,
            )
        except ImportError:
            logger.warning("firebase-admin not installed, using HTTP fallback")
            return self._send_http_fallback(
                token=token,
                title=title,
                body=body,
                data=data,
                image_url=image_url,
                sound=sound,
                badge=badge,
                click_action=click_action,
                ttl=ttl,
                priority=priority,
            )
    
    def _send_firebase_admin(
        self,
        token: str,
        title: str,
        body: str,
        data: dict | None,
        image_url: str | None,
        sound: str,
        badge: int | None,
        click_action: str | None,
        ttl: int,
        priority: str,
    ) -> FCMResult:
        """Send using firebase-admin SDK."""
        import firebase_admin
        from firebase_admin import messaging
        
        # Initialize if not already done
        if not firebase_admin._apps:
            cred = None
            
            # Try service account
            service_account = getattr(settings, 'FIREBASE_SERVICE_ACCOUNT', None)
            if service_account:
                import json
                try:
                    cred = firebase_admin.credentials.Certificate(
                        json.loads(service_account) if isinstance(service_account, str) else service_account
                    )
                except Exception as e:
                    logger.error(f"Failed to load Firebase service account: {e}")
            
            # Try service account file
            if not cred:
                service_file = getattr(settings, 'FIREBASE_SERVICE_ACCOUNT_FILE', None)
                if service_file:
                    try:
                        cred = firebase_admin.credentials.Certificate(service_file)
                    except Exception as e:
                        logger.error(f"Failed to load Firebase service file: {e}")
            
            if cred:
                firebase_admin.initialize_app(cred)
            else:
                return FCMResult(success=False, error="No valid Firebase credentials found")
        
        # Build message
        android_config = messaging.AndroidConfig(
            priority=priority,
            ttl=ttl,
            notification=messaging.AndroidNotification(
                title=title,
                body=body,
                image_url=image_url,
                sound=sound,
            ) if image_url or sound != "default" else None,
        )
        
        apns_config = None
        if badge is not None:
            apns_config = messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(
                        badge=badge,
                        sound=sound if sound != "default" else None,
                    )
                ),
                fcm_options=messaging.APNSFCMOptions(
                    image_url=image_url,
                ) if image_url else None,
            )
        
        # Build notification
        notification = messaging.Notification(
            title=title,
            body=body,
            image=image_url,
        )
        
        message = messaging.Message(
            notification=notification,
            data=data or {},
            token=token,
            android=android_config,
            apns=apns_config,
        )
        
        try:
            response = messaging.send(message)
            logger.info(f"FCM message sent: {response}")
            return FCMResult(success=True, message_id=response)
        except messaging.UnregisteredError:
            return FCMResult(success=False, error="Device token is unregistered")
        except messaging.QuotaExceededError:
            return FCMResult(success=False, error="Quota exceeded")
        except Exception as e:
            logger.error(f"FCM send error: {e}")
            return FCMResult(success=False, error=str(e))
    
    def _send_http_fallback(
        self,
        token: str,
        title: str,
        body: str,
        data: dict | None,
        image_url: str | None,
        sound: str,
        badge: int | None,
        click_action: str | None,
        ttl: int,
        priority: str,
    ) -> FCMResult:
        """Fallback HTTP v1 API implementation."""
        import requests
        
        if not self.api_key:
            return FCMResult(success=False, error="FCM_API_KEY not configured")
        
        url = f"https://fcm.googleapis.com/v1/projects/{self.project_id}/messages:send"
        
        message = {
            "message": {
                "token": token,
                "notification": {
                    "title": title,
                    "body": body,
                },
                "android": {
                    "priority": priority.upper(),
                    "ttl": f"{ttl}s",
                },
                "webpush": {
                    "headers": {
                        "urgency": priority,
                    },
                },
            }
        }
        
        if image_url:
            message["message"]["android"]["notification"] = {"image": image_url}
            message["message"]["webpush"]["fcmOptions"] = {"image": image_url}
        
        if data:
            message["message"]["data"] = data
        
        if click_action:
            message["message"]["webpush"] = message.get("webpush", {})
            message["message"]["webpush"]["notification"] = {
                "click_action": click_action,
            }
        
        try:
            response = requests.post(
                url,
                json=message,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=10,
            )
            
            if response.status_code == 200:
                result = response.json()
                msg_id = result.get("name", "")
                return FCMResult(success=True, message_id=msg_id)
            else:
                return FCMResult(success=False, error=f"HTTP {response.status_code}: {response.text}")
        except Exception as e:
            return FCMResult(success=False, error=str(e))
    
    def send_to_topic(
        self,
        topic: str,
        title: str,
        body: str,
        data: dict | None = None,
        image_url: str | None = None,
    ) -> FCMResult:
        """
        Send push notification to a topic (e.g., "chama_123", "announcements").
        
        Args:
            topic: Topic name (no /topics/ prefix needed)
            title: Notification title
            body: Notification body
            data: Optional data payload
            image_url: Optional image URL
        
        Returns:
            FCMResult with success status and details
        """
        if not self.enabled:
            return FCMResult(
                success=False,
                error="FCM is not configured. Set FCM_API_KEY or FIREBASE_SERVICE_ACCOUNT in settings."
            )
        
        try:
            import firebase_admin
            from firebase_admin import messaging
            
            if not firebase_admin._apps:
                # Initialize (reuse logic from send)
                cred = None
                service_account = getattr(settings, 'FIREBASE_SERVICE_ACCOUNT', None)
                if service_account:
                    import json
                    try:
                        cred = firebase_admin.credentials.Certificate(
                            json.loads(service_account) if isinstance(service_account, str) else service_account
                        )
                    except Exception:
                        pass
                
                if cred:
                    firebase_admin.initialize_app(cred)
                else:
                    return FCMResult(success=False, error="No valid Firebase credentials")
            
            # Add /topics/ prefix if not present
            if not topic.startswith('/topics/'):
                topic = f'/topics/{topic}'
            
            message = messaging.Message(
                notification=messaging.Notification(title=title, body=body, image=image_url),
                data=data or {},
                topic=topic,
            )
            
            response = messaging.send(message)
            return FCMResult(success=True, message_id=response)
            
        except ImportError:
            return FCMResult(success=False, error="firebase-admin not installed")
        except Exception as e:
            logger.error(f"FCM topic send error: {e}")
            return FCMResult(success=False, error=str(e))
    
    def subscribe_to_topic(self, tokens: list, topic: str) -> tuple:
        """
        Subscribe devices to a topic.
        
        Args:
            tokens: List of device tokens
            topic: Topic name
        
        Returns:
            (success_count, failure_count, errors)
        """
        if not self.enabled:
            return (0, len(tokens), ["FCM not configured"])
        
        try:
            import firebase_admin
            from firebase_admin import messaging
            
            if not firebase_admin._apps:
                return (0, len(tokens), ["Firebase not initialized"])
            
            # Add /topics/ prefix
            if not topic.startswith('/topics/'):
                topic = f'/topics/{topic}'
            
            response = messaging.subscribe_to_topic(tokens, topic)
            return (response.success_count, response.failure_count, response.errors)
        except Exception as e:
            logger.error(f"FCM subscribe error: {e}")
            return (0, len(tokens), [str(e)])
    
    def unsubscribe_from_topic(self, tokens: list, topic: str) -> tuple:
        """Unsubscribe devices from a topic."""
        if not self.enabled:
            return (0, len(tokens), ["FCM not configured"])
        
        try:
            import firebase_admin
            from firebase_admin import messaging
            
            if not firebase_admin._apps:
                return (0, len(tokens), ["Firebase not initialized"])
            
            if not topic.startswith('/topics/'):
                topic = f'/topics/{topic}'
            
            response = messaging.unsubscribe_from_topic(tokens, topic)
            return (response.success_count, response.failure_count, response.errors)
        except Exception as e:
            logger.error(f"FCM unsubscribe error: {e}")
            return (0, len(tokens), [str(e)])


class ExpoPushProvider:
    """Expo Push API provider for Expo/React Native clients."""

    endpoint = "https://exp.host/--/api/v2/push/send"

    def __init__(self):
        self.enabled = bool(getattr(settings, "EXPO_PUSH_ENABLED", True))
        self.access_token = getattr(settings, "EXPO_PUSH_ACCESS_TOKEN", "")

    def send(
        self,
        token: str,
        title: str,
        body: str,
        data: dict | None = None,
        image_url: str | None = None,
        sound: str = "default",
        badge: int | None = None,
        **kwargs,
    ) -> FCMResult:
        if not self.enabled:
            return FCMResult(success=False, error="Expo push is disabled")

        import requests

        payload = {
            "to": token,
            "title": title,
            "body": body,
            "data": data or {},
            "sound": sound,
        }
        if badge is not None:
            payload["badge"] = badge
        if image_url:
            payload["richContent"] = {"image": image_url}

        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
            "Content-Type": "application/json",
        }
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        try:
            response = requests.post(
                self.endpoint,
                json=payload,
                headers=headers,
                timeout=10,
            )
            response.raise_for_status()
            response_data = response.json().get("data", {})
            if response_data.get("status") == "error":
                return FCMResult(
                    success=False,
                    error=response_data.get("message", "Expo push delivery failed"),
                )
            return FCMResult(
                success=True,
                message_id=response_data.get("id"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Expo push send error: %s", exc)
            return FCMResult(success=False, error=str(exc))


# Singleton instance
_fcm_provider = None
_expo_provider = None


def get_fcm_provider() -> FCMProvider:
    """Get singleton FCM provider instance."""
    global _fcm_provider
    if _fcm_provider is None:
        _fcm_provider = FCMProvider()
    return _fcm_provider


def get_expo_provider() -> ExpoPushProvider:
    global _expo_provider
    if _expo_provider is None:
        _expo_provider = ExpoPushProvider()
    return _expo_provider


def is_expo_push_token(token: str) -> bool:
    token = (token or "").strip()
    return token.startswith("ExponentPushToken[") or token.startswith(
        "ExpoPushToken["
    )


def send_push_notification(
    token: str,
    title: str,
    body: str,
    **kwargs,
) -> FCMResult:
    """
    Convenience function to send push notification.
    
    Args:
        token: FCM device token
        title: Notification title
        body: Notification body
        **kwargs: Additional args (data, image_url, sound, badge, etc.)
    
    Returns:
        FCMResult with success status
    """
    provider = get_expo_provider() if is_expo_push_token(token) else get_fcm_provider()
    return provider.send(token, title, body, **kwargs)


def send_push_to_topic(
    topic: str,
    title: str,
    body: str,
    **kwargs,
) -> FCMResult:
    """Send push notification to a topic."""
    provider = get_fcm_provider()
    return provider.send_to_topic(topic, title, body, **kwargs)


def send_push_to_user(
    user,
    title: str,
    body: str,
    data: dict | None = None,
    image_url: str | None = None,
    badge: int | None = None,
) -> FCMResult:
    """
    Send push notification to a user based on their FCM token.
    
    Args:
        user: User object with fcm_token attribute
        title: Notification title
        body: Notification body
        data: Optional data payload
        image_url: Optional image URL
    
    Returns:
        FCMResult with success status
    """
    tokens: list[str] = []

    try:
        from apps.notifications.models import DeviceToken

        tokens = list(
            DeviceToken.objects.filter(user=user, is_active=True)
            .values_list("token", flat=True)
            .distinct()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load device tokens for user %s: %s", user, exc)

    fallback_token = getattr(user, "fcm_token", None)
    if fallback_token and fallback_token not in tokens:
        tokens.append(fallback_token)

    if not tokens:
        try:
            from apps.accounts.models import UserPreference

            pref = UserPreference.objects.filter(user=user).first()
            pref_token = getattr(pref, "fcm_token", None) if pref else None
            if pref_token:
                tokens.append(pref_token)
        except Exception:
            pass

    if not tokens:
        return FCMResult(
            success=False,
            error="User has no registered push token",
        )

    failures: list[str] = []
    for token in tokens:
        result = send_push_notification(
            token=token,
            title=title,
            body=body,
            data=data,
            image_url=image_url,
            badge=badge,
        )
        if result.success:
            return result
        failures.append(result.error or "Unknown push delivery error")

    return FCMResult(
        success=False,
        error="; ".join(failures),
        failure_count=len(failures),
    )
