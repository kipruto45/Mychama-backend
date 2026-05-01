from __future__ import annotations

import inspect
import re

from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.openapi import AutoSchema
from drf_spectacular.plumbing import build_serializer_context
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from rest_framework.views import APIView


class SchemaFallbackSerializer(serializers.Serializer):
    """Schema-only fallback for legacy APIViews without explicit serializers."""


class MyChamaJWTAuthenticationExtension(OpenApiAuthenticationExtension):
    """Expose MyChama JWT auth as a standard bearer/JWT scheme."""

    target_class = "core.authentication.MyChamaJWTAuthentication"
    name = "BearerAuth"
    match_subclasses = True
    priority = 1

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": (
                "JWT bearer token using the Authorization header. "
                "Format: `Bearer <access_token>`."
            ),
        }


class MyChamaAutoSchema(AutoSchema):
    """Schema defaults for legacy APIViews and stable operation ids."""

    _path_parameter_pattern = re.compile(r"^\{(?P<name>[^}]+)\}$")

    def _get_serializer(self):
        view = self.view
        context = build_serializer_context(view)

        try:
            if isinstance(view, GenericAPIView):
                if view.__class__.get_serializer == GenericAPIView.get_serializer:
                    serializer_class = view.get_serializer_class()
                    if serializer_class is None:
                        return SchemaFallbackSerializer(context=context)
                    return serializer_class(context=context)
                serializer = view.get_serializer(context=context)
                return serializer or SchemaFallbackSerializer(context=context)

            if isinstance(view, APIView):
                if callable(getattr(view, "get_serializer", None)):
                    serializer = view.get_serializer(context=context)
                    return serializer or SchemaFallbackSerializer(context=context)
                if callable(getattr(view, "get_serializer_class", None)):
                    serializer_class = view.get_serializer_class()
                    if serializer_class is None:
                        return SchemaFallbackSerializer(context=context)
                    return serializer_class(context=context)
                serializer_class = getattr(view, "serializer_class", None)
                if serializer_class is None:
                    return SchemaFallbackSerializer(context=context)
                if inspect.isclass(serializer_class) and issubclass(
                    serializer_class, serializers.Serializer
                ):
                    return serializer_class(context=context)
                return serializer_class
        except Exception:
            return SchemaFallbackSerializer(context=context)

        return SchemaFallbackSerializer(context=context)

    def get_operation_id(self) -> str:
        override = getattr(self, "overrides", {}).get("operation_id")
        if override:
            return override

        tokens = self._tokenize_operation_path()
        action = self._resolve_action()
        return "_".join([action, *tokens])

    def _tokenize_operation_path(self) -> list[str]:
        raw_path = re.sub(r"^/api/v[0-9]+/?", "", self.path)
        segments = [segment for segment in raw_path.strip("/").split("/") if segment]
        tokens: list[str] = []

        for segment in segments:
            param_match = self._path_parameter_pattern.match(segment)
            if param_match:
                param_name = param_match.group("name").replace("-", "_")
                tokens.extend(["by", param_name])
                continue

            normalized = segment.replace("-", "_")
            tokens.extend([token for token in normalized.split("_") if token])

        return tokens or ["root"]

    def _resolve_action(self) -> str:
        method = self.method.upper()
        segments = [segment for segment in self.path.strip("/").split("/") if segment]
        last_segment = segments[-1] if segments else ""
        last_is_parameter = bool(self._path_parameter_pattern.match(last_segment))

        if method == "GET":
            return "retrieve" if last_is_parameter else "list"
        if method == "POST":
            if not last_is_parameter and last_segment not in {"api", "v1"}:
                normalized = last_segment.replace("-", "_")
                if normalized and normalized not in {"payments", "notifications", "reports"}:
                    return normalized
            return "create"
        if method == "PATCH":
            return "partial_update"
        if method == "PUT":
            return "update"
        if method == "DELETE":
            return "destroy" if last_is_parameter else "delete"
        return self.method_mapping[method.lower()]


def deduplicate_schema_endpoints(endpoints):
    """
    Remove duplicate trailing-slash aliases and duplicate method/path registrations.

    The project exposes several legacy/canonical route aliases that point at the same
    runtime handler. Keeping both in the schema produces noisy operationId collisions
    without adding useful documentation value.
    """

    deduplicated = []
    seen: set[tuple[str, str]] = set()
    seen_callbacks: set[tuple[str, object]] = set()

    def freeze(value):
        if isinstance(value, dict):
            return tuple(sorted((key, freeze(item)) for key, item in value.items()))
        if isinstance(value, (list, tuple, set)):
            return tuple(freeze(item) for item in value)
        try:
            hash(value)
            return value
        except TypeError:
            return repr(value)

    def callback_signature(callback):
        callback_cls = getattr(callback, "cls", None)
        if callback_cls is None:
            return None
        actions = freeze(getattr(callback, "actions", None) or {})
        initkwargs = freeze(getattr(callback, "initkwargs", None) or {})
        return callback_cls, actions, initkwargs

    for path, path_regex, method, callback in endpoints:
        normalized_path = path.rstrip("/") or "/"
        key = (normalized_path, method.upper())
        if key in seen:
            continue
        signature = callback_signature(callback)
        if signature is not None:
            callback_key = (method.upper(), signature)
            if callback_key in seen_callbacks:
                continue
            seen_callbacks.add(callback_key)
        seen.add(key)
        deduplicated.append((path, path_regex, method, callback))

    return deduplicated
