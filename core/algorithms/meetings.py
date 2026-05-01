from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class MeetingWindow:
    start: datetime
    end: datetime
    metadata: dict[str, Any] | None = None


def build_meeting_window(
    *,
    start: datetime,
    duration_minutes: int,
    metadata: dict[str, Any] | None = None,
) -> MeetingWindow:
    safe_duration = max(1, int(duration_minutes))
    end = start + timedelta(minutes=safe_duration)
    return MeetingWindow(start=start, end=end, metadata=metadata or {})


def windows_overlap(left: MeetingWindow, right: MeetingWindow) -> bool:
    return left.start < right.end and right.start < left.end


def detect_overlapping_windows(
    *,
    proposed: MeetingWindow,
    existing: list[MeetingWindow],
) -> list[MeetingWindow]:
    return [window for window in existing if windows_overlap(proposed, window)]
