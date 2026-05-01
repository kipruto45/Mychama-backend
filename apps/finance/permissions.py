"""
Role-based permissions for the finance app.

Defines role-based access control (RBAC) for financial operations.
Each permission class maps to specific roles that are allowed to perform the action.
"""

from rest_framework import permissions

from apps.chama.models import MembershipRole
from apps.chama.permissions import get_membership
from apps.chama.services import get_effective_role


def get_chama_membership(user, chama_id):
    """Get user's membership in a chama."""
    return get_membership(user, chama_id)


def has_role(user, chama_id, allowed_roles: set) -> bool:
    """Check if user has one of the allowed roles in a chama."""
    membership = get_chama_membership(user, chama_id)
    if not membership:
        return False
    effective_role = get_effective_role(user, chama_id, membership)
    return effective_role in allowed_roles


class FinanceScopedPermission(permissions.BasePermission):
    """
    Base class for finance-scoped permissions.
    
    Subclasses must define required_roles as a set of allowed roles.
    """
    required_roles: set = set()
    message = "Not authorized for this finance operation."

    def _get_chama_id(self, view):
        """Extract chama_id from view."""
        if hasattr(view, "get_permission_chama_id"):
            return view.get_permission_chama_id()
        
        # Try to get from URL kwargs
        chama_id = view.kwargs.get("chama_id")
        if chama_id:
            return chama_id
        
        # Try from query params
        chama_id = view.request.query_params.get("chama_id")
        if chama_id:
            return chama_id
        
        # Try from request data
        chama_id = view.request.data.get("chama_id")
        return chama_id

    def has_permission(self, request, view):
        """Check if user has permission."""
        chama_id = self._get_chama_id(view)
        if not chama_id:
            return False
        
        if not request.user or not request.user.is_authenticated:
            return False

        if not self.required_roles:
            # Just check membership exists
            return bool(get_chama_membership(request.user, chama_id))
        
        return has_role(request.user, chama_id, self.required_roles)


# ============================================================================
# ROLE PERMISSIONS MATRIX
# ============================================================================
#
# | Role              | View Contributions | Record | View Loans | Approve | Disburse | View Reports |
# |-------------------|-------------------|--------|------------|---------|-----------|--------------|
# | MEMBER            | ✓ (own)           | ✗      | ✓ (own)    | ✗       | ✗         | ✗            |
# | TREASURER         | ✓                 | ✓      | ✓          | ✓       | ✗*        | ✓            |
# | SECRETARY         | ✓                 | ✗      | ✓          | ✗       | ✗         | ✓            |
# | AUDITOR           | ✓                 | ✗      | ✓          | ✗       | ✗         | ✓            |
# | CHAMA_ADMIN       | ✓                 | ✓      | ✓          | ✓       | ✓         | ✓            |
# | SUPERADMIN        | ✓                 | ✓      | ✓          | ✓       | ✓         | ✓            |
#
# * Requires separate_disburser setting on loan product

# ============================================================================
# PERMISSION CLASSES
# ============================================================================

class IsFinanceMember(FinanceScopedPermission):
    """
    Allow any active member of the chama.
    
    Used for read-only access to financial data.
    """
    required_roles = {
        MembershipRole.MEMBER,
        MembershipRole.TREASURER,
        MembershipRole.SECRETARY,
        MembershipRole.AUDITOR,
        MembershipRole.CHAMA_ADMIN,
        
        MembershipRole.ADMIN,
        MembershipRole.SUPERADMIN,
    }
    message = "Must be an active member to view financial data."


class CanRecordContribution(FinanceScopedPermission):
    """
    Allow recording contributions.
    
    Roles: TREASURER, CHAMA_ADMIN, ADMIN, SUPERADMIN
    """
    required_roles = {
        MembershipRole.TREASURER,
        MembershipRole.CHAMA_ADMIN,
        
        MembershipRole.ADMIN,
        MembershipRole.SUPERADMIN,
    }
    message = "Only treasurer or chama admin can record contributions."


class CanViewAllContributions(FinanceScopedPermission):
    """
    Allow viewing all members' contributions.
    
    Roles: TREASURER, SECRETARY, AUDITOR, CHAMA_ADMIN, ADMIN, SUPERADMIN
    """
    required_roles = {
        MembershipRole.TREASURER,
        MembershipRole.SECRETARY,
        MembershipRole.AUDITOR,
        MembershipRole.CHAMA_ADMIN,
        
        MembershipRole.ADMIN,
        MembershipRole.SUPERADMIN,
    }
    message = "Not authorized to view all contributions."


class CanViewOwnContributions(permissions.BasePermission):
    """
    Allow viewing own contributions only.
    """
    message = "Must be authenticated to view contributions."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)


class CanRequestLoan(permissions.BasePermission):
    """
    Allow active members to request loans.
    
    Members with active membership can request loans.
    """
    message = "Must be an active member to request a loan."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Check if user has an active membership
        from apps.chama.models import Membership, MemberStatus
        return Membership.objects.filter(
            user=request.user,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        ).exists()


class CanViewAllLoans(FinanceScopedPermission):
    """
    Allow viewing all loans in the chama.
    
    Roles: TREASURER, SECRETARY, AUDITOR, CHAMA_ADMIN, ADMIN, SUPERADMIN
    """
    required_roles = {
        MembershipRole.TREASURER,
        MembershipRole.SECRETARY,
        MembershipRole.AUDITOR,
        MembershipRole.CHAMA_ADMIN,
        
        MembershipRole.ADMIN,
        MembershipRole.SUPERADMIN,
    }
    message = "Not authorized to view all loans."


class CanApproveLoan(FinanceScopedPermission):
    """
    Allow approving loan requests.
    
    Roles: TREASURER, CHAMA_ADMIN, ADMIN, SUPERADMIN
    
    Note: Treasurer approval may be bypassed if loan product doesn't require it.
    """
    required_roles = {
        MembershipRole.TREASURER,
        MembershipRole.CHAMA_ADMIN,
        
        MembershipRole.ADMIN,
        MembershipRole.SUPERADMIN,
    }
    message = "Only treasurer or chama admin can approve loans."


class CanDisburseLoan(FinanceScopedPermission):
    """
    Allow disbursing approved loans.
    
    Roles: CHAMA_ADMIN, ADMIN, SUPERADMIN
    
    Note: Requires separate_disburser setting on loan product.
    TREASURER cannot disburse by default (separation of duties).
    """
    required_roles = {
        MembershipRole.CHAMA_ADMIN,
        
        MembershipRole.ADMIN,
        MembershipRole.SUPERADMIN,
    }
    message = "Only chama admin can disburse loans."


class CanViewFinancialReports(FinanceScopedPermission):
    """
    Allow viewing financial reports.
    
    Roles: TREASURER, SECRETARY, AUDITOR, CHAMA_ADMIN, ADMIN, SUPERADMIN
    """
    required_roles = {
        MembershipRole.TREASURER,
        MembershipRole.SECRETARY,
        MembershipRole.AUDITOR,
        MembershipRole.CHAMA_ADMIN,
        
        MembershipRole.ADMIN,
        MembershipRole.SUPERADMIN,
    }
    message = "Not authorized to view financial reports."


class CanManageLoanProducts(FinanceScopedPermission):
    """
    Allow creating/editing loan products.
    
    Roles: CHAMA_ADMIN, ADMIN, SUPERADMIN
    """
    required_roles = {
        MembershipRole.CHAMA_ADMIN,
        
        MembershipRole.ADMIN,
        MembershipRole.SUPERADMIN,
    }
    message = "Only chama admin can manage loan products."


class CanManageContributionTypes(FinanceScopedPermission):
    """
    Allow creating/editing contribution types.
    
    Roles: TREASURER, CHAMA_ADMIN, ADMIN, SUPERADMIN
    """
    required_roles = {
        MembershipRole.TREASURER,
        MembershipRole.CHAMA_ADMIN,
        
        MembershipRole.ADMIN,
        MembershipRole.SUPERADMIN,
    }
    message = "Only treasurer or chama admin can manage contribution types."


class CanIssuePenalty(FinanceScopedPermission):
    """
    Allow issuing penalties.
    
    Roles: TREASURER, CHAMA_ADMIN, ADMIN, SUPERADMIN
    """
    required_roles = {
        MembershipRole.TREASURER,
        MembershipRole.CHAMA_ADMIN,
        
        MembershipRole.ADMIN,
        MembershipRole.SUPERADMIN,
    }
    message = "Only treasurer or chama admin can issue penalties."


class CanCloseMonth(FinanceScopedPermission):
    """
    Allow closing the monthly accounting period.
    
    Roles: TREASURER, CHAMA_ADMIN, ADMIN, SUPERADMIN
    """
    required_roles = {
        MembershipRole.TREASURER,
        MembershipRole.CHAMA_ADMIN,
        
        MembershipRole.ADMIN,
        MembershipRole.SUPERADMIN,
    }
    message = "Only treasurer or chama admin can close the month."


class CanMakeAdjustments(FinanceScopedPermission):
    """
    Allow making manual adjustments to ledger.
    
    Roles: CHAMA_ADMIN, ADMIN, SUPERADMIN
    
    Note: This is a highly sensitive operation.
    """
    required_roles = {
        MembershipRole.CHAMA_ADMIN,
        
        MembershipRole.ADMIN,
        MembershipRole.SUPERADMIN,
    }
    message = "Only chama admin can make manual adjustments."


class IsTreasurer(FinanceScopedPermission):
    """
    Allow treasurer-specific operations.
    
    Roles: TREASURER, CHAMA_ADMIN, ADMIN, SUPERADMIN
    """
    required_roles = {
        MembershipRole.TREASURER,
        MembershipRole.CHAMA_ADMIN,
        
        MembershipRole.ADMIN,
        MembershipRole.SUPERADMIN,
    }
    message = "Only treasurer can perform this action."


class IsChamaAdminOnly(FinanceScopedPermission):
    """
    Allow only chama admin (not including treasurer).
    
    Roles: CHAMA_ADMIN, ADMIN, SUPERADMIN
    """
    required_roles = {
        MembershipRole.CHAMA_ADMIN,
        
        MembershipRole.ADMIN,
        MembershipRole.SUPERADMIN,
    }
    message = "Only chama admin can perform this action."


class CanManageLoanRecovery(FinanceScopedPermission):
    """Allow recording recovery actions and default management."""

    required_roles = {
        MembershipRole.TREASURER,
        MembershipRole.CHAMA_ADMIN,
        MembershipRole.ADMIN,
        MembershipRole.SUPERADMIN,
    }
    message = "Only treasurer or chama admin can manage loan recovery."


class CanRestructureLoan(FinanceScopedPermission):
    """Allow approving loan restructures."""

    required_roles = {
        MembershipRole.TREASURER,
        MembershipRole.CHAMA_ADMIN,
        MembershipRole.ADMIN,
        MembershipRole.SUPERADMIN,
    }
    message = "Only treasurer or chama admin can restructure loans."


class CanWriteOffLoan(FinanceScopedPermission):
    """Allow writing off loans."""

    required_roles = {
        MembershipRole.CHAMA_ADMIN,
        MembershipRole.ADMIN,
        MembershipRole.SUPERADMIN,
    }
    message = "Only chama admin can write off loans."


class CanWaiveLoanPenalty(FinanceScopedPermission):
    """Allow waiving loan penalties."""

    required_roles = {
        MembershipRole.CHAMA_ADMIN,
        MembershipRole.ADMIN,
        MembershipRole.SUPERADMIN,
    }
    message = "Only chama admin can waive loan penalties."


class CanViewLoanReports(FinanceScopedPermission):
    """Allow access to loan reporting and portfolio analytics."""

    required_roles = {
        MembershipRole.TREASURER,
        MembershipRole.AUDITOR,
        MembershipRole.SECRETARY,
        MembershipRole.CHAMA_ADMIN,
        MembershipRole.ADMIN,
        MembershipRole.SUPERADMIN,
    }
    message = "Not authorized to view loan reports."


class IsAuditorOrHigher(FinanceScopedPermission):
    """
    Allow auditor or higher roles.
    
    Roles: AUDITOR, TREASURER, SECRETARY, CHAMA_ADMIN, ADMIN, SUPERADMIN
    """
    required_roles = {
        MembershipRole.AUDITOR,
        MembershipRole.TREASURER,
        MembershipRole.SECRETARY,
        MembershipRole.CHAMA_ADMIN,
        
        MembershipRole.ADMIN,
        MembershipRole.SUPERADMIN,
    }
    message = "Only auditor or higher can perform this action."
