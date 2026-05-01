"""Smart chama endpoints backed by real finance, governance, and workflow data."""

from __future__ import annotations

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.chama.models import Chama
from apps.chama.services import get_effective_role
from apps.chama.smart_features import (
    answer_ai_question,
    build_admin_action_center,
    build_smart_dashboard,
    get_active_membership_for_chama,
)
from core.algorithms.smart_ai_assistant import get_quick_prompts
from core.permissions import IsChamaMember


def _get_chama_or_404(chama_id):
    try:
        return Chama.objects.get(id=chama_id)
    except Chama.DoesNotExist:
        return None


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsChamaMember])
def smart_dashboard(request, chama_id):
    chama = _get_chama_or_404(chama_id)
    if not chama:
        return Response({"error": "Chama not found"}, status=status.HTTP_404_NOT_FOUND)
    payload = build_smart_dashboard(request.user, chama)
    return Response(payload)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsChamaMember])
def admin_action_center(request, chama_id):
    chama = _get_chama_or_404(chama_id)
    if not chama:
        return Response({"error": "Chama not found"}, status=status.HTTP_404_NOT_FOUND)
    payload = build_admin_action_center(request.user, chama)
    return Response(payload)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsChamaMember])
def ai_assistant_query(request, chama_id):
    chama = _get_chama_or_404(chama_id)
    if not chama:
        return Response({"error": "Chama not found"}, status=status.HTTP_404_NOT_FOUND)
    query = str(request.data.get("query") or "").strip()
    if not query:
        return Response({"error": "Query is required"}, status=status.HTTP_400_BAD_REQUEST)
    payload = answer_ai_question(request.user, chama, query)
    return Response(payload)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsChamaMember])
def ai_quick_prompts(request, chama_id):
    chama = _get_chama_or_404(chama_id)
    if not chama:
        return Response({"error": "Chama not found"}, status=status.HTTP_404_NOT_FOUND)
    membership = get_active_membership_for_chama(request.user, chama.id)
    effective_role = get_effective_role(request.user, chama.id, membership) or membership.role
    return Response({"prompts": get_quick_prompts(effective_role)})


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsChamaMember])
def chama_health_score(request, chama_id):
    chama = _get_chama_or_404(chama_id)
    if not chama:
        return Response({"error": "Chama not found"}, status=status.HTTP_404_NOT_FOUND)
    payload = build_smart_dashboard(request.user, chama)
    return Response(payload["health_score"])
