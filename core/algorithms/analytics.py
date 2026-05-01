from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _add_months(base: date, offset: int) -> date:
    month_index = (base.month - 1) + int(offset)
    year = base.year + (month_index // 12)
    month = (month_index % 12) + 1
    day = min(base.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def compute_member_activity_cohorts(
    *,
    join_month_by_member: dict[str, date],
    activity_months_by_member: dict[str, set[date]],
    horizon_months: int = 6,
) -> dict[str, dict]:
    horizon = max(1, min(24, int(horizon_months)))
    cohorts: dict[str, set[str]] = {}
    for member_id, joined_date in join_month_by_member.items():
        cohort_key = _month_start(joined_date).isoformat()
        cohorts.setdefault(cohort_key, set()).add(member_id)

    normalized_activity: dict[str, set[date]] = {}
    for member_id, dates in activity_months_by_member.items():
        normalized_activity[str(member_id)] = {_month_start(item) for item in dates}

    matrix: dict[str, dict] = {}
    for cohort_key, member_ids in sorted(cohorts.items()):
        cohort_start = date.fromisoformat(cohort_key)
        cohort_size = len(member_ids)
        points: list[dict] = []
        for offset in range(horizon):
            month_ref = _add_months(cohort_start, offset)
            active_members = 0
            for member_id in member_ids:
                active_months = normalized_activity.get(member_id, set())
                if _month_start(month_ref) in active_months:
                    active_members += 1
            retention = Decimal("0.00")
            if cohort_size > 0:
                retention = (
                    Decimal(active_members) / Decimal(cohort_size) * Decimal("100")
                ).quantize(Decimal("0.01"))
            points.append(
                {
                    "offset": offset,
                    "month": month_ref.isoformat(),
                    "active_members": active_members,
                    "retention_percent": str(retention),
                }
            )
        matrix[cohort_key] = {
            "cohort_size": cohort_size,
            "points": points,
        }
    return matrix


def rank_top_n(*, rows: list[dict], key: str, n: int = 10) -> list[dict]:
    limit = max(1, int(n))
    return sorted(rows, key=lambda item: item.get(key, 0), reverse=True)[:limit]
