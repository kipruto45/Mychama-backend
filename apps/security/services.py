from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from datetime import timezone as dt_timezone

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.security.models import (
    AccountLock,
    AuditChainCheckpoint,
    AuditLog,
    DeviceSession,
    LoginAttempt,
    RefreshTokenRecord,
    SecurityAlert,
    SecurityEvent,
)
from core.request_context import get_correlation_id


class SecurityService:
    REFRESH_FAMILY_CLAIM = "fam"
    AUDIT_HASH_ALGORITHM = "sha256"

    @staticmethod
    def failure_limit() -> int:
        return max(1, int(getattr(settings, "LOGIN_LOCKOUT_FAILURE_LIMIT", 5)))

    @staticmethod
    def cooldown_seconds() -> int:
        return max(60, int(getattr(settings, "LOGIN_LOCKOUT_COOLDOWN_SECONDS", 900)))

    @staticmethod
    def max_active_sessions() -> int:
        return max(1, int(getattr(settings, "MAX_ACTIVE_SESSIONS_PER_USER", 3)))

    @staticmethod
    def inactivity_timeout_seconds() -> int:
        return max(60, int(getattr(settings, "SESSION_INACTIVITY_TIMEOUT_SECONDS", 300)))

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
        revoked = list(queryset.values_list("session_key", flat=True))
        count = queryset.update(is_revoked=True, last_seen=timezone.now())
        if revoked:
            now = timezone.now()
            RefreshTokenRecord.objects.filter(
                user=user,
                jti__in=[value for value in revoked if value],
                revoked_at__isnull=True,
            ).update(revoked_at=now, revoked_reason="session_revoked")
        return count

    @staticmethod
    def record_security_event(
        *,
        user,
        event_type: str,
        description: str = "",
        metadata: dict | None = None,
        ip_address: str | None = None,
        user_agent: str = "",
    ) -> SecurityEvent:
        return SecurityEvent.objects.create(
            user=user,
            event_type=event_type,
            description=description,
            metadata=metadata or {},
            ip_address=ip_address,
            user_agent=user_agent,
        )

    @staticmethod
    def create_security_alert(
        *,
        user=None,
        chama=None,
        alert_type: str,
        level: str,
        title: str,
        message: str,
        metadata: dict | None = None,
        ip_address: str | None = None,
    ) -> SecurityAlert:
        return SecurityAlert.objects.create(
            user=user,
            chama=chama,
            alert_type=alert_type,
            level=level,
            title=title,
            message=message,
            metadata=metadata or {},
            ip_address=ip_address,
            created_by=user,
            updated_by=user,
        )

    @staticmethod
    def _extract_refresh_expiry(refresh: RefreshToken):
        exp = refresh.get("exp")
        if not exp:
            return timezone.now() + timedelta(days=7)
        return datetime.fromtimestamp(exp, tz=UTC)

    @staticmethod
    @transaction.atomic
    def register_refresh_token(
        *,
        user,
        refresh: RefreshToken,
        device_name: str = "",
        device_id: str = "",
        ip_address: str | None = None,
        user_agent: str = "",
        chama=None,
        parent_jti: str = "",
        family_id: str | None = None,
        previous_session_key: str | None = None,
    ) -> tuple[RefreshTokenRecord, DeviceSession, bool]:
        jti = str(refresh.get("jti", "")).strip()
        if not jti:
            raise TokenError("Refresh token did not include a jti.")

        resolved_family = family_id or str(refresh.get(SecurityService.REFRESH_FAMILY_CLAIM, "")).strip()
        if not resolved_family:
            resolved_family = str(uuid.uuid4())
            refresh[SecurityService.REFRESH_FAMILY_CLAIM] = resolved_family

        session_key = previous_session_key or jti
        session, is_new_device = SecurityService.register_device_session(
            user=user,
            chama=chama,
            device_name=device_name,
            ip_address=ip_address,
            user_agent=user_agent,
            session_key=session_key,
        )
        if previous_session_key and previous_session_key != jti:
            session.session_key = jti
            session.last_seen = timezone.now()
            session.save(update_fields=["session_key", "last_seen"])

        record, _ = RefreshTokenRecord.objects.update_or_create(
            jti=jti,
            defaults={
                "user": user,
                "family_id": uuid.UUID(str(resolved_family)),
                "parent_jti": parent_jti or "",
                "device_name": device_name,
                "device_id": device_id,
                "ip_address": ip_address,
                "user_agent": user_agent or "",
                "expires_at": SecurityService._extract_refresh_expiry(refresh),
                "revoked_at": None,
                "revoked_reason": "",
            },
        )
        SecurityService.enforce_session_limit(user=user)
        return record, session, is_new_device

    @staticmethod
    def enforce_session_limit(*, user) -> int:
        active_sessions = list(
            DeviceSession.objects.filter(user=user, is_revoked=False).order_by("-last_seen", "-created_at")
        )
        max_sessions = SecurityService.max_active_sessions()
        if len(active_sessions) <= max_sessions:
            return 0

        now = timezone.now()
        revoked_sessions = active_sessions[max_sessions:]
        session_ids = [session.id for session in revoked_sessions]
        session_keys = [session.session_key for session in revoked_sessions if session.session_key]
        count = DeviceSession.objects.filter(id__in=session_ids).update(
            is_revoked=True,
            last_seen=now,
        )
        if session_keys:
            RefreshTokenRecord.objects.filter(
                user=user,
                jti__in=session_keys,
                revoked_at__isnull=True,
            ).update(revoked_at=now, revoked_reason="session_limit")
        return count

    @staticmethod
    def revoke_refresh_family(*, user, family_id: str, reason: str) -> int:
        now = timezone.now()
        records = RefreshTokenRecord.objects.filter(
            user=user,
            family_id=uuid.UUID(str(family_id)),
            revoked_at__isnull=True,
        )
        session_keys = list(records.values_list("jti", flat=True))
        updated = records.update(revoked_at=now, revoked_reason=reason)
        if session_keys:
            DeviceSession.objects.filter(
                user=user,
                session_key__in=session_keys,
                is_revoked=False,
            ).update(is_revoked=True, last_seen=now)
        return updated

    @staticmethod
    def rotate_refresh_token(
        *,
        raw_refresh_token: str,
        device_name: str = "",
        device_id: str = "",
        ip_address: str | None = None,
        user_agent: str = "",
    ) -> tuple[RefreshToken, RefreshTokenRecord, bool]:
        old_refresh = RefreshToken(raw_refresh_token)
        user_id = old_refresh.get("user_id")
        jti = str(old_refresh.get("jti", "")).strip()
        family_id = str(old_refresh.get(SecurityService.REFRESH_FAMILY_CLAIM, "")).strip() or str(uuid.uuid4())
        if not user_id or not jti:
            raise TokenError("Refresh token payload is incomplete.")

        record = RefreshTokenRecord.objects.filter(jti=jti).select_related("user").first()
        user = record.user if record else None
        if user is None:
            from django.contrib.auth import get_user_model

            user_model = get_user_model()
            user = user_model.objects.get(id=user_id)

        if record and (record.revoked_at or record.used_at):
            SecurityService.revoke_refresh_family(
                user=user,
                family_id=family_id,
                reason="refresh_reuse_detected",
            )
            SecurityService.create_security_alert(
                user=user,
                alert_type=SecurityAlert.AlertType.SUSPICIOUS_LOGIN,
                level=SecurityAlert.Level.CRITICAL,
                title="Refresh token reuse detected",
                message="A rotated refresh token was presented again. All active sessions were revoked.",
                metadata={"family_id": family_id, "jti": jti},
                ip_address=ip_address,
            )
            SecurityService.record_security_event(
                user=user,
                event_type="suspicious_activity",
                description="Refresh token reuse detected",
                metadata={"family_id": family_id, "jti": jti},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            raise TokenError("Refresh token reuse detected.")

        if record:
            record.used_at = timezone.now()
            record.revoked_at = timezone.now()
            record.revoked_reason = "rotated"
            record.save(update_fields=["used_at", "revoked_at", "revoked_reason"])

        try:
            old_refresh.blacklist()
        except Exception:
            pass

        new_refresh = RefreshToken.for_user(user)
        new_refresh[SecurityService.REFRESH_FAMILY_CLAIM] = family_id
        new_record, _session, is_new_device = SecurityService.register_refresh_token(
            user=user,
            refresh=new_refresh,
            device_name=device_name,
            device_id=device_id,
            ip_address=ip_address,
            user_agent=user_agent,
            parent_jti=jti,
            family_id=family_id,
            previous_session_key=jti,
        )
        SecurityService.record_security_event(
            user=user,
            event_type="token_refreshed",
            description="Refresh token rotated successfully",
            metadata={"family_id": family_id, "previous_jti": jti, "new_jti": new_record.jti},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return new_refresh, new_record, is_new_device

    @staticmethod
    def revoke_all_refresh_tokens(*, user, reason: str = "session_revoked") -> int:
        now = timezone.now()
        return RefreshTokenRecord.objects.filter(
            user=user,
            revoked_at__isnull=True,
        ).update(revoked_at=now, revoked_reason=reason)

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
        metadata = metadata or {}
        trace_id = (get_correlation_id() or "").strip()

        last_record = AuditLog.objects.select_for_update().order_by("-chain_index").first()
        chain_index = (last_record.chain_index if last_record else 0) + 1
        prev_hash = last_record.event_hash if last_record else AuditLog.GENESIS_HASH
        payload = {
            "action_type": action_type,
            "target_type": target_type,
            "target_id": target_id,
            "actor_id": str(actor.id) if actor else None,
            "chama_id": str(chama.id) if chama else None,
            "metadata": metadata,
            "ip_address": ip_address,
            "trace_id": trace_id,
        }
        event_hash = SecurityService._hash_audit_event(prev_hash=prev_hash, payload=payload)

        return AuditLog.objects.create(
            chama=chama,
            actor=actor,
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            metadata=metadata,
            ip_address=ip_address,
            trace_id=trace_id,
            chain_index=chain_index,
            prev_hash=prev_hash,
            event_hash=event_hash,
            created_by=actor,
            updated_by=actor,
        )

    @staticmethod
    def _hash_audit_event(*, prev_hash: str, payload: dict) -> str:
        canonical_payload = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.new(
            SecurityService.AUDIT_HASH_ALGORITHM,
            f"{prev_hash}:{canonical_payload}".encode(),
        ).hexdigest()

    @staticmethod
    def create_audit_checkpoint(*, checkpoint_date=None) -> AuditChainCheckpoint:
        checkpoint_date = checkpoint_date or timezone.localdate()
        last_record = AuditLog.objects.order_by("-chain_index").first()
        record_count = AuditLog.objects.count()
        last_chain_index = last_record.chain_index if last_record else 0
        last_event_hash = last_record.event_hash if last_record else AuditLog.GENESIS_HASH
        signature = hashlib.sha256(
            f"{checkpoint_date.isoformat()}:{last_chain_index}:{last_event_hash}:{record_count}:{settings.SECRET_KEY}".encode()
        ).hexdigest()
        checkpoint, _created = AuditChainCheckpoint.objects.update_or_create(
            checkpoint_date=checkpoint_date,
            defaults={
                "last_chain_index": last_chain_index,
                "last_event_hash": last_event_hash,
                "record_count": record_count,
                "signature": signature,
            },
        )
        return checkpoint
