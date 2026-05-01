from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.chama.models import MembershipRole

ScreenKey = str
FieldTag = str
ActionCode = str
RoleCode = str
DataScope = str


@dataclass(frozen=True)
class BackendScreenPolicy:
    visible: bool
    read_only: bool
    data_scope: DataScope
    allowed_actions: tuple[ActionCode, ...]
    blocked_actions: tuple[ActionCode, ...]
    hidden_field_tags: tuple[FieldTag, ...]


def policy(
    *,
    visible: bool = True,
    read_only: bool = False,
    data_scope: DataScope,
    allowed_actions: tuple[ActionCode, ...] = (),
    blocked_actions: tuple[ActionCode, ...] = (),
    hidden_field_tags: tuple[FieldTag, ...] = (),
) -> BackendScreenPolicy:
    return BackendScreenPolicy(
        visible=visible,
        read_only=read_only,
        data_scope=data_scope,
        allowed_actions=allowed_actions,
        blocked_actions=blocked_actions,
        hidden_field_tags=hidden_field_tags,
    )


PLATFORM_ONLY = ("platform", "provider", "infrastructure")
MODERATION_ONLY = ("moderation", "support_resolution")
ROLE_CONTROL = ("role_control", "member_lifecycle")
FINANCE_PRIVATE = ("finance_private", "balance_private", "payment_ops")
GOVERNANCE_DRAFTS = ("governance_draft", "minutes_draft")
AUDIT_ONLY = ("audit_private", "audit_write")
MUTATION_ONLY = ("mutation",)


SCREEN_ROLE_POLICIES: dict[ScreenKey, dict[RoleCode, BackendScreenPolicy]] = {
    "dashboard": {
        MembershipRole.SUPERADMIN: policy(
            data_scope="platform_all",
            allowed_actions=("view_platform_health", "view_provider_status", "view_risk_flags"),
            hidden_field_tags=("member_private",),
        ),
        MembershipRole.ADMIN: policy(
            data_scope="platform_support",
            allowed_actions=("view_support_queue", "view_disputes", "view_moderation"),
            blocked_actions=("assign_roles", "verify_payments", "edit_governance"),
            hidden_field_tags=("member_private", "finance_private", "provider"),
        ),
        MembershipRole.CHAMA_ADMIN: policy(
            data_scope="scoped_chama",
            allowed_actions=("view_approvals", "manage_members", "manage_governance"),
            blocked_actions=("platform_admin", "provider_admin"),
            hidden_field_tags=PLATFORM_ONLY,
        ),
        MembershipRole.TREASURER: policy(
            data_scope="scoped_chama",
            allowed_actions=("verify_payments", "view_reconciliation", "view_finance_summary"),
            blocked_actions=("assign_roles", "remove_members", "finalize_governance"),
            hidden_field_tags=PLATFORM_ONLY + ("governance_draft",),
        ),
        MembershipRole.SECRETARY: policy(
            data_scope="scoped_chama",
            allowed_actions=("manage_meetings", "record_minutes", "send_announcements"),
            blocked_actions=("verify_payments", "approve_loans", "close_month"),
            hidden_field_tags=PLATFORM_ONLY + FINANCE_PRIVATE,
        ),
        MembershipRole.AUDITOR: policy(
            data_scope="scoped_chama",
            read_only=True,
            allowed_actions=("view_compliance", "view_audit_alerts"),
            blocked_actions=("create", "edit", "delete", "approve"),
            hidden_field_tags=MUTATION_ONLY + GOVERNANCE_DRAFTS + AUDIT_ONLY,
        ),
        MembershipRole.MEMBER: policy(
            data_scope="own_records",
            allowed_actions=("view_due_items", "request_loan", "make_payment"),
            blocked_actions=("approve", "verify_payments", "assign_roles", "view_platform_health"),
            hidden_field_tags=PLATFORM_ONLY + MODERATION_ONLY + FINANCE_PRIVATE + AUDIT_ONLY + ROLE_CONTROL,
        ),
    },
    "chamas_list": {
        MembershipRole.SUPERADMIN: policy(data_scope="platform_all", allowed_actions=("view_chamas", "moderate_chama")),
        MembershipRole.ADMIN: policy(
            data_scope="platform_moderation",
            allowed_actions=("view_chamas", "view_flagged_chamas"),
            blocked_actions=("edit_chama",),
            hidden_field_tags=("finance_private",),
        ),
        MembershipRole.CHAMA_ADMIN: policy(data_scope="scoped_chama", allowed_actions=("view_chamas", "open_workspace")),
        MembershipRole.TREASURER: policy(data_scope="scoped_chama", allowed_actions=("view_chamas", "open_workspace")),
        MembershipRole.SECRETARY: policy(data_scope="scoped_chama", allowed_actions=("view_chamas", "open_workspace")),
        MembershipRole.AUDITOR: policy(
            data_scope="scoped_chama",
            read_only=True,
            allowed_actions=("view_chamas", "open_workspace"),
        ),
        MembershipRole.MEMBER: policy(data_scope="own_records", allowed_actions=("view_chamas", "open_workspace")),
    },
    "chama_details": {
        MembershipRole.SUPERADMIN: policy(
            data_scope="investigation_only",
            read_only=True,
            allowed_actions=("view_chama_details", "view_system_metadata"),
            hidden_field_tags=("member_private",),
        ),
        MembershipRole.ADMIN: policy(
            data_scope="platform_moderation",
            read_only=True,
            allowed_actions=("view_chama_details", "view_support_context"),
            hidden_field_tags=FINANCE_PRIVATE + ROLE_CONTROL,
        ),
        MembershipRole.CHAMA_ADMIN: policy(
            data_scope="scoped_chama",
            allowed_actions=("view_chama_details", "edit_chama", "manage_documents", "manage_policies"),
            hidden_field_tags=PLATFORM_ONLY,
        ),
        MembershipRole.TREASURER: policy(
            data_scope="scoped_chama",
            allowed_actions=("view_chama_details", "view_finance_tab"),
            blocked_actions=("assign_roles", "edit_policies"),
            hidden_field_tags=PLATFORM_ONLY + GOVERNANCE_DRAFTS,
        ),
        MembershipRole.SECRETARY: policy(
            data_scope="scoped_chama",
            allowed_actions=("view_chama_details", "view_meeting_records", "view_announcements"),
            blocked_actions=("view_finance_tab",),
            hidden_field_tags=PLATFORM_ONLY + FINANCE_PRIVATE,
        ),
        MembershipRole.AUDITOR: policy(
            data_scope="scoped_chama",
            read_only=True,
            allowed_actions=("view_chama_details", "view_history"),
            hidden_field_tags=MUTATION_ONLY,
        ),
        MembershipRole.MEMBER: policy(
            data_scope="own_records",
            allowed_actions=("view_chama_details", "view_rules", "view_announcements"),
            hidden_field_tags=PLATFORM_ONLY + MODERATION_ONLY + FINANCE_PRIVATE + GOVERNANCE_DRAFTS,
        ),
    },
    "members": {
        MembershipRole.SUPERADMIN: policy(
            data_scope="platform_all",
            allowed_actions=("view_members", "moderate_member"),
            hidden_field_tags=("payment_token",),
        ),
        MembershipRole.ADMIN: policy(
            data_scope="platform_moderation",
            allowed_actions=("view_members", "view_case_history"),
            blocked_actions=("assign_roles", "suspend_member", "remove_member"),
            hidden_field_tags=FINANCE_PRIVATE + ROLE_CONTROL,
        ),
        MembershipRole.CHAMA_ADMIN: policy(
            data_scope="scoped_chama",
            allowed_actions=("view_members", "assign_roles", "suspend_member", "remove_member"),
            hidden_field_tags=PLATFORM_ONLY,
        ),
        MembershipRole.TREASURER: policy(
            data_scope="scoped_chama",
            allowed_actions=("view_members", "view_financial_profile"),
            blocked_actions=("assign_roles", "suspend_member", "remove_member"),
            hidden_field_tags=PLATFORM_ONLY + MODERATION_ONLY + ROLE_CONTROL,
        ),
        MembershipRole.SECRETARY: policy(
            data_scope="scoped_chama",
            allowed_actions=("view_members", "view_participation_profile", "message_member"),
            blocked_actions=("assign_roles", "suspend_member", "remove_member"),
            hidden_field_tags=PLATFORM_ONLY + FINANCE_PRIVATE + ROLE_CONTROL,
        ),
        MembershipRole.AUDITOR: policy(
            data_scope="scoped_chama",
            read_only=True,
            allowed_actions=("view_members", "view_role_history", "view_audit_history"),
            blocked_actions=("assign_roles", "suspend_member", "remove_member"),
            hidden_field_tags=MUTATION_ONLY,
        ),
        MembershipRole.MEMBER: policy(
            data_scope="own_records",
            read_only=True,
            allowed_actions=("view_member_directory",),
            blocked_actions=("view_financial_profile", "assign_roles", "remove_member"),
            hidden_field_tags=PLATFORM_ONLY + MODERATION_ONLY + FINANCE_PRIVATE + AUDIT_ONLY + ROLE_CONTROL,
        ),
    },
    "contributions": {
        MembershipRole.SUPERADMIN: policy(
            data_scope="platform_all",
            read_only=True,
            allowed_actions=("view_contribution_analytics",),
            hidden_field_tags=("other_member_finance",),
        ),
        MembershipRole.ADMIN: policy(
            data_scope="platform_support",
            read_only=True,
            allowed_actions=("view_contribution_cases",),
            hidden_field_tags=("other_member_finance",),
        ),
        MembershipRole.CHAMA_ADMIN: policy(
            data_scope="scoped_chama",
            allowed_actions=("view_contributions", "review_compliance"),
            hidden_field_tags=PLATFORM_ONLY,
        ),
        MembershipRole.TREASURER: policy(
            data_scope="scoped_chama",
            allowed_actions=("view_contributions", "record_contribution", "export_contributions"),
            hidden_field_tags=PLATFORM_ONLY,
        ),
        MembershipRole.SECRETARY: policy(
            data_scope="scoped_chama",
            read_only=True,
            allowed_actions=("view_contributions", "send_reminders"),
            hidden_field_tags=PLATFORM_ONLY + ("verifier_assignment",) + FINANCE_PRIVATE,
        ),
        MembershipRole.AUDITOR: policy(
            data_scope="scoped_chama",
            read_only=True,
            allowed_actions=("view_contributions", "export_contributions"),
            hidden_field_tags=MUTATION_ONLY,
        ),
        MembershipRole.MEMBER: policy(
            data_scope="own_records",
            allowed_actions=("view_own_contributions", "make_payment"),
            hidden_field_tags=PLATFORM_ONLY + MODERATION_ONLY + ("other_member_finance",),
        ),
    },
    "finance": {
        MembershipRole.SUPERADMIN: policy(
            data_scope="platform_all",
            read_only=True,
            allowed_actions=("view_finance_health",),
        ),
        MembershipRole.ADMIN: policy(
            data_scope="platform_support",
            read_only=True,
            allowed_actions=("view_finance_exceptions",),
            hidden_field_tags=("member_private",),
        ),
        MembershipRole.CHAMA_ADMIN: policy(
            data_scope="scoped_chama",
            allowed_actions=("view_finance", "open_finance_ops"),
            hidden_field_tags=PLATFORM_ONLY,
        ),
        MembershipRole.TREASURER: policy(
            data_scope="scoped_chama",
            allowed_actions=("view_finance", "manage_finance"),
            hidden_field_tags=PLATFORM_ONLY,
        ),
        MembershipRole.SECRETARY: policy(
            visible=False,
            data_scope="scoped_chama",
            hidden_field_tags=PLATFORM_ONLY + FINANCE_PRIVATE,
        ),
        MembershipRole.AUDITOR: policy(
            data_scope="scoped_chama",
            read_only=True,
            allowed_actions=("view_finance",),
            hidden_field_tags=MUTATION_ONLY,
        ),
        MembershipRole.MEMBER: policy(
            visible=False,
            data_scope="own_records",
            hidden_field_tags=PLATFORM_ONLY + FINANCE_PRIVATE,
        ),
    },
    "payments": {
        MembershipRole.SUPERADMIN: policy(
            data_scope="platform_all",
            read_only=True,
            allowed_actions=("view_provider_payment_health",),
            hidden_field_tags=("receipt_private",),
        ),
        MembershipRole.ADMIN: policy(
            data_scope="platform_support",
            allowed_actions=("view_payment_disputes", "view_payment_failures"),
            blocked_actions=("verify_payments", "record_manual_payment"),
            hidden_field_tags=("payment_token", "receipt_private"),
        ),
        MembershipRole.CHAMA_ADMIN: policy(
            data_scope="scoped_chama",
            allowed_actions=("view_payment_exceptions",),
            blocked_actions=("verify_payments", "record_manual_payment"),
            hidden_field_tags=PLATFORM_ONLY,
        ),
        MembershipRole.TREASURER: policy(
            data_scope="scoped_chama",
            allowed_actions=("view_payments", "verify_payments", "open_reconciliation"),
            hidden_field_tags=PLATFORM_ONLY,
        ),
        MembershipRole.SECRETARY: policy(
            visible=False,
            data_scope="scoped_chama",
            blocked_actions=("view_payments", "verify_payments", "record_manual_payment"),
            hidden_field_tags=PLATFORM_ONLY + FINANCE_PRIVATE,
        ),
        MembershipRole.AUDITOR: policy(
            data_scope="scoped_chama",
            read_only=True,
            allowed_actions=("view_payments", "view_receipts"),
            blocked_actions=("verify_payments", "record_manual_payment"),
            hidden_field_tags=MUTATION_ONLY + AUDIT_ONLY,
        ),
        MembershipRole.MEMBER: policy(
            data_scope="own_records",
            allowed_actions=("view_own_payments", "make_payment", "retry_payment"),
            blocked_actions=("view_other_member_payments", "verify_payments"),
            hidden_field_tags=PLATFORM_ONLY + MODERATION_ONLY + ("other_member_finance", "reconciliation"),
        ),
    },
    "loans": {
        MembershipRole.SUPERADMIN: policy(
            data_scope="platform_all",
            read_only=True,
            allowed_actions=("view_loan_risk",),
            hidden_field_tags=("committee_note",),
        ),
        MembershipRole.ADMIN: policy(
            data_scope="platform_support",
            allowed_actions=("view_loan_escalations",),
            blocked_actions=("approve_loan", "disburse_loan"),
            hidden_field_tags=("committee_note", "recovery_write"),
        ),
        MembershipRole.CHAMA_ADMIN: policy(
            data_scope="scoped_chama",
            allowed_actions=("view_loans", "approve_loan", "disburse_loan", "view_recovery"),
            hidden_field_tags=PLATFORM_ONLY,
        ),
        MembershipRole.TREASURER: policy(
            data_scope="scoped_chama",
            allowed_actions=("view_loans", "approve_loan", "view_recovery"),
            blocked_actions=("disburse_loan",),
            hidden_field_tags=PLATFORM_ONLY,
        ),
        MembershipRole.SECRETARY: policy(
            visible=False,
            data_scope="scoped_chama",
            blocked_actions=("view_loans", "approve_loan", "disburse_loan"),
            hidden_field_tags=PLATFORM_ONLY + FINANCE_PRIVATE,
        ),
        MembershipRole.AUDITOR: policy(
            data_scope="scoped_chama",
            read_only=True,
            allowed_actions=("view_loans", "view_recovery"),
            blocked_actions=("approve_loan", "disburse_loan"),
            hidden_field_tags=MUTATION_ONLY + ("recovery_write",),
        ),
        MembershipRole.MEMBER: policy(
            data_scope="own_records",
            allowed_actions=("view_own_loans", "request_loan", "repay_loan"),
            blocked_actions=("view_other_member_loans", "approve_loan", "disburse_loan"),
            hidden_field_tags=PLATFORM_ONLY + MODERATION_ONLY + ("other_member_finance", "committee_note"),
        ),
    },
    "meetings": {
        MembershipRole.CHAMA_ADMIN: policy(
            data_scope="scoped_chama",
            allowed_actions=("create_meeting", "edit_meeting", "cancel_meeting"),
            hidden_field_tags=PLATFORM_ONLY,
        ),
        MembershipRole.TREASURER: policy(
            data_scope="scoped_chama",
            read_only=True,
            allowed_actions=("view_meetings",),
            hidden_field_tags=PLATFORM_ONLY + GOVERNANCE_DRAFTS,
        ),
        MembershipRole.SECRETARY: policy(
            data_scope="scoped_chama",
            allowed_actions=("create_meeting", "edit_meeting", "record_attendance", "record_minutes"),
            hidden_field_tags=PLATFORM_ONLY,
        ),
        MembershipRole.AUDITOR: policy(
            data_scope="scoped_chama",
            read_only=True,
            allowed_actions=("view_meetings",),
            hidden_field_tags=MUTATION_ONLY + GOVERNANCE_DRAFTS,
        ),
        MembershipRole.MEMBER: policy(
            data_scope="own_records",
            read_only=True,
            allowed_actions=("view_meetings",),
            hidden_field_tags=PLATFORM_ONLY + GOVERNANCE_DRAFTS,
        ),
    },
    "governance": {
        MembershipRole.CHAMA_ADMIN: policy(
            data_scope="scoped_chama",
            allowed_actions=("create_motion", "finalize_motion", "view_results"),
            hidden_field_tags=PLATFORM_ONLY,
        ),
        MembershipRole.TREASURER: policy(
            data_scope="scoped_chama",
            read_only=True,
            allowed_actions=("view_motions", "vote"),
            blocked_actions=("finalize_motion",),
            hidden_field_tags=PLATFORM_ONLY + GOVERNANCE_DRAFTS,
        ),
        MembershipRole.SECRETARY: policy(
            data_scope="scoped_chama",
            read_only=True,
            allowed_actions=("view_motions", "view_governance_records"),
            hidden_field_tags=PLATFORM_ONLY,
        ),
        MembershipRole.AUDITOR: policy(
            data_scope="scoped_chama",
            read_only=True,
            allowed_actions=("view_motions", "view_results"),
            blocked_actions=("create_motion", "finalize_motion"),
            hidden_field_tags=MUTATION_ONLY + GOVERNANCE_DRAFTS,
        ),
        MembershipRole.MEMBER: policy(
            data_scope="own_records",
            allowed_actions=("view_motions", "vote", "view_results"),
            blocked_actions=("create_motion", "finalize_motion"),
            hidden_field_tags=PLATFORM_ONLY + MODERATION_ONLY + GOVERNANCE_DRAFTS,
        ),
    },
    "announcements": {
        MembershipRole.SUPERADMIN: policy(visible=False, data_scope="platform_all", hidden_field_tags=("chama_internal",)),
        MembershipRole.ADMIN: policy(visible=False, data_scope="platform_support", hidden_field_tags=("chama_internal",)),
        MembershipRole.CHAMA_ADMIN: policy(
            data_scope="scoped_chama",
            allowed_actions=("create_announcement", "edit_announcement", "publish_announcement", "pin_announcement"),
        ),
        MembershipRole.TREASURER: policy(
            data_scope="scoped_chama",
            read_only=True,
            allowed_actions=("view_announcements",),
            hidden_field_tags=("announcement_draft",),
        ),
        MembershipRole.SECRETARY: policy(
            data_scope="scoped_chama",
            allowed_actions=("create_announcement", "edit_announcement", "publish_announcement"),
        ),
        MembershipRole.AUDITOR: policy(
            data_scope="scoped_chama",
            read_only=True,
            allowed_actions=("view_announcements",),
            hidden_field_tags=MUTATION_ONLY + ("announcement_draft",),
        ),
        MembershipRole.MEMBER: policy(
            data_scope="own_records",
            read_only=True,
            allowed_actions=("view_announcements",),
            hidden_field_tags=("announcement_draft",),
        ),
    },
    "notifications": {
        MembershipRole.SUPERADMIN: policy(data_scope="platform_all", allowed_actions=("view_notifications", "view_system_alerts")),
        MembershipRole.ADMIN: policy(data_scope="platform_support", allowed_actions=("view_notifications", "view_support_alerts")),
        MembershipRole.CHAMA_ADMIN: policy(data_scope="scoped_chama", allowed_actions=("view_notifications", "manage_preferences")),
        MembershipRole.TREASURER: policy(data_scope="scoped_chama", allowed_actions=("view_notifications", "manage_preferences")),
        MembershipRole.SECRETARY: policy(data_scope="scoped_chama", allowed_actions=("view_notifications", "manage_preferences")),
        MembershipRole.AUDITOR: policy(
            data_scope="scoped_chama",
            read_only=True,
            allowed_actions=("view_notifications", "manage_preferences"),
            hidden_field_tags=MUTATION_ONLY,
        ),
        MembershipRole.MEMBER: policy(data_scope="own_records", allowed_actions=("view_notifications", "manage_preferences")),
    },
    "reports": {
        MembershipRole.SUPERADMIN: policy(
            data_scope="platform_all",
            allowed_actions=("view_platform_reports", "export_reports"),
        ),
        MembershipRole.ADMIN: policy(
            data_scope="platform_support",
            allowed_actions=("view_operations_reports", "export_reports"),
            hidden_field_tags=("group_finance_payload",),
        ),
        MembershipRole.CHAMA_ADMIN: policy(
            data_scope="scoped_chama",
            allowed_actions=("view_reports", "export_reports"),
        ),
        MembershipRole.TREASURER: policy(
            data_scope="scoped_chama",
            allowed_actions=("view_finance_reports", "export_reports"),
            hidden_field_tags=("governance_private",),
        ),
        MembershipRole.SECRETARY: policy(
            data_scope="scoped_chama",
            allowed_actions=("view_meeting_reports",),
            blocked_actions=("view_finance_reports",),
            hidden_field_tags=("finance_private",),
        ),
        MembershipRole.AUDITOR: policy(
            data_scope="scoped_chama",
            read_only=True,
            allowed_actions=("view_audit_reports", "export_reports"),
        ),
        MembershipRole.MEMBER: policy(
            data_scope="own_records",
            read_only=True,
            allowed_actions=("view_personal_statement",),
            hidden_field_tags=("group_finance_payload", "audit_private"),
        ),
    },
    "profile": {
        MembershipRole.SUPERADMIN: policy(data_scope="own_records", allowed_actions=("view_profile", "edit_profile")),
        MembershipRole.ADMIN: policy(data_scope="own_records", allowed_actions=("view_profile", "edit_profile")),
        MembershipRole.CHAMA_ADMIN: policy(data_scope="own_records", allowed_actions=("view_profile", "edit_profile")),
        MembershipRole.TREASURER: policy(data_scope="own_records", allowed_actions=("view_profile", "edit_profile")),
        MembershipRole.SECRETARY: policy(data_scope="own_records", allowed_actions=("view_profile", "edit_profile")),
        MembershipRole.AUDITOR: policy(data_scope="own_records", allowed_actions=("view_profile", "edit_profile")),
        MembershipRole.MEMBER: policy(data_scope="own_records", allowed_actions=("view_profile", "edit_profile")),
    },
    "settings": {
        MembershipRole.SUPERADMIN: policy(data_scope="own_records", allowed_actions=("view_settings", "edit_settings")),
        MembershipRole.ADMIN: policy(data_scope="own_records", allowed_actions=("view_settings", "edit_settings")),
        MembershipRole.CHAMA_ADMIN: policy(data_scope="own_records", allowed_actions=("view_settings", "edit_settings")),
        MembershipRole.TREASURER: policy(data_scope="own_records", allowed_actions=("view_settings", "edit_settings")),
        MembershipRole.SECRETARY: policy(data_scope="own_records", allowed_actions=("view_settings", "edit_settings")),
        MembershipRole.AUDITOR: policy(data_scope="own_records", allowed_actions=("view_settings", "edit_settings")),
        MembershipRole.MEMBER: policy(data_scope="own_records", allowed_actions=("view_settings", "edit_settings")),
    },
    "support": {
        MembershipRole.SUPERADMIN: policy(
            data_scope="platform_support",
            allowed_actions=("view_support_cases", "inspect_escalation"),
        ),
        MembershipRole.ADMIN: policy(
            data_scope="platform_support",
            allowed_actions=("view_support_cases", "resolve_case", "escalate_case"),
        ),
        MembershipRole.CHAMA_ADMIN: policy(
            data_scope="scoped_chama",
            allowed_actions=("view_support_cases", "resolve_case"),
        ),
        MembershipRole.TREASURER: policy(
            data_scope="scoped_chama",
            allowed_actions=("view_support_cases", "reply_case"),
            hidden_field_tags=("meeting_private",),
        ),
        MembershipRole.SECRETARY: policy(
            data_scope="scoped_chama",
            allowed_actions=("view_support_cases", "reply_case"),
            hidden_field_tags=("finance_private",),
        ),
        MembershipRole.AUDITOR: policy(
            data_scope="scoped_chama",
            read_only=True,
            allowed_actions=("view_support_cases",),
            hidden_field_tags=MUTATION_ONLY,
        ),
        MembershipRole.MEMBER: policy(
            data_scope="own_records",
            allowed_actions=("view_support_cases", "create_case", "reply_case"),
            hidden_field_tags=PLATFORM_ONLY + MODERATION_ONLY,
        ),
    },
    "ai_assistant": {
        MembershipRole.SUPERADMIN: policy(data_scope="platform_all", allowed_actions=("ask_ai",)),
        MembershipRole.ADMIN: policy(data_scope="platform_support", allowed_actions=("ask_ai",)),
        MembershipRole.CHAMA_ADMIN: policy(data_scope="scoped_chama", allowed_actions=("ask_ai",)),
        MembershipRole.TREASURER: policy(data_scope="scoped_chama", allowed_actions=("ask_ai",)),
        MembershipRole.SECRETARY: policy(data_scope="scoped_chama", allowed_actions=("ask_ai",)),
        MembershipRole.AUDITOR: policy(data_scope="scoped_chama", allowed_actions=("ask_ai",), hidden_field_tags=MUTATION_ONLY),
        MembershipRole.MEMBER: policy(
            data_scope="own_records",
            allowed_actions=("ask_ai",),
            hidden_field_tags=PLATFORM_ONLY + MODERATION_ONLY + FINANCE_PRIVATE + AUDIT_ONLY,
        ),
    },
}


def get_screen_policy(screen: ScreenKey, role: RoleCode) -> BackendScreenPolicy:
    screen_policies = SCREEN_ROLE_POLICIES.get(screen, {})
    if role in screen_policies:
        return screen_policies[role]
    return policy(
        visible=False,
        read_only=True,
        data_scope="own_records",
        blocked_actions=("screen_not_available",),
        hidden_field_tags=PLATFORM_ONLY + MODERATION_ONLY + FINANCE_PRIVATE + AUDIT_ONLY,
    )


def can_access_screen(screen: ScreenKey, role: RoleCode) -> bool:
    return get_screen_policy(screen, role).visible


def is_read_only_screen(screen: ScreenKey, role: RoleCode) -> bool:
    return get_screen_policy(screen, role).read_only


def can_perform_screen_action(screen: ScreenKey, role: RoleCode, action: ActionCode) -> bool:
    screen_policy = get_screen_policy(screen, role)
    if not screen_policy.visible or action in screen_policy.blocked_actions:
        return False
    if screen_policy.read_only and action not in screen_policy.allowed_actions:
        return False
    return action in screen_policy.allowed_actions


def filter_payload_for_role(
    payload: dict[str, Any],
    *,
    screen: ScreenKey,
    role: RoleCode,
    field_tags: dict[str, set[FieldTag]],
) -> dict[str, Any]:
    hidden_tags = set(get_screen_policy(screen, role).hidden_field_tags)
    filtered: dict[str, Any] = {}
    for field_name, value in payload.items():
        tags = field_tags.get(field_name, set())
        if hidden_tags.intersection(tags):
            continue
        filtered[field_name] = value
    return filtered


def list_allowed_fields(
    *,
    screen: ScreenKey,
    role: RoleCode,
    field_tags: dict[str, set[FieldTag]],
) -> list[str]:
    hidden_tags = set(get_screen_policy(screen, role).hidden_field_tags)
    return [
        field_name
        for field_name, tags in field_tags.items()
        if not hidden_tags.intersection(tags)
    ]
