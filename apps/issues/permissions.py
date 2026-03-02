from apps.chama.models import MembershipRole
from apps.chama.permissions import get_membership
from apps.chama.services import get_effective_role
from apps.issues.models import Issue, IssueCategory

ADMIN_ROLES = {
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.SECRETARY,
}

READ_ALL_ROLES = {
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.SECRETARY,
    MembershipRole.AUDITOR,
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


def can_view_issue(user, membership, issue: Issue) -> bool:
    if user.is_superuser:
        return True
    if not membership:
        return False

    role = _role_for(user, membership)
    if role in READ_ALL_ROLES:
        return True

    if role == MembershipRole.TREASURER:
        return (
            issue.category == IssueCategory.FINANCE
            or issue.created_by_id == user.id
            or issue.reported_user_id == user.id
        )

    return issue.created_by_id == user.id or issue.reported_user_id == user.id


def can_moderate_issue(user, membership, issue: Issue) -> bool:
    if user.is_superuser:
        return True
    if not membership:
        return False

    role = _role_for(user, membership)
    if role in ADMIN_ROLES:
        return True

    return role == MembershipRole.TREASURER and issue.category == IssueCategory.FINANCE


def can_comment_issue(user, membership, issue: Issue) -> bool:
    if user.is_superuser:
        return True
    if not membership:
        return False

    role = _role_for(user, membership)
    if role in ADMIN_ROLES:
        return True

    if role == MembershipRole.TREASURER:
        return issue.category == IssueCategory.FINANCE

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
    if role in ADMIN_ROLES:
        return True

    return role == MembershipRole.TREASURER and issue.category == IssueCategory.FINANCE
