from __future__ import annotations

from datetime import datetime, timezone as dt_timezone

from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from django.utils import timezone

from apps.security.models import DeviceSession
from apps.security.services import SecurityService


class MyChamaJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None

        user, token = result
        jti = str(token.get("jti", "")).strip()
        if not jti:
            return result

        issued_at = token.get("iat")
        if issued_at and getattr(user, "password_changed_at", None):
            token_issued_at = datetime.fromtimestamp(int(issued_at), tz=dt_timezone.utc)
            password_changed_at = user.password_changed_at
            if password_changed_at.tzinfo is None:
                password_changed_at = password_changed_at.replace(tzinfo=dt_timezone.utc)
            if token_issued_at < password_changed_at:
                raise AuthenticationFailed("Session is no longer valid. Please sign in again.")

        session = DeviceSession.objects.filter(user=user, session_key=jti).first()
        if session is None:
            return result

        if session.is_revoked:
            raise AuthenticationFailed("This session has been revoked. Please sign in again.")

        now = timezone.now()
        if (now - session.last_seen).total_seconds() > SecurityService.inactivity_timeout_seconds():
            SecurityService.revoke_session(session=session)
            SecurityService.record_security_event(
                user=user,
                event_type="session_expired",
                description="Session expired due to inactivity timeout",
                metadata={"session_key": session.session_key},
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
            raise AuthenticationFailed("Session expired due to inactivity. Please sign in again.")

        DeviceSession.objects.filter(id=session.id).update(last_seen=now)
        return result
