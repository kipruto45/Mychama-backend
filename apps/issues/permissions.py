from django.db.models import Q

from apps.chama.models import MembershipRole
from apps.chama.permissions import get_membership
from apps.chama.services import get_effective_role
from apps.issues.models import Issue, IssueCategory

CHAMA_WIDE_ISSUE_MANAGERS = {
    MembershipRole.CHAMA_ADMIN,
}

READ_ALL_ROLES = {
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.SECRETARY,
    MembershipRole.AUDITOR,
}

TREASURER_ISSUE_CATEGORIES = {
    IssueCategory.FINANCIAL,
    IssueCategory.LOAN_DISPUTE,
    IssueCategory.PAYMENT_DISPUTE,
}

SECRETARY_ISSUE_CATEGORIES = {
    IssueCategory.GOVERNANCE,
    IssueCategory.MEMBER_CONDUCT,
    IssueCategory.OPERATIONAL,
}


def get_issue_membership(user, chama_id):
    if not user or not user.is_authenticated:
        return None
    if user.is_superuser:
        return None
    return get_membership(user, chama_id)


def _role_for(user, membership) -> str | None:
    if not membership:
        return None
    return get_effective_role(user, membership.chama_id, membership)


def _can_handle_category(role: str | None, issue: Issue) -> bool:
    if role == MembershipRole.TREASURER:
        return issue.category in TREASURER_ISSUE_CATEGORIES
    if role == MembershipRole.SECRETARY:
        return issue.category in SECRETARY_ISSUE_CATEGORIES
    return role in CHAMA_WIDE_ISSUE_MANAGERS


def filter_issue_queryset(queryset, user, membership):
    if user.is_superuser:
        return queryset
    if not membership:
        return queryset.none()

    role = _role_for(user, membership)
    if role in CHAMA_WIDE_ISSUE_MANAGERS | {MembershipRole.AUDITOR}:
        return queryset
    if role == MembershipRole.SECRETARY:
        return queryset.filter(
            Q(category__in=SECRETARY_ISSUE_CATEGORIES)
            | Q(created_by=user)
            | Q(reported_user=user)
        )
    if role == MembershipRole.TREASURER:
        return queryset.filter(
            Q(category__in=TREASURER_ISSUE_CATEGORIES)
            | Q(created_by=user)
            | Q(reported_user=user)
        )
    return queryset.filter(Q(created_by=user) | Q(reported_user=user))


def can_view_issue(user, membership, issue: Issue) -> bool:
    if user.is_superuser:
        return True
    if not membership:
        return False

    role = _role_for(user, membership)
    if role in CHAMA_WIDE_ISSUE_MANAGERS | {MembershipRole.AUDITOR}:
        return True

    if role in {MembershipRole.TREASURER, MembershipRole.SECRETARY}:
        return _can_handle_category(role, issue) or issue.created_by_id == user.id or issue.reported_user_id == user.id

    return issue.created_by_id == user.id or issue.reported_user_id == user.id


def can_moderate_issue(user, membership, issue: Issue) -> bool:
    if user.is_superuser:
        return True
    if not membership:
        return False

    role = _role_for(user, membership)
    if role in CHAMA_WIDE_ISSUE_MANAGERS:
        return True

    if role in {MembershipRole.TREASURER, MembershipRole.SECRETARY}:
        return _can_handle_category(role, issue)
    return False


def can_comment_issue(user, membership, issue: Issue) -> bool:
    if user.is_superuser:
        return True
    if not membership:
        return False

    role = _role_for(user, membership)
    if role in CHAMA_WIDE_ISSUE_MANAGERS:
        return True

    if role in {MembershipRole.TREASURER, MembershipRole.SECRETARY}:
        return _can_handle_category(role, issue)

    if role == MembershipRole.AUDITOR:
        return False

    return issue.created_by_id == user.id or issue.reported_user_id == user.id


def can_reopen_issue(user, membership, issue: Issue) -> bool:
    if user.is_superuser:
        return True

    if issue.created_by_id == user.id:
        return True

    if not membership:
        return False

    role = _role_for(user, membership)
    if role in CHAMA_WIDE_ISSUE_MANAGERS:
        return True

    if role in {MembershipRole.TREASURER, MembershipRole.SECRETARY}:
        return _can_handle_category(role, issue)
    return False


def can_edit_issue(user, membership, issue: Issue) -> bool:
    if user.is_superuser:
        return True

    if not membership:
        return False

    role = _role_for(user, membership)
    
    if issue.created_by_id == user.id:
        return issue.status in {"open", "reopened"}

    if role in CHAMA_WIDE_ISSUE_MANAGERS:
        return True

    if issue.assigned_to_id == user.id:
        return True

    return False


def can_assign_issue(user, membership, issue: Issue) -> bool:
    if user.is_superuser:
        return True

    if not membership:
        return False

    role = _role_for(user, membership)

    if role in CHAMA_WIDE_ISSUE_MANAGERS:
        return True

    if issue.assigned_to_id == user.id:
        return True

    return False


def can_approve_resolution(user, membership, issue: Issue) -> bool:
    if user.is_superuser:
        return True

    if not membership:
        return False

    role = _role_for(user, membership)

    if role in CHAMA_WIDE_ISSUE_MANAGERS:
        return True

    return False


def can_escalate_issue(user, membership, issue: Issue) -> bool:
    if user.is_superuser:
        return True

    if not membership:
        return False

    role = _role_for(user, membership)

    if role in CHAMA_WIDE_ISSUE_MANAGERS:
        return True

    return False


def can_rate_issue(user, membership, issue: Issue) -> bool:
    if not membership:
        return False

    return issue.created_by_id == user.id


def can_add_internal_comment(user, membership, issue: Issue) -> bool:
    if user.is_superuser:
        return True

    if not membership:
        return False

    role = _role_for(user, membership)

    if role in {MembershipRole.CHAMA_ADMIN, MembershipRole.SECRETARY, MembershipRole.TREASURER}:
        return True

    return False


def can_execute_resolution(user, membership, issue: Issue) -> bool:
    if user.is_superuser:
        return True

    if not membership:
        return False

    role = _role_for(user, membership)

    if role in CHAMA_WIDE_ISSUE_MANAGERS:
        return True

    return False


def can_issue_warning(user, membership, issue: Issue) -> bool:
    if user.is_superuser:
        return True

    if not membership:
        return False

    role = _role_for(user, membership)

    if role in CHAMA_WIDE_ISSUE_MANAGERS:
        return True

    return False


def can_suspend_user(user, membership, issue: Issue) -> bool:
    if user.is_superuser:
        return True

    if not membership:
        return False

    role = _role_for(user, membership)

    if role in CHAMA_WIDE_ISSUE_MANAGERS:
        return True

    return False


def can_view_internal_notes(user, membership, issue: Issue) -> bool:
    if user.is_superuser:
        return True

    if not membership:
        return False

    role = _role_for(user, membership)

    if role in {MembershipRole.CHAMA_ADMIN, MembershipRole.SECRETARY, MembershipRole.TREASURER}:
        return True

    if issue.assigned_to_id == user.id:
        return True

    return False


def can_create_system_issue(user):
    return user.is_superuser


def can_view_stats(user, membership):
    if user.is_superuser:
        return True

    if not membership:
        return False

    role = _role_for(user, membership)

    if role in {MembershipRole.CHAMA_ADMIN, MembershipRole.SECRETARY, MembershipRole.TREASURER, MembershipRole.AUDITOR}:
        return True

    return False
