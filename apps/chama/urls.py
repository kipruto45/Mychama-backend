from django.urls import path

from apps.chama.smart_dashboard_views import (
    admin_action_center,
    ai_assistant_query,
    ai_quick_prompts,
    chama_health_score,
    smart_dashboard,
)
from apps.chama.views import (
    ChamaDetailView,
    ChamaListCreateView,
    GlobalInviteListView,
    InviteAcceptView,
    InviteCodeAcceptView,
    InviteCodeValidateView,
    InviteDeclineView,
    InviteDetailView,
    InviteJoinView,
    InviteLinkListCreateView,
    InviteLinkResendView,
    InviteLinkRevokeView,
    InviteListView,
    InviteResendView,
    InviteRevokeView,
    InviteTokenDetailView,
    InviteValidateView,
    JoinCodeEnableDisableView,
    JoinCodeJoinAliasView,
    JoinCodeJoinView,
    JoinCodeRotateView,
    JoinCodeSettingsView,
    JoinCodeValidateAliasView,
    JoinCodeValidateView,
    MembershipApproveView,
    MembershipListView,
    MembershipRejectView,
    MembershipRequestApproveView,
    MembershipRequestListView,
    MembershipRequestNeedsInfoView,
    MembershipRequestRejectView,
    MembershipRoleUpdateView,
    MyMembershipRequestsView,
    MyMembershipsView,
    RequestJoinView,
    RoleDelegationListCreateView,
    RoleDelegationRevokeView,
)
from apps.chama.wizard_views import (
    add_members,
    bank_setup,
    complete_wizard,
    contribution_setup,
    group_setup,
    loan_types,
    wizard_status,
)
from apps.invites.views import (
    AcceptInviteView,
    CreateInviteLinkView,
    ListInviteLinksView,
    RegenerateInviteView,
    RevokeInviteView,
    ShareMessageView,
    ValidateInviteView,
)

app_name = "chama"

urlpatterns = [
    path("invites/", GlobalInviteListView.as_view(), name="invites-global-list"),
    path("invites/code/validate/", InviteCodeValidateView.as_view(), name="invite-code-validate"),
    path("invites/code/accept/", InviteCodeAcceptView.as_view(), name="invite-code-accept"),
    # Keep static `code/*` routes above token routes to avoid collisions.
    path("invites/<str:token>/", InviteTokenDetailView.as_view(), name="invite-token-detail"),
    path("invites/<str:token>/accept/", InviteAcceptView.as_view(), name="invite-token-accept"),
    path("invites/<str:token>/decline/", InviteDeclineView.as_view(), name="invite-token-decline"),
    path("invites/<uuid:id>/revoke/", InviteRevokeView.as_view(), name="invite-revoke-global"),
    path("invites/<uuid:id>/resend/", InviteResendView.as_view(), name="invite-resend-global"),
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
    # Join request (support both trailing and non-trailing slash variants).
    path("<uuid:id>/request-join", RequestJoinView.as_view(), name="chama-request-join"),
    path("<uuid:id>/request-join/", RequestJoinView.as_view()),
    path(
        "<uuid:id>/membership-requests",
        MembershipRequestListView.as_view(),
        name="chama-membership-requests",
    ),
    path("<uuid:id>/membership-requests/", MembershipRequestListView.as_view()),
    path(
        "<uuid:id>/membership-requests/<uuid:request_id>/approve",
        MembershipRequestApproveView.as_view(),
        name="chama-membership-request-approve",
    ),
    path(
        "<uuid:id>/membership-requests/<uuid:request_id>/approve/",
        MembershipRequestApproveView.as_view(),
    ),
    path(
        "<uuid:id>/membership-requests/<uuid:request_id>/reject",
        MembershipRequestRejectView.as_view(),
        name="chama-membership-request-reject",
    ),
    path(
        "<uuid:id>/membership-requests/<uuid:request_id>/reject/",
        MembershipRequestRejectView.as_view(),
    ),
    path(
        "<uuid:id>/membership-requests/<uuid:request_id>/needs-info",
        MembershipRequestNeedsInfoView.as_view(),
        name="chama-membership-request-needs-info",
    ),
    path(
        "<uuid:id>/membership-requests/<uuid:request_id>/needs-info/",
        MembershipRequestNeedsInfoView.as_view(),
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
    path("<uuid:id>/members/", MembershipListView.as_view(), name="chama-members"),
    path("<uuid:id>/members", MembershipListView.as_view()),
    # Backward/forward compatible alias: some clients use `memberships` wording.
    path("<uuid:id>/memberships/", MembershipListView.as_view(), name="chama-memberships"),
    path("<uuid:id>/memberships", MembershipListView.as_view()),
    path(
        "<uuid:id>/members/<uuid:membership_id>/approve",
        MembershipApproveView.as_view(),
        name="chama-member-approve",
    ),
    path("<uuid:id>/members/<uuid:membership_id>/approve/", MembershipApproveView.as_view()),
    path(
        "<uuid:id>/members/<uuid:membership_id>/reject",
        MembershipRejectView.as_view(),
        name="chama-member-reject",
    ),
    path("<uuid:id>/members/<uuid:membership_id>/reject/", MembershipRejectView.as_view()),
    path(
        "<uuid:id>/members/<uuid:membership_id>/role",
        MembershipRoleUpdateView.as_view(),
        name="chama-member-role",
    ),
    path("<uuid:id>/members/<uuid:membership_id>/role/", MembershipRoleUpdateView.as_view()),
    path(
        "<uuid:id>/role-delegations",
        RoleDelegationListCreateView.as_view(),
        name="chama-role-delegations",
    ),
    path("<uuid:id>/role-delegations/", RoleDelegationListCreateView.as_view()),
    path(
        "<uuid:id>/role-delegations/<uuid:delegation_id>/revoke",
        RoleDelegationRevokeView.as_view(),
        name="chama-role-delegation-revoke",
    ),
    path(
        "<uuid:id>/role-delegations/<uuid:delegation_id>/revoke/",
        RoleDelegationRevokeView.as_view(),
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
    
    # Smart Dashboard endpoints
    path("<uuid:chama_id>/smart-dashboard/", smart_dashboard, name="smart-dashboard"),
    path("<uuid:chama_id>/admin-action-center/", admin_action_center, name="admin-action-center"),
    path("<uuid:chama_id>/ai-assistant/", ai_assistant_query, name="ai-assistant-query"),
    path("<uuid:chama_id>/ai-quick-prompts/", ai_quick_prompts, name="ai-quick-prompts"),
    path("<uuid:chama_id>/health-score/", chama_health_score, name="chama-health-score"),
    
    # Mobile Invite endpoints
    path("invites/mobile/create/<uuid:chama_id>/", CreateInviteLinkView.as_view(), name="mobile-invite-create"),
    path("invites/mobile/validate/<str:token>/", ValidateInviteView.as_view(), name="mobile-invite-validate"),
    path("invites/mobile/accept/", AcceptInviteView.as_view(), name="mobile-invite-accept"),
    path("invites/mobile/revoke/<str:token>/", RevokeInviteView.as_view(), name="mobile-invite-revoke"),
    path("invites/mobile/regenerate/<str:token>/", RegenerateInviteView.as_view(), name="mobile-invite-regenerate"),
    path("invites/mobile/list/<uuid:chama_id>/", ListInviteLinksView.as_view(), name="mobile-invite-list"),
    path("invites/mobile/share/<str:token>/", ShareMessageView.as_view(), name="mobile-invite-share"),
]
