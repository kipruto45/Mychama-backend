from __future__ import annotations

from django.contrib.auth import get_user_model

from apps.notifications.models import NotificationType
from apps.notifications.services import NotificationService

from apps.accounts.models import MemberKYC


def notify_member_status(kyc_record: MemberKYC, *, message: str, subject: str) -> None:
    NotificationService.send_notification(
        user=kyc_record.user,
        chama=kyc_record.chama,
        channels=["in_app", "sms", "email"],
        message=message,
        subject=subject,
        notification_type=NotificationType.SYSTEM,
        idempotency_key=f"kyc-status:{kyc_record.id}:{subject}",
    )


def notify_system_admins(kyc_record: MemberKYC, *, subject: str, message: str) -> None:
    user_model = get_user_model()
    admins = user_model.objects.filter(is_active=True).filter(is_staff=True)[:20]
    for admin in admins:
        NotificationService.send_notification(
            user=admin,
            chama=kyc_record.chama,
            channels=["in_app", "email"],
            message=message,
            subject=subject,
            notification_type=NotificationType.SECURITY_ALERT,
            idempotency_key=f"kyc-admin:{kyc_record.id}:{admin.id}:{subject}",
        )
