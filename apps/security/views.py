from __future__ import annotations

import csv
import json
import uuid

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chama.permissions import get_membership
from apps.security.models import AuditLog, DeviceSession
from apps.security.permissions import IsSecurityAuditReader
from apps.security.serializers import (
    AuditFilterSerializer,
    DeviceSessionSerializer,
    RevokeSessionSerializer,
)
from apps.security.services import SecurityService
from core.models import AuditLog as CoreAuditLog


def _build_scoped_audit_rows(
    *, request, chama_id: str | None, action_type: str
) -> list[dict]:
    security_queryset = AuditLog.objects.select_related("actor", "chama")
    core_queryset = CoreAuditLog.objects.select_related("actor")

    if chama_id:
        if not get_membership(request.user, chama_id) and not request.user.is_superuser:
            raise PermissionError("You are not authorized for this chama.")
        security_queryset = security_queryset.filter(chama_id=chama_id)
        core_queryset = core_queryset.filter(chama_id=chama_id)

    if action_type:
        security_queryset = security_queryset.filter(action_type=action_type)
        core_queryset = core_queryset.filter(action=action_type)

    security_rows = [
        {
            "id": str(item.id),
            "source": "security",
            "chama": str(item.chama_id) if item.chama_id else None,
            "actor": str(item.actor_id) if item.actor_id else None,
            "action_type": item.action_type,
            "target_type": item.target_type,
            "target_id": item.target_id,
            "metadata": item.metadata,
            "ip_address": item.ip_address,
            "created_at": timezone.localtime(item.created_at).isoformat(),
        }
        for item in security_queryset.order_by("-created_at")[:500]
    ]

    core_rows = [
        {
            "id": str(item.id),
            "source": "core",
            "chama": str(item.chama_id) if item.chama_id else None,
            "actor": str(item.actor_id) if item.actor_id else None,
            "action_type": item.action,
            "target_type": item.entity_type,
            "target_id": str(item.entity_id) if item.entity_id else "",
            "metadata": item.metadata,
            "ip_address": None,
            "created_at": timezone.localtime(item.created_at).isoformat(),
        }
        for item in core_queryset.order_by("-created_at")[:500]
    ]

    rows = sorted(
        [*security_rows, *core_rows],
        key=lambda row: row["created_at"],
        reverse=True,
    )
    return rows[:500]


class SecurityScopeMixin:
    def _parse_uuid(self, raw_value: str | None, field_name: str):
        if raw_value in [None, ""]:
            return None
        try:
            return str(uuid.UUID(str(raw_value)))
        except (TypeError, ValueError) as exc:
            raise ValidationError({field_name: "Invalid UUID."}) from exc

    def get_scoped_chama_id(self, *, required: bool = False):
        values = [
            self._parse_uuid(self.request.query_params.get("chama_id"), "chama_id"),
            self._parse_uuid(self.request.headers.get("X-CHAMA-ID"), "X-CHAMA-ID"),
        ]
        values = [value for value in values if value]

        resolved = None
        for value in values:
            if resolved and resolved != value:
                raise ValidationError({"chama_id": "Conflicting chama scope values."})
            resolved = value

        if required and not resolved:
            raise ValidationError({"chama_id": "chama_id is required."})
        return resolved


class DeviceSessionListView(SecurityScopeMixin, generics.ListAPIView):
    serializer_class = DeviceSessionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = DeviceSession.objects.filter(user=self.request.user).order_by(
            "-last_seen"
        )
        chama_id = self.get_scoped_chama_id(required=False)
        if chama_id:
            if not get_membership(self.request.user, chama_id):
                raise ValidationError(
                    {"detail": "You are not an approved active member in this chama."}
                )
            queryset = queryset.filter(chama_id=chama_id)
        return queryset


class DeviceSessionRevokeView(SecurityScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id):
        serializer = RevokeSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        session = get_object_or_404(DeviceSession, id=id, user=request.user)
        SecurityService.revoke_session(session=session)
        SecurityService.create_audit_log(
            action_type="REVOKE_SESSION",
            target_type="DeviceSession",
            target_id=str(session.id),
            actor=request.user,
            chama=session.chama,
            metadata={"reason": serializer.validated_data.get("reason", "")},
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        return Response(
            {"detail": "Session revoked successfully."},
            status=status.HTTP_200_OK,
        )


class DeviceSessionRevokeAllView(SecurityScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        current_session_key = str(request.data.get("current_session_key", "")).strip()
        revoked = SecurityService.revoke_all_sessions(
            user=request.user,
            except_session_key=current_session_key or None,
        )
        SecurityService.create_audit_log(
            action_type="REVOKE_ALL_SESSIONS",
            target_type="User",
            target_id=str(request.user.id),
            actor=request.user,
            metadata={"revoked_count": revoked},
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        return Response(
            {"detail": "All sessions revoked.", "revoked": revoked},
            status=status.HTTP_200_OK,
        )


class SecurityAuditLogListView(SecurityScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsSecurityAuditReader]

    def get(self, request):
        filter_serializer = AuditFilterSerializer(data=request.query_params)
        filter_serializer.is_valid(raise_exception=True)

        chama_id = (
            str(filter_serializer.validated_data["chama_id"])
            if filter_serializer.validated_data.get("chama_id")
            else self.get_scoped_chama_id(required=False)
        )
        action_type = (
            filter_serializer.validated_data.get("action_type") or ""
        ).strip()
        try:
            rows = _build_scoped_audit_rows(
                request=request,
                chama_id=chama_id,
                action_type=action_type,
            )
        except PermissionError:
            return Response(
                {"detail": "You are not authorized for this chama."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response({"count": len(rows), "results": rows})


class SecurityAuditLogExportView(SecurityScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsSecurityAuditReader]

    def get(self, request):
        filter_serializer = AuditFilterSerializer(data=request.query_params)
        filter_serializer.is_valid(raise_exception=True)

        chama_id = (
            str(filter_serializer.validated_data["chama_id"])
            if filter_serializer.validated_data.get("chama_id")
            else self.get_scoped_chama_id(required=False)
        )
        action_type = (
            filter_serializer.validated_data.get("action_type") or ""
        ).strip()
        try:
            rows = _build_scoped_audit_rows(
                request=request,
                chama_id=chama_id,
                action_type=action_type,
            )
        except PermissionError:
            return Response(
                {"detail": "You are not authorized for this chama."},
                status=status.HTTP_403_FORBIDDEN,
            )

        scope = chama_id or "all"
        filename = f"audit-log-{scope}-{timezone.localdate().isoformat()}.csv"
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'

        writer = csv.writer(response)
        writer.writerow(
            [
                "id",
                "source",
                "chama",
                "actor",
                "action_type",
                "target_type",
                "target_id",
                "ip_address",
                "metadata",
                "created_at",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row["id"],
                    row["source"],
                    row["chama"],
                    row["actor"],
                    row["action_type"],
                    row["target_type"],
                    row["target_id"],
                    row["ip_address"],
                    json.dumps(row["metadata"], ensure_ascii=True, default=str),
                    row["created_at"],
                ]
            )
        return response
