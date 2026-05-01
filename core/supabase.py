from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests
from django.conf import settings

from core.safe_http import OutboundPolicy, safe_request


def _normalize_supabase_url(value: str) -> str:
    return value.rstrip("/")


def _storage_object_path(object_path: str) -> str:
    return quote(object_path.lstrip("/"), safe="/")


@dataclass(frozen=True)
class SupabaseConfig:
    url: str
    service_role_key: str
    timeout: int
    database_host: str
    storage_bucket: str
    storage_public: bool
    signed_url_ttl: int

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.service_role_key)

    @property
    def rest_url(self) -> str:
        return f"{self.url}/rest/v1" if self.url else ""

    @property
    def auth_url(self) -> str:
        return f"{self.url}/auth/v1" if self.url else ""

    @property
    def storage_url(self) -> str:
        return f"{self.url}/storage/v1" if self.url else ""

    @property
    def database_uses_supabase(self) -> bool:
        return self.database_host.endswith(".supabase.co")


def get_supabase_config() -> SupabaseConfig:
    return SupabaseConfig(
        url=_normalize_supabase_url(getattr(settings, "SUPABASE_URL", "")),
        service_role_key=getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", ""),
        timeout=max(getattr(settings, "SUPABASE_REQUEST_TIMEOUT", 5), 1),
        database_host=str(getattr(settings, "SUPABASE_DATABASE_HOST", "")),
        storage_bucket=str(getattr(settings, "SUPABASE_STORAGE_BUCKET", "")).strip(),
        storage_public=bool(getattr(settings, "SUPABASE_STORAGE_PUBLIC", False)),
        signed_url_ttl=max(getattr(settings, "SUPABASE_STORAGE_SIGNED_URL_TTL", 3600), 60),
    )


class SupabaseAdminClient:
    def __init__(self, config: SupabaseConfig | None = None):
        self.config = config or get_supabase_config()

    def _policy(self) -> OutboundPolicy:
        from urllib.parse import urlparse

        hostname = urlparse(self.config.url).hostname if self.config.url else ""
        if not hostname:
            raise ValueError("Supabase URL is invalid or missing")
        return OutboundPolicy(allowed_hosts={hostname})

    def is_configured(self) -> bool:
        return self.config.enabled

    def build_headers(
        self,
        *,
        extra_headers: dict[str, str] | None = None,
        use_service_role: bool = True,
    ) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
        }
        if use_service_role and self.config.service_role_key:
            headers["apikey"] = self.config.service_role_key
            headers["Authorization"] = f"Bearer {self.config.service_role_key}"
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
        use_service_role: bool = True,
    ) -> requests.Response:
        if not self.is_configured():
            raise ValueError("Supabase is not configured")

        target = path if path.startswith("http") else f"{self.config.url}/{path.lstrip('/')}"
        response = safe_request(
            method.upper(),
            target,
            policy=self._policy(),
            params=params,
            json=json,
            headers=self.build_headers(
                extra_headers=headers,
                use_service_role=use_service_role,
            ),
            timeout=float(timeout or self.config.timeout),
        )
        return response

    def check_health(self) -> dict[str, Any]:
        if not self.config.url:
            return {
                "status": "not_configured",
                "message": "SUPABASE_URL is missing",
                "configured": False,
            }

        if not self.config.service_role_key:
            return {
                "status": "degraded",
                "message": "SUPABASE_SERVICE_ROLE_KEY is missing",
                "configured": False,
            }

        try:
            response = self.request("GET", "auth/v1/settings")
            if response.ok:
                return {
                    "status": "healthy",
                    "message": "Supabase auth endpoint reachable",
                    "configured": True,
                    "http_status": response.status_code,
                }

            status = "degraded" if response.status_code < 500 else "unhealthy"
            return {
                "status": status,
                "message": "Supabase responded with a non-success status",
                "configured": True,
                "http_status": response.status_code,
            }
        except requests.RequestException as exc:
            return {
                "status": "unhealthy",
                "message": str(exc),
                "configured": True,
            }

    def create_public_storage_url(self, bucket: str, object_path: str) -> str:
        if not self.config.url:
            return ""
        safe_bucket = quote(bucket.strip(), safe="")
        safe_path = _storage_object_path(object_path)
        return f"{self.config.storage_url}/object/public/{safe_bucket}/{safe_path}"

    def create_signed_storage_url(
        self,
        bucket: str,
        object_path: str,
        *,
        expires_in: int | None = None,
    ) -> str:
        response = self.request(
            "POST",
            f"storage/v1/object/sign/{quote(bucket.strip(), safe='')}/{_storage_object_path(object_path)}",
            json={"expiresIn": expires_in or self.config.signed_url_ttl},
        )
        response.raise_for_status()
        payload = response.json()
        signed_path = payload.get("signedURL") or payload.get("signedUrl")
        if not signed_path:
            raise ValueError("Supabase did not return a signed URL")
        if str(signed_path).startswith("http"):
            return str(signed_path)
        return f"{self.config.storage_url}{signed_path}"

    def upload_storage_object(
        self,
        bucket: str,
        object_path: str,
        payload: bytes,
        *,
        content_type: str = "application/octet-stream",
        upsert: bool = True,
    ) -> dict[str, Any]:
        response = safe_request(
            "POST",
            f"{self.config.storage_url}/object/{quote(bucket.strip(), safe='')}/{_storage_object_path(object_path)}",
            data=payload,
            policy=self._policy(),
            headers=self.build_headers(
                extra_headers={
                    "Content-Type": content_type,
                    "x-upsert": "true" if upsert else "false",
                }
            ),
            timeout=float(self.config.timeout),
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    def download_storage_object(self, bucket: str, object_path: str) -> bytes:
        response = self.request(
            "GET",
            f"storage/v1/object/authenticated/{quote(bucket.strip(), safe='')}/{_storage_object_path(object_path)}",
        )
        response.raise_for_status()
        return response.content

    def delete_storage_object(self, bucket: str, object_path: str) -> None:
        response = self.request(
            "DELETE",
            f"storage/v1/object/{quote(bucket.strip(), safe='')}/{_storage_object_path(object_path)}",
        )
        if response.status_code not in {200, 204, 404}:
            response.raise_for_status()

    def get_storage_object_info(
        self,
        bucket: str,
        object_path: str,
    ) -> dict[str, Any] | None:
        response = self.request(
            "GET",
            f"storage/v1/object/info/{quote(bucket.strip(), safe='')}/{_storage_object_path(object_path)}",
        )
        if response.status_code == 404:
            return None
        if response.status_code == 400:
            # Supabase Storage may return 400 "Bad Request" for missing objects on this endpoint.
            # Only treat it as "missing" when the payload indicates a not-found error; otherwise
            # propagate the error so misconfiguration (e.g. missing bucket) is visible.
            body_parts: list[str] = []
            try:
                body_parts.append(json.dumps(response.json(), sort_keys=True))
            except ValueError:
                pass
            if response.text:
                body_parts.append(response.text)
            text = " ".join(body_parts).lower()
            if ("not found" in text) or ("does not exist" in text) or ("no such" in text):
                if "bucket" not in text:
                    return None
        response.raise_for_status()
        return response.json()


def get_supabase_health_snapshot() -> dict[str, Any]:
    config = get_supabase_config()
    client = SupabaseAdminClient(config)
    health = client.check_health()
    health["url"] = config.url or None
    health["rest_url"] = config.rest_url or None
    health["storage_url"] = config.storage_url or None
    health["database_host"] = config.database_host or None
    health["database_uses_supabase"] = config.database_uses_supabase
    health["storage_bucket"] = config.storage_bucket or None
    health["storage_public"] = config.storage_public
    return health
