from __future__ import annotations

import re


def classify_issue_text(text: str) -> dict[str, str]:
    content = str(text or "").lower()
    if any(token in content for token in {"loan", "repay", "interest", "default"}):
        category = "loan"
    elif any(token in content for token in {"fraud", "money", "finance", "receipt"}):
        category = "finance"
    elif any(token in content for token in {"meeting", "minutes", "attendance"}):
        category = "meeting"
    elif any(token in content for token in {"bug", "error", "system", "portal"}):
        category = "technical"
    elif any(token in content for token in {"harass", "abuse", "misconduct", "threat"}):
        category = "behavior"
    else:
        category = "other"

    priority = "medium"
    if any(token in content for token in {"urgent", "immediate", "critical", "fraud"}):
        priority = "high"
    elif any(token in content for token in {"minor", "low", "later"}):
        priority = "low"

    suggested_role = "SECRETARY"
    if category in {"finance", "loan"}:
        suggested_role = "TREASURER"
    if category in {"behavior"}:
        suggested_role = "CHAMA_ADMIN"

    return {
        "category": category,
        "priority": priority,
        "suggested_assignee_role": suggested_role,
    }


def extract_action_items_from_text(minutes_text: str) -> list[dict[str, str]]:
    content = str(minutes_text or "")
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    action_items: list[dict[str, str]] = []
    marker = re.compile(r"^(?:[-*]\s+|\d+\.\s+)(.+)$")
    owner = re.compile(r"(?:owner|assigned to)\s*:\s*([A-Za-z\s]+)", re.IGNORECASE)

    for line in lines:
        matched = marker.match(line)
        if not matched:
            continue
        item_text = matched.group(1).strip()
        owner_match = owner.search(item_text)
        assigned_to = owner_match.group(1).strip() if owner_match else ""
        action_items.append({"text": item_text, "assigned_to": assigned_to})
    return action_items


def validate_required_keys(payload: dict, required_keys: set[str]) -> bool:
    if not isinstance(payload, dict):
        return False
    missing = required_keys.difference(payload.keys())
    return not missing
