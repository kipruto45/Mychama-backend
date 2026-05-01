from __future__ import annotations

import logging
from typing import Any

from django.core.cache import cache

from apps.ai.services import AIGatewayService
from apps.billing.services import get_entitlements
from apps.chama.permissions import get_membership
from apps.chama.services import ADMIN_EQUIVALENT_ROLES, get_effective_role

logger = logging.getLogger(__name__)


class ContextResolver:
    """
    Resolve role-aware assistant context for a user, optionally scoped to a chama.

    This is used for:
    - UI personalization (suggestion chips)
    - prompt/orchestration context (role + allowed tools)
    """

    CACHE_TIMEOUT_SECONDS = 120

    def __init__(self, user, chama_id: str | None = None):
        self.user = user
        self.chama_id = str(chama_id) if chama_id else None

    def resolve(self) -> dict[str, Any]:
        cache_key = f"ai_ctx:{self.user.id}:{self.chama_id or 'none'}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        payload = self._build_context()
        cache.set(cache_key, payload, timeout=self.CACHE_TIMEOUT_SECONDS)
        return payload

    def _build_context(self) -> dict[str, Any]:
        role = "member"
        membership_role = None
        chama_payload = None
        features = {}

        if getattr(self.user, "is_staff", False) and getattr(self.user, "is_superuser", False):
            role = "system_admin"

        membership = None
        if self.chama_id:
            membership = get_membership(self.user, self.chama_id)
            if membership:
                membership_role = get_effective_role(self.user, self.chama_id, membership)
                chama_payload = {"id": str(membership.chama_id), "name": membership.chama.name}
                features = get_entitlements(membership.chama)
                if membership_role in ADMIN_EQUIVALENT_ROLES:
                    role = "chama_admin"

        allowed_tools: list[str] = []
        if membership_role:
            allowed_tools = [t.get("name") for t in AIGatewayService._responses_tools_for_user(membership_role=membership_role)]

        return {
            "user_id": str(self.user.id),
            "full_name": getattr(self.user, "full_name", "") or "",
            "phone": getattr(self.user, "phone", "") or "",
            "role": role,
            "membership_role": membership_role or (membership.role if membership else None),
            "chama": chama_payload,
            "features": features,
            "allowed_tools": allowed_tools,
        }

    def clear_cache(self) -> None:
        cache_key = f"ai_ctx:{self.user.id}:{self.chama_id or 'none'}"
        cache.delete(cache_key)
