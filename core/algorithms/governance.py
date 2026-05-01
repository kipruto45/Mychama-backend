from __future__ import annotations

from math import ceil


def is_valid_transition(
    *,
    current: str,
    target: str,
    allowed_map: dict[str, set[str]],
) -> bool:
    if current == target:
        return True
    return target in allowed_map.get(current, set())


def quorum_required(
    *,
    total_members: int,
    quorum_percentage: int,
    minimum_votes: int = 1,
) -> int:
    if total_members <= 0:
        return 0
    ratio = max(0, min(100, int(quorum_percentage))) / 100
    required = int(ceil(total_members * ratio))
    return max(minimum_votes, required)


def consensus_reached(
    *,
    approvals: int,
    total_required: int,
) -> bool:
    return int(approvals) >= max(1, int(total_required))
