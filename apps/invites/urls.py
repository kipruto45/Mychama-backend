from __future__ import annotations

from django.urls import path

from apps.chama.views import (
    GlobalInviteListView,
    InviteAcceptView,
    InviteCodeAcceptView,
    InviteCodeValidateView,
    InviteDeclineView,
    InviteResendView,
    InviteRevokeView,
    InviteTokenDetailView,
)

app_name = "invites"

urlpatterns = [
    path("", GlobalInviteListView.as_view(), name="invites-global-list"),
    path("code/validate/", InviteCodeValidateView.as_view(), name="invite-code-validate"),
    path("code/accept/", InviteCodeAcceptView.as_view(), name="invite-code-accept"),
    path("<str:token>/", InviteTokenDetailView.as_view(), name="invite-token-detail"),
    path("<str:token>/accept/", InviteAcceptView.as_view(), name="invite-token-accept"),
    path("<str:token>/decline/", InviteDeclineView.as_view(), name="invite-token-decline"),
    path("<uuid:id>/revoke/", InviteRevokeView.as_view(), name="invite-revoke-global"),
    path("<uuid:id>/resend/", InviteResendView.as_view(), name="invite-resend-global"),
]

