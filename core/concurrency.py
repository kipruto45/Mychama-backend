from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import APIException, ValidationError


class PreconditionFailed(APIException):
    status_code = 412
    default_code = "precondition_failed"
    default_detail = "Resource was modified by another request. Refresh and retry."


def _parse_timestamp(raw_value: str) -> datetime | None:
    value = str(raw_value or "").strip()
    if not value:
        return None

    parsed = parse_datetime(value)
    if parsed is None:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def enforce_if_unmodified_since(request, *, current_updated_at: datetime):
    raw_value = request.headers.get("If-Unmodified-Since") or request.headers.get(
        "X-If-Unmodified-At"
    )
    if not raw_value:
        return

    expected = _parse_timestamp(raw_value)
    if expected is None:
        raise ValidationError(
            {
                "If-Unmodified-Since": (
                    "Invalid timestamp format. Use RFC 1123 or ISO-8601."
                )
            }
        )

    current = current_updated_at.astimezone(UTC).replace(microsecond=0)
    candidate = expected.astimezone(UTC).replace(microsecond=0)
    if current > candidate:
        raise PreconditionFailed()
