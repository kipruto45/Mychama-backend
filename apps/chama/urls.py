from django.urls import path

from apps.chama.views import (
    ChamaDetailView,
    ChamaListCreateView,
    InviteDetailView,
    InviteListView,
    JoinCodeJoinAliasView,
    JoinCodeValidateView,
    JoinCodeValidateAliasView,
    JoinCodeJoinView,
    JoinCodeRotateView,
    JoinCodeSettingsView,
    JoinCodeEnableDisableView,
    MembershipApproveView,
    MembershipListView,
    MembershipRequestApproveView,
    MembershipRequestListView,
    MembershipRequestNeedsInfoView,
    MembershipRequestRejectView,
    MembershipRejectView,
    MembershipRoleUpdateView,
    InviteLinkListCreateView,
    InviteLinkResendView,
    InviteLinkRevokeView,
    RoleDelegationListCreateView,
    RoleDelegationRevokeView,
    RequestJoinView,
    InviteValidateView,
    InviteJoinView,
    MyMembershipRequestsView,
    MyMembershipsView,
)
from apps.chama.wizard_views import (
    wizard_status,
    group_setup,
    add_members,
    contribution_setup,
    loan_types,
    bank_setup,
    complete_wizard,
)

app_name = "chama"

urlpatterns = [
    # Join Code Management (Admin)
    path(
        "<uuid:id>/join-code/rotate/",
        JoinCodeRotateView.as_view(),
        name="join-code-rotate",
    ),
    path(
        "<uuid:id>/join-code/settings/",
        JoinCodeSettingsView.as_view(),
        name="join-code-settings",
    ),
    path(
        "<uuid:id>/join-code/",
        JoinCodeEnableDisableView.as_view(),
        name="join-code-enable-disable",
    ),
    # Public join code endpoints (no auth required)
    path(
        "join-codes/validate/<str:code>/",
        JoinCodeValidateView.as_view(),
        name="join-code-validate",
    ),
    path(
        "join-codes/validate/<str:code>",
        JoinCodeValidateView.as_view(),
    ),
    path("join-code/validate", JoinCodeValidateAliasView.as_view(), name="join-code-validate-alias"),
    # Public invite endpoints
    path(
        "invites/validate/<str:token>/",
        InviteValidateView.as_view(),
        name="invite-validate",
    ),
    path("invites/validate/<str:token>", InviteValidateView.as_view()),
    path(
        "invites/<str:token>/join/",
        InviteJoinView.as_view(),
        name="invite-join",
    ),
    path("invites/<str:token>/join", InviteJoinView.as_view()),
    # Authenticated join code endpoint
    path(
        "join-codes/<str:code>/join/",
        JoinCodeJoinView.as_view(),
        name="join-code-join",
    ),
    path(
        "join-codes/<str:code>/join",
        JoinCodeJoinView.as_view(),
    ),
    path("join", JoinCodeJoinAliasView.as_view(), name="join-code-join-alias"),
    path("", ChamaListCreateView.as_view(), name="chama-list-create"),
    # Backward-compatible alias for tests/clients that reverse with `pk`.
    path("<uuid:pk>/", ChamaDetailView.as_view(), name="chama-detail"),
    path("<uuid:id>/", ChamaDetailView.as_view(), name="chama-detail"),
    path("<uuid:id>/request-join", RequestJoinView.as_view(), name="chama-request-join"),
    path(
        "<uuid:id>/membership-requests",
        MembershipRequestListView.as_view(),
        name="chama-membership-requests",
    ),
    path(
        "<uuid:id>/membership-requests/<uuid:request_id>/approve",
        MembershipRequestApproveView.as_view(),
        name="chama-membership-request-approve",
    ),
    path(
        "<uuid:id>/membership-requests/<uuid:request_id>/reject",
        MembershipRequestRejectView.as_view(),
        name="chama-membership-request-reject",
    ),
    path(
        "<uuid:id>/membership-requests/<uuid:request_id>/needs-info",
        MembershipRequestNeedsInfoView.as_view(),
        name="chama-membership-request-needs-info",
    ),
    path(
        "<uuid:id>/invite-links/",
        InviteLinkListCreateView.as_view(),
        name="chama-invite-links",
    ),
    path("<uuid:id>/invite-links", InviteLinkListCreateView.as_view()),
    path(
        "<uuid:id>/invite-links/<uuid:invite_id>/resend/",
        InviteLinkResendView.as_view(),
        name="chama-invite-link-resend",
    ),
    path("<uuid:id>/invite-links/<uuid:invite_id>/resend", InviteLinkResendView.as_view()),
    path(
        "<uuid:id>/invite-links/<uuid:invite_id>/revoke/",
        InviteLinkRevokeView.as_view(),
        name="chama-invite-link-revoke",
    ),
    path("<uuid:id>/invite-links/<uuid:invite_id>/revoke", InviteLinkRevokeView.as_view()),
    path(
        "<uuid:id>/invites/",
        InviteListView.as_view(),
        name="chama-invites",
    ),
    path("<uuid:id>/invites", InviteListView.as_view()),
    path(
        "<uuid:id>/invites/<uuid:invite_id>/",
        InviteDetailView.as_view(),
        name="chama-invite-detail",
    ),
    path("<uuid:id>/invites/<uuid:invite_id>", InviteDetailView.as_view()),
    path("<uuid:id>/members", MembershipListView.as_view(), name="chama-members"),
    path(
        "<uuid:id>/members/<uuid:membership_id>/approve",
        MembershipApproveView.as_view(),
        name="chama-member-approve",
    ),
    path(
        "<uuid:id>/members/<uuid:membership_id>/reject",
        MembershipRejectView.as_view(),
        name="chama-member-reject",
    ),
    path(
        "<uuid:id>/members/<uuid:membership_id>/role",
        MembershipRoleUpdateView.as_view(),
        name="chama-member-role",
    ),
    path(
        "<uuid:id>/role-delegations",
        RoleDelegationListCreateView.as_view(),
        name="chama-role-delegations",
    ),
    path(
        "<uuid:id>/role-delegations/<uuid:delegation_id>/revoke",
        RoleDelegationRevokeView.as_view(),
        name="chama-role-delegation-revoke",
    ),
    # Wizard endpoints
    path("wizard/status", wizard_status, name="wizard-status"),
    path("wizard/group-setup", group_setup, name="wizard-group-setup"),
    path("wizard/add-members", add_members, name="wizard-add-members"),
    path("wizard/contribution-setup", contribution_setup, name="wizard-contribution-setup"),
    path("wizard/loan-types", loan_types, name="wizard-loan-types"),
    path("wizard/bank-setup", bank_setup, name="wizard-bank-setup"),
    path("wizard/complete", complete_wizard, name="wizard-complete"),
    
    # User-facing endpoints (no chama scope required)
    path("my/membership-requests", MyMembershipRequestsView.as_view(), name="my-membership-requests"),
    path("my/memberships", MyMembershipsView.as_view(), name="my-memberships"),
]
