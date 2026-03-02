from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta


@dataclass(frozen=True)
class NotificationPreferenceSnapshot:
    sms_enabled: bool = True
    email_enabled: bool = True
    in_app_enabled: bool = True
    critical_only: bool = False
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None


def in_quiet_hours(
    *,
    now: datetime,
    start: time | None,
    end: time | None,
) -> bool:
    if not start or not end:
        return False
    current = now.timetz().replace(tzinfo=None)
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end


def should_send_topic_notification(
    *,
    last_sent_at: datetime | None,
    now: datetime,
    minimum_interval: timedelta = timedelta(days=1),
) -> bool:
    if last_sent_at is None:
        return True
    return (now - last_sent_at) >= minimum_interval


def channel_routing(
    *,
    requested_channels: list[str],
    preference: NotificationPreferenceSnapshot,
    priority: str = "normal",
) -> list[str]:
    channels: list[str] = []
    normalized_priority = str(priority or "normal").lower()
    allow_non_critical = not preference.critical_only or normalized_priority in {
        "high",
        "critical",
    }

    for channel in requested_channels:
        name = str(channel).lower()
        if name == "in_app" and preference.in_app_enabled:
            channels.append(name)
        elif name == "sms" and preference.sms_enabled and allow_non_critical:
            channels.append(name)
        elif name == "email" and preference.email_enabled and allow_non_critical:
            channels.append(name)
    return channels
