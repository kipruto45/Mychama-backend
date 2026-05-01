from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def clean_temporary_files():
    temp_root = Path(getattr(settings, "TEMP_FILE_ROOT", "/tmp"))
    cutoff = timezone.now().timestamp() - 86400
    deleted = 0

    if temp_root.exists():
        for path in temp_root.glob("mychama-*"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    deleted += 1
            except OSError:
                logger.exception("Failed deleting temp file %s", path)

    return {"deleted": deleted, "root": str(temp_root)}


@shared_task
def optimize_database():
    vendor = connection.vendor
    with connection.cursor() as cursor:
        if vendor == "postgresql":
            cursor.execute("SELECT 1")
            return {"status": "ok", "vendor": vendor, "action": "noop_safe_check"}
        if vendor == "sqlite":
            cursor.execute("PRAGMA optimize")
            return {"status": "ok", "vendor": vendor, "action": "pragma_optimize"}
        cursor.execute("SELECT 1")
    return {"status": "ok", "vendor": vendor, "action": "noop"}


@shared_task
def health_check():
    api_status = "healthy"
    db_status = "healthy"
    cache_status = "healthy"
    redis_status = "unknown"
    mpesa_status = "disabled"

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        db_status = f"unhealthy:{exc}"

    try:
        cache.set("automation:health-check", "ok", timeout=30)
        if cache.get("automation:health-check") != "ok":
            cache_status = "unhealthy:cache_mismatch"
        redis_status = "healthy"
    except Exception as exc:  # noqa: BLE001
        cache_status = f"unhealthy:{exc}"
        redis_status = f"unhealthy:{exc}"

    try:
        from apps.notifications.push import get_fcm_provider

        get_fcm_provider()
    except Exception:  # noqa: BLE001
        logger.exception("Push provider health probe failed")

    if getattr(settings, "MPESA_USE_STUB", True):
        mpesa_status = "stub"
    elif getattr(settings, "MPESA_CONSUMER_KEY", "") and getattr(settings, "MPESA_CONSUMER_SECRET", ""):
        mpesa_status = "configured"

    overall = "healthy"
    if "unhealthy" in db_status or "unhealthy" in cache_status:
        overall = "degraded"

    return {
        "status": overall,
        "checked_at": timezone.now().isoformat(),
        "api": api_status,
        "database": db_status,
        "cache": cache_status,
        "redis": redis_status,
        "mpesa": mpesa_status,
    }


@shared_task
def create_backup_snapshot():
    backup_root = Path(getattr(settings, "BACKUP_DIR", str(Path(settings.BASE_DIR) / "backups")))
    backup_root.mkdir(parents=True, exist_ok=True)
    timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
    manifest_path = backup_root / f"backup_manifest_{timestamp}.json"

    manifest = {
        "created_at": timezone.now().isoformat(),
        "database_engine": settings.DATABASES["default"]["ENGINE"],
        "database_name": settings.DATABASES["default"].get("NAME"),
        "pg_dump_available": shutil.which("pg_dump") is not None,
        "status": "manifest_only",
    }

    if settings.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql" and shutil.which("pg_dump"):
        dump_path = backup_root / f"mychama_{timestamp}.sql"
        command = [
            "pg_dump",
            settings.DATABASES["default"].get("NAME", ""),
            "-f",
            str(dump_path),
        ]
        host = settings.DATABASES["default"].get("HOST")
        user = settings.DATABASES["default"].get("USER")
        port = settings.DATABASES["default"].get("PORT")
        if host:
            command.extend(["-h", str(host)])
        if user:
            command.extend(["-U", str(user)])
        if port:
            command.extend(["-p", str(port)])
        env = None
        password = settings.DATABASES["default"].get("PASSWORD")
        if password:
            env = {**os.environ, "PGPASSWORD": str(password)}
        try:
            subprocess.run(command, check=True, capture_output=True, text=True, env=env)
            manifest["status"] = "backup_created"
            manifest["backup_file"] = str(dump_path)
        except Exception as exc:  # noqa: BLE001
            manifest["status"] = "backup_failed"
            manifest["error"] = str(exc)

    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
