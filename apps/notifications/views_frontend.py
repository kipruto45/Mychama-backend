from __future__ import annotations

from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import TemplateView

from apps.chama.models import Membership, MembershipRole, MemberStatus
from apps.chama.services import get_effective_role
from apps.notifications.models import (
    BroadcastAnnouncement,
    BroadcastAnnouncementStatus,
    BroadcastTarget,
    Notification,
    NotificationInboxStatus,
    NotificationPreference,
    NotificationPriority,
    NotificationType,
)
from apps.notifications.services import NotificationService

ANNOUNCEMENT_ROLES = {
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.SECRETARY,
    MembershipRole.TREASURER,
}


def _resolve_membership(request):
    scoped_chama_id = request.GET.get("chama_id") or request.session.get("active_chama_id")
    memberships = (
        Membership.objects.select_related("chama")
        .filter(
            user=request.user,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        )
        .order_by("joined_at")
    )

    membership = None
    if scoped_chama_id:
        membership = memberships.filter(chama_id=scoped_chama_id).first()

    if membership is None:
        membership = memberships.first()

    if membership:
        request.session["active_chama_id"] = str(membership.chama_id)

    return membership


def _normalize_priority(raw_value: str) -> str:
    value = (raw_value or "").strip().lower()
    mapping = {
        "low": NotificationPriority.LOW,
        "normal": NotificationPriority.NORMAL,
        "medium": NotificationPriority.NORMAL,
        "high": NotificationPriority.HIGH,
        "urgent": NotificationPriority.HIGH,
        "critical": NotificationPriority.CRITICAL,
    }
    return mapping.get(value, NotificationPriority.NORMAL)


def _notification_queryset_for_request(request, membership):
    queryset = Notification.objects.filter(recipient=request.user)
    if membership:
        queryset = queryset.filter(chama=membership.chama)
    return queryset.order_by("-created_at")


def _get_or_create_preference(request, membership):
    if not membership:
        return None
    preference, _ = NotificationPreference.objects.get_or_create(
        user=request.user,
        chama=membership.chama,
        defaults={"created_by": request.user, "updated_by": request.user},
    )
    return preference


@method_decorator(login_required, name="dispatch")
class NotificationListView(TemplateView):
    template_name = "notifications/inbox.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        queryset = _notification_queryset_for_request(self.request, membership)

        status_filter = (self.request.GET.get("status") or "").strip().lower()
        if status_filter in {
            NotificationInboxStatus.UNREAD,
            NotificationInboxStatus.READ,
            NotificationInboxStatus.ARCHIVED,
        }:
            queryset = queryset.filter(inbox_status=status_filter)

        category = (self.request.GET.get("category") or "").strip().lower()
        if category:
            queryset = queryset.filter(category=category)

        context["notifications"] = queryset[:200]
        context["status_filter"] = status_filter
        context["category_filter"] = category
        context["title"] = "Notifications"
        context["active_membership"] = membership
        return context


@method_decorator(login_required, name="dispatch")
class NotificationDetailView(TemplateView):
    template_name = "notifications/detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        notification = get_object_or_404(
            _notification_queryset_for_request(self.request, membership),
            id=self.kwargs["notification_id"],
        )
        if notification.inbox_status == NotificationInboxStatus.UNREAD:
            notification.inbox_status = NotificationInboxStatus.READ
            notification.read_at = timezone.now()
            notification.save(update_fields=["inbox_status", "read_at", "updated_at"])

        context["notification"] = notification
        context["title"] = "Notification Detail"
        context["active_membership"] = membership
        return context


@method_decorator(login_required, name="dispatch")
class NotificationPopupView(TemplateView):
    template_name = "notifications/notification_popup.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["notifications"] = _notification_queryset_for_request(
            self.request,
            membership,
        )[:200]
        context["title"] = "Notification Center"
        context["active_membership"] = membership
        return context


@method_decorator(login_required, name="dispatch")
class NotificationPopupDetailView(TemplateView):
    template_name = "notifications/notification_popup_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        notification = get_object_or_404(
            _notification_queryset_for_request(self.request, membership),
            id=self.kwargs["notification_id"],
        )
        if notification.inbox_status == NotificationInboxStatus.UNREAD:
            notification.inbox_status = NotificationInboxStatus.READ
            notification.read_at = timezone.now()
            notification.save(update_fields=["inbox_status", "read_at", "updated_at"])

        context["notification"] = notification
        context["title"] = "Notification Detail"
        context["active_membership"] = membership
        return context


@method_decorator(login_required, name="dispatch")
class NotificationDeleteView(View):
    def post(self, request, *args, **kwargs):
        membership = _resolve_membership(request)
        notification = get_object_or_404(
            _notification_queryset_for_request(request, membership),
            id=kwargs["notification_id"],
        )
        notification.inbox_status = NotificationInboxStatus.ARCHIVED
        notification.save(update_fields=["inbox_status", "updated_at"])
        messages.success(request, "Notification removed.")
        return redirect("notifications:popup")


@method_decorator(login_required, name="dispatch")
class NotificationSettingsView(TemplateView):
    template_name = "notifications/preferences.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["active_membership"] = membership
        context["title"] = "Notification Preferences"
        context["preference"] = _get_or_create_preference(self.request, membership)
        return context

    def post(self, request, *args, **kwargs):
        membership = _resolve_membership(request)
        preference = _get_or_create_preference(request, membership)
        if not preference:
            messages.error(request, "Join an active approved chama first.")
            return redirect("notifications:inbox")

        preference.sms_enabled = bool(request.POST.get("sms_enabled"))
        preference.email_enabled = bool(request.POST.get("email_enabled"))
        preference.in_app_enabled = bool(request.POST.get("in_app_enabled"))
        preference.critical_only_mode = bool(request.POST.get("critical_only_mode"))

        quiet_start = (request.POST.get("quiet_hours_start") or "").strip()
        quiet_end = (request.POST.get("quiet_hours_end") or "").strip()
        language = (request.POST.get("language") or "en").strip().lower()

        if quiet_start:
            try:
                preference.quiet_hours_start = datetime.strptime(quiet_start, "%H:%M").time()
            except ValueError:
                messages.error(request, "Invalid quiet start time. Use HH:MM.")
                return self.render_to_response(self.get_context_data())

        if quiet_end:
            try:
                preference.quiet_hours_end = datetime.strptime(quiet_end, "%H:%M").time()
            except ValueError:
                messages.error(request, "Invalid quiet end time. Use HH:MM.")
                return self.render_to_response(self.get_context_data())

        if language in {"en", "sw"}:
            preference.language = language

        preference.updated_by = request.user
        preference.save()

        messages.success(request, "Notification preferences updated successfully.")
        return redirect("notifications:preferences")


@method_decorator(login_required, name="dispatch")
class BroadcastCreateView(TemplateView):
    template_name = "notifications/broadcast_create.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        context["title"] = "Broadcast Announcement"
        context["active_membership"] = membership
        return context

    def post(self, request, *args, **kwargs):
        membership = _resolve_membership(request)
        if not membership:
            messages.error(request, "You must belong to an approved active chama.")
            return redirect("chama:chama_create")

        effective_role = get_effective_role(request.user, membership.chama_id, membership)
        if effective_role not in ANNOUNCEMENT_ROLES:
            messages.error(
                request,
                "Only chama admins, secretaries, or treasurers can broadcast announcements.",
            )
            return redirect("notifications:inbox")

        title = (request.POST.get("title") or "").strip()
        message_text = (request.POST.get("message") or "").strip()
        priority = _normalize_priority(request.POST.get("priority"))
        send_email = bool(request.POST.get("send_email"))
        send_sms = bool(request.POST.get("send_sms"))
        send_push = bool(request.POST.get("send_push")) or True

        target = (request.POST.get("target") or BroadcastTarget.ALL).strip().lower()
        if target not in {BroadcastTarget.ALL, BroadcastTarget.ROLE, BroadcastTarget.SPECIFIC}:
            target = BroadcastTarget.ALL

        if not title or not message_text:
            messages.error(request, "Title and message are required.")
            return self.render_to_response(self.get_context_data())

        announcement = BroadcastAnnouncement.objects.create(
            chama=membership.chama,
            title=title,
            message=message_text,
            target=target,
            channels=[
                *(["email"] if send_email else []),
                *(["sms"] if send_sms else []),
                *(["in_app"] if send_push else []),
            ]
            or ["in_app"],
            status=BroadcastAnnouncementStatus.PENDING,
            created_by=request.user,
            updated_by=request.user,
        )

        members_qs = Membership.objects.select_related("user").filter(
            chama=membership.chama,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        )

        raw_role_filter = request.POST.getlist("target_roles")
        role_filter: list[str] = []
        for chunk in raw_role_filter:
            for role_item in str(chunk).split(","):
                role_item = role_item.strip().upper()
                if role_item:
                    role_filter.append(role_item)
        if target == BroadcastTarget.ROLE and role_filter:
            members_qs = members_qs.filter(role__in=role_filter)
            announcement.target_roles = role_filter
            announcement.save(update_fields=["target_roles", "updated_at"])

        specific_member_ids = request.POST.getlist("target_member_ids")
        if target == BroadcastTarget.SPECIFIC and specific_member_ids:
            members_qs = members_qs.filter(user_id__in=specific_member_ids)
            announcement.target_member_ids = specific_member_ids
            announcement.save(update_fields=["target_member_ids", "updated_at"])

        created_count = 0
        for target_membership in members_qs:
            NotificationService.send_notification(
                user=target_membership.user,
                chama=membership.chama,
                channels=announcement.channels,
                message=message_text,
                subject=title,
                notification_type=NotificationType.GENERAL_ANNOUNCEMENT,
                priority=priority,
                idempotency_key=f"broadcast-web:{announcement.id}:{target_membership.user_id}",
                actor=request.user,
            )
            created_count += 1

        announcement.status = BroadcastAnnouncementStatus.SENT
        announcement.sent_at = timezone.now()
        announcement.save(update_fields=["status", "sent_at", "updated_at"])

        messages.success(request, f"Announcement sent to {created_count} member(s).")
        return redirect("notifications:broadcast_history")


@method_decorator(login_required, name="dispatch")
class BroadcastHistoryView(TemplateView):
    template_name = "notifications/broadcast_history.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _resolve_membership(self.request)
        if membership and get_effective_role(
            self.request.user,
            membership.chama_id,
            membership,
        ) in ANNOUNCEMENT_ROLES.union({MembershipRole.AUDITOR}):
            history = BroadcastAnnouncement.objects.filter(chama=membership.chama).order_by(
                "-created_at"
            )[:200]
        else:
            history = BroadcastAnnouncement.objects.none()

        context["history"] = history
        context["title"] = "Broadcast History"
        context["active_membership"] = membership
        return context


# Function-based views for backward compatibility.
@login_required
def notification_list_view(request):
    return NotificationListView.as_view()(request)


@login_required
def notification_detail_view(request, notification_id):
    return NotificationDetailView.as_view()(request, notification_id=notification_id)


@login_required
def notification_settings_view(request):
    return NotificationSettingsView.as_view()(request)


@login_required
def notification_popup_view(request):
    return NotificationPopupView.as_view()(request)


@login_required
def notification_popup_detail_view(request, notification_id):
    return NotificationPopupDetailView.as_view()(request, notification_id=notification_id)


@login_required
def notification_delete_view(request, notification_id):
    return NotificationDeleteView.as_view()(request, notification_id=notification_id)


@login_required
def create_announcement_view(request):
    return BroadcastCreateView.as_view()(request)


@login_required
def broadcast_history_view(request):
    return BroadcastHistoryView.as_view()(request)
