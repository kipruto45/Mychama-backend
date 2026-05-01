from __future__ import annotations

import logging
import mimetypes
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import requests
from django.core.files.base import ContentFile, File
from django.core.files.storage import Storage
from django.core.files.uploadedfile import UploadedFile
from django.utils.deconstruct import deconstructible

from core.supabase import SupabaseAdminClient, get_supabase_config

logger = logging.getLogger(__name__)


class DocumentType(str, Enum):
    """Supported document types for organization."""

    # KYC Documents
    KYC_ID_FRONT = "kyc/id_front"
    KYC_ID_BACK = "kyc/id_back"
    KYC_SELFIE = "kyc/selfie"
    KYC_SUPPORTING = "kyc/supporting"

    # User Documents
    USER_AVATAR = "profile/avatar"
    USER_DOCUMENTS = "profile/documents"

    # Chama Documents
    CHAMA_LOGO = "chama/logo"
    CHAMA_BANNER = "chama/banner"
    CHAMA_DOCUMENTS = "chama/documents"
    CHAMA_MINUTES = "chama/meeting_minutes"
    CHAMA_INVOICES = "chama/invoices"
    CHAMA_RECEIPTS = "chama/receipts"
    CHAMA_REPORTS = "chama/reports"


class StorageError(Exception):
    """Base exception for storage operations."""

    pass


class StorageFileNotFound(StorageError):
    """Raised when a file is not found in storage."""

    pass


class StorageUploadError(StorageError):
    """Raised when file upload fails."""

    pass


class StorageDownloadError(StorageError):
    """Raised when file download fails."""

    pass


def _normalize_name(name: str) -> str:
    return name.replace("\\", "/").lstrip("/")


def _read_content(content: Any) -> bytes:
    if isinstance(content, bytes):
        return content
    if hasattr(content, "chunks"):
        return b"".join(content.chunks())
    if hasattr(content, "read"):
        data = content.read()
        return data if isinstance(data, bytes) else str(data).encode()
    raise TypeError("Unsupported file content object for storage upload")


@deconstructible
class SupabaseStorage(Storage):
    # File size limits (in bytes)
    DEFAULT_MAX_SIZE = 52_428_800  # 50MB
    AVATAR_MAX_SIZE = 5_242_880  # 5MB
    DOCUMENT_MAX_SIZE = 52_428_800  # 50MB

    # Allowed MIME types
    ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/jpg", "image/gif"}
    ALLOWED_DOCUMENT_TYPES = {
        "application/pdf",
        "text/csv",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
        "application/vnd.ms-excel",
        "text/plain",
    }
    ALLOWED_MEDIA_TYPES = ALLOWED_IMAGE_TYPES | ALLOWED_DOCUMENT_TYPES

    def __init__(self, bucket_name: str | None = None):
        self.config = get_supabase_config()
        self.bucket_name = bucket_name or self.config.storage_bucket
        if not self.bucket_name:
            raise ValueError("SUPABASE_STORAGE_BUCKET must be configured")
        self.client = SupabaseAdminClient(self.config)

    def _open(self, name: str, mode: str = "rb") -> File:
        data = self.client.download_storage_object(self.bucket_name, _normalize_name(name))
        return ContentFile(data, name=name)

    def _save(self, name: str, content: Any) -> str:
        normalized_name = _normalize_name(name)
        payload = _read_content(content)
        content_type = getattr(content, "content_type", None) or mimetypes.guess_type(normalized_name)[0]
        self.client.upload_storage_object(
            self.bucket_name,
            normalized_name,
            payload,
            content_type=content_type or "application/octet-stream",
            upsert=True,
        )
        return normalized_name

    def delete(self, name: str) -> None:
        self.client.delete_storage_object(self.bucket_name, _normalize_name(name))

    def exists(self, name: str) -> bool:
        try:
            return (
                self.client.get_storage_object_info(self.bucket_name, _normalize_name(name))
                is not None
            )
        except requests.HTTPError as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in {400, 404}:
                return False
            logger.warning(
                "Supabase exists() check failed",
                extra={
                    "bucket": self.bucket_name,
                    "path": _normalize_name(name),
                    "status_code": status_code,
                },
            )
            return False
        except requests.RequestException:
            logger.warning(
                "Supabase exists() check failed due to request error",
                extra={"bucket": self.bucket_name, "path": _normalize_name(name)},
            )
            return False

    def url(self, name: str) -> str:
        normalized_name = _normalize_name(name)
        if self.config.storage_public:
            return self.client.create_public_storage_url(self.bucket_name, normalized_name)
        return self.client.create_signed_storage_url(self.bucket_name, normalized_name)

    def size(self, name: str) -> int:
        info = self.client.get_storage_object_info(self.bucket_name, _normalize_name(name)) or {}
        metadata = info.get("metadata") or {}
        size = metadata.get("size") or info.get("size")
        return int(size or 0)

    def get_modified_time(self, name: str):
        raise NotImplementedError("Supabase storage does not expose modified time here")

    # Enhanced methods for KYC documents, chama documents, and avatars

    def _validate_file(
        self,
        file_content: bytes | UploadedFile,
        max_size: int | None = None,
        allowed_types: set[str] | None = None,
    ) -> tuple[bytes, str]:
        """
        Validate file content and type.

        Args:
            file_content: File bytes or UploadedFile
            max_size: Maximum file size in bytes
            allowed_types: Set of allowed MIME types

        Returns:
            Tuple of (file_bytes, content_type)

        Raises:
            StorageError: If validation fails
        """
        # Handle UploadedFile
        if isinstance(file_content, UploadedFile):
            content_type = file_content.content_type or "application/octet-stream"
            file_bytes = file_content.read()
        else:
            file_bytes = file_content
            content_type = "application/octet-stream"

        # Validate size
        max_size = max_size or self.DEFAULT_MAX_SIZE
        if len(file_bytes) > max_size:
            raise StorageError(
                f"File size {len(file_bytes)} bytes exceeds maximum {max_size} bytes"
            )

        # Validate type
        if allowed_types and content_type not in allowed_types:
            raise StorageError(
                f"File type {content_type} not allowed. "
                f"Allowed types: {', '.join(sorted(allowed_types))}"
            )

        return file_bytes, content_type

    def _generate_safe_filename(self, original_filename: str | None) -> str:
        """Generate a safe filename from the original."""
        if not original_filename:
            return f"document_{datetime.now().isoformat()}"

        # Extract extension
        name_part = Path(original_filename).name
        if "." in name_part:
            _, ext = name_part.rsplit(".", 1)
        else:
            ext = ""

        # Generate safe name
        safe_name = original_filename.replace(" ", "_").replace("/", "_")
        safe_name = "".join(c for c in safe_name if c.isalnum() or c in "._-")

        return f"{safe_name}_{datetime.now().isoformat()}{f'.{ext}' if ext else ''}"

    def upload_kyc_document(
        self,
        user_id: str,
        document_type: DocumentType,
        file_content: bytes | UploadedFile,
        original_filename: str | None = None,
    ) -> str:
        """
        Upload a KYC document for a user.

        Args:
            user_id: UUID of the user
            document_type: Type of KYC document
            file_content: File bytes or UploadedFile
            original_filename: Original filename (for reference)

        Returns:
            Storage path to the uploaded file

        Raises:
            StorageUploadError: If upload fails
        """
        try:
            file_bytes, content_type = self._validate_file(
                file_content,
                max_size=self.DOCUMENT_MAX_SIZE,
                allowed_types=self.ALLOWED_MEDIA_TYPES,
            )

            # Generate safe filename
            filename = self._generate_safe_filename(original_filename)

            # Build storage path: users/{user_id}/kyc/{doc_type}/{filename}
            storage_path = f"users/{user_id}/{document_type.value}/{filename}"

            # Upload to Supabase Storage
            self.client.upload_storage_object(
                bucket=self.bucket_name,
                object_path=storage_path,
                payload=file_bytes,
                content_type=content_type,
                upsert=True,
            )

            return storage_path

        except StorageError:
            raise
        except Exception as exc:
            raise StorageUploadError(f"Failed to upload KYC document: {exc}") from exc

    def upload_chama_document(
        self,
        chama_id: str,
        document_type: DocumentType,
        file_content: bytes | UploadedFile,
        original_filename: str | None = None,
    ) -> str:
        """
        Upload a document for a chama.

        Args:
            chama_id: UUID of the chama
            document_type: Type of document
            file_content: File bytes or UploadedFile
            original_filename: Original filename

        Returns:
            Storage path to the uploaded file

        Raises:
            StorageUploadError: If upload fails
        """
        try:
            file_bytes, content_type = self._validate_file(
                file_content,
                max_size=self.DOCUMENT_MAX_SIZE,
                allowed_types=self.ALLOWED_MEDIA_TYPES,
            )

            # Generate safe filename
            filename = self._generate_safe_filename(original_filename)

            # Build storage path: chama/{chama_id}/{doc_type}/{filename}
            storage_path = f"chama/{chama_id}/{document_type.value}/{filename}"

            # Upload to Supabase Storage
            self.client.upload_storage_object(
                bucket=self.bucket_name,
                object_path=storage_path,
                payload=file_bytes,
                content_type=content_type,
                upsert=True,
            )

            return storage_path

        except StorageError:
            raise
        except Exception as exc:
            raise StorageUploadError(f"Failed to upload chama document: {exc}") from exc

    def upload_user_avatar(
        self,
        user_id: str,
        file_content: bytes | UploadedFile,
    ) -> str:
        """
        Upload a user avatar.

        Args:
            user_id: UUID of the user
            file_content: Image file bytes or UploadedFile

        Returns:
            Storage path to the avatar

        Raises:
            StorageUploadError: If upload fails
        """
        try:
            file_bytes, content_type = self._validate_file(
                file_content,
                max_size=self.AVATAR_MAX_SIZE,
                allowed_types=self.ALLOWED_IMAGE_TYPES,
            )

            # Generate safe filename
            filename = self._generate_safe_filename("avatar")

            # Build storage path
            storage_path = f"users/{user_id}/profile/avatar/{filename}"

            # Upload to Supabase Storage
            self.client.upload_storage_object(
                bucket=self.bucket_name,
                object_path=storage_path,
                payload=file_bytes,
                content_type=content_type,
                upsert=True,
            )

            return storage_path

        except StorageError:
            raise
        except Exception as exc:
            raise StorageUploadError(f"Failed to upload avatar: {exc}") from exc

    def get_authenticated_url(self, storage_path: str) -> str:
        """Get authenticated URL for a file."""
        return self.client.create_public_storage_url(self.bucket_name, storage_path)

    def get_signed_url(self, storage_path: str, expires_in: int | None = None) -> str:
        """Get signed URL for a file (time-limited access)."""
        return self.client.create_signed_storage_url(
            bucket=self.bucket_name,
            object_path=storage_path,
            expires_in=expires_in,
        )
