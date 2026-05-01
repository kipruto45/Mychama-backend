from __future__ import annotations

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

from apps.chama.models import Invite
from apps.chama.serializers import InviteTokenLookupSerializer


def _invite_state(status: str | None, is_valid: bool) -> str:
    normalized = str(status or "").strip().lower()
    if is_valid:
        return "ready"
    if normalized in {"expired", "revoked", "accepted", "declined", "rejected", "cancelled"}:
        return normalized
    return "invalid"


def _invite_copy(state: str) -> tuple[str, str]:
    copy_map = {
        "ready": (
            "You've been invited to join this chama.",
            "Open the MyChama app to review the invite, sign in or create an account, and continue securely.",
        ),
        "expired": (
            "This invite has expired.",
            "Ask the chama admin to send a new invite or share a fresh join code.",
        ),
        "revoked": (
            "This invite is no longer valid.",
            "The chama admin has withdrawn it. Ask for a new invite if you still want to join.",
        ),
        "accepted": (
            "This invite has already been used.",
            "If you already joined, open the app and sign in. Otherwise, ask the chama admin for a new invite.",
        ),
        "declined": (
            "This invite was declined.",
            "You can still join later with a fresh invite or join code from the chama admin.",
        ),
        "rejected": (
            "This invite is no longer available.",
            "Please ask the chama admin for a new invite.",
        ),
        "cancelled": (
            "This invite is no longer available.",
            "Please ask the chama admin for a new invite.",
        ),
        "invalid": (
            "This invite could not be opened.",
            "Check the link or ask the chama admin for a new invite or code.",
        ),
    }
    return copy_map.get(state, copy_map["invalid"])


def _browser_platform(user_agent: str) -> str:
    normalized = str(user_agent or "").lower()
    if "iphone" in normalized or "ipad" in normalized or "ipod" in normalized:
        return "ios"
    if "android" in normalized:
        return "android"
    return "web"


def _invite_target_hint(payload: dict) -> str | None:
    invitee_phone = str(payload.get("invitee_phone") or "").strip()
    invitee_email = str(payload.get("invitee_email") or "").strip()
    if invitee_phone:
        return invitee_phone
    if invitee_email:
        return invitee_email
    return None


def _landing_base_context(request) -> dict:
    play_store_url = getattr(settings, "PLAY_STORE_URL", "").strip()
    app_store_url = getattr(settings, "APP_STORE_URL", "").strip()
    deep_link_scheme = getattr(settings, "DEEP_LINK_SCHEME", "mychama")
    site_url = getattr(settings, "SITE_URL", "https://mychama.app").rstrip("/")
    return {
        "site_url": site_url,
        "play_store_url": play_store_url,
        "app_store_url": app_store_url,
        "deep_link_scheme": deep_link_scheme,
        "platform": _browser_platform(request.META.get("HTTP_USER_AGENT", "")),
        "now": timezone.now(),
    }


def _invite_landing_context(*, request, payload: dict | None, entry_mode: str, token: str | None = None, code: str | None = None) -> dict:
    base_context = _landing_base_context(request)
    state = _invite_state(payload.get("status") if payload else None, bool(payload and payload.get("is_valid")))
    headline, subheadline = _invite_copy(state)
    invite_code = str(payload.get("code") if payload else code or "").strip()
    token_value = str(payload.get("token") if payload else token or "").strip()
    app_target_url = (
        f"{base_context['deep_link_scheme']}://invite/{token_value}"
        if entry_mode == "link" and token_value
        else f"{base_context['deep_link_scheme']}://join/code/{invite_code}"
        if invite_code
        else f"{base_context['deep_link_scheme']}://welcome"
    )
    web_continue_url = (
        f"{base_context['site_url']}/join/code/{invite_code}" if invite_code else f"{base_context['site_url']}/join/code/"
    )

    return {
        **base_context,
        "entry_mode": entry_mode,
        "invite": payload,
        "invite_code": invite_code,
        "invite_token": token_value,
        "app_target_url": app_target_url,
        "web_continue_url": web_continue_url,
        "state": state,
        "headline": headline,
        "subheadline": subheadline,
        "target_hint": _invite_target_hint(payload or {}),
    }


@require_GET
@cache_control(public=True, max_age=60)
def invite_landing_view(request, token: str):
    invite = Invite.resolve_presented_token(
        token,
        queryset=Invite.objects.select_related("chama", "invited_by"),
    )
    payload = InviteTokenLookupSerializer(invite).data if invite else None
    context = _invite_landing_context(
        request=request,
        payload=payload,
        entry_mode="link",
        token=token,
        code=payload.get("code") if payload else None,
    )
    return render(
        request,
        "invites/landing.html",
        context=context,
        status=200 if context["state"] == "ready" else 404,
    )


@require_GET
@cache_control(public=True, max_age=60)
def join_code_landing_view(request, code: str | None = None):
    invite_code = str(code or request.GET.get("code") or "").strip().upper()
    invite = (
        Invite.resolve_code(
            invite_code,
            queryset=Invite.objects.select_related("chama", "invited_by"),
        )
        if invite_code
        else None
    )
    payload = InviteTokenLookupSerializer(invite).data if invite else None
    context = _invite_landing_context(
        request=request,
        payload=payload,
        entry_mode="code",
        code=invite_code,
    )
    context["allow_code_entry"] = True
    if not invite_code:
        context["state"] = "ready"
        context["headline"] = "Enter your invite code in MyChama."
        context["subheadline"] = (
            "Open the app, choose Join with Code, then enter the code shared by the chama admin."
        )
    return render(
        request,
        "invites/landing.html",
        context=context,
        status=200 if invite_code and context["state"] == "ready" else 404 if invite_code else 200,
    )


@require_GET
@cache_control(public=True, max_age=300)
def assetlinks_view(request):
    package_name = getattr(settings, "ANDROID_APPLICATION_ID", "com.mychama.app")
    fingerprints = getattr(settings, "ANDROID_SHA256_CERT_FINGERPRINTS", [])
    if isinstance(fingerprints, str):
        fingerprints = [value.strip() for value in fingerprints.split(",") if value.strip()]

    payload = []
    if fingerprints:
        payload.append(
            {
                "relation": ["delegate_permission/common.handle_all_urls"],
                "target": {
                    "namespace": "android_app",
                    "package_name": package_name,
                    "sha256_cert_fingerprints": fingerprints,
                },
            }
        )

    return JsonResponse(payload, safe=False)


@require_GET
@cache_control(public=True, max_age=300)
def apple_app_site_association_view(request):
    associated_app_id = getattr(settings, "IOS_ASSOCIATED_APP_ID", "").strip()
    details = []
    if associated_app_id:
        details.append(
            {
                "appIDs": [associated_app_id],
                "components": [
                    {"/": "/invite/*"},
                    {"/": "/join/code/*"},
                    {"/": "/join/code/"},
                ],
            }
        )

    payload = {
        "applinks": {
            "apps": [],
            "details": details,
        }
    }
    return JsonResponse(payload)
