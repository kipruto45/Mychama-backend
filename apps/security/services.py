from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.security.models import AccountLock, AuditLog, DeviceSession, LoginAttempt


class SecurityService:
    @staticmethod
    def failure_limit() -> int:
        return max(1, int(getattr(settings, "LOGIN_LOCKOUT_FAILURE_LIMIT", 5)))

    @staticmethod
    def cooldown_seconds() -> int:
        return max(60, int(getattr(settings, "LOGIN_LOCKOUT_COOLDOWN_SECONDS", 900)))

    @staticmethod
    def record_login_attempt(
        *,
        identifier: str,
        ip_address: str | None,
        device_info: str,
        success: bool,
        user=None,
    ) -> LoginAttempt:
        return LoginAttempt.objects.create(
            user=user,
            user_identifier=identifier,
            ip_address=ip_address,
            device_info=device_info,
            success=success,
        )

    @staticmethod
    def lock_identifier(*, identifier: str, user=None, reason: str):
        locked_until = timezone.now() + timedelta(seconds=SecurityService.cooldown_seconds())
        return AccountLock.objects.create(
            user=user,
            user_identifier=identifier,
            locked_until=locked_until,
            reason=reason,
        )

    @staticmethod
    def is_locked(*, identifier: str, user=None) -> bool:
        now = timezone.now()
        queryset = AccountLock.objects.filter(
            user_identifier=identifier,
            locked_until__gt=now,
        )
        if user:
            queryset = queryset | AccountLock.objects.filter(
                user=user,
                locked_until__gt=now,
            )
        return queryset.exists()

    @staticmethod
    def maybe_lock_after_failure(*, identifier: str, user=None, reason: str):
        window_start = timezone.now() - timedelta(seconds=SecurityService.cooldown_seconds())
        failed_count = LoginAttempt.objects.filter(
            user_identifier=identifier,
            success=False,
            created_at__gte=window_start,
        ).count()
        if failed_count >= SecurityService.failure_limit():
            return SecurityService.lock_identifier(identifier=identifier, user=user, reason=reason)
        return None

    @staticmethod
    def clear_identifier_locks(*, identifier: str):
        AccountLock.objects.filter(user_identifier=identifier).delete()

    @staticmethod
    def clear_expired_locks() -> int:
        deleted, _ = AccountLock.objects.filter(locked_until__lte=timezone.now()).delete()
        return deleted

    @staticmethod
    @transaction.atomic
    def register_device_session(
        *,
        user,
        chama=None,
        device_name: str,
        ip_address: str | None,
        user_agent: str,
        session_key: str,
    ) -> tuple[DeviceSession, bool]:
        now = timezone.now()
        existing = None
        if session_key:
            existing = DeviceSession.objects.filter(
                user=user,
                session_key=session_key,
            ).first()

        if existing:
            existing.chama = chama
            existing.device_name = device_name
            existing.ip_address = ip_address
            existing.user_agent = user_agent
            existing.last_seen = now
            existing.is_revoked = False
            existing.save(
                update_fields=[
                    "chama",
                    "device_name",
                    "ip_address",
                    "user_agent",
                    "last_seen",
                    "is_revoked",
                ]
            )
            return existing, False

        similar = DeviceSession.objects.filter(
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
            is_revoked=False,
        ).first()
        is_new_device = similar is None

        if similar:
            similar.last_seen = now
            similar.chama = chama
            similar.device_name = device_name
            if session_key:
                similar.session_key = session_key
            similar.save(
                update_fields=["last_seen", "chama", "device_name", "session_key"]
            )
            return similar, is_new_device

        session = DeviceSession.objects.create(
            user=user,
            chama=chama,
            device_name=device_name,
            ip_address=ip_address,
            user_agent=user_agent,
            session_key=session_key,
            last_seen=now,
            is_revoked=False,
        )
        return session, is_new_device

    @staticmethod
    def revoke_session(*, session: DeviceSession):
        if session.is_revoked:
            return session
        session.is_revoked = True
        session.last_seen = timezone.now()
        session.save(update_fields=["is_revoked", "last_seen"])
        return session

    @staticmethod
    def revoke_all_sessions(*, user, except_session_key: str | None = None) -> int:
        queryset = DeviceSession.objects.filter(user=user, is_revoked=False)
        if except_session_key:
            queryset = queryset.exclude(session_key=except_session_key)
        return queryset.update(is_revoked=True, last_seen=timezone.now())

    @staticmethod
    def create_audit_log(
        *,
        action_type: str,
        target_type: str,
        actor=None,
        chama=None,
        target_id: str = "",
        metadata: dict | None = None,
        ip_address: str | None = None,
    ) -> AuditLog:
        return AuditLog.objects.create(
            chama=chama,
            actor=actor,
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            metadata=metadata or {},
            ip_address=ip_address,
            created_by=actor,
            updated_by=actor,
        )
