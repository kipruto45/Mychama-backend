from __future__ import annotations

import csv
import json
import logging
import uuid
from collections import Counter
from datetime import timedelta

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import generics, permissions, serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chama.permissions import get_membership
from apps.security.models import AuditLog, DeviceSession, TrustedDevice
from apps.security.permissions import IsSecurityAuditReader
from apps.security.pin_service import PinService, PinType, StepUpAuthService
from apps.security.rbac import build_role_catalog, build_user_access_snapshot
from apps.security.serializers import (
    AuditFilterSerializer,
    DeviceSessionSerializer,
    PinSetSerializer,
    PinStatusSerializer,
    PinVerifySerializer,
    RBACRoleSerializer,
    RevokeSessionSerializer,
    TrustedDeviceCreateSerializer,
    TrustedDeviceSerializer,
    UserAccessSerializer,
)
from apps.security.services import SecurityService
from core.api_response import error_response, success_response
from core.models import AuditLog as CoreAuditLog
from core.schema import error_response_serializer, success_response_serializer

logger = logging.getLogger(__name__)
SCHEMA_CHAMA_ID = "00000000-0000-0000-0000-000000000000"

simple_count_response = inline_serializer(
    name="SecurityCountPayload",
    fields={"revoked": serializers.IntegerField()},
)
pin_verify_payload = inline_serializer(
    name="PinVerifyPayload",
    fields={
        "verified": serializers.BooleanField(),
        "step_up_required": serializers.BooleanField(),
    },
)
trusted_device_check_payload = inline_serializer(
    name="TrustedDeviceCheckPayload",
    fields={
        "trusted": serializers.BooleanField(),
        "device": TrustedDeviceSerializer(required=False, allow_null=True),
    },
)
security_audit_row = inline_serializer(
    name="SecurityAuditRow",
    fields={
        "id": serializers.CharField(),
        "source": serializers.CharField(),
        "chama": serializers.CharField(allow_null=True),
        "actor": serializers.CharField(allow_null=True),
        "action_type": serializers.CharField(),
        "target_type": serializers.CharField(allow_blank=True),
        "target_id": serializers.CharField(allow_blank=True),
        "metadata": serializers.JSONField(),
        "ip_address": serializers.CharField(allow_null=True),
        "created_at": serializers.DateTimeField(),
    },
)
security_audit_list_payload = inline_serializer(
    name="SecurityAuditListPayload",
    fields={
        "count": serializers.IntegerField(),
        "results": serializers.ListField(child=serializers.JSONField()),
    },
)
fingerprint_query_serializer = inline_serializer(
    name="TrustedDeviceCheckQuery",
    fields={"fingerprint": serializers.CharField()},
)
api_inventory_item = inline_serializer(
    name="ApiInventoryItem",
    fields={
        "path": serializers.CharField(),
        "method": serializers.CharField(),
        "operation_id": serializers.CharField(allow_blank=True),
        "tags": serializers.ListField(child=serializers.CharField(), required=False),
        "auth_required": serializers.BooleanField(),
    },
)
api_inventory_payload = inline_serializer(
    name="ApiInventoryPayload",
    fields={
        "generated_at": serializers.DateTimeField(),
        "count": serializers.IntegerField(),
        "public_count": serializers.IntegerField(),
        "protected_count": serializers.IntegerField(),
        "tag_counts": serializers.DictField(child=serializers.IntegerField()),
        "items": serializers.ListField(child=serializers.JSONField()),
    },
)


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
        if getattr(self, "swagger_fake_view", False):
            return SCHEMA_CHAMA_ID if required else None
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


class ApiInventoryView(APIView):
    permission_classes = [permissions.IsAdminUser]
    serializer_class = api_inventory_payload

    @extend_schema(
        tags=["Security"],
        operation_id="get_api_inventory",
        responses={200: api_inventory_payload},
    )
    def get(self, request, *args, **kwargs):
        generator = SchemaGenerator()
        schema = generator.get_schema(request=request, public=True) or {}

        items: list[dict] = []
        tag_counter: Counter[str] = Counter()
        public_count = 0
        protected_count = 0

        paths = schema.get("paths") or {}
        for path, operations in paths.items():
            if not isinstance(operations, dict):
                continue
            for method, operation in operations.items():
                method_upper = str(method).upper()
                if method_upper not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                    continue
                operation = operation or {}
                operation_id = str(operation.get("operationId") or "")
                tags = operation.get("tags") or []
                if isinstance(tags, list):
                    tag_counter.update(str(tag) for tag in tags if str(tag))
                security = operation.get("security")
                auth_required = security is None or bool(security)
                if auth_required:
                    protected_count += 1
                else:
                    public_count += 1

                items.append(
                    {
                        "path": str(path),
                        "method": method_upper,
                        "operation_id": operation_id,
                        "tags": [str(tag) for tag in (tags or []) if str(tag)],
                        "auth_required": bool(auth_required),
                    }
                )

        items.sort(key=lambda item: (item["path"], item["method"]))
        return success_response(
            data={
                "generated_at": timezone.now(),
                "count": len(items),
                "public_count": public_count,
                "protected_count": protected_count,
                "tag_counts": dict(tag_counter),
                "items": items,
            }
        )


class DeviceSessionListView(SecurityScopeMixin, generics.ListAPIView):
    serializer_class = DeviceSessionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return DeviceSession.objects.none()
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
    serializer_class = RevokeSessionSerializer

    @extend_schema(
        tags=["Security"],
        operation_id="revoke_device_session",
        request=RevokeSessionSerializer,
        responses={
            200: success_response_serializer(
                name="RevokeDeviceSessionResponse",
                include_message=True,
            ),
            400: error_response_serializer(name="RevokeDeviceSessionError"),
        },
    )
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
        return success_response(message="Session revoked successfully.")


class DeviceSessionRevokeAllView(SecurityScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = inline_serializer(
        name="RevokeAllSessionsRequest",
        fields={
            "current_session_key": serializers.CharField(
                required=False,
                allow_blank=True,
            )
        },
    )

    @extend_schema(
        tags=["Security"],
        operation_id="revoke_all_device_sessions",
        request=serializer_class,
        responses={
            200: success_response_serializer(
                name="RevokeAllDeviceSessionsResponse",
                data=simple_count_response,
                include_message=True,
            ),
        },
    )
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
        return success_response(
            data={"revoked": revoked},
            message="All sessions revoked.",
        )


class TrustedDeviceListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return TrustedDeviceCreateSerializer
        return TrustedDeviceSerializer

    @extend_schema(
        tags=["Security"],
        operation_id="list_trusted_devices",
        responses={200: TrustedDeviceSerializer(many=True)},
    )
    def get(self, request):
        devices = TrustedDevice.objects.filter(user=request.user).order_by("-last_used_at")
        return Response(TrustedDeviceSerializer(devices, many=True).data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Security"],
        operation_id="create_trusted_device",
        request=TrustedDeviceCreateSerializer,
        responses={200: TrustedDeviceSerializer, 201: TrustedDeviceSerializer},
    )
    def post(self, request):
        serializer = TrustedDeviceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        now = timezone.now()
        device, _created = TrustedDevice.objects.update_or_create(
            user=request.user,
            fingerprint=serializer.validated_data["fingerprint"],
            defaults={
                "device_name": serializer.validated_data.get("device_name", ""),
                "device_type": serializer.validated_data.get("device_type", "unknown"),
                "user_agent": serializer.validated_data.get("user_agent", ""),
                "ip_address": request.META.get("REMOTE_ADDR"),
                "is_trusted": True,
                "trusted_at": now,
                "expires_at": now + timedelta(days=90),
            },
        )
        SecurityService.record_security_event(
            user=request.user,
            event_type="device_trusted",
            description="Device marked as trusted",
            metadata={"fingerprint": device.fingerprint, "device_name": device.device_name},
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        return Response(TrustedDeviceSerializer(device).data, status=status.HTTP_200_OK)


class TrustedDeviceDeleteView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = TrustedDeviceSerializer

    @extend_schema(
        tags=["Security"],
        operation_id="revoke_trusted_device",
        responses={204: OpenApiResponse(description="Trusted device revoked.")},
    )
    def delete(self, request, id):
        device = get_object_or_404(TrustedDevice, id=id, user=request.user)
        device.is_trusted = False
        device.expires_at = timezone.now()
        device.save(update_fields=["is_trusted", "expires_at", "last_used_at"])
        SecurityService.record_security_event(
            user=request.user,
            event_type="device_revoked",
            description="Trusted device revoked",
            metadata={"fingerprint": device.fingerprint, "device_name": device.device_name},
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class TrustedDeviceRevokeAllView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = simple_count_response

    @extend_schema(
        tags=["Security"],
        operation_id="revoke_all_trusted_devices",
        responses={
            200: success_response_serializer(
                name="RevokeAllTrustedDevicesResponse",
                data=simple_count_response,
                include_message=True,
            )
        },
    )
    def post(self, request):
        now = timezone.now()
        revoked = TrustedDevice.objects.filter(user=request.user, is_trusted=True).update(
            is_trusted=False,
            expires_at=now,
            last_used_at=now,
        )
        SecurityService.record_security_event(
            user=request.user,
            event_type="device_revoked",
            description="All trusted devices revoked",
            metadata={"revoked_count": revoked},
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        return success_response(data={"revoked": revoked}, message="Trusted devices revoked.")


class TrustedDeviceCheckView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = trusted_device_check_payload

    @extend_schema(
        tags=["Security"],
        operation_id="check_trusted_device",
        parameters=[fingerprint_query_serializer],
        responses={200: trusted_device_check_payload},
    )
    def get(self, request):
        fingerprint = str(request.query_params.get("fingerprint", "")).strip()
        if not fingerprint:
            raise ValidationError({"fingerprint": "fingerprint is required."})
        device = TrustedDevice.objects.filter(
            user=request.user,
            fingerprint=fingerprint,
        ).first()
        return Response(
            {
                "trusted": bool(device and device.is_active_trusted),
                "device": TrustedDeviceSerializer(device).data if device else None,
            },
            status=status.HTTP_200_OK,
        )


class PinStatusView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PinStatusSerializer

    @extend_schema(
        tags=["Security"],
        operation_id="get_pin_status",
        responses={
            200: success_response_serializer(
                name="PinStatusResponse",
                data=PinStatusSerializer,
            )
        },
    )
    def get(self, request):
        payload = {
            "has_transaction_pin": PinService.has_pin(
                request.user,
                PinType.TRANSACTION,
            ),
            "has_withdrawal_pin": PinService.has_pin(
                request.user,
                PinType.WITHDRAWAL,
            ),
            "withdrawal_pin_required": bool(
                getattr(settings, "WITHDRAWAL_PIN_REQUIRED", True)
            ),
        }
        return success_response(data=PinStatusSerializer(payload).data)


class PinSetView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PinSetSerializer

    @extend_schema(
        tags=["Security"],
        operation_id="set_or_change_pin",
        request=PinSetSerializer,
        responses={
            200: success_response_serializer(
                name="PinSetResponse",
                data=inline_serializer(
                    name="PinSetPayload",
                    fields={"pin_type": serializers.CharField()},
                ),
                include_message=True,
            ),
            400: error_response_serializer(name="PinSetError"),
        },
    )
    def post(self, request):
        serializer = PinSetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        pin_type = PinType[serializer.validated_data["pin_type"].upper()]
        has_existing_pin = PinService.has_pin(request.user, pin_type)

        if has_existing_pin:
            current_pin = serializer.validated_data.get("current_pin", "")
            if not current_pin:
                return error_response(
                    "Current PIN is required to change an existing PIN.",
                    code="CURRENT_PIN_REQUIRED",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            changed, message = PinService.change_pin(
                request.user,
                current_pin,
                serializer.validated_data["pin"],
                pin_type,
            )
            if not changed:
                return error_response(
                    message,
                    code="PIN_CHANGE_FAILED",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            action_type = "PIN_CHANGED"
            success_message = message
        else:
            PinService.set_pin(
                request.user,
                serializer.validated_data["pin"],
                pin_type,
            )
            action_type = "PIN_SET"
            success_message = "PIN set successfully."

        SecurityService.create_audit_log(
            action_type=action_type,
            target_type="User",
            target_id=str(request.user.id),
            actor=request.user,
            metadata={"pin_type": serializer.validated_data["pin_type"]},
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        return success_response(
            data={"pin_type": serializer.validated_data["pin_type"]},
            message=success_message,
        )


class PinVerifyView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PinVerifySerializer

    @extend_schema(
        tags=["Security"],
        operation_id="verify_pin",
        request=PinVerifySerializer,
        responses={
            200: success_response_serializer(
                name="PinVerifyResponse",
                data=pin_verify_payload,
                include_message=True,
            ),
            400: error_response_serializer(name="PinVerifyError"),
        },
    )
    def post(self, request):
        serializer = PinVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        pin_type = PinType[serializer.validated_data["pin_type"].upper()]
        action = serializer.validated_data.get("action", "").strip()
        risk_score = serializer.validated_data.get("risk_score", 0)

        if action:
            requires_step_up, _reason = StepUpAuthService.requires_step_up(
                request.user,
                action=action,
                risk_score=risk_score,
            )
            if not requires_step_up:
                return success_response(
                    data={"verified": True, "step_up_required": False},
                    message="Step-up verification not required.",
                )

        verified, message = PinService.verify_pin(
            request.user,
            serializer.validated_data["pin"],
            pin_type,
        )
        if not verified:
            code = "PIN_INVALID"
            lowered = message.lower()
            if "frozen" in lowered:
                code = "ACCOUNT_FROZEN"
            elif "locked" in lowered:
                code = "PIN_LOCKED"
            return error_response(
                message,
                code=code,
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        SecurityService.create_audit_log(
            action_type="PIN_VERIFIED",
            target_type="User",
            target_id=str(request.user.id),
            actor=request.user,
            metadata={
                "pin_type": serializer.validated_data["pin_type"],
                "action": action,
                "risk_score": risk_score,
            },
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        return success_response(
            data={"verified": True, "step_up_required": bool(action)},
            message="PIN verified successfully.",
        )


class SecurityAuditLogListView(SecurityScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsSecurityAuditReader]
    serializer_class = AuditFilterSerializer

    @extend_schema(
        tags=["Security"],
        operation_id="list_security_audit_logs",
        parameters=[AuditFilterSerializer],
        responses={
            200: success_response_serializer(
                name="SecurityAuditLogListResponse",
                data=security_audit_list_payload,
            ),
            403: error_response_serializer(name="SecurityAuditLogListError"),
        },
    )
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
                {
                    "success": False,
                    "message": "You are not authorized for this chama.",
                    "errors": {},
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        return success_response(data={"count": len(rows), "results": rows})


class SecurityAuditLogExportView(SecurityScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsSecurityAuditReader]
    serializer_class = AuditFilterSerializer

    @extend_schema(
        tags=["Security"],
        operation_id="export_security_audit_logs",
        parameters=[AuditFilterSerializer],
        responses={
            200: OpenApiResponse(
                response=OpenApiTypes.BINARY,
                description="CSV export of scoped audit logs.",
            ),
            403: error_response_serializer(name="SecurityAuditLogExportError"),
        },
    )
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
                {
                    "success": False,
                    "message": "You are not authorized for this chama.",
                    "errors": {},
                },
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


class RBACRoleListView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = RBACRoleSerializer

    @extend_schema(
        tags=["Security"],
        operation_id="list_rbac_roles",
        responses={
            200: success_response_serializer(
                name="RBACRoleListResponse",
                data=RBACRoleSerializer(many=True),
            )
        },
    )
    def get(self, request):
        serializer = RBACRoleSerializer(build_role_catalog(), many=True)
        return success_response(data=serializer.data)


class CurrentUserAccessView(SecurityScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = UserAccessSerializer

    @extend_schema(
        tags=["Security"],
        operation_id="get_current_user_access",
        responses={
            200: success_response_serializer(
                name="CurrentUserAccessResponse",
                data=UserAccessSerializer,
            ),
            500: error_response_serializer(name="CurrentUserAccessError"),
        },
    )
    def get(self, request):
        chama_id = self.get_scoped_chama_id(required=False)
        try:
            access = build_user_access_snapshot(user=request.user, chama_id=chama_id)
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to build user access snapshot",
                extra={"user_id": str(request.user.id), "chama_id": chama_id},
            )
            return Response(
                {
                    "success": False,
                    "message": "Unable to resolve access details.",
                    "errors": {},
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        serializer = UserAccessSerializer(access)
        return success_response(data=serializer.data)
