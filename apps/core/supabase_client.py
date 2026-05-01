"""
Compatibility wrapper for Supabase access from app code.

This module keeps the old import path while routing storage and REST calls
through the shared backend implementation in `core.supabase`.
"""

import logging
from typing import Any

from django.conf import settings

from core.supabase import SupabaseAdminClient

logger = logging.getLogger(__name__)


class SupabaseService:
    """Service for interacting with Supabase."""

    _client: SupabaseAdminClient | None = None
    _admin_client: SupabaseAdminClient | None = None

    @classmethod
    def get_client(cls) -> SupabaseAdminClient:
        if cls._client is None:
            cls._client = SupabaseAdminClient()
        return cls._client

    @classmethod
    def get_admin_client(cls) -> SupabaseAdminClient:
        if cls._admin_client is None:
            cls._admin_client = SupabaseAdminClient()
        return cls._admin_client

    @classmethod
    def sign_up(cls, email: str, password: str, metadata: dict | None = None) -> dict[str, Any]:
        client = cls.get_client()
        try:
            response = client.request(
                "POST",
                "auth/v1/signup",
                headers={"apikey": getattr(settings, "SUPABASE_ANON_KEY", "")},
                json={"email": email, "password": password, "data": metadata or {}},
                use_service_role=False,
            )
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except Exception as exc:
            logger.error("Sign up failed for %s: %s", email, exc)
            return {"success": False, "error": str(exc)}

    @classmethod
    def sign_in(cls, email: str, password: str) -> dict[str, Any]:
        client = cls.get_client()
        try:
            response = client.request(
                "POST",
                "auth/v1/token?grant_type=password",
                headers={"apikey": getattr(settings, "SUPABASE_ANON_KEY", "")},
                json={"email": email, "password": password},
                use_service_role=False,
            )
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except Exception as exc:
            logger.error("Sign in failed for %s: %s", email, exc)
            return {"success": False, "error": str(exc)}

    @classmethod
    def sign_out(cls, access_token: str) -> dict[str, Any]:
        client = cls.get_client()
        try:
            response = client.request(
                "POST",
                "auth/v1/logout",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "apikey": getattr(settings, "SUPABASE_ANON_KEY", ""),
                },
                use_service_role=False,
            )
            response.raise_for_status()
            return {"success": True}
        except Exception as exc:
            logger.error("Sign out failed: %s", exc)
            return {"success": False, "error": str(exc)}

    @classmethod
    def reset_password(cls, email: str) -> dict[str, Any]:
        client = cls.get_client()
        try:
            response = client.request(
                "POST",
                "auth/v1/recover",
                headers={"apikey": getattr(settings, "SUPABASE_ANON_KEY", "")},
                json={"email": email},
                use_service_role=False,
            )
            response.raise_for_status()
            return {"success": True}
        except Exception as exc:
            logger.error("Password reset failed for %s: %s", email, exc)
            return {"success": False, "error": str(exc)}

    @classmethod
    def get_user(cls, access_token: str) -> dict[str, Any] | None:
        client = cls.get_client()
        try:
            response = client.request(
                "GET",
                "auth/v1/user",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "apikey": getattr(settings, "SUPABASE_ANON_KEY", ""),
                },
                use_service_role=False,
            )
            response.raise_for_status()
            return {"success": True, "user": response.json()}
        except Exception as exc:
            logger.error("Get user failed: %s", exc)
            return None

    @classmethod
    def refresh_token(cls, refresh_token: str) -> dict[str, Any]:
        client = cls.get_client()
        try:
            response = client.request(
                "POST",
                "auth/v1/token?grant_type=refresh_token",
                headers={"apikey": getattr(settings, "SUPABASE_ANON_KEY", "")},
                json={"refresh_token": refresh_token},
                use_service_role=False,
            )
            response.raise_for_status()
            return {"success": True, "session": response.json()}
        except Exception as exc:
            logger.error("Token refresh failed: %s", exc)
            return {"success": False, "error": str(exc)}

    @classmethod
    def insert(cls, table: str, data: dict[str, Any], use_admin: bool = False) -> dict[str, Any]:
        client = cls.get_admin_client() if use_admin else cls.get_client()
        try:
            response = client.request(
                "POST",
                f"rest/v1/{table}",
                json=data,
                headers={"Prefer": "return=representation"},
            )
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except Exception as exc:
            logger.error("Insert into %s failed: %s", table, exc)
            return {"success": False, "error": str(exc)}

    @classmethod
    def select(
        cls,
        table: str,
        columns: str = "*",
        filters: dict[str, Any] | None = None,
        use_admin: bool = False,
    ) -> dict[str, Any]:
        client = cls.get_admin_client() if use_admin else cls.get_client()
        try:
            params: dict[str, Any] = {"select": columns}
            if filters:
                for key, value in filters.items():
                    params[key] = f"eq.{value}"
            response = client.request("GET", f"rest/v1/{table}", params=params)
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except Exception as exc:
            logger.error("Select from %s failed: %s", table, exc)
            return {"success": False, "error": str(exc)}

    @classmethod
    def update(
        cls,
        table: str,
        data: dict[str, Any],
        filters: dict[str, Any],
        use_admin: bool = False,
    ) -> dict[str, Any]:
        client = cls.get_admin_client() if use_admin else cls.get_client()
        try:
            params = {key: f"eq.{value}" for key, value in filters.items()}
            response = client.request(
                "PATCH",
                f"rest/v1/{table}",
                params=params,
                json=data,
                headers={"Prefer": "return=representation"},
            )
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except Exception as exc:
            logger.error("Update in %s failed: %s", table, exc)
            return {"success": False, "error": str(exc)}

    @classmethod
    def delete(cls, table: str, filters: dict[str, Any], use_admin: bool = False) -> dict[str, Any]:
        client = cls.get_admin_client() if use_admin else cls.get_client()
        try:
            params = {key: f"eq.{value}" for key, value in filters.items()}
            response = client.request(
                "DELETE",
                f"rest/v1/{table}",
                params=params,
                headers={"Prefer": "return=representation"},
            )
            response.raise_for_status()
            return {"success": True, "data": response.json() if response.content else []}
        except Exception as exc:
            logger.error("Delete from %s failed: %s", table, exc)
            return {"success": False, "error": str(exc)}

    @classmethod
    def upload_file(
        cls,
        bucket: str,
        path: str,
        file_content: bytes,
        content_type: str = "application/octet-stream",
        use_admin: bool = False,
    ) -> dict[str, Any]:
        client = cls.get_admin_client() if use_admin else cls.get_client()
        try:
            response = client.upload_storage_object(
                bucket=bucket,
                object_path=path,
                payload=file_content,
                content_type=content_type,
                upsert=True,
            )
            return {"success": True, "path": path, "data": response}
        except Exception as exc:
            logger.error("File upload failed: %s", exc)
            return {"success": False, "error": str(exc)}

    @classmethod
    def get_public_url(cls, bucket: str, path: str) -> str:
        client = cls.get_client()
        return client.create_public_storage_url(bucket, path)

    @classmethod
    def get_storage_url(cls, bucket: str, path: str) -> str:
        client = cls.get_client()
        if getattr(settings, "SUPABASE_STORAGE_PUBLIC", False):
            return client.create_public_storage_url(bucket, path)
        return client.create_signed_storage_url(bucket, path)
