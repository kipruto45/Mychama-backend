from apps.accounts.models import UserPreference
from apps.chama.models import Membership, MemberStatus
from apps.chama.services import get_effective_role
from apps.notifications.models import Notification, NotificationInboxStatus


def ui_preferences(request):
    if not request.user.is_authenticated:
        return {
            "active_chama_id": None,
            "low_data_mode": False,
            "chama_switch_options": [],
            "unread_notifications_count": 0,
        }

    preference = UserPreference.objects.filter(user=request.user).first()
    active_chama_id = None
    low_data_mode = False
    if preference:
        active_chama_id = (
            str(preference.active_chama_id) if preference.active_chama_id else None
        )
        low_data_mode = bool(preference.low_data_mode)

    # RequestFactory requests used in tests can skip SessionMiddleware.
    session = getattr(request, "session", None)
    session_chama = session.get("active_chama_id") if session is not None else None
    if session_chama:
        active_chama_id = str(session_chama)

    memberships = list(
        Membership.objects.select_related("chama")
        .filter(
            user=request.user,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        )
        .order_by("chama__name")
    )
    chama_switch_options = [
        {
            "id": str(membership.chama_id),
            "name": membership.chama.name,
            "role": get_effective_role(request.user, membership.chama_id, membership)
            or membership.role,
        }
        for membership in memberships
    ]

    if not active_chama_id and chama_switch_options:
        active_chama_id = chama_switch_options[0]["id"]

    unread_notifications_count = Notification.objects.filter(
        recipient=request.user,
        inbox_status=NotificationInboxStatus.UNREAD,
    ).count()

    return {
        "active_chama_id": active_chama_id,
        "low_data_mode": low_data_mode,
        "chama_switch_options": chama_switch_options,
        "unread_notifications_count": unread_notifications_count,
    }
