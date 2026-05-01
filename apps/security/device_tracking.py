"""
Trusted Device Tracking Service

Manages device fingerprinting, trusted device registration,
and suspicious login detection for enhanced security.
"""

import hashlib
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.security.models import DeviceLoginAttempt, TrustedDevice

logger = logging.getLogger(__name__)


class DeviceTrackingService:
    """Service for tracking and managing trusted devices."""

    # Configuration
    MAX_TRUSTED_DEVICES = getattr(settings, 'MAX_TRUSTED_DEVICES', 5)
    DEVICE_TRUST_DURATION_DAYS = getattr(settings, 'DEVICE_TRUST_DURATION_DAYS', 90)
    SUSPICIOUS_LOGIN_THRESHOLD = getattr(settings, 'SUSPICIOUS_LOGIN_THRESHOLD', 3)

    @staticmethod
    def generate_device_fingerprint(
        user_agent: str,
        ip_address: str,
        screen_resolution: str | None = None,
        timezone_str: str | None = None,
        language: str | None = None,
    ) -> str:
        """
        Generate a unique device fingerprint from device characteristics.
        """
        components = [
            user_agent or '',
            ip_address or '',
            screen_resolution or '',
            timezone_str or '',
            language or '',
        ]
        
        fingerprint_data = '|'.join(components)
        return hashlib.sha256(fingerprint_data.encode()).hexdigest()

    @staticmethod
    def get_or_create_device(
        user: User,
        fingerprint: str,
        device_name: str = '',
        device_type: str = 'unknown',
        user_agent: str = '',
        ip_address: str = '',
    ) -> tuple[TrustedDevice, bool]:
        """
        Get existing device or create new one.
        Returns (device, created) tuple.
        """
        device, created = TrustedDevice.objects.get_or_create(
            user=user,
            fingerprint=fingerprint,
            defaults={
                'device_name': device_name or 'Unknown Device',
                'device_type': device_type,
                'user_agent': user_agent,
                'ip_address': ip_address,
                'is_trusted': False,
                'last_used_at': timezone.now(),
            }
        )

        if not created:
            # Update last used timestamp and IP
            device.last_used_at = timezone.now()
            device.ip_address = ip_address
            device.user_agent = user_agent
            device.save(update_fields=['last_used_at', 'ip_address', 'user_agent'])

        return device, created

    @staticmethod
    def is_device_trusted(user: User, fingerprint: str) -> bool:
        """Check if device is trusted for user."""
        return TrustedDevice.objects.filter(
            user=user,
            fingerprint=fingerprint,
            is_trusted=True,
            expires_at__gt=timezone.now(),
        ).exists()

    @staticmethod
    @transaction.atomic
    def trust_device(
        user: User,
        fingerprint: str,
        device_name: str = '',
        device_type: str = 'unknown',
        user_agent: str = '',
        ip_address: str = '',
    ) -> TrustedDevice:
        """
        Mark device as trusted.
        Enforces maximum trusted device limit.
        """
        device, created = DeviceTrackingService.get_or_create_device(
            user=user,
            fingerprint=fingerprint,
            device_name=device_name,
            device_type=device_type,
            user_agent=user_agent,
            ip_address=ip_address,
        )

        # Check if already trusted
        if device.is_trusted and device.expires_at > timezone.now():
            return device

        # Enforce max trusted devices limit
        trusted_count = TrustedDevice.objects.filter(
            user=user,
            is_trusted=True,
            expires_at__gt=timezone.now(),
        ).count()

        if trusted_count >= DeviceTrackingService.MAX_TRUSTED_DEVICES:
            # Remove oldest trusted device
            oldest_device = TrustedDevice.objects.filter(
                user=user,
                is_trusted=True,
            ).order_by('last_used_at').first()
            
            if oldest_device:
                oldest_device.is_trusted = False
                oldest_device.save(update_fields=['is_trusted'])
                logger.info(
                    f"Removed oldest trusted device for user {user.id} "
                    f"to enforce limit of {DeviceTrackingService.MAX_TRUSTED_DEVICES}"
                )

        # Trust the device
        device.is_trusted = True
        device.trusted_at = timezone.now()
        device.expires_at = timezone.now() + timedelta(
            days=DeviceTrackingService.DEVICE_TRUST_DURATION_DAYS
        )
        device.save(update_fields=['is_trusted', 'trusted_at', 'expires_at'])

        logger.info(f"Device {device.id} trusted for user {user.id}")
        return device

    @staticmethod
    def revoke_device_trust(user: User, device_id: str) -> bool:
        """Revoke trust for a specific device."""
        try:
            device = TrustedDevice.objects.get(
                id=device_id,
                user=user,
            )
            device.is_trusted = False
            device.save(update_fields=['is_trusted'])
            logger.info(f"Revoked trust for device {device_id} for user {user.id}")
            return True
        except TrustedDevice.DoesNotExist:
            return False

    @staticmethod
    def revoke_all_trusted_devices(user: User) -> int:
        """Revoke trust for all devices except current one."""
        count = TrustedDevice.objects.filter(
            user=user,
            is_trusted=True,
        ).update(is_trusted=False)
        
        logger.info(f"Revoked trust for {count} devices for user {user.id}")
        return count

    @staticmethod
    def get_trusted_devices(user: User) -> list[TrustedDevice]:
        """Get all trusted devices for user."""
        return list(TrustedDevice.objects.filter(
            user=user,
            is_trusted=True,
            expires_at__gt=timezone.now(),
        ).order_by('-last_used_at'))

    @staticmethod
    def record_login_attempt(
        user: User | None,
        fingerprint: str,
        ip_address: str,
        user_agent: str,
        success: bool,
        failure_reason: str = '',
    ) -> DeviceLoginAttempt:
        """Record a login attempt for security analysis."""
        attempt = DeviceLoginAttempt.objects.create(
            user=user,
            fingerprint=fingerprint,
            ip_address=ip_address,
            user_agent=user_agent,
            success=success,
            failure_reason=failure_reason,
        )

        # Check for suspicious activity
        if not success and user:
            DeviceTrackingService._check_suspicious_activity(user, ip_address)

        return attempt

    @staticmethod
    def _check_suspicious_activity(user: User, ip_address: str) -> None:
        """Check for suspicious login patterns."""
        # Count recent failed attempts from this IP
        recent_failures = DeviceLoginAttempt.objects.filter(
            user=user,
            ip_address=ip_address,
            success=False,
            created_at__gte=timezone.now() - timedelta(hours=1),
        ).count()

        if recent_failures >= DeviceTrackingService.SUSPICIOUS_LOGIN_THRESHOLD:
            logger.warning(
                f"Suspicious activity detected for user {user.id} "
                f"from IP {ip_address}: {recent_failures} failed attempts"
            )
            
            # TODO: Send security notification
            # from apps.notifications.services import NotificationService
            # NotificationService.send_security_alert(
            #     user=user,
            #     alert_type='suspicious_login_attempts',
            #     metadata={
            #         'ip_address': ip_address,
            #         'failed_attempts': recent_failures,
            #     }
            # )

    @staticmethod
    def get_login_history(
        user: User,
        limit: int = 20,
    ) -> list[DeviceLoginAttempt]:
        """Get recent login attempts for user."""
        return list(DeviceLoginAttempt.objects.filter(
            user=user,
        ).order_by('-created_at')[:limit])

    @staticmethod
    def cleanup_expired_devices() -> int:
        """Clean up expired trusted devices."""
        count, _ = TrustedDevice.objects.filter(
            expires_at__lt=timezone.now(),
        ).delete()
        
        if count > 0:
            logger.info(f"Cleaned up {count} expired trusted devices")
        
        return count
