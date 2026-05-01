from django.urls import path

from . import views_frontend

app_name = "notifications"

urlpatterns = [
    path("", views_frontend.notification_list_view, name="inbox"),
    path("list/", views_frontend.notification_list_view, name="notification_list"),
    path(
        "<uuid:notification_id>/",
        views_frontend.notification_detail_view,
        name="detail",
    ),
    path("popup/", views_frontend.notification_popup_view, name="popup"),
    path(
        "popup/<uuid:notification_id>/",
        views_frontend.notification_popup_detail_view,
        name="popup_detail",
    ),
    path(
        "popup/<uuid:notification_id>/delete/",
        views_frontend.notification_delete_view,
        name="popup_delete",
    ),
    path("preferences/", views_frontend.notification_settings_view, name="preferences"),
    path(
        "settings/",
        views_frontend.notification_settings_view,
        name="notification_settings",
    ),
    path(
        "broadcast/create/",
        views_frontend.create_announcement_view,
        name="broadcast_create",
    ),
    path(
        "broadcast/history/",
        views_frontend.broadcast_history_view,
        name="broadcast_history",
    ),
    # Legacy aliases used by dashboards.
    path("create-announcement/", views_frontend.create_announcement_view, name="create_announcement"),
    path("send/", views_frontend.create_announcement_view, name="send"),
    path("announcements/", views_frontend.notification_list_view, name="announcements"),
]
