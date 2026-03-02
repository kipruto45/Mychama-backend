import uuid
import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)
from django.db import models, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import filters, generics, permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import MemberCard
from apps.chama.models import (
    Chama,
    ChamaStatus,
    Invite,
    InviteLink,
    JoinCodeMode,
    MemberStatus,
    Membership,
    MembershipRequest,
    MembershipRequestSource,
    MembershipRequestStatus,
    MembershipRole,
    RoleDelegation,
)
from apps.chama.permissions import (
    IsApprovedActiveMember,
    IsChamaAdmin,
    IsMembershipApprover,
    get_membership,
)
from apps.chama.services import ADMIN_EQUIVALENT_ROLES, get_effective_role
from apps.chama.serializers import (
    ChamaCreateSerializer,
    ChamaSerializer,
    ChamaUpdateSerializer,
    InviteSerializer,
    InviteLinkCreateSerializer,
    InviteLinkSerializer,
    MembershipRequestDecisionSerializer,
    MembershipRequestFilterSerializer,
    MembershipRequestSerializer,
    MembershipRoleUpdateSerializer,
    MembershipSerializer,
    RequestJoinSerializer,
    RoleDelegationCreateSerializer,
    RoleDelegationSerializer,
)
from core.audit import create_activity_log, create_audit_log
from core.utils import normalize_kenyan_phone

INVITE_ASSIGNABLE_ROLES = {
    MembershipRole.MEMBER,
    MembershipRole.TREASURER,
    MembershipRole.SECRETARY,
    MembershipRole.AUDITOR,
}


def _invite_membership_role(role: str | None) -> str:
    if role in INVITE_ASSIGNABLE_ROLES:
        return role
    return MembershipRole.MEMBER


def _resolved_join_mode(chama: Chama) -> str:
    join_mode = getattr(chama, "join_mode", "")
    if join_mode in {JoinCodeMode.AUTO_JOIN, JoinCodeMode.APPROVAL_REQUIRED}:
        return join_mode
    if chama.allow_public_join and not chama.require_approval:
        return JoinCodeMode.AUTO_JOIN
    return JoinCodeMode.APPROVAL_REQUIRED


def _active_member_count(chama: Chama) -> int:
    return Membership.objects.filter(
        chama=chama,
        status=MemberStatus.ACTIVE,
        is_active=True,
        is_approved=True,
        exited_at__isnull=True,
    ).count()


def _join_capacity_snapshot(chama: Chama) -> dict:
    active_members = _active_member_count(chama)
    configured_limit = int(chama.max_members or 0) or None
    billing_limit = None

    try:
        from apps.billing.services import check_seat_limit

        billing_limit = int(check_seat_limit(chama).get("limit") or 0) or None
    except Exception:  # noqa: BLE001
        billing_limit = None

    candidate_limits = [limit for limit in [configured_limit, billing_limit] if limit]
    effective_limit = min(candidate_limits) if candidate_limits else None
    available = None if effective_limit is None else max(0, effective_limit - active_members)

    return {
        "active_members": active_members,
        "configured_limit": configured_limit,
        "billing_limit": billing_limit,
        "effective_limit": effective_limit,
        "available": available,
        "is_available": effective_limit is None or active_members < effective_limit,
    }


def _assert_join_capacity(chama: Chama) -> dict:
    capacity = _join_capacity_snapshot(chama)
    if capacity["is_available"]:
        return capacity

    raise ValidationError(
        {
            "detail": (
                "This chama has reached its member limit "
                f"({capacity['active_members']}/{capacity['effective_limit']}). "
                "Upgrade the billing plan or increase the group capacity before adding more members."
            )
        }
    )


def _effective_invite_role_for_creator(*, chama: Chama, actor, requested_role: str | None) -> str:
    resolved_role = _invite_membership_role(requested_role)
    if resolved_role == MembershipRole.MEMBER or getattr(actor, "is_superuser", False):
        return resolved_role

    membership = get_membership(actor, chama.id)
    effective_role = get_effective_role(actor, chama.id, membership)
    if effective_role in ADMIN_EQUIVALENT_ROLES:
        return resolved_role
    return MembershipRole.MEMBER


def _invite_payload(invite_link: InviteLink) -> dict:
    payload = InviteLinkSerializer(invite_link).data
    payload["is_valid"] = (
        invite_link.chama.status == ChamaStatus.ACTIVE and invite_link.is_valid()
    )
    payload["join_mode"] = _resolved_join_mode(invite_link.chama)
    payload["capacity"] = _join_capacity_snapshot(invite_link.chama)
    return payload


def _rate_limit_values(setting_name: str, default_limit: int, default_window: int) -> tuple[int, int]:
    raw_value = getattr(settings, setting_name, (default_limit, default_window))
    if isinstance(raw_value, (list, tuple)) and len(raw_value) == 2:
        try:
            return max(1, int(raw_value[0])), max(1, int(raw_value[1]))
        except (TypeError, ValueError):
            return default_limit, default_window
    return default_limit, default_window


def _rate_limit_identity(request) -> str:
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if getattr(request.user, "is_authenticated", False):
        return f"user:{request.user.id}"
    return request.META.get("REMOTE_ADDR", "unknown")


def _is_rate_limited(request, scope: str, *, limit: int, window_seconds: int) -> bool:
    identity = _rate_limit_identity(request)
    cache_key = f"rate-limit:{scope}:{identity}"
    current = cache.get(cache_key)
    if current is None:
        cache.set(cache_key, 1, timeout=window_seconds)
        return False
    try:
        current = cache.incr(cache_key)
    except ValueError:
        cache.set(cache_key, 1, timeout=window_seconds)
        return False
    return current > limit


def _rate_limited_response(detail: str, *, scope: str) -> Response:
    logger.warning("Rate limit triggered for %s", scope)
    return Response({"detail": detail}, status=status.HTTP_429_TOO_MANY_REQUESTS)


def _find_invite_link_by_token(presented_token: str):
    return InviteLink.resolve_presented_token(
        presented_token,
        queryset=InviteLink.objects.select_related("chama", "created_by"),
    )


def _can_manage_invites(*, actor, chama: Chama) -> bool:
    if getattr(actor, "is_superuser", False):
        return True
    membership = get_membership(actor, chama.id)
    if not membership:
        return False
    role = get_effective_role(actor, chama.id, membership)
    return role in {
        MembershipRole.CHAMA_ADMIN,
        MembershipRole.ADMIN,
        MembershipRole.SECRETARY,
    }


def _lock_invite_link(*, token: str | None = None, invite_id=None, chama: Chama | None = None) -> InviteLink:
    queryset = InviteLink.objects.select_related("chama", "created_by").select_for_update()
    if invite_id:
        queryset = queryset.filter(id=invite_id)
    if chama:
        queryset = queryset.filter(chama=chama)

    if token:
        invite_link = InviteLink.resolve_presented_token(token, queryset=queryset)
    else:
        invite_link = queryset.first()
    if not invite_link:
        raise ValidationError({"detail": "Invite link not found."})
    if invite_link.chama.status != ChamaStatus.ACTIVE:
        raise ValidationError({"detail": "This chama is not accepting invite joins right now."})
    if not invite_link.is_valid():
        raise ValidationError({"detail": "Invite link is invalid or expired."})
    return invite_link


def _consume_invite_link(invite_link: InviteLink, *, actor) -> InviteLink:
    invite_link.current_uses += 1
    if invite_link.max_uses and invite_link.current_uses >= invite_link.max_uses:
        invite_link.is_active = False
    invite_link.updated_by = actor
    update_fields = ["current_uses", "updated_by", "updated_at"]
    if not invite_link.is_active:
        update_fields.insert(1, "is_active")
    invite_link.save(update_fields=update_fields)
    return invite_link


def _expire_pending_requests(*, user, chama, actor_id) -> None:
    MembershipRequest.objects.filter(
        user=user,
        chama=chama,
        status=MembershipRequestStatus.PENDING,
        expires_at__lte=timezone.now(),
    ).update(
        status=MembershipRequestStatus.EXPIRED,
        updated_by_id=actor_id,
    )


def _pending_request_for_user(*, user, chama):
    return (
        MembershipRequest.objects.filter(
            user=user,
            chama=chama,
            status=MembershipRequestStatus.PENDING,
            expires_at__gt=timezone.now(),
        )
        .order_by("-created_at")
        .first()
    )


def _membership_request_expiry_at():
    expires_days = max(
        1,
        int(getattr(settings, "MEMBERSHIP_REQUEST_EXPIRY_DAYS", 7)),
    )
    return timezone.now() + timedelta(days=expires_days)


def _ensure_member_card(*, membership: Membership, chama: Chama) -> None:
    MemberCard.objects.get_or_create(
        user=membership.user,
        chama=chama,
        is_active=True,
        defaults={
            "card_number": (
                f"CHM-{str(chama.id).split('-')[0].upper()}-"
                f"{str(membership.user_id).split('-')[0].upper()}"
            ),
            "qr_token": uuid.uuid4().hex + uuid.uuid4().hex[:16],
        },
    )


def _prepare_pending_membership(*, user, chama, actor, role: str | None) -> Membership:
    membership, _ = Membership.objects.get_or_create(
        user=user,
        chama=chama,
        defaults={
            "role": _invite_membership_role(role),
            "status": MemberStatus.PENDING,
            "is_active": False,
            "is_approved": False,
            "joined_at": timezone.now(),
            "created_by": actor,
            "updated_by": actor,
        },
    )
    membership.role = _invite_membership_role(role or membership.role)
    membership.status = MemberStatus.PENDING
    membership.is_active = False
    membership.is_approved = False
    membership.approved_at = None
    membership.approved_by = None
    membership.exited_at = None
    membership.suspension_reason = ""
    membership.exit_reason = ""
    membership.updated_by = actor
    membership.save(
        update_fields=[
            "role",
            "status",
            "is_active",
            "is_approved",
            "approved_at",
            "approved_by",
            "exited_at",
            "suspension_reason",
            "exit_reason",
            "updated_by",
            "updated_at",
        ]
    )
    return membership


def _activate_membership(*, membership: Membership, actor, role: str | None) -> Membership:
    membership.role = _invite_membership_role(role or membership.role)
    membership.status = MemberStatus.ACTIVE
    membership.is_active = True
    membership.is_approved = True
    membership.suspension_reason = ""
    membership.exit_reason = ""
    membership.approved_at = timezone.now()
    membership.approved_by = actor
    membership.exited_at = None
    membership.updated_by = actor
    membership.save(
        update_fields=[
            "role",
            "status",
            "is_active",
            "is_approved",
            "suspension_reason",
            "exit_reason",
            "approved_at",
            "approved_by",
            "exited_at",
            "updated_by",
            "updated_at",
        ]
    )
    return membership


def _notify_membership_request_reviewers(*, chama: Chama, actor, membership_request: MembershipRequest) -> None:
    try:
        from apps.notifications.models import NotificationType
        from apps.notifications.services import NotificationService

        reviewers = Membership.objects.select_related("user").filter(
            chama=chama,
            role__in=[
                MembershipRole.CHAMA_ADMIN,
                MembershipRole.ADMIN,
                MembershipRole.SECRETARY,
            ],
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        )
        for reviewer in reviewers:
            NotificationService.send_notification(
                user=reviewer.user,
                chama=chama,
                channels=["in_app"],
                message=f"New membership request from {membership_request.user.full_name}.",
                subject="Membership request pending review",
                notification_type=NotificationType.SYSTEM,
                idempotency_key=(
                    f"membership-request-review:{membership_request.id}:{reviewer.user_id}"
                ),
                actor=actor,
            )
    except Exception:  # noqa: BLE001
        pass


def _notify_member_joined(*, chama: Chama, membership: Membership, actor, via_invite: bool) -> None:
    try:
        from apps.notifications.models import NotificationType
        from apps.notifications.services import NotificationService

        NotificationService.send_notification(
            user=membership.user,
            chama=chama,
            channels=["sms", "in_app"],
            message=(
                f"Welcome to {chama.name}. "
                f"Your membership is now active via {'invite' if via_invite else 'approval'}."
            ),
            subject="Membership active",
            notification_type=NotificationType.SYSTEM,
            idempotency_key=f"membership-active:{membership.id}",
            actor=actor,
        )
    except Exception:  # noqa: BLE001
        pass


class ChamaScopeMixin:
    chama_lookup_url_kwarg = "id"

    def get_scoped_chama_id(self):
        path_chama_id = (
            self.kwargs.get(self.chama_lookup_url_kwarg)
            or self.kwargs.get("chama_id")
            or self.kwargs.get("pk")
        )
        header_chama_id = self.request.headers.get("X-CHAMA-ID")

        if path_chama_id:
            try:
                path_chama_id = str(uuid.UUID(str(path_chama_id)))
            except ValueError as exc:
                raise ValidationError({"detail": "Invalid chama id in URL."}) from exc

        if header_chama_id:
            try:
                header_chama_id = str(uuid.UUID(str(header_chama_id)))
            except ValueError as exc:
                raise ValidationError({"detail": "Invalid X-CHAMA-ID header."}) from exc

        if (
            header_chama_id
            and path_chama_id
            and str(header_chama_id) != str(path_chama_id)
        ):
            raise ValidationError({"detail": "X-CHAMA-ID must match chama id in URL."})

        scoped_chama_id = header_chama_id or path_chama_id
        if not scoped_chama_id:
            raise ValidationError(
                {
                    "detail": "Chama scope missing. Provide chama id in URL or X-CHAMA-ID header."
                }
            )

        return scoped_chama_id

    def get_scoped_chama(self):
        return get_object_or_404(Chama, id=self.get_scoped_chama_id())


class ChamaListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ["name", "county", "subcounty"]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return ChamaCreateSerializer
        return ChamaSerializer

    def get_queryset(self):
        queryset = Chama.objects.filter(
            memberships__user=self.request.user,
            memberships__is_active=True,
            memberships__is_approved=True,
            memberships__status=MemberStatus.ACTIVE,
        ).distinct()

        header_chama_id = self.request.headers.get("X-CHAMA-ID")
        if header_chama_id:
            queryset = queryset.filter(id=header_chama_id)

        return queryset

    def perform_create(self, serializer):
        now = timezone.now()
        chama = serializer.save(
            created_by=self.request.user,
            updated_by=self.request.user,
        )

        Membership.objects.create(
            user=self.request.user,
            chama=chama,
            role=MembershipRole.CHAMA_ADMIN,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            joined_at=now,
            approved_at=now,
            approved_by=self.request.user,
            created_by=self.request.user,
            updated_by=self.request.user,
        )


class ChamaDetailView(ChamaScopeMixin, generics.RetrieveUpdateAPIView):
    queryset = Chama.objects.all()
    lookup_field = "id"
    lookup_url_kwarg = "id"

    def get_serializer_class(self):
        if self.request.method in {"PATCH", "PUT"}:
            return ChamaUpdateSerializer
        return ChamaSerializer

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:
            return [permissions.IsAuthenticated(), IsApprovedActiveMember()]
        return [permissions.IsAuthenticated(), IsChamaAdmin()]

    def get_object(self):
        chama = self.get_scoped_chama()
        self.check_object_permissions(self.request, chama)
        return chama


class InviteValidateView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request, *args, **kwargs):
        limit, window = _rate_limit_values("INVITE_VALIDATE_RATE_LIMIT", 20, 300)
        if _is_rate_limited(
            request,
            "invite-validate",
            limit=limit,
            window_seconds=window,
        ):
            return _rate_limited_response(
                "Too many invite validation attempts. Please try again shortly.",
                scope="invite-validate",
            )

        invite_link = _find_invite_link_by_token(self.kwargs["token"])
        if not invite_link:
            return Response(
                {"detail": "Invite link not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(_invite_payload(invite_link), status=status.HTTP_200_OK)


def _invite_join_response(*, request, presented_token: str) -> Response:
    invite_link = _lock_invite_link(token=presented_token)
    chama = invite_link.chama

    if Membership.objects.filter(
        user=request.user,
        chama=chama,
        status=MemberStatus.ACTIVE,
        is_active=True,
        is_approved=True,
        exited_at__isnull=True,
    ).exists():
        return Response(
            {"detail": "You are already an approved member of this chama."},
            status=status.HTTP_409_CONFLICT,
        )

    if not request.user.phone_verified:
        return Response(
            {"detail": "Verify your phone number before joining via invite."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if invite_link.restricted_phone:
        restricted_phone = invite_link.restricted_phone
        try:
            restricted_phone = normalize_kenyan_phone(restricted_phone)
        except ValueError:
            pass
        if restricted_phone != request.user.phone:
            return Response(
                {"detail": "This invite link is restricted to another phone number."},
                status=status.HTTP_403_FORBIDDEN,
            )

    try:
        _assert_join_capacity(chama)
    except ValidationError as exc:
        return Response(exc.detail, status=status.HTTP_402_PAYMENT_REQUIRED)

    if invite_link.approval_required:
        _expire_pending_requests(
            user=request.user,
            chama=chama,
            actor_id=request.user.id,
        )
        pending_request = _pending_request_for_user(user=request.user, chama=chama)
        if pending_request:
            return Response(
                {
                    "detail": "You already have a pending join request for this chama.",
                    "membership_request": MembershipRequestSerializer(pending_request).data,
                },
                status=status.HTTP_200_OK,
            )

        _prepare_pending_membership(
            user=request.user,
            chama=chama,
            actor=request.user,
            role=invite_link.role,
        )
        membership_request = MembershipRequest.objects.create(
            user=request.user,
            chama=chama,
            status=MembershipRequestStatus.PENDING,
            requested_via=MembershipRequestSource.INVITE_LINK,
            invite_link=invite_link,
            request_note=str(request.data.get("request_note", "")).strip(),
            ip_address=RequestJoinView._client_ip(request),
            device_info=RequestJoinView._device_info(request),
            expires_at=_membership_request_expiry_at(),
            created_by=request.user,
            updated_by=request.user,
        )
        _consume_invite_link(invite_link, actor=request.user)

        create_activity_log(
            actor=request.user,
            chama_id=chama.id,
            action="membership_request_submitted",
            entity_type="MembershipRequest",
            entity_id=membership_request.id,
            metadata={
                "invite_link_id": str(invite_link.id),
                "join_source": "invite_link",
                "phone_verified": request.user.phone_verified,
            },
        )
        create_audit_log(
            actor=request.user,
            chama_id=chama.id,
            action="membership_request_created",
            entity_type="MembershipRequest",
            entity_id=membership_request.id,
            metadata={
                "status": membership_request.status,
                "invite_link_id": str(invite_link.id),
            },
        )
        _notify_membership_request_reviewers(
            chama=chama,
            actor=request.user,
            membership_request=membership_request,
        )
        return Response(
            {
                "detail": "Join request submitted and pending review.",
                "membership_request": MembershipRequestSerializer(membership_request).data,
                "invite": _invite_payload(invite_link),
            },
            status=status.HTTP_201_CREATED,
        )

    membership, _ = Membership.objects.get_or_create(
        user=request.user,
        chama=chama,
        defaults={
            "role": _invite_membership_role(invite_link.role),
            "status": MemberStatus.PENDING,
            "is_active": False,
            "is_approved": False,
            "joined_at": timezone.now(),
            "created_by": request.user,
            "updated_by": request.user,
        },
    )
    membership = _activate_membership(
        membership=membership,
        actor=request.user,
        role=invite_link.role,
    )
    _ensure_member_card(membership=membership, chama=chama)
    _consume_invite_link(invite_link, actor=request.user)

    MembershipRequest.objects.filter(
        user=request.user,
        chama=chama,
        status__in=[
            MembershipRequestStatus.PENDING,
            MembershipRequestStatus.NEEDS_INFO,
        ],
    ).update(
        status=MembershipRequestStatus.APPROVED,
        reviewed_by_id=request.user.id,
        reviewed_at=timezone.now(),
        phone_verified_at_approval=request.user.phone_verified_at or timezone.now(),
        review_note="Auto-approved via invite link.",
        updated_by_id=request.user.id,
    )

    create_activity_log(
        actor=request.user,
        chama_id=chama.id,
        action="invite_link_joined",
        entity_type="InviteLink",
        entity_id=invite_link.id,
        metadata={"membership_id": str(membership.id), "role": membership.role},
    )
    create_audit_log(
        actor=request.user,
        chama_id=chama.id,
        action="membership_activated_via_invite",
        entity_type="Membership",
        entity_id=membership.id,
        metadata={
            "invite_link_id": str(invite_link.id),
            "role": membership.role,
        },
    )
    _notify_member_joined(
        chama=chama,
        membership=membership,
        actor=request.user,
        via_invite=True,
    )
    return Response(
        {
            "detail": "Invite accepted. Your membership is now active.",
            "membership": MembershipSerializer(membership).data,
            "invite": _invite_payload(invite_link),
        },
        status=status.HTTP_201_CREATED,
    )


class InviteJoinView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        limit, window = _rate_limit_values("INVITE_ACCEPT_RATE_LIMIT", 10, 300)
        if _is_rate_limited(
            request,
            "invite-accept",
            limit=limit,
            window_seconds=window,
        ):
            return _rate_limited_response(
                "Too many invite acceptance attempts. Please try again shortly.",
                scope="invite-accept",
            )
        return _invite_join_response(request=request, presented_token=self.kwargs["token"])


class InviteLookupAliasView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request, *args, **kwargs):
        token = str(request.query_params.get("token", "")).strip()
        if not token:
            return Response({"detail": "token is required."}, status=status.HTTP_400_BAD_REQUEST)
        limit, window = _rate_limit_values("INVITE_VALIDATE_RATE_LIMIT", 20, 300)
        if _is_rate_limited(
            request,
            "invite-validate",
            limit=limit,
            window_seconds=window,
        ):
            return _rate_limited_response(
                "Too many invite validation attempts. Please try again shortly.",
                scope="invite-validate",
            )
        invite_link = _find_invite_link_by_token(token)
        if not invite_link:
            return Response({"detail": "Invite link not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(_invite_payload(invite_link), status=status.HTTP_200_OK)


class InviteAcceptAliasView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        token = str(request.data.get("token", "")).strip()
        if not token:
            return Response({"detail": "token is required."}, status=status.HTTP_400_BAD_REQUEST)
        limit, window = _rate_limit_values("INVITE_ACCEPT_RATE_LIMIT", 10, 300)
        if _is_rate_limited(
            request,
            "invite-accept",
            limit=limit,
            window_seconds=window,
        ):
            return _rate_limited_response(
                "Too many invite acceptance attempts. Please try again shortly.",
                scope="invite-accept",
            )
        return _invite_join_response(request=request, presented_token=token)


class InviteCreateAliasView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        chama_id = request.data.get("chama_id")
        if not chama_id:
            return Response({"detail": "chama_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        chama = get_object_or_404(Chama, id=chama_id)
        if not _can_manage_invites(actor=request.user, chama=chama):
            return Response(
                {"detail": "Only chama approvers can create invite links."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = InviteLinkCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        requested_role = serializer.validated_data.get("preassigned_role", "")
        effective_role = _effective_invite_role_for_creator(
            chama=chama,
            actor=request.user,
            requested_role=requested_role,
        )
        invite_link = InviteLink.objects.create(
            chama=chama,
            created_by=request.user,
            approval_required=serializer.validated_data["approval_required"],
            max_uses=serializer.validated_data.get("max_uses"),
            expires_at=serializer.validated_data.get("expires_at")
            or (
                timezone.now()
                + timedelta(days=serializer.validated_data["expires_in_days"])
            ),
            restricted_phone=serializer.validated_data.get("restricted_phone", ""),
            preassigned_role=effective_role,
            is_active=True,
            updated_by=request.user,
        )
        create_audit_log(
            actor=request.user,
            chama_id=chama.id,
            action="invite_link_created",
            entity_type="InviteLink",
            entity_id=invite_link.id,
            metadata={
                "max_uses": invite_link.max_uses,
                "restricted_phone": invite_link.restricted_phone,
                "approval_required": invite_link.approval_required,
                "role": invite_link.role,
                "requested_role": requested_role or MembershipRole.MEMBER,
                "role_adjusted": effective_role != _invite_membership_role(requested_role),
                "via_alias": True,
            },
        )
        return Response(InviteLinkSerializer(invite_link).data, status=status.HTTP_201_CREATED)


class InviteRevokeAliasView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        chama_id = request.data.get("chama_id")
        invite_id = request.data.get("invite_id")
        if not chama_id or not invite_id:
            return Response(
                {"detail": "chama_id and invite_id are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        chama = get_object_or_404(Chama, id=chama_id)
        if not _can_manage_invites(actor=request.user, chama=chama):
            return Response(
                {"detail": "Only chama approvers can revoke invite links."},
                status=status.HTTP_403_FORBIDDEN,
            )
        invite_link = get_object_or_404(
            InviteLink,
            id=invite_id,
            chama=chama,
        )
        if not invite_link.is_active:
            return Response(
                {"detail": "Invite link already inactive."},
                status=status.HTTP_200_OK,
            )

        invite_link.is_active = False
        invite_link.revoked_at = timezone.now()
        invite_link.revoke_reason = (
            str(request.data.get("reason", "")).strip() or "Revoked by chama reviewer."
        )
        invite_link.updated_by = request.user
        invite_link.save(
            update_fields=[
                "is_active",
                "revoked_at",
                "revoke_reason",
                "updated_by",
                "updated_at",
            ]
        )
        create_audit_log(
            actor=request.user,
            chama_id=chama.id,
            action="invite_link_revoked",
            entity_type="InviteLink",
            entity_id=invite_link.id,
            metadata={"reason": invite_link.revoke_reason, "via_alias": True},
        )
        return Response({"detail": "Invite link revoked."}, status=status.HTTP_200_OK)


class RequestJoinView(ChamaScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def _client_ip(request) -> str | None:
        forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")

    @staticmethod
    def _device_info(request) -> str:
        return str(request.META.get("HTTP_USER_AGENT", "")).strip()[:255]

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()
        serializer = RequestJoinSerializer(
            data=request.data,
            context={"chama": chama, "user": request.user},
        )
        serializer.is_valid(raise_exception=True)

        active_membership = Membership.objects.filter(
            user=request.user,
            chama=chama,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            exited_at__isnull=True,
        ).first()
        if active_membership:
            return Response(
                {"detail": "You are already an approved member of this chama."},
                status=status.HTTP_200_OK,
            )

        try:
            _assert_join_capacity(chama)
        except ValidationError as exc:
            return Response(exc.detail, status=status.HTTP_402_PAYMENT_REQUIRED)

        invite_link = serializer.validated_data.get("invite_link")
        if invite_link and not invite_link.approval_required:
            if not request.user.phone_verified:
                return Response(
                    {"detail": "Verify your phone number before joining via invite."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            locked_invite_link = _lock_invite_link(invite_id=invite_link.id, chama=chama)
            membership, _ = Membership.objects.get_or_create(
                user=request.user,
                chama=chama,
                defaults={
                    "role": _invite_membership_role(locked_invite_link.role),
                    "status": MemberStatus.PENDING,
                    "is_active": False,
                    "is_approved": False,
                    "joined_at": timezone.now(),
                    "created_by": request.user,
                    "updated_by": request.user,
                },
            )
            membership = _activate_membership(
                membership=membership,
                actor=request.user,
                role=locked_invite_link.role,
            )
            _ensure_member_card(membership=membership, chama=chama)
            _consume_invite_link(locked_invite_link, actor=request.user)

            MembershipRequest.objects.filter(
                user=request.user,
                chama=chama,
                status__in=[
                    MembershipRequestStatus.PENDING,
                    MembershipRequestStatus.NEEDS_INFO,
                ],
            ).update(
                status=MembershipRequestStatus.APPROVED,
                reviewed_by_id=request.user.id,
                reviewed_at=timezone.now(),
                phone_verified_at_approval=request.user.phone_verified_at or timezone.now(),
                review_note="Auto-approved via invite link.",
                updated_by_id=request.user.id,
            )

            create_activity_log(
                actor=request.user,
                chama_id=chama.id,
                action="invite_link_joined",
                entity_type="InviteLink",
                entity_id=locked_invite_link.id,
                metadata={"membership_id": str(membership.id), "role": membership.role},
            )
            create_audit_log(
                actor=request.user,
                chama_id=chama.id,
                action="membership_activated_via_invite",
                entity_type="Membership",
                entity_id=membership.id,
                metadata={
                    "invite_link_id": str(locked_invite_link.id),
                    "role": membership.role,
                },
            )
            _notify_member_joined(
                chama=chama,
                membership=membership,
                actor=request.user,
                via_invite=True,
            )
            return Response(
                {
                    "detail": "Invite accepted. Your membership is now active.",
                    "membership": MembershipSerializer(membership).data,
                    "invite": _invite_payload(locked_invite_link),
                },
                status=status.HTTP_201_CREATED,
            )

        _expire_pending_requests(
            user=request.user,
            chama=chama,
            actor_id=request.user.id,
        )
        pending_request = _pending_request_for_user(user=request.user, chama=chama)
        if pending_request:
            return Response(
                {
                    "detail": "You already have a pending join request for this chama.",
                    "membership_request_id": str(pending_request.id),
                },
                status=status.HTTP_200_OK,
            )

        if invite_link:
            invite_link = _lock_invite_link(invite_id=invite_link.id, chama=chama)
            _prepare_pending_membership(
                user=request.user,
                chama=chama,
                actor=request.user,
                role=invite_link.role,
            )

        membership_request = MembershipRequest.objects.create(
            user=request.user,
            chama=chama,
            status=MembershipRequestStatus.PENDING,
            requested_via=(
                MembershipRequestSource.INVITE_LINK
                if invite_link
                else (
                    MembershipRequestSource.JOIN_CODE
                    if serializer.validated_data.get("join_code")
                    else MembershipRequestSource.PUBLIC_JOIN
                )
            ),
            invite_link=invite_link,
            request_note=serializer.validated_data.get("request_note", ""),
            ip_address=self._client_ip(request),
            device_info=self._device_info(request),
            expires_at=_membership_request_expiry_at(),
            created_by=request.user,
            updated_by=request.user,
        )

        invite_link = serializer.validated_data.get("invite_link")
        if invite_link:
            invite_link = _lock_invite_link(invite_id=invite_link.id, chama=chama)
            _consume_invite_link(invite_link, actor=request.user)

        create_activity_log(
            actor=request.user,
            chama_id=chama.id,
            action="membership_request_submitted",
            entity_type="MembershipRequest",
            entity_id=membership_request.id,
            metadata={
                "invite_link_id": str(invite_link.id) if invite_link else "",
                "has_join_code": bool(serializer.validated_data.get("join_code")),
                "phone_verified": request.user.phone_verified,
            },
        )
        create_audit_log(
            actor=request.user,
            chama_id=chama.id,
            action="membership_request_created",
            entity_type="MembershipRequest",
            entity_id=membership_request.id,
            metadata={
                "status": membership_request.status,
                "expires_at": membership_request.expires_at.isoformat(),
                "phone_verified": request.user.phone_verified,
            },
        )

        try:
            from apps.ai.membership_review import process_membership_ai_review

            process_membership_ai_review.delay(str(membership_request.id))
        except Exception:  # noqa: BLE001
            # Join request must persist even if background AI task queue is unavailable.
            pass

        _notify_membership_request_reviewers(
            chama=chama,
            actor=request.user,
            membership_request=membership_request,
        )

        return Response(
            {
                "detail": "Join request submitted and pending admin approval.",
                "membership_request_id": str(membership_request.id),
                "phone_verified": request.user.phone_verified,
            },
            status=status.HTTP_201_CREATED,
        )


class MembershipListView(ChamaScopeMixin, generics.ListAPIView):
    serializer_class = MembershipSerializer
    permission_classes = [permissions.IsAuthenticated, IsApprovedActiveMember]
    filter_backends = [filters.SearchFilter]
    search_fields = ["user__full_name", "user__phone", "user__email"]

    def get_queryset(self):
        chama = self.get_scoped_chama()
        queryset = Membership.objects.select_related("user", "approved_by").filter(
            chama=chama,
            is_active=True,
        )

        requester_membership = get_membership(self.request.user, chama.id)
        if (
            requester_membership
            and get_effective_role(self.request.user, chama.id, requester_membership)
            in ADMIN_EQUIVALENT_ROLES
        ):
            return queryset

        return queryset.filter(is_approved=True)


class MembershipRequestListView(ChamaScopeMixin, generics.ListAPIView):
    serializer_class = MembershipRequestSerializer
    permission_classes = [permissions.IsAuthenticated, IsMembershipApprover]
    filter_backends = [filters.SearchFilter]
    search_fields = ["user__full_name", "user__phone", "user__email", "request_note"]

    def get_queryset(self):
        chama = self.get_scoped_chama()
        filter_serializer = MembershipRequestFilterSerializer(data=self.request.query_params)
        filter_serializer.is_valid(raise_exception=True)
        queryset = MembershipRequest.objects.select_related("user", "reviewed_by").filter(
            chama=chama
        )
        status_value = filter_serializer.validated_data.get("status")
        if status_value:
            queryset = queryset.filter(status=status_value)
        return queryset.order_by("-created_at")


class MembershipRequestApproveView(ChamaScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsMembershipApprover]

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()
        membership_request = get_object_or_404(
            MembershipRequest.objects.select_related("user"),
            id=self.kwargs["request_id"],
            chama=chama,
        )

        if membership_request.status not in {
            MembershipRequestStatus.PENDING,
            MembershipRequestStatus.NEEDS_INFO,
        }:
            return Response(
                {"detail": "Only pending/needs-info requests can be approved."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if membership_request.expires_at <= timezone.now():
            membership_request.status = MembershipRequestStatus.EXPIRED
            membership_request.reviewed_by = request.user
            membership_request.reviewed_at = timezone.now()
            membership_request.updated_by = request.user
            membership_request.save(
                update_fields=[
                    "status",
                    "reviewed_by",
                    "reviewed_at",
                    "updated_by",
                    "updated_at",
                ]
            )
            return Response(
                {"detail": "Membership request is expired."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not membership_request.user.phone_verified:
            return Response(
                {
                    "detail": (
                        "Phone verification is required before approval."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            _assert_join_capacity(chama)
        except ValidationError as exc:
            return Response(exc.detail, status=status.HTTP_402_PAYMENT_REQUIRED)

        membership, _ = Membership.objects.get_or_create(
            user=membership_request.user,
            chama=chama,
            defaults={
                "role": MembershipRole.MEMBER,
                "status": MemberStatus.PENDING,
                "is_active": False,
                "is_approved": False,
                "joined_at": timezone.now(),
                "created_by": request.user,
                "updated_by": request.user,
            },
        )
        membership = _activate_membership(
            membership=membership,
            actor=request.user,
            role=membership.role,
        )

        membership_request.status = MembershipRequestStatus.APPROVED
        membership_request.phone_verified_at_approval = (
            membership_request.user.phone_verified_at or timezone.now()
        )
        membership_request.reviewed_by = request.user
        membership_request.reviewed_at = timezone.now()
        membership_request.review_note = (
            str(request.data.get("note", "")).strip()
            or "Approved by chama reviewer."
        )
        membership_request.updated_by = request.user
        membership_request.save(
            update_fields=[
                "status",
                "phone_verified_at_approval",
                "reviewed_by",
                "reviewed_at",
                "review_note",
                "updated_by",
                "updated_at",
            ]
        )

        _ensure_member_card(membership=membership, chama=chama)

        create_audit_log(
            actor=request.user,
            chama_id=chama.id,
            action="membership_request_approved",
            entity_type="MembershipRequest",
            entity_id=membership_request.id,
            metadata={
                "membership_id": str(membership.id),
                "member_user_id": str(membership.user_id),
            },
        )

        _notify_member_joined(
            chama=chama,
            membership=membership,
            actor=request.user,
            via_invite=False,
        )

        return Response(
            {
                "detail": "Membership request approved successfully.",
                "membership": MembershipSerializer(membership).data,
                "membership_request": MembershipRequestSerializer(membership_request).data,
            },
            status=status.HTTP_200_OK,
        )


class MembershipRequestRejectView(ChamaScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsMembershipApprover]

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()
        membership_request = get_object_or_404(
            MembershipRequest.objects.select_related("user"),
            id=self.kwargs["request_id"],
            chama=chama,
        )
        serializer = MembershipRequestDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if membership_request.status not in {
            MembershipRequestStatus.PENDING,
            MembershipRequestStatus.NEEDS_INFO,
        }:
            return Response(
                {"detail": "Only pending/needs-info requests can be rejected."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        membership_request.status = MembershipRequestStatus.REJECTED
        membership_request.reviewed_by = request.user
        membership_request.reviewed_at = timezone.now()
        membership_request.review_note = (
            serializer.validated_data.get("note", "").strip()
            or "Rejected by chama reviewer."
        )
        membership_request.updated_by = request.user
        membership_request.save(
            update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "review_note",
                "updated_by",
                "updated_at",
            ]
        )

        create_audit_log(
            actor=request.user,
            chama_id=chama.id,
            action="membership_request_rejected",
            entity_type="MembershipRequest",
            entity_id=membership_request.id,
            metadata={"member_user_id": str(membership_request.user_id)},
        )

        try:
            from apps.notifications.models import NotificationType
            from apps.notifications.services import NotificationService

            NotificationService.send_notification(
                user=membership_request.user,
                chama=chama,
                channels=["in_app"],
                message=(
                    f"Your request to join {chama.name} was rejected."
                ),
                subject="Membership request update",
                notification_type=NotificationType.SYSTEM,
                idempotency_key=f"membership-request-rejected:{membership_request.id}",
                actor=request.user,
            )
        except Exception:  # noqa: BLE001
            pass

        return Response(
            {"detail": "Membership request rejected."},
            status=status.HTTP_200_OK,
        )


class MembershipRequestNeedsInfoView(ChamaScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsMembershipApprover]

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()
        membership_request = get_object_or_404(
            MembershipRequest.objects.select_related("user"),
            id=self.kwargs["request_id"],
            chama=chama,
        )
        serializer = MembershipRequestDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if membership_request.status not in {
            MembershipRequestStatus.PENDING,
            MembershipRequestStatus.NEEDS_INFO,
        }:
            return Response(
                {"detail": "Only pending/needs-info requests can be updated."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        membership_request.status = MembershipRequestStatus.NEEDS_INFO
        membership_request.reviewed_by = request.user
        membership_request.reviewed_at = timezone.now()
        membership_request.review_note = (
            serializer.validated_data.get("note", "").strip()
            or "Additional information required."
        )
        membership_request.updated_by = request.user
        membership_request.save(
            update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "review_note",
                "updated_by",
                "updated_at",
            ]
        )

        create_audit_log(
            actor=request.user,
            chama_id=chama.id,
            action="membership_request_needs_info",
            entity_type="MembershipRequest",
            entity_id=membership_request.id,
            metadata={"member_user_id": str(membership_request.user_id)},
        )
        return Response(
            {
                "detail": "Membership request marked as needs-info.",
                "membership_request": MembershipRequestSerializer(membership_request).data,
            },
            status=status.HTTP_200_OK,
        )


class InviteListView(ChamaScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsMembershipApprover]

    def get(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()
        queryset = Invite.objects.filter(chama=chama).select_related(
            "chama",
            "invited_by",
            "accepted_by",
        )
        return Response(
            InviteSerializer(queryset, many=True).data,
            status=status.HTTP_200_OK,
        )


class InviteDetailView(ChamaScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsMembershipApprover]

    def delete(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()
        invite = get_object_or_404(Invite, id=self.kwargs["invite_id"], chama=chama)
        invite_id = invite.id
        invite_identifier = invite.identifier
        invite.delete()
        create_audit_log(
            actor=request.user,
            chama_id=chama.id,
            action="invite_deleted",
            entity_type="Invite",
            entity_id=invite_id,
            metadata={"identifier": invite_identifier},
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class InviteLinkListCreateView(ChamaScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsMembershipApprover]

    def get(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()
        queryset = InviteLink.objects.filter(chama=chama).select_related("created_by")
        return Response(InviteLinkSerializer(queryset, many=True).data, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()
        serializer = InviteLinkCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        requested_role = serializer.validated_data.get("preassigned_role", "")
        effective_role = _effective_invite_role_for_creator(
            chama=chama,
            actor=request.user,
            requested_role=requested_role,
        )

        invite_link = InviteLink.objects.create(
            chama=chama,
            token=InviteLink.generate_token(),
            created_by=request.user,
            approval_required=serializer.validated_data["approval_required"],
            max_uses=serializer.validated_data.get("max_uses"),
            expires_at=serializer.validated_data.get("expires_at")
            or (
                timezone.now()
                + timedelta(days=serializer.validated_data["expires_in_days"])
            ),
            restricted_phone=serializer.validated_data.get("restricted_phone", ""),
            preassigned_role=effective_role,
            is_active=True,
            updated_by=request.user,
        )
        create_audit_log(
            actor=request.user,
            chama_id=chama.id,
            action="invite_link_created",
            entity_type="InviteLink",
            entity_id=invite_link.id,
            metadata={
                "max_uses": invite_link.max_uses,
                "restricted_phone": invite_link.restricted_phone,
                "approval_required": invite_link.approval_required,
                "role": invite_link.role,
                "requested_role": requested_role or MembershipRole.MEMBER,
                "role_adjusted": effective_role != _invite_membership_role(requested_role),
            },
        )
        return Response(InviteLinkSerializer(invite_link).data, status=status.HTTP_201_CREATED)


class InviteLinkResendView(ChamaScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsMembershipApprover]

    def post(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()
        invite_link = get_object_or_404(
            InviteLink,
            id=self.kwargs["invite_id"],
            chama=chama,
        )

        if not invite_link.is_active:
            return Response(
                {"detail": "Invite link is inactive. Create a new one."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        extension_days = max(1, int(request.data.get("extend_days", 7)))
        if invite_link.expires_at <= timezone.now():
            invite_link.expires_at = timezone.now() + timedelta(days=extension_days)
            invite_link.save(update_fields=["expires_at", "updated_at"])

        create_audit_log(
            actor=request.user,
            chama_id=chama.id,
            action="invite_link_resent",
            entity_type="InviteLink",
            entity_id=invite_link.id,
            metadata={"extend_days": extension_days},
        )
        return Response(
            {
                "detail": "Invite link is active and ready to resend.",
                "invite_link": InviteLinkSerializer(invite_link).data,
            },
            status=status.HTTP_200_OK,
        )


class InviteLinkRevokeView(ChamaScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsMembershipApprover]

    def post(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()
        invite_link = get_object_or_404(
            InviteLink,
            id=self.kwargs["invite_id"],
            chama=chama,
        )
        if not invite_link.is_active:
            return Response(
                {"detail": "Invite link already inactive."},
                status=status.HTTP_200_OK,
            )

        invite_link.is_active = False
        invite_link.revoked_at = timezone.now()
        invite_link.revoke_reason = (
            str(request.data.get("reason", "")).strip() or "Revoked by chama reviewer."
        )
        invite_link.updated_by = request.user
        invite_link.save(
            update_fields=[
                "is_active",
                "revoked_at",
                "revoke_reason",
                "updated_by",
                "updated_at",
            ]
        )
        create_audit_log(
            actor=request.user,
            chama_id=chama.id,
            action="invite_link_revoked",
            entity_type="InviteLink",
            entity_id=invite_link.id,
            metadata={"reason": invite_link.revoke_reason},
        )
        return Response({"detail": "Invite link revoked."}, status=status.HTTP_200_OK)


class MembershipApproveView(ChamaScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsChamaAdmin]

    def post(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()
        membership = get_object_or_404(
            Membership,
            id=self.kwargs["membership_id"],
            chama=chama,
        )

        if membership.status != MemberStatus.ACTIVE or not membership.is_active or not membership.is_approved:
            try:
                _assert_join_capacity(chama)
            except ValidationError as exc:
                return Response(exc.detail, status=status.HTTP_402_PAYMENT_REQUIRED)

        membership.is_active = True
        membership.is_approved = True
        membership.status = MemberStatus.ACTIVE
        membership.suspension_reason = ""
        membership.exit_reason = ""
        membership.approved_at = timezone.now()
        membership.approved_by = request.user
        membership.exited_at = None
        membership.updated_by = request.user
        membership.save(
            update_fields=[
                "is_active",
                "is_approved",
                "status",
                "suspension_reason",
                "exit_reason",
                "approved_at",
                "approved_by",
                "exited_at",
                "updated_by",
                "updated_at",
            ]
        )

        MembershipRequest.objects.filter(
            user=membership.user,
            chama=chama,
            status__in=[
                MembershipRequestStatus.PENDING,
                MembershipRequestStatus.NEEDS_INFO,
            ],
        ).update(
            status=MembershipRequestStatus.APPROVED,
            reviewed_by_id=request.user.id,
            reviewed_at=timezone.now(),
            phone_verified_at_approval=membership.user.phone_verified_at or timezone.now(),
            review_note="Approved from membership approval endpoint.",
            updated_by_id=request.user.id,
        )

        MemberCard.objects.get_or_create(
            user=membership.user,
            chama=chama,
            is_active=True,
            defaults={
                "card_number": (
                    f"CHM-{str(chama.id).split('-')[0].upper()}-"
                    f"{str(membership.user_id).split('-')[0].upper()}"
                ),
                "qr_token": uuid.uuid4().hex + uuid.uuid4().hex[:16],
            },
        )

        try:
            from apps.notifications.models import NotificationType
            from apps.notifications.services import NotificationService

            NotificationService.send_notification(
                user=membership.user,
                chama=chama,
                channels=["sms", "email"],
                message=(
                    f"Welcome to {chama.name}. You can now deposit to the chama via M-Pesa."
                ),
                subject="Membership approved",
                notification_type=NotificationType.SYSTEM,
                idempotency_key=f"membership-approved:{membership.id}",
                actor=request.user,
            )
        except Exception:  # noqa: BLE001
            # Membership approval should not fail due to notification issues.
            pass

        create_audit_log(
            actor=request.user,
            chama_id=chama.id,
            action="membership_approved",
            entity_type="Membership",
            entity_id=membership.id,
            metadata={"member_user_id": str(membership.user_id)},
        )

        return Response(
            MembershipSerializer(membership).data, status=status.HTTP_200_OK
        )


class MembershipRejectView(ChamaScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsChamaAdmin]

    def post(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()
        membership = get_object_or_404(
            Membership,
            id=self.kwargs["membership_id"],
            chama=chama,
        )

        membership.is_active = False
        membership.is_approved = False
        membership.status = MemberStatus.EXITED
        membership.exit_reason = (
            str(request.data.get("reason", "")).strip() or "Rejected by chama admin."
        )
        membership.exited_at = timezone.now()
        membership.updated_by = request.user
        membership.save(
            update_fields=[
                "is_active",
                "is_approved",
                "status",
                "exit_reason",
                "exited_at",
                "updated_by",
                "updated_at",
            ]
        )

        MembershipRequest.objects.filter(
            user=membership.user,
            chama=chama,
            status__in=[
                MembershipRequestStatus.PENDING,
                MembershipRequestStatus.NEEDS_INFO,
            ],
        ).update(
            status=MembershipRequestStatus.REJECTED,
            reviewed_by_id=request.user.id,
            reviewed_at=timezone.now(),
            review_note=membership.exit_reason,
            updated_by_id=request.user.id,
        )

        create_audit_log(
            actor=request.user,
            chama_id=chama.id,
            action="membership_rejected",
            entity_type="Membership",
            entity_id=membership.id,
            metadata={"member_user_id": str(membership.user_id)},
        )

        return Response(
            {"detail": "Membership request rejected."},
            status=status.HTTP_200_OK,
        )


class MembershipRoleUpdateView(ChamaScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsChamaAdmin]

    def patch(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()
        membership = get_object_or_404(
            Membership,
            id=self.kwargs["membership_id"],
            chama=chama,
        )

        serializer = MembershipRoleUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        old_role = membership.role
        new_role = serializer.validated_data["role"]

        membership.role = new_role
        membership.updated_by = request.user
        membership.save(update_fields=["role", "updated_by", "updated_at"])

        create_audit_log(
            actor=request.user,
            chama_id=chama.id,
            action="membership_role_changed",
            entity_type="Membership",
            entity_id=membership.id,
            metadata={
                "old_role": old_role,
                "new_role": new_role,
                "target_user_id": str(membership.user_id),
            },
        )

        return Response(
            MembershipSerializer(membership).data, status=status.HTTP_200_OK
        )


class RoleDelegationListCreateView(ChamaScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsChamaAdmin]

    def get(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()
        queryset = RoleDelegation.objects.select_related(
            "delegator",
            "delegatee",
            "revoked_by",
        ).filter(chama=chama).order_by("-created_at")
        return Response(RoleDelegationSerializer(queryset, many=True).data)

    def post(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()
        serializer = RoleDelegationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        delegatee_membership = Membership.objects.filter(
            chama=chama,
            user_id=serializer.validated_data["delegatee_id"],
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        ).first()
        if not delegatee_membership:
            return Response(
                {"detail": "delegatee must be an approved active member in this chama."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        delegation = RoleDelegation.objects.create(
            chama=chama,
            delegator=request.user,
            delegatee=delegatee_membership.user,
            role=serializer.validated_data["role"],
            starts_at=serializer.validated_data["starts_at"],
            ends_at=serializer.validated_data["ends_at"],
            is_active=True,
            created_by=request.user,
            updated_by=request.user,
        )

        create_audit_log(
            actor=request.user,
            chama_id=chama.id,
            action="role_delegation_created",
            entity_type="RoleDelegation",
            entity_id=delegation.id,
            metadata={
                "delegatee_id": str(delegatee_membership.user_id),
                "role": delegation.role,
                "starts_at": delegation.starts_at.isoformat(),
                "ends_at": delegation.ends_at.isoformat(),
                "note": serializer.validated_data.get("note", ""),
            },
        )
        return Response(
            RoleDelegationSerializer(delegation).data,
            status=status.HTTP_201_CREATED,
        )


class RoleDelegationRevokeView(ChamaScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsChamaAdmin]

    def post(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()
        delegation = get_object_or_404(
            RoleDelegation,
            id=self.kwargs["delegation_id"],
            chama=chama,
        )
        if not delegation.is_active:
            return Response(
                {"detail": "Delegation is already inactive."},
                status=status.HTTP_200_OK,
            )

        delegation.is_active = False
        delegation.revoked_at = timezone.now()
        delegation.revoked_by = request.user
        delegation.revoke_reason = str(request.data.get("reason", "")).strip()
        delegation.updated_by = request.user
        delegation.save(
            update_fields=[
                "is_active",
                "revoked_at",
                "revoked_by",
                "revoke_reason",
                "updated_by",
                "updated_at",
            ]
        )

        create_audit_log(
            actor=request.user,
            chama_id=chama.id,
            action="role_delegation_revoked",
            entity_type="RoleDelegation",
            entity_id=delegation.id,
            metadata={
                "delegatee_id": str(delegation.delegatee_id),
                "role": delegation.role,
                "reason": delegation.revoke_reason,
            },
        )
        return Response(RoleDelegationSerializer(delegation).data, status=status.HTTP_200_OK)


# ==================== Join Code Views ====================


def _join_code_payload(chama: Chama) -> dict:
    """Build payload for join code validation response."""
    from apps.chama.serializers import ChamaSerializer

    capacity = _join_capacity_snapshot(chama)
    join_mode = _resolved_join_mode(chama)

    return {
        "valid": True,
        "id": str(chama.id),
        "name": chama.name,
        "description": chama.description,
        "county": chama.county,
        "subcounty": chama.subcounty,
        "join_enabled": getattr(chama, "join_enabled", True),
        "join_code": chama.join_code,
        "join_code_expires_at": chama.join_code_expires_at,
        "join_mode": join_mode,
        "allow_public_join": chama.allow_public_join,
        "require_approval": join_mode != JoinCodeMode.AUTO_JOIN,
        "member_count": capacity["active_members"],
        "max_members": capacity["effective_limit"] or chama.max_members,
        "members_remaining": capacity["available"],
        "chama": ChamaSerializer(chama).data,
        "requires_approval": join_mode != JoinCodeMode.AUTO_JOIN,
        "capacity": capacity,
    }


def _validate_join_code(code: str) -> Chama:
    """Validate join code and return the chama if valid."""
    normalized_code = str(code or "").strip().upper()
    chama = (
        Chama.objects.filter(
            join_code__iexact=normalized_code,
            join_enabled=True,
        )
        .filter(status=ChamaStatus.ACTIVE)
        .first()
    )
    if not chama:
        raise ValidationError({"detail": "Invalid join code."})
    if chama.join_code_expires_at and chama.join_code_expires_at <= timezone.now():
        raise ValidationError({"detail": "Join code has expired."})
    return chama


class JoinCodeValidateView(APIView):
    """Public endpoint to validate a join code and get chama info."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request, code):
        limit, window = _rate_limit_values("JOIN_CODE_VALIDATE_RATE_LIMIT", 20, 300)
        if _is_rate_limited(
            request,
            "join-code-validate",
            limit=limit,
            window_seconds=window,
        ):
            return _rate_limited_response(
                "Too many join code checks. Please try again shortly.",
                scope="join-code-validate",
            )
        try:
            chama = _validate_join_code(code)
        except ValidationError as e:
            return Response(e.detail, status=status.HTTP_404_NOT_FOUND)
        return Response(_join_code_payload(chama), status=status.HTTP_200_OK)


class JoinCodeValidateAliasView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request, *args, **kwargs):
        join_code = str(request.data.get("join_code", "")).strip()
        if not join_code:
            return Response({"detail": "join_code is required."}, status=status.HTTP_400_BAD_REQUEST)
        return JoinCodeValidateView().get(request, join_code)


class JoinCodeJoinView(APIView):
    """Authenticated endpoint to join a chama using a join code."""

    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, code):
        limit, window = _rate_limit_values("JOIN_CODE_JOIN_RATE_LIMIT", 10, 300)
        if _is_rate_limited(
            request,
            "join-code-join",
            limit=limit,
            window_seconds=window,
        ):
            return _rate_limited_response(
                "Too many join attempts. Please try again shortly.",
                scope="join-code-join",
            )
        try:
            chama = _validate_join_code(code)
        except ValidationError as e:
            return Response(e.detail, status=status.HTTP_404_NOT_FOUND)
        join_mode = _resolved_join_mode(chama)

        # Check if already a member
        existing_membership = Membership.objects.filter(
            user=request.user,
            chama=chama,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            exited_at__isnull=True,
        ).first()
        if existing_membership:
            return Response(
                {"detail": "You are already an approved member of this chama.", "membership": MembershipSerializer(existing_membership).data},
                status=status.HTTP_200_OK,
            )

        # Check for existing pending request
        pending_request = MembershipRequest.objects.filter(
            user=request.user,
            chama=chama,
            status=MembershipRequestStatus.PENDING,
        ).first()
        if pending_request:
            return Response(
                {
                    "detail": "You already have a pending join request for this chama.",
                    "membership_request": MembershipRequestSerializer(pending_request).data,
                },
                status=status.HTTP_200_OK,
            )

        try:
            _assert_join_capacity(chama)
        except ValidationError as exc:
            return Response(exc.detail, status=status.HTTP_402_PAYMENT_REQUIRED)

        if join_mode == JoinCodeMode.AUTO_JOIN:
            # Direct join - no approval required
            if not request.user.phone_verified:
                return Response(
                    {"detail": "Verify your phone number before joining a chama."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Create membership
            membership, _ = Membership.objects.get_or_create(
                user=request.user,
                chama=chama,
                defaults={
                    "role": MembershipRole.MEMBER,
                    "status": MemberStatus.PENDING,
                    "is_active": False,
                    "is_approved": False,
                    "joined_at": timezone.now(),
                    "created_by": request.user,
                    "updated_by": request.user,
                },
            )

            # Activate membership directly (public join = auto-approve)
            membership = _activate_membership(
                membership=membership,
                actor=request.user,
                role=MembershipRole.MEMBER,
            )
            _ensure_member_card(membership=membership, chama=chama)

            # Create activity and audit logs
            create_activity_log(
                actor=request.user,
                chama_id=chama.id,
                action="joined_via_join_code",
                entity_type="Membership",
                entity_id=membership.id,
                metadata={"membership_id": str(membership.id), "role": membership.role},
            )
            create_audit_log(
                actor=request.user,
                chama_id=chama.id,
                action="membership_created_via_join_code",
                entity_type="Membership",
                entity_id=membership.id,
                metadata={"role": membership.role, "source": "join_code"},
            )

            _notify_member_joined(
                chama=chama,
                membership=membership,
                actor=request.user,
                via_invite=False,
            )

            return Response(
                {
                    "detail": "You have successfully joined the chama.",
                    "membership": MembershipSerializer(membership).data,
                },
                status=status.HTTP_201_CREATED,
            )

        # Requires approval - create membership request
        _expire_pending_requests(user=request.user, chama=chama, actor_id=request.user.id)

        _prepare_pending_membership(
            user=request.user,
            chama=chama,
            actor=request.user,
            role=MembershipRole.MEMBER,
        )

        # Create membership request
        membership_request = MembershipRequest.objects.create(
            user=request.user,
            chama=chama,
            status=MembershipRequestStatus.PENDING,
            requested_via=MembershipRequestSource.JOIN_CODE,
            request_note=str(request.data.get("request_note", "")).strip(),
            ip_address=RequestJoinView._client_ip(request),
            device_info=RequestJoinView._device_info(request),
            expires_at=_membership_request_expiry_at(),
            created_by=request.user,
            updated_by=request.user,
        )

        create_activity_log(
            actor=request.user,
            chama_id=chama.id,
            action="membership_request_submitted_via_join_code",
            entity_type="MembershipRequest",
            entity_id=membership_request.id,
            metadata={"join_code": code},
        )
        create_audit_log(
            actor=request.user,
            chama_id=chama.id,
            action="membership_request_created",
            entity_type="MembershipRequest",
            entity_id=membership_request.id,
            metadata={"status": membership_request.status, "source": "join_code"},
        )

        return Response(
            {
                "detail": "Your join request has been submitted and is pending approval.",
                "membership_request": MembershipRequestSerializer(membership_request).data,
            },
            status=status.HTTP_201_CREATED,
        )


class JoinCodeJoinAliasView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        join_code = str(request.data.get("join_code", "")).strip()
        if not join_code:
            return Response({"detail": "join_code is required."}, status=status.HTTP_400_BAD_REQUEST)
        return JoinCodeJoinView().post(request, join_code)


# ============================================
# Join Code Management Views
# ============================================


class JoinCodeRotateView(ChamaScopeMixin, APIView):
    """Rotate (regenerate) the join code for a Chama."""
    permission_classes = [permissions.IsAuthenticated, IsChamaAdmin]

    def post(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()
        old_code = chama.join_code
        
        # Generate new join code
        new_code = chama.generate_join_code()
        
        # Log the rotation
        create_audit_log(
            actor=request.user,
            chama_id=chama.id,
            action="join_code_rotated",
            entity_type="Chama",
            entity_id=chama.id,
            metadata={"old_code": old_code, "new_code": new_code},
        )
        
        return Response(
            {
                "detail": "Join code rotated successfully.",
                "join_code": new_code,
                "expires_at": chama.join_code_expires_at,
            },
            status=status.HTTP_200_OK,
        )


class JoinCodeSettingsView(ChamaScopeMixin, APIView):
    """Get and update join code settings for a Chama."""
    permission_classes = [permissions.IsAuthenticated, IsChamaAdmin]

    def get(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()
        capacity = _join_capacity_snapshot(chama)
        return Response(
            {
                "join_code": chama.join_code if getattr(chama, "join_enabled", True) else "",
                "join_enabled": getattr(chama, "join_enabled", True),
                "join_code_expires_at": chama.join_code_expires_at,
                "join_mode": _resolved_join_mode(chama),
                "allow_public_join": chama.allow_public_join,
                "require_approval": chama.require_approval,
                "max_members": chama.max_members,
                "current_member_count": capacity["active_members"],
                "members_remaining": capacity["available"],
                "billing_member_limit": capacity["billing_limit"],
            },
            status=status.HTTP_200_OK,
        )

    def patch(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()
        
        # Fields that can be updated
        allowed_fields = [
            "allow_public_join",
            "require_approval",
            "max_members",
            "join_mode",
        ]
        
        updated_fields = []
        for field in allowed_fields:
            if field in request.data:
                if field == "join_mode":
                    join_mode = str(request.data[field]).strip()
                    if join_mode not in {
                        JoinCodeMode.AUTO_JOIN,
                        JoinCodeMode.APPROVAL_REQUIRED,
                    }:
                        return Response(
                            {"detail": "Invalid join mode."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    chama.apply_join_mode(join_mode)
                    updated_fields.extend(["join_mode", "allow_public_join", "require_approval"])
                    continue
                setattr(chama, field, request.data[field])
                updated_fields.append(field)

        if "join_mode" not in request.data and (
            "allow_public_join" in request.data or "require_approval" in request.data
        ):
            derived_join_mode = (
                JoinCodeMode.AUTO_JOIN
                if bool(getattr(chama, "allow_public_join", False)) and not bool(getattr(chama, "require_approval", True))
                else JoinCodeMode.APPROVAL_REQUIRED
            )
            chama.join_mode = derived_join_mode
            updated_fields.append("join_mode")
        
        if updated_fields:
            chama.save(update_fields=list(dict.fromkeys(updated_fields)))
            
            # Log the settings change
            create_audit_log(
                actor=request.user,
                chama_id=chama.id,
                action="join_settings_updated",
                entity_type="Chama",
                entity_id=chama.id,
                metadata={"updated_fields": updated_fields},
            )
        
        return Response(
            {
                "detail": "Join settings updated successfully.",
                "join_code": chama.join_code if getattr(chama, "join_enabled", True) else "",
                "join_enabled": getattr(chama, "join_enabled", True),
                "join_code_expires_at": chama.join_code_expires_at,
                "join_mode": _resolved_join_mode(chama),
                "allow_public_join": chama.allow_public_join,
                "require_approval": chama.require_approval,
                "max_members": chama.max_members,
            },
            status=status.HTTP_200_OK,
        )


class JoinCodeEnableDisableView(ChamaScopeMixin, APIView):
    """Enable or disable the join code for a Chama."""
    permission_classes = [permissions.IsAuthenticated, IsChamaAdmin]

    def post(self, request, *args, **kwargs):
        """Enable join code by generating one if not exists."""
        chama = self.get_scoped_chama()

        if not getattr(chama, "join_enabled", True) or not chama.join_code:
            chama.join_enabled = True
            if not chama.join_code:
                chama.generate_join_code()
            else:
                if not chama.join_code_expires_at:
                    chama.join_code_expires_at = timezone.now() + timedelta(days=30)
                chama.save(update_fields=["join_enabled", "join_code_expires_at"])
            action = "join_code_enabled"
        else:
            action = "join_code_accessed"
        
        create_audit_log(
            actor=request.user,
            chama_id=chama.id,
            action=action,
            entity_type="Chama",
            entity_id=chama.id,
            metadata={"join_code": chama.join_code},
        )
        
        return Response(
            {
                "detail": "Join code is now enabled.",
                "join_code": chama.join_code,
                "join_enabled": True,
                "join_mode": _resolved_join_mode(chama),
                "join_code_expires_at": chama.join_code_expires_at,
            },
            status=status.HTTP_200_OK,
        )

    def delete(self, request, *args, **kwargs):
        """Disable join code by marking it inactive."""
        chama = self.get_scoped_chama()
        old_code = chama.join_code
        
        chama.join_enabled = False
        chama.join_code_expires_at = None
        chama.save(update_fields=["join_enabled", "join_code_expires_at"])
        
        create_audit_log(
            actor=request.user,
            chama_id=chama.id,
            action="join_code_disabled",
            entity_type="Chama",
            entity_id=chama.id,
            metadata={"old_code": old_code},
        )
        
        return Response(
            {"detail": "Join code has been disabled.", "join_enabled": False, "join_code": ""},
            status=status.HTTP_200_OK,
        )


# ============================================
# User-Facing Membership Request Views
# ============================================


class MyMembershipRequestsView(APIView):
    """Get current user's membership requests."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        requests = MembershipRequest.objects.filter(
            user=request.user
        ).select_related("chama").order_by("-created_at")
        
        serializer = MembershipRequestSerializer(requests, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class MyMembershipsView(APIView):
    """Get current user's memberships (chamas they belong to)."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        memberships = Membership.objects.filter(
            user=request.user,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            exited_at__isnull=True,
        ).select_related("chama").order_by("-joined_at")
        
        data = []
        for m in memberships:
            data.append({
                "id": str(m.id),
                "chama_id": str(m.chama.id),
                "chama_name": m.chama.name,
                "role": m.role,
                "role_display": m.get_role_display(),
                "status": m.status,
                "joined_at": m.joined_at,
            })
        return Response(data, status=status.HTTP_200_OK)
