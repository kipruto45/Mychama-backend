"""
Security Notification Service

Sends security-related notifications to users for:
- Login alerts
- Account lockouts
- Password changes
- Suspicious activity
- Device management
"""

import logging

from django.utils import timezone

from apps.accounts.models import User
from apps.notifications.services import NotificationService

logger = logging.getLogger(__name__)


class SecurityNotificationService:
    """Service for sending security-related notifications."""

    @staticmethod
    def send_login_alert(
        user: User,
        ip_address: str,
        user_agent: str,
        success: bool,
        device_name: str = '',
    ) -> None:
        """
        Send login success/failure alert.
        """
        if success:
            subject = "Login Successful"
            message = f"""
Hello {user.full_name},

A successful login was detected on your account.

Details:
- Time: {timezone.now().strftime('%Y-%m-%d %H:%M:%S UTC')}
- IP Address: {ip_address}
- Device: {device_name or 'Unknown'}

If this was you, no action is needed.
If you did not log in, please change your password immediately and contact support.

Best regards,
Digital Chama Security Team
            """
        else:
            subject = "Failed Login Attempt"
            message = f"""
Hello {user.full_name},

A failed login attempt was detected on your account.

Details:
- Time: {timezone.now().strftime('%Y-%m-%d %H:%M:%S UTC')}
- IP Address: {ip_address}
- Device: {device_name or 'Unknown'}

If this was you, please verify your credentials.
If you did not attempt to log in, please change your password immediately.

Best regards,
Digital Chama Security Team
            """

        try:
            NotificationService.send_email(
                user=user,
                subject=subject,
                message=message,
                notification_type='security',
            )
            logger.info(f"Login alert sent to user {user.id}")
        except Exception as e:
            logger.error(f"Failed to send login alert to user {user.id}: {e}")

    @staticmethod
    def send_account_locked_alert(
        user: User,
        locked_until: timezone.datetime,
        failed_attempts: int,
        ip_address: str,
    ) -> None:
        """
        Send account locked notification.
        """
        subject = "Account Temporarily Locked"
        message = f"""
Hello {user.full_name},

Your account has been temporarily locked due to multiple failed login attempts.

Details:
- Locked at: {timezone.now().strftime('%Y-%m-%d %H:%M:%S UTC')}
- Locked until: {locked_until.strftime('%Y-%m-%d %H:%M:%S UTC')}
- Failed attempts: {failed_attempts}
- Last IP: {ip_address}

Your account will be automatically unlocked after the lockout period.
If you did not make these attempts, please contact support immediately.

Best regards,
Digital Chama Security Team
        """

        try:
            NotificationService.send_email(
                user=user,
                subject=subject,
                message=message,
                notification_type='security',
            )
            logger.info(f"Account locked alert sent to user {user.id}")
        except Exception as e:
            logger.error(f"Failed to send account locked alert to user {user.id}: {e}")

    @staticmethod
    def send_password_changed_alert(
        user: User,
        ip_address: str,
    ) -> None:
        """
        Send password changed notification.
        """
        subject = "Password Changed Successfully"
        message = f"""
Hello {user.full_name},

Your password has been successfully changed.

Details:
- Time: {timezone.now().strftime('%Y-%m-%d %H:%M:%S UTC')}
- IP Address: {ip_address}

If you did not make this change, please contact support immediately.

Best regards,
Digital Chama Security Team
        """

        try:
            NotificationService.send_email(
                user=user,
                subject=subject,
                message=message,
                notification_type='security',
            )
            logger.info(f"Password changed alert sent to user {user.id}")
        except Exception as e:
            logger.error(f"Failed to send password changed alert to user {user.id}: {e}")

    @staticmethod
    def send_suspicious_activity_alert(
        user: User,
        activity_type: str,
        ip_address: str,
        details: str,
    ) -> None:
        """
        Send suspicious activity notification.
        """
        subject = "Suspicious Activity Detected"
        message = f"""
Hello {user.full_name},

We detected suspicious activity on your account.

Activity: {activity_type}
Details:
- Time: {timezone.now().strftime('%Y-%m-%d %H:%M:%S UTC')}
- IP Address: {ip_address}
- Details: {details}

Please review your account activity and change your password if necessary.
If you recognize this activity, no action is needed.

Best regards,
Digital Chama Security Team
        """

        try:
            NotificationService.send_email(
                user=user,
                subject=subject,
                message=message,
                notification_type='security',
            )
            logger.info(f"Suspicious activity alert sent to user {user.id}")
        except Exception as e:
            logger.error(f"Failed to send suspicious activity alert to user {user.id}: {e}")

    @staticmethod
    def send_new_device_alert(
        user: User,
        device_name: str,
        ip_address: str,
        user_agent: str,
    ) -> None:
        """
        Send new device login notification.
        """
        subject = "New Device Login Detected"
        message = f"""
Hello {user.full_name},

A login was detected from a new device.

Details:
- Time: {timezone.now().strftime('%Y-%m-%d %H:%M:%S UTC')}
- Device: {device_name}
- IP Address: {ip_address}
- User Agent: {user_agent}

If this was you, you can trust this device for future logins.
If you did not log in from this device, please change your password immediately.

Best regards,
Digital Chama Security Team
        """

        try:
            NotificationService.send_email(
                user=user,
                subject=subject,
                message=message,
                notification_type='security',
            )
            logger.info(f"New device alert sent to user {user.id}")
        except Exception as e:
            logger.error(f"Failed to send new device alert to user {user.id}: {e}")

    @staticmethod
    def send_device_trusted_alert(
        user: User,
        device_name: str,
    ) -> None:
        """
        Send device trusted notification.
        """
        subject = "Device Trusted Successfully"
        message = f"""
Hello {user.full_name},

Your device has been successfully trusted.

Details:
- Time: {timezone.now().strftime('%Y-%m-%d %H:%M:%S UTC')}
- Device: {device_name}

This device will be remembered for future logins.
You can manage your trusted devices in your account settings.

Best regards,
Digital Chama Security Team
        """

        try:
            NotificationService.send_email(
                user=user,
                subject=subject,
                message=message,
                notification_type='security',
            )
            logger.info(f"Device trusted alert sent to user {user.id}")
        except Exception as e:
            logger.error(f"Failed to send device trusted alert to user {user.id}: {e}")

    @staticmethod
    def send_otp_sent_alert(
        user: User,
        purpose: str,
        delivery_method: str,
    ) -> None:
        """
        Send OTP sent notification.
        """
        subject = "Verification Code Sent"
        message = f"""
Hello {user.full_name},

A verification code has been sent to your {delivery_method}.

Purpose: {purpose}
Time: {timezone.now().strftime('%Y-%m-%d %H:%M:%S UTC')}

The code will expire in 5 minutes.
Do not share this code with anyone.

Best regards,
Digital Chama Security Team
        """

        try:
            NotificationService.send_email(
                user=user,
                subject=subject,
                message=message,
                notification_type='security',
            )
            logger.info(f"OTP sent alert sent to user {user.id}")
        except Exception as e:
            logger.error(f"Failed to send OTP sent alert to user {user.id}: {e}")
