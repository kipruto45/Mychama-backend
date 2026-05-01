from __future__ import annotations

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class PostHogService:
    """Small PostHog wrapper for server-side analytics and health checks."""

    def __init__(self) -> None:
        self.enabled = bool(getattr(settings, "POSTHOG_ENABLED", False))
        self.api_key = str(getattr(settings, "POSTHOG_API_KEY", "") or "").strip()
        self.host = str(
            getattr(settings, "POSTHOG_HOST", "https://app.posthog.com") or ""
        ).rstrip("/")
        self.timeout = float(getattr(settings, "SUPABASE_REQUEST_TIMEOUT", 5))

    def capture(
        self,
        *,
        event: str,
        distinct_id: str,
        properties: dict | None = None,
    ) -> bool:
        if not self.enabled or not self.api_key:
            return False

        try:
            response = requests.post(
                f"{self.host}/capture/",
                json={
                    "api_key": self.api_key,
                    "event": event,
                    "distinct_id": distinct_id,
                    "properties": properties or {},
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            return True
        except requests.RequestException as exc:
            logger.warning("PostHog capture failed: %s", exc)
            return False

    def health_snapshot(self) -> dict:
        if not self.enabled:
            return {
                "status": "disabled",
                "host": self.host,
                "configured": False,
            }

        if not self.api_key:
            return {
                "status": "unhealthy",
                "host": self.host,
                "configured": False,
                "error": "POSTHOG_API_KEY is missing",
            }

        return {
            "status": "healthy",
            "host": self.host,
            "configured": True,
        }


posthog_service = PostHogService()

