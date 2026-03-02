"""
Admin Management API Views
Comprehensive REST API endpoints for admin operations.
"""
from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import generics, permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.admin_management.services import AdminManagementService
from apps.chama.models import MemberStatus, Membership, MembershipRequest, MembershipRole
from apps.finance.models import Loan

User = get_user_model()


class IsAdminUser(permissions.BasePermission):
    """Permission check for admin users."""

    admin_equivalent_roles = (
        MembershipRole.SUPERADMIN,
        MembershipRole.ADMIN,
        MembershipRole.CHAMA_ADMIN,
    )

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.is_staff or request.user.is_superuser:
            return True

        return Membership.objects.filter(
            user=request.user,
            role__in=self.admin_equivalent_roles,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        ).exists()


# ============ USER MANAGEMENT ============

class UserListCreateView(APIView):
    """List all users or create a new user."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        search = request.GET.get("search", "")
        is_active = request.GET.get("is_active")
        
        if is_active is not None:
            is_active = is_active.lower() == "true"

        result = AdminManagementService.get_all_users(
            page=page,
            page_size=page_size,
            search=search,
            is_active=is_active,
        )
        return Response(result)

    def post(self, request):
        required_fields = ["phone", "full_name"]
        for field in required_fields:
            if not request.data.get(field):
                return Response(
                    {"error": f"{field} is required"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        try:
            user = AdminManagementService.create_user(
                phone=request.data["phone"],
                full_name=request.data["full_name"],
                email=request.data.get("email", ""),
                is_active=request.data.get("is_active", True),
                is_staff=request.data.get("is_staff", False),
                is_superuser=request.data.get("is_superuser", False),
            )
            return Response({
                "id": user.id,
                "phone": user.phone,
                "full_name": user.full_name,
                "message": "User created successfully"
            }, status=status.HTTP_201_CREATED)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class UserDetailView(APIView):
    """Get, update, or delete a specific user."""
    permission_classes = [IsAdminUser]

    def get(self, request, user_id):
        users = AdminManagementService.get_all_users(search="", page=1, page_size=1000)
        for user in users["users"]:
            if user["id"] == user_id:
                return Response(user)
        return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

    def put(self, request, user_id):
        try:
            user = AdminManagementService.update_user(
                user_id=user_id,
                full_name=request.data.get("full_name"),
                email=request.data.get("email"),
                is_active=request.data.get("is_active"),
                phone=request.data.get("phone"),
            )
            return Response({
                "id": user.id,
                "phone": user.phone,
                "full_name": user.full_name,
                "message": "User updated successfully"
            })
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, user_id):
        try:
            AdminManagementService.deactivate_user(user_id)
            return Response({"message": "User deactivated successfully"})
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)


class UserActivateView(APIView):
    """Activate a user account."""
    permission_classes = [IsAdminUser]

    def post(self, request, user_id):
        try:
            user = AdminManagementService.activate_user(user_id)
            return Response({"message": f"User {user.full_name} activated successfully"})
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)


# ============ CHAMA MANAGEMENT ============

class ChamaListCreateView(APIView):
    """List all chamas or create a new chama."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        search = request.GET.get("search", "")
        status_filter = request.GET.get("status")

        result = AdminManagementService.get_all_chamas(
            page=page,
            page_size=page_size,
            search=search,
            status=status_filter,
        )
        return Response(result)

    def post(self, request):
        required_fields = ["name"]
        for field in required_fields:
            if not request.data.get(field):
                return Response(
                    {"error": f"{field} is required"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        chama = AdminManagementService.create_chama(
            name=request.data["name"],
            description=request.data.get("description", ""),
            currency=request.data.get("currency", "KES"),
            max_members=request.data.get("max_members", 100),
            admin_user_id=request.data.get("admin_user_id"),
        )
        return Response({
            "id": chama.id,
            "name": chama.name,
            "message": "Chama created successfully"
        }, status=status.HTTP_201_CREATED)


# ============ MEMBERSHIP MANAGEMENT ============

class MemberListView(APIView):
    """List all members with filtering."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")
        search = request.GET.get("search", "")
        status_filter = request.GET.get("status")
        role = request.GET.get("role")

        result = AdminManagementService.get_all_members(
            chama_id=int(chama_id) if chama_id else None,
            page=page,
            page_size=page_size,
            search=search,
            status=status_filter,
            role=role,
        )
        return Response(result)


class MemberRoleUpdateView(APIView):
    """Update a member's role."""
    permission_classes = [IsAdminUser]

    def post(self, request, membership_id):
        if not request.data.get("new_role"):
            return Response(
                {"error": "new_role is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            membership = AdminManagementService.update_member_role(
                membership_id=membership_id,
                new_role=request.data["new_role"],
                actor=request.user,
            )
            return Response({
                "id": membership.id,
                "role": membership.role,
                "message": "Member role updated successfully"
            })
        except Membership.DoesNotExist:
            return Response({"error": "Membership not found"}, status=status.HTTP_404_NOT_FOUND)


class MemberApproveView(APIView):
    """Approve a member."""
    permission_classes = [IsAdminUser]

    def post(self, request, membership_id):
        try:
            membership = AdminManagementService.approve_membership(membership_id, request.user)
            return Response({
                "id": membership.id,
                "status": membership.status,
                "message": "Membership approved successfully"
            })
        except Membership.DoesNotExist:
            return Response({"error": "Membership not found"}, status=status.HTTP_404_NOT_FOUND)


class MemberRejectView(APIView):
    """Reject a member."""
    permission_classes = [IsAdminUser]

    def post(self, request, membership_id):
        try:
            reason = request.data.get("reason", "")
            membership = AdminManagementService.reject_membership(membership_id, request.user, reason)
            return Response({
                "id": membership.id,
                "status": membership.status,
                "message": "Membership rejected successfully"
            })
        except Membership.DoesNotExist:
            return Response({"error": "Membership not found"}, status=status.HTTP_404_NOT_FOUND)


# ============ MEMBERSHIP REQUESTS ============

class MembershipRequestListView(APIView):
    """List all membership requests."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")
        status_filter = request.GET.get("status")

        result = AdminManagementService.get_all_membership_requests(
            chama_id=int(chama_id) if chama_id else None,
            page=page,
            page_size=page_size,
            status=status_filter,
        )
        return Response(result)


class MembershipRequestApproveView(APIView):
    """Approve a membership request."""
    permission_classes = [IsAdminUser]

    def post(self, request, request_id):
        try:
            membership_request = AdminManagementService.approve_membership_request(request_id, request.user)
            return Response({
                "id": membership_request.id,
                "status": membership_request.status,
                "message": "Membership request approved successfully"
            })
        except MembershipRequest.DoesNotExist:
            return Response({"error": "Membership request not found"}, status=status.HTTP_404_NOT_FOUND)


class MembershipRequestRejectView(APIView):
    """Reject a membership request."""
    permission_classes = [IsAdminUser]

    def post(self, request, request_id):
        try:
            reason = request.data.get("reason", "")
            membership_request = AdminManagementService.reject_membership_request(request_id, request.user, reason)
            return Response({
                "id": membership_request.id,
                "status": membership_request.status,
                "message": "Membership request rejected successfully"
            })
        except MembershipRequest.DoesNotExist:
            return Response({"error": "Membership request not found"}, status=status.HTTP_404_NOT_FOUND)


# ============ LOAN MANAGEMENT ============

class LoanListView(APIView):
    """List all loans with filtering."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")
        status_filter = request.GET.get("status")
        member_id = request.GET.get("member_id")

        result = AdminManagementService.get_all_loans(
            chama_id=int(chama_id) if chama_id else None,
            page=page,
            page_size=page_size,
            status=status_filter,
            member_id=int(member_id) if member_id else None,
        )
        return Response(result)


class LoanCreateView(APIView):
    """Create a new loan."""
    permission_classes = [IsAdminUser]

    def post(self, request):
        required_fields = ["chama_id", "member_id", "amount"]
        for field in required_fields:
            if not request.data.get(field):
                return Response(
                    {"error": f"{field} is required"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        try:
            loan = AdminManagementService.create_loan(
                chama_id=request.data["chama_id"],
                member_id=request.data["member_id"],
                amount=Decimal(str(request.data["amount"])),
                product_id=request.data.get("product_id"),
                purpose=request.data.get("purpose", ""),
                actor=request.user,
            )
            return Response({
                "id": loan.id,
                "status": loan.status,
                "message": "Loan created successfully"
            }, status=status.HTTP_201_CREATED)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class LoanApproveView(APIView):
    """Approve a loan."""
    permission_classes = [IsAdminUser]

    def post(self, request, loan_id):
        note = request.data.get("note", "")
        try:
            loan = AdminManagementService.approve_loan(loan_id, request.user, note)
            return Response({
                "id": loan.id,
                "status": loan.status,
                "message": "Loan approved successfully"
            })
        except Loan.DoesNotExist:
            return Response({"error": "Loan not found"}, status=status.HTTP_404_NOT_FOUND)


class LoanRejectView(APIView):
    """Reject a loan."""
    permission_classes = [IsAdminUser]

    def post(self, request, loan_id):
        note = request.data.get("note", "")
        try:
            loan = AdminManagementService.reject_loan(loan_id, request.user, note)
            return Response({
                "id": loan.id,
                "status": loan.status,
                "message": "Loan rejected successfully"
            })
        except Loan.DoesNotExist:
            return Response({"error": "Loan not found"}, status=status.HTTP_404_NOT_FOUND)


class LoanDisburseView(APIView):
    """Disburse a loan."""
    permission_classes = [IsAdminUser]

    def post(self, request, loan_id):
        try:
            loan = AdminManagementService.disburse_loan(loan_id, request.user)
            return Response({
                "id": loan.id,
                "status": loan.status,
                "disbursed_at": loan.disbursed_at.isoformat() if loan.disbursed_at else None,
                "message": "Loan disbursed successfully"
            })
        except Loan.DoesNotExist:
            return Response({"error": "Loan not found"}, status=status.HTTP_404_NOT_FOUND)


# ============ CONTRIBUTION MANAGEMENT ============

class ContributionListView(APIView):
    """List all contributions with filtering."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")
        member_id = request.GET.get("member_id")
        date_from = request.GET.get("date_from")
        date_to = request.GET.get("date_to")

        result = AdminManagementService.get_all_contributions(
            chama_id=int(chama_id) if chama_id else None,
            page=page,
            page_size=page_size,
            member_id=int(member_id) if member_id else None,
            date_from=date_from if date_from else None,
            date_to=date_to if date_to else None,
        )
        return Response(result)


class ContributionCreateView(APIView):
    """Create a new contribution."""
    permission_classes = [IsAdminUser]

    def post(self, request):
        required_fields = ["chama_id", "member_id", "amount"]
        for field in required_fields:
            if not request.data.get(field):
                return Response(
                    {"error": f"{field} is required"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        try:
            contribution = AdminManagementService.create_contribution(
                chama_id=request.data["chama_id"],
                member_id=request.data["member_id"],
                amount=Decimal(str(request.data["amount"])),
                contribution_type_id=request.data.get("contribution_type_id"),
                date_paid=datetime.strptime(request.data["date_paid"], "%Y-%m-%d").date() if request.data.get("date_paid") else None,
                note=request.data.get("note", ""),
                actor=request.user,
            )
            return Response({
                "id": contribution.id,
                "amount": float(contribution.amount),
                "message": "Contribution created successfully"
            }, status=status.HTTP_201_CREATED)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class ContributionSummaryView(APIView):
    """Get contribution summary."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        chama_id = request.GET.get("chama_id")
        date_from = request.GET.get("date_from")
        date_to = request.GET.get("date_to")

        result = AdminManagementService.get_contribution_summary(
            chama_id=int(chama_id) if chama_id else None,
            date_from=datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else None,
            date_to=datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else None,
        )
        return Response(result)


# ============ WITHDRAWAL MANAGEMENT ============

class WithdrawalListView(APIView):
    """List all withdrawals with filtering."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")
        status_filter = request.GET.get("status")

        result = AdminManagementService.get_all_withdrawals(
            chama_id=int(chama_id) if chama_id else None,
            page=page,
            page_size=page_size,
            status=status_filter,
        )
        return Response(result)


# ============ TRANSACTIONS ============

class TransactionListView(APIView):
    """List all transactions."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")
        transaction_type = request.GET.get("type")
        date_from = request.GET.get("date_from")
        date_to = request.GET.get("date_to")

        result = AdminManagementService.get_all_transactions(
            chama_id=int(chama_id) if chama_id else None,
            page=page,
            page_size=page_size,
            transaction_type=transaction_type,
            date_from=datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else None,
            date_to=datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else None,
        )
        return Response(result)


class WalletBalanceView(APIView):
    """Get wallet balance."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        chama_id = request.GET.get("chama_id")
        if not chama_id:
            return Response(
                {"error": "chama_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        result = AdminManagementService.get_wallet_balance(int(chama_id))
        return Response(result)


# ============ PAYMENTS ============

class PaymentListView(APIView):
    """List all payments."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")
        status_filter = request.GET.get("status")
        payment_type = request.GET.get("type")

        result = AdminManagementService.get_all_payments(
            chama_id=int(chama_id) if chama_id else None,
            page=page,
            page_size=page_size,
            status=status_filter,
            payment_type=payment_type,
        )
        return Response(result)


# ============ PENALTIES ============

class PenaltyListView(APIView):
    """List all penalties."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")
        is_paid = request.GET.get("is_paid")

        result = AdminManagementService.get_all_penalties(
            chama_id=int(chama_id) if chama_id else None,
            page=page,
            page_size=page_size,
            is_paid=is_paid.lower() == "true" if is_paid else None,
        )
        return Response(result)


class PenaltyCreateView(APIView):
    """Create a new penalty."""
    permission_classes = [IsAdminUser]

    def post(self, request):
        required_fields = ["chama_id", "member_id", "amount", "reason"]
        for field in required_fields:
            if not request.data.get(field):
                return Response(
                    {"error": f"{field} is required"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        try:
            penalty = AdminManagementService.issue_penalty(
                chama_id=request.data["chama_id"],
                member_id=request.data["member_id"],
                amount=Decimal(str(request.data["amount"])),
                reason=request.data["reason"],
                actor=request.user,
            )
            return Response({
                "id": penalty.id,
                "amount": float(penalty.amount),
                "message": "Penalty issued successfully"
            }, status=status.HTTP_201_CREATED)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


# ============ MANUAL ADJUSTMENTS ============

class ManualAdjustmentCreateView(APIView):
    """Create a manual adjustment."""
    permission_classes = [IsAdminUser]

    def post(self, request):
        required_fields = ["chama_id", "amount", "direction", "description"]
        for field in required_fields:
            if not request.data.get(field):
                return Response(
                    {"error": f"{field} is required"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        try:
            adjustment = AdminManagementService.create_manual_adjustment(
                chama_id=request.data["chama_id"],
                amount=Decimal(str(request.data["amount"])),
                direction=request.data["direction"],
                description=request.data["description"],
                member_id=request.data.get("member_id"),
                actor=request.user,
            )
            return Response({
                "id": adjustment.id if adjustment else None,
                "message": "Manual adjustment created successfully"
            }, status=status.HTTP_201_CREATED)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


# ============ DASHBOARD & REPORTS ============

class DashboardMetricsView(APIView):
    """Get dashboard metrics."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        chama_id = request.GET.get("chama_id")
        result = AdminManagementService.get_dashboard_metrics(
            chama_id=int(chama_id) if chama_id else None
        )
        return Response(result)


class MonthlyTrendsView(APIView):
    """Get monthly trends."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        chama_id = request.GET.get("chama_id")
        months = int(request.GET.get("months", 12))
        result = AdminManagementService.get_monthly_trends(
            chama_id=int(chama_id) if chama_id else None,
            months=months,
        )
        return Response(result)


class RecentActivityView(APIView):
    """Get recent activity."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        chama_id = request.GET.get("chama_id")
        limit = int(request.GET.get("limit", 20))
        result = AdminManagementService.get_recent_activity(
            chama_id=int(chama_id) if chama_id else None,
            limit=limit,
        )
        return Response(result)


# ============ MEETINGS & GOVERNANCE ============

class MeetingListView(APIView):
    """List all meetings."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")
        status = request.GET.get("status")

        result = AdminManagementService.get_all_meetings(
            chama_id=int(chama_id) if chama_id else None,
            page=page,
            page_size=page_size,
            status=status,
        )
        return Response(result)


class MeetingDetailView(APIView):
    """Get meeting details."""
    permission_classes = [IsAdminUser]

    def get(self, request, meeting_id):
        result = AdminManagementService.get_meeting_detail(meeting_id)
        return Response(result)


class MeetingApproveMinutesView(APIView):
    """Approve meeting minutes."""
    permission_classes = [IsAdminUser]

    def post(self, request, meeting_id):
        try:
            result = AdminManagementService.approve_meeting_minutes(meeting_id, request.user)
            return Response(result)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class AttendanceListView(APIView):
    """List meeting attendance."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        meeting_id = request.GET.get("meeting_id")
        if not meeting_id:
            return Response({"error": "meeting_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        result = AdminManagementService.get_meeting_attendance(int(meeting_id))
        return Response(result)


class ResolutionListView(APIView):
    """List all resolutions."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")
        status = request.GET.get("status")

        result = AdminManagementService.get_all_resolutions(
            chama_id=int(chama_id) if chama_id else None,
            page=page,
            page_size=page_size,
            status=status,
        )
        return Response(result)


class ResolutionUpdateView(APIView):
    """Update resolution status."""
    permission_classes = [IsAdminUser]

    def post(self, request, resolution_id):
        new_status = request.data.get("status")
        if not new_status:
            return Response({"error": "status is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            result = AdminManagementService.update_resolution_status(resolution_id, new_status, request.user)
            return Response(result)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


# ============ ISSUES & SERVICE DESK ============

class IssueListView(APIView):
    """List all issues."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")
        status = request.GET.get("status")
        category = request.GET.get("category")
        priority = request.GET.get("priority")

        result = AdminManagementService.get_all_issues(
            chama_id=int(chama_id) if chama_id else None,
            page=page,
            page_size=page_size,
            status=status,
            category=category,
            priority=priority,
        )
        return Response(result)


class IssueDetailView(APIView):
    """Get issue details."""
    permission_classes = [IsAdminUser]

    def get(self, request, issue_id):
        result = AdminManagementService.get_issue_detail(issue_id)
        return Response(result)


class IssueUpdateView(APIView):
    """Update issue status."""
    permission_classes = [IsAdminUser]

    def post(self, request, issue_id):
        status = request.data.get("status")
        assigned_to = request.data.get("assigned_to")
        priority = request.data.get("priority")
        try:
            result = AdminManagementService.update_issue(
                issue_id=issue_id,
                status=status,
                assigned_to=assigned_to,
                priority=priority,
                actor=request.user,
            )
            return Response(result)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class IssueAssignView(APIView):
    """Assign issue to user."""
    permission_classes = [IsAdminUser]

    def post(self, request, issue_id):
        user_id = request.data.get("user_id")
        if not user_id:
            return Response({"error": "user_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            result = AdminManagementService.assign_issue(issue_id, int(user_id), request.user)
            return Response(result)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class WarningListView(APIView):
    """List all warnings."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")
        status = request.GET.get("status")

        result = AdminManagementService.get_all_warnings(
            chama_id=int(chama_id) if chama_id else None,
            page=page,
            page_size=page_size,
            status=status,
        )
        return Response(result)


class WarningCreateView(APIView):
    """Create a warning."""
    permission_classes = [IsAdminUser]

    def post(self, request):
        required_fields = ["chama_id", "user_id", "reason", "message_to_user"]
        for field in required_fields:
            if not request.data.get(field):
                return Response({"error": f"{field} is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            result = AdminManagementService.create_warning(
                chama_id=request.data["chama_id"],
                user_id=request.data["user_id"],
                reason=request.data["reason"],
                message_to_user=request.data["message_to_user"],
                severity=request.data.get("severity", "medium"),
                actor=request.user,
            )
            return Response(result, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class SuspensionListView(APIView):
    """List all suspensions."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")
        is_active = request.GET.get("is_active")

        result = AdminManagementService.get_all_suspensions(
            chama_id=int(chama_id) if chama_id else None,
            page=page,
            page_size=page_size,
            is_active=is_active.lower() == "true" if is_active else None,
        )
        return Response(result)


class SuspensionCreateView(APIView):
    """Create a suspension."""
    permission_classes = [IsAdminUser]

    def post(self, request):
        required_fields = ["chama_id", "user_id", "reason"]
        for field in required_fields:
            if not request.data.get(field):
                return Response({"error": f"{field} is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            result = AdminManagementService.create_suspension(
                chama_id=request.data["chama_id"],
                user_id=request.data["user_id"],
                reason=request.data["reason"],
                ends_at=request.data.get("ends_at"),
                actor=request.user,
            )
            return Response(result, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class SuspensionLiftView(APIView):
    """Lift a suspension."""
    permission_classes = [IsAdminUser]

    def post(self, request, suspension_id):
        try:
            result = AdminManagementService.lift_suspension(
                suspension_id=suspension_id,
                actor=request.user,
                reason=request.data.get("reason", ""),
            )
            return Response(result)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


# ============ NOTIFICATIONS & BROADCASTS ============

class NotificationListView(APIView):
    """List all notifications."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")
        status = request.GET.get("status")
        category = request.GET.get("category")

        result = AdminManagementService.get_all_notifications(
            chama_id=int(chama_id) if chama_id else None,
            page=page,
            page_size=page_size,
            status=status,
            category=category,
        )
        return Response(result)


class NotificationTemplateListView(APIView):
    """List notification templates."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")

        result = AdminManagementService.get_notification_templates(
            chama_id=int(chama_id) if chama_id else None,
            page=page,
            page_size=page_size,
        )
        return Response(result)


class BroadcastListView(APIView):
    """List all broadcasts."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")
        status = request.GET.get("status")

        result = AdminManagementService.get_all_broadcasts(
            chama_id=int(chama_id) if chama_id else None,
            page=page,
            page_size=page_size,
            status=status,
        )
        return Response(result)


class BroadcastCreateView(APIView):
    """Create a broadcast."""
    permission_classes = [IsAdminUser]

    def post(self, request):
        required_fields = ["chama_id", "title", "message"]
        for field in required_fields:
            if not request.data.get(field):
                return Response({"error": f"{field} is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            result = AdminManagementService.create_broadcast(
                chama_id=request.data["chama_id"],
                title=request.data["title"],
                message=request.data["message"],
                target=request.data.get("target", "all"),
                target_roles=request.data.get("target_roles", []),
                target_member_ids=request.data.get("target_member_ids", []),
                channels=request.data.get("channels", ["in_app"]),
                scheduled_at=request.data.get("scheduled_at"),
                actor=request.user,
            )
            return Response(result, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


# ============ SECURITY CENTER ============

class LoginAttemptListView(APIView):
    """List login attempts."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        success = request.GET.get("success")
        date_from = request.GET.get("date_from")
        date_to = request.GET.get("date_to")

        result = AdminManagementService.get_login_attempts(
            page=page,
            page_size=page_size,
            success=success.lower() == "true" if success else None,
            date_from=datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else None,
            date_to=datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else None,
        )
        return Response(result)


class OTPDeliveryLogListView(APIView):
    """List OTP delivery logs."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        status_value = request.GET.get("status")
        channel = request.GET.get("channel")
        purpose = request.GET.get("purpose")
        search = request.GET.get("search", "")

        result = AdminManagementService.get_otp_delivery_logs(
            page=page,
            page_size=page_size,
            status=status_value,
            channel=channel,
            purpose=purpose,
            search=search,
        )
        return Response(result)


class SessionListView(APIView):
    """List user sessions."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        user_id = request.GET.get("user_id")
        is_active = request.GET.get("is_active")

        result = AdminManagementService.get_user_sessions(
            user_id=int(user_id) if user_id else None,
            page=page,
            page_size=page_size,
            is_active=is_active.lower() == "true" if is_active else None,
        )
        return Response(result)


class SessionRevokeView(APIView):
    """Revoke a session."""
    permission_classes = [IsAdminUser]

    def post(self, request, session_id):
        try:
            result = AdminManagementService.revoke_session(session_id)
            return Response(result)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class AuditLogListView(APIView):
    """List audit logs."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")
        action_type = request.GET.get("action_type")
        actor_id = request.GET.get("actor_id")
        date_from = request.GET.get("date_from")
        date_to = request.GET.get("date_to")

        result = AdminManagementService.get_audit_logs(
            chama_id=int(chama_id) if chama_id else None,
            action_type=action_type,
            actor_id=int(actor_id) if actor_id else None,
            page=page,
            page_size=page_size,
            date_from=datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else None,
            date_to=datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else None,
        )
        return Response(result)


class SecurityAlertListView(APIView):
    """List security alerts."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")
        level = request.GET.get("level")
        is_resolved = request.GET.get("is_resolved")

        result = AdminManagementService.get_security_alerts(
            chama_id=int(chama_id) if chama_id else None,
            page=page,
            page_size=page_size,
            level=level,
            is_resolved=is_resolved.lower() == "true" if is_resolved else None,
        )
        return Response(result)


class SecurityAlertResolveView(APIView):
    """Resolve a security alert."""
    permission_classes = [IsAdminUser]

    def post(self, request, alert_id):
        try:
            result = AdminManagementService.resolve_security_alert(alert_id, request.user)
            return Response(result)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


# ============ REPORTS & ANALYTICS ============

class ReportListView(APIView):
    """List report requests."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")
        status = request.GET.get("status")
        report_type = request.GET.get("type")

        result = AdminManagementService.get_report_requests(
            chama_id=int(chama_id) if chama_id else None,
            page=page,
            page_size=page_size,
            status=status,
            report_type=report_type,
        )
        return Response(result)


class ReportGenerateView(APIView):
    """Generate a new report."""
    permission_classes = [IsAdminUser]

    def post(self, request):
        required_fields = ["chama_id", "report_type", "format"]
        for field in required_fields:
            if not request.data.get(field):
                return Response({"error": f"{field} is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            result = AdminManagementService.generate_report(
                chama_id=request.data["chama_id"],
                report_type=request.data["report_type"],
                format=request.data["format"],
                filters=request.data.get("filters", {}),
                actor=request.user,
            )
            return Response(result, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class ScheduledReportListView(APIView):
    """List scheduled reports."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")
        is_active = request.GET.get("is_active")

        result = AdminManagementService.get_scheduled_reports(
            chama_id=int(chama_id) if chama_id else None,
            page=page,
            page_size=page_size,
            is_active=is_active.lower() == "true" if is_active else None,
        )
        return Response(result)


# ============ PAYMENTS & M-PESA OPERATIONS ============

class MpesaTransactionListView(APIView):
    """List M-Pesa transactions."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")
        status = request.GET.get("status")
        purpose = request.GET.get("purpose")
        date_from = request.GET.get("date_from")
        date_to = request.GET.get("date_to")

        result = AdminManagementService.get_mpesa_transactions(
            chama_id=int(chama_id) if chama_id else None,
            page=page,
            page_size=page_size,
            status=status,
            purpose=purpose,
            date_from=datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else None,
            date_to=datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else None,
        )
        return Response(result)


class PaymentDisputeListView(APIView):
    """List payment disputes."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")
        status = request.GET.get("status")

        result = AdminManagementService.get_payment_disputes(
            chama_id=int(chama_id) if chama_id else None,
            page=page,
            page_size=page_size,
            status=status,
        )
        return Response(result)


class PaymentRefundListView(APIView):
    """List payment refunds."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")
        status = request.GET.get("status")

        result = AdminManagementService.get_payment_refunds(
            chama_id=int(chama_id) if chama_id else None,
            page=page,
            page_size=page_size,
            status=status,
        )
        return Response(result)


class RefundApproveView(APIView):
    """Approve a refund."""
    permission_classes = [IsAdminUser]

    def post(self, request, refund_id):
        try:
            result = AdminManagementService.approve_refund(refund_id, request.user)
            return Response(result)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class RefundRejectView(APIView):
    """Reject a refund."""
    permission_classes = [IsAdminUser]

    def post(self, request, refund_id):
        reason = request.data.get("reason", "")
        try:
            result = AdminManagementService.reject_refund(refund_id, request.user, reason)
            return Response(result)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


# ============ AI ADMIN DASHBOARD ============

class AIInsightsView(APIView):
    """Get AI insights."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        chama_id = request.GET.get("chama_id")
        result = AdminManagementService.get_ai_insights(
            chama_id=int(chama_id) if chama_id else None
        )
        return Response(result)


class AIFraudAlertsView(APIView):
    """Get AI fraud alerts."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        status = request.GET.get("status")

        result = AdminManagementService.get_ai_fraud_alerts(
            page=page,
            page_size=page_size,
            status=status,
        )
        return Response(result)


class AIRiskScoresView(APIView):
    """Get AI risk scores."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        chama_id = request.GET.get("chama_id")
        member_id = request.GET.get("member_id")
        result = AdminManagementService.get_ai_risk_scores(
            chama_id=int(chama_id) if chama_id else None,
            member_id=int(member_id) if member_id else None,
        )
        return Response(result)


# ============ AUTOMATIONS CENTER ============

class AutomationListView(APIView):
    """List automations."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")
        is_active = request.GET.get("is_active")
        trigger_type = request.GET.get("trigger_type")

        result = AdminManagementService.get_automations(
            chama_id=int(chama_id) if chama_id else None,
            page=page,
            page_size=page_size,
            is_active=is_active.lower() == "true" if is_active else None,
            trigger_type=trigger_type,
        )
        return Response(result)


class AutomationToggleView(APIView):
    """Toggle automation status."""
    permission_classes = [IsAdminUser]

    def post(self, request, automation_id):
        is_active = request.data.get("is_active")
        if is_active is None:
            return Response({"error": "is_active is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            result = AdminManagementService.toggle_automation(automation_id, is_active)
            return Response(result)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class AutomationLogListView(APIView):
    """List automation execution logs."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        automation_id = request.GET.get("automation_id")
        status = request.GET.get("status")

        result = AdminManagementService.get_automation_logs(
            automation_id=int(automation_id) if automation_id else None,
            page=page,
            page_size=page_size,
            status=status,
        )
        return Response(result)


# ============ APPROVALS CENTER ============

class ApprovalSummaryView(APIView):
    """Get approval summary."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        chama_id = request.GET.get("chama_id")
        result = AdminManagementService.get_approval_summary(
            chama_id=int(chama_id) if chama_id else None
        )
        return Response(result)


class PendingDisbursementsView(APIView):
    """List pending loan disbursements."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")

        result = AdminManagementService.get_pending_disbursements(
            chama_id=int(chama_id) if chama_id else None,
            page=page,
            page_size=page_size,
        )
        return Response(result)


class PendingWithdrawalsView(APIView):
    """List pending withdrawals."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")

        result = AdminManagementService.get_pending_withdrawals(
            chama_id=int(chama_id) if chama_id else None,
            page=page,
            page_size=page_size,
        )
        return Response(result)


class WithdrawalApproveView(APIView):
    """Approve a withdrawal."""
    permission_classes = [IsAdminUser]

    def post(self, request, withdrawal_id):
        try:
            result = AdminManagementService.approve_withdrawal(withdrawal_id, request.user)
            return Response(result)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class WithdrawalRejectView(APIView):
    """Reject a withdrawal."""
    permission_classes = [IsAdminUser]

    def post(self, request, withdrawal_id):
        reason = request.data.get("reason", "")
        try:
            result = AdminManagementService.reject_withdrawal(withdrawal_id, request.user, reason)
            return Response(result)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


# ============ FINANCE LEDGER ============

class LedgerListView(APIView):
    """List ledger entries."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        chama_id = request.GET.get("chama_id")
        entry_type = request.GET.get("type")
        date_from = request.GET.get("date_from")
        date_to = request.GET.get("date_to")

        result = AdminManagementService.get_ledger_entries(
            chama_id=int(chama_id) if chama_id else None,
            page=page,
            page_size=page_size,
            entry_type=entry_type,
            date_from=datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else None,
            date_to=datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else None,
        )
        return Response(result)


class LedgerSummaryView(APIView):
    """Get ledger summary."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        chama_id = request.GET.get("chama_id")
        date_from = request.GET.get("date_from")
        date_to = request.GET.get("date_to")

        result = AdminManagementService.get_ledger_summary(
            chama_id=int(chama_id) if chama_id else None,
            date_from=datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else None,
            date_to=datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else None,
        )
        return Response(result)


# ============ ADMIN SETTINGS ============

class AdminSettingsView(APIView):
    """Get admin settings."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        result = AdminManagementService.get_admin_settings()
        return Response(result)

    def put(self, request):
        try:
            result = AdminManagementService.update_admin_settings(
                settings=request.data,
                actor=request.user,
            )
            return Response(result)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class SystemHealthView(APIView):
    """Get system health status."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        result = AdminManagementService.get_system_health()
        return Response(result)
