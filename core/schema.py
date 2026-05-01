from __future__ import annotations

from collections.abc import Mapping

from drf_spectacular.utils import inline_serializer
from rest_framework import serializers


def _coerce_field(field):
    if isinstance(field, serializers.BaseSerializer):
        return field
    if isinstance(field, type) and issubclass(field, serializers.Serializer):
        return field()
    return field


def success_response_serializer(
    *,
    name: str,
    data=None,
    include_message: bool = False,
    meta=None,
    extra_fields: Mapping[str, object] | None = None,
):
    fields: dict[str, object] = {
        "success": serializers.BooleanField(default=True),
    }
    if data is not None:
        fields["data"] = _coerce_field(data)
    if include_message:
        fields["message"] = serializers.CharField(required=False, allow_blank=True)
    if meta is not None:
        fields["meta"] = _coerce_field(meta)
    if extra_fields:
        for key, value in extra_fields.items():
            fields[key] = _coerce_field(value)
    return inline_serializer(name=name, fields=fields)


def error_response_serializer(*, name: str):
    return inline_serializer(
        name=name,
        fields={
            "success": serializers.BooleanField(default=False),
            "message": serializers.CharField(),
            "errors": serializers.DictField(required=False),
        },
    )
